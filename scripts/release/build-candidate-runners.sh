#!/usr/bin/env bash
# Candidate-only, native runner build/pull qualification; never logs in/publishes.
set -euo pipefail
provider=${1:?provider}; architecture=${2:?architecture}; output=${3:?output}; previous=${4:-}
root=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$output"
output=$(cd "$output" && pwd)
registry_state="$output/$provider-$architecture.registry-state.txt"
overlap=${SYMPHONY_CANDIDATE_CACHE_OVERLAP:-false}
case "$overlap" in true|false) ;; *) echo 'invalid cache overlap mode' >&2; exit 1 ;; esac
cache_pid=''; cache_state=''
if [ "$overlap" = true ] || [ -n "${SYMPHONY_CANDIDATE_CACHE_DIAGNOSTIC:-}" ]; then
  [ -z "$previous" ] && [ "${SYMPHONY_CANDIDATE_CAPTURE_INPUTS:-}" = 1 ] && [ -n "${SYMPHONY_CANDIDATE_BUILD_COMMAND:-}" ] || { echo 'cache overlap requires one current source and captured inputs' >&2; exit 1; }
  command -v timeout >/dev/null || { echo 'bounded cache export requires GNU timeout' >&2; exit 1; }
  cache_state="$output/$provider-$architecture-cache-export"
  mkdir -m 700 "$cache_state"
fi
cache_event() {
  [ -n "$cache_state" ] || return 0
  python3 - "$cache_state/events.jsonl" "$1" <<'PYEVENT'
import json,sys,time
with open(sys.argv[1],'a') as output:output.write(json.dumps(dict(event=sys.argv[2],wall_seconds=time.time(),monotonic_seconds=time.monotonic()))+'\n')
PYEVENT
}
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  # Reap the one direct timeout child before registry cleanup on every exit.
  # GNU timeout forwards TERM to the export command and bounds it with KILL.
  if [ -n "$cache_pid" ]; then
    kill -TERM "$cache_pid" 2>/dev/null || true
    wait "$cache_pid" 2>/dev/null || true
    cache_pid=''
  fi
  cache_event cleanup || status=1
  python3 "$root/scripts/release/test_registry.py" cleanup "$registry_state" || status=1
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$root/scripts/release/test_registry.py" start "$registry_state"
registry_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container_id"])' "$registry_state")
images=()
records=()
for selection in current previous; do
  source=$root; destination=$output; tag=candidate
  if [ "$selection" = previous ]; then
    [ -n "$previous" ] || continue
    [[ "$previous" =~ ^[a-f0-9]{40}$ ]] || { echo 'previous_revision must be a full source SHA' >&2; exit 1; }
    source="$RUNNER_TEMP/previous-source"
    bash "$root/scripts/release/prepare-candidate-source.sh" "$previous" "$source"
    destination="$output/previous"; mkdir -p "$destination"; tag=previous
    grep -q 'package_platform=linux_x86_64' "$source/scripts/symphony-package.sh" || { echo 'previous source lacks Linux package capability' >&2; exit 1; }
  fi
  revision=$(git -C "$source" rev-parse HEAD)
  version=$(sed -nE 's/^[[:space:]]*version: "([^"]+)",$/\1/p' "$source/elixir/mix.exs" | head -n1)
  image="localhost:5000/symphony-$provider:$tag"
  record_flags=(); build_flags=()
  unset SYMPHONY_CANDIDATE_INPUT_IDENTITY
  if [ "${SYMPHONY_CANDIDATE_CAPTURE_INPUTS:-}" = 1 ]; then
    inputs="$destination/$provider-$architecture.inputs.txt"
    python3 "$root/scripts/release/runner_inputs.py" --root "$source" --provider "$provider" --architecture "$architecture" --output "$inputs"
    SYMPHONY_CANDIDATE_INPUT_IDENTITY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["input_identity"])' "$inputs")
    export SYMPHONY_CANDIDATE_INPUT_IDENTITY
    if [ "$selection" = current ] && [ -n "${SYMPHONY_CANDIDATE_EXPECTED_INPUT_IDENTITY:-}" ]; then
      [ "$SYMPHONY_CANDIDATE_INPUT_IDENTITY" = "$SYMPHONY_CANDIDATE_EXPECTED_INPUT_IDENTITY" ] || { echo 'selected input identity differs from current plan' >&2; exit 1; }
    fi
    record_flags=(--inputs "$inputs" --source-root "$source" --archive-tag "$image")
    build_flags=(--build-arg "SYMPHONY_INPUT_IDENTITY=$SYMPHONY_CANDIDATE_INPUT_IDENTITY")
  fi
  if [ -n "${SYMPHONY_CANDIDATE_BUILD_COMMAND:-}" ]; then
    [ -x "$SYMPHONY_CANDIDATE_BUILD_COMMAND" ] || { echo 'candidate build command is not executable' >&2; exit 1; }
    cache_event load-start
    if [ "$overlap" = true ]; then
      SYMPHONY_CANDIDATE_CACHE_PHASE=load SYMPHONY_CANDIDATE_CACHE_STATE="$cache_state" \
        "$SYMPHONY_CANDIDATE_BUILD_COMMAND" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection"
      cache_event load-complete
      SYMPHONY_CANDIDATE_CACHE_PHASE=export SYMPHONY_CANDIDATE_CACHE_STATE="$cache_state" \
        timeout --signal=TERM --kill-after=5s 600 "$SYMPHONY_CANDIDATE_BUILD_COMMAND" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection" \
        > "$cache_state/export.log" 2>&1 &
      cache_pid=$!
      cache_event cache-export-launched
    else
      SYMPHONY_CANDIDATE_CACHE_PHASE=serial SYMPHONY_CANDIDATE_CACHE_STATE="$cache_state" \
        "$SYMPHONY_CANDIDATE_BUILD_COMMAND" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection"
      cache_event load-complete
    fi
  else
    docker build --target "$provider" -f "$source/runner/Dockerfile" \
      --build-arg SYMPHONY_VERSION="$version" --build-arg SYMPHONY_REVISION="$revision" ${build_flags[@]+"${build_flags[@]}"} -t "$image" "$source"
  fi
  cache_event qualification-transfer-start
  docker push "$image"
  digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")
  docker image rm "$image"
  if docker image inspect "$digest" >/dev/null 2>&1; then docker image rm "$digest"; fi
  if docker image inspect "$digest" >/dev/null 2>&1; then echo 'Selected image still cached; refusing false pull proof' >&2; exit 1; fi
  docker pull "$digest"
  # Qualify the exact source contract; historical proof is not current readiness.
  if [ "$selection" = previous ]; then
    git -C "$source" diff --quiet "$revision" -- scripts/release scripts/symphony || { echo 'historical qualification sources changed' >&2; exit 1; }
    bash "$source/scripts/release/runner-smoke.sh" "$digest" "$provider" "$architecture" "$revision"
    printf '%s\n' historical-source-contract-only > "$destination/$provider-$architecture.qualification-scope.txt"
  else
    bash "$root/scripts/release/runner-smoke.sh" "$digest" "$provider" "$architecture" "$revision"
    printf '%s\n' current-source-contract > "$destination/$provider-$architecture.qualification-scope.txt"
  fi
  # New reference environments qualify their own committed Node/npm contract.
  # Historical build sources and retained components keep their original proof.
  if git -C "$source" cat-file -e "$revision:runner/node-smoke.sh" 2>/dev/null; then
    git -C "$source" show "$revision:runner/node-smoke.sh" | bash -s -- "$digest"
  fi
  python3 "$root/scripts/release/record.py" --image "$digest" --provider "$provider" --architecture "$architecture" \
    --revision "$revision" --registry-state "$registry_state" ${record_flags[@]+"${record_flags[@]}"} --output "$destination/$provider-$architecture.json"
  cache_event qualification-record-complete
  docker tag "$digest" "$image"
  images+=("$image")
  records+=("$destination/$provider-$architecture.json")
