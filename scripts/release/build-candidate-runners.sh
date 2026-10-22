#!/usr/bin/env bash
# Candidate-only, native runner build/pull qualification; never logs in/publishes.
set -euo pipefail
provider=${1:?provider}; architecture=${2:?architecture}; output=${3:?output}; previous=${4:-}
root=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$output"
output=$(cd "$output" && pwd)
registry_state="$output/$provider-$architecture.registry-state.txt"
cleanup() {
  local status=$?
  trap - EXIT
  python3 "$root/scripts/release/test_registry.py" cleanup "$registry_state" || status=1
  exit "$status"
}
trap cleanup EXIT
python3 "$root/scripts/release/test_registry.py" start "$registry_state"
registry_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container_id"])' "$registry_state")
images=()
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
    "$SYMPHONY_CANDIDATE_BUILD_COMMAND" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection"
  else
    docker build --target "$provider" -f "$source/runner/Dockerfile" \
      --build-arg SYMPHONY_VERSION="$version" --build-arg SYMPHONY_REVISION="$revision" ${build_flags[@]+"${build_flags[@]}"} -t "$image" "$source"
  fi
  docker push "$image"
  digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")
  docker image rm "$image"
  if docker image inspect "$digest" >/dev/null 2>&1; then docker image rm "$digest"; fi
  if docker image inspect "$digest" >/dev/null 2>&1; then echo 'Selected image still cached; refusing false pull proof' >&2; exit 1; fi
  docker pull "$digest"
  bash "$root/scripts/release/runner-smoke.sh" "$digest" "$provider" "$architecture" "$revision"
  python3 "$root/scripts/release/record.py" --image "$digest" --provider "$provider" --architecture "$architecture" \
    --revision "$revision" ${record_flags[@]+"${record_flags[@]}"} --output "$destination/$provider-$architecture.json"
  docker tag "$digest" "$image"
  images+=("$image")
done
# One archive for both real source revisions shares their common layers.
docker save "${images[@]}" | gzip > "$output/$provider-$architecture.docker.tar.gz"
(cd "$output" && sha256sum ./*.docker.tar.gz > "$provider-$architecture.sha256")
docker logs "$registry_id" > "$output/$provider-$architecture.registry.log" 2>&1