done
# One archive for both real source revisions shares their common layers.
export_arguments=()
for image in "${images[@]}"; do export_arguments+=(--image "$image"); done
for record in "${records[@]}"; do export_arguments+=(--record "$record"); done
cache_event archive-start
python3 "$root/scripts/release/export-image.py" "${export_arguments[@]}" --registry-state "$registry_state" \
  --output "$output/$provider-$architecture.docker.tar.gz" \
  --checksum-output "$output/$provider-$architecture.sha256"
cache_event archive-complete
docker logs "$registry_id" > "$output/$provider-$architecture.registry.log" 2>&1

if [ -n "$cache_pid" ]; then
  # Qualification, original record and archive use the first loaded image.
  # Neither a failed export nor an image replacement may become producer success.
  cache_status=0
  wait "$cache_pid" || cache_status=$?
  cache_pid=''
  [ "$cache_status" = 0 ] || { echo 'max cache export failed' >&2; exit "$cache_status"; }
  [ -f "$cache_state/export.json" ] || { echo 'missing completed cache export metadata' >&2; exit 1; }
  cache_event cache-export-joined
fi
if [ -n "$cache_state" ]; then
  expected_id=$(python3 - "$cache_state" <<'PYJOIN'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);request=json.loads((root/'request.json').read_text());result=json.loads((root/'export.json').read_text()) if (root/'export.json').exists() else None
if result:
 ref=result['buildx.build.ref']
 assert ref.startswith(request['builder']+'/') and ref!=request['build_ref'], 'cache export did not complete on the original builder'
print(request['image_id'])
PYJOIN
)
  [ "$(docker image inspect --format '{{.Id}}' "${images[0]}")" = "$expected_id" ] || { echo 'loaded image identity changed during cache export' >&2; exit 1; }
fi
cache_event producer-complete
