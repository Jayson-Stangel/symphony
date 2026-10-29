#!/usr/bin/env bash
# Controller build callback: actual source context, native platform, bounded GHA cache.
set -euo pipefail
phase=${SYMPHONY_CANDIDATE_CACHE_PHASE:-serial}
case "$phase" in serial|load|export) ;; *) echo 'unsupported candidate cache phase' >&2; exit 1 ;; esac
provider=${1:?}; architecture=${2:?}; source=${3:?}; image=${4:?}; version=${5:?}; revision=${6:?}; selection=${7:?}
case "$provider/$architecture/$selection" in codex/arm64/current|codex/x86_64/current|claude/arm64/current|claude/x86_64/current|codex/arm64/previous|codex/x86_64/previous|claude/arm64/previous|claude/x86_64/previous) ;; *) echo 'unsupported cached candidate selection' >&2; exit 1 ;; esac
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] && [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$ ]] || { echo 'invalid candidate source/version identity' >&2; exit 1; }
expected_tag=candidate; [ "$selection" != previous ] || expected_tag=previous
[ "$image" = "localhost:5000/symphony-$provider:$expected_tag" ] || { echo 'candidate tag differs from selected source role' >&2; exit 1; }
source=$(cd "$source" && pwd -P)
[ -f "$source/runner/Dockerfile" ] && [ ! -L "$source/runner/Dockerfile" ] || { echo 'missing or nonregular source recipe' >&2; exit 1; }
[ "$(git -C "$source" rev-parse HEAD)" = "$revision" ] || { echo 'candidate source revision differs' >&2; exit 1; }
[ -z "$(git -C "$source" status --porcelain --untracked-files=all -- runner elixir .codex/skills .dockerignore)" ] || { echo 'candidate runtime inputs are dirty' >&2; exit 1; }
[ "${SYMPHONY_GHA_RUNNER_CACHE:-}" = true ] || { echo 'Actions candidate cache is not configured' >&2; exit 1; }
[ -n "${ACTIONS_RUNTIME_TOKEN:-}" ] && [ -n "${ACTIONS_RESULTS_URL:-}" ] || { echo 'Actions cache runtime unavailable' >&2; exit 1; }
root=$(cd "$(dirname "$0")/../.." && pwd)
policy=$(mktemp)
export_started=false
cache_export_event() {
  python3 - "$state" "$1" "${2:-}" <<'PYEVENT'
import hashlib,json,sys,time
from pathlib import Path
state=Path(sys.argv[1]);raw=(state/'request.json').read_bytes();request=json.loads(raw)
value=dict(event=sys.argv[2],wall_seconds=time.time(),monotonic_seconds=time.monotonic(),
           request_sha256=hashlib.sha256(raw).hexdigest(),builder=request['builder'],
           image_id=request['image_id'],build_ref=request['build_ref'])
if sys.argv[3]:value['exit_status']=int(sys.argv[3])
with (state/'events.jsonl').open('a') as output:output.write(json.dumps(value)+'\n')
PYEVENT
}
finish() {
  status=$?
  trap - EXIT
  if [ "$export_started" = true ]; then
    cache_export_event cache-export-complete "$status" || { [ "$status" != 0 ] || status=1; }
  fi
  rm -f "$policy"
  exit "$status"
}
trap finish EXIT
EVENT=${GITHUB_EVENT_NAME:?} REF=${GITHUB_REF:?} TARGET=$provider GITHUB_OUTPUT=$policy bash "$root/scripts/release/runner-cache-policy.sh"
namespace=$(sed -n 's/^namespace=//p' "$policy")
arch=$(sed -n 's/^arch=//p' "$policy")
expected=$architecture; [ "$architecture" != x86_64 ] || expected=amd64
[ "$arch" = "$expected" ] || { echo 'candidate architecture differs from native builder' >&2; exit 1; }
flags=()
for partition in "$namespace" trusted; do
  for target in codex claude browser-tools; do
    flags+=(--cache-from "type=gha,scope=runner-v1-$arch-$partition-$target")
    flags+=(--cache-from "type=gha,scope=runner-v1-$arch-$partition-$target-prior")
  done
done
scope="runner-v1-$arch-$namespace-$provider"
[ "$selection" != previous ] || scope="$scope-prior"
diagnostic=${SYMPHONY_CANDIDATE_CACHE_DIAGNOSTIC:-}
case "$diagnostic" in
  '') ;;
  serial|overlap)
    [ "$provider/$architecture/$selection" = claude/arm64/current ] || { echo 'controller cache diagnostic is Claude ARM64 only' >&2; exit 1; }
    [ "$GITHUB_EVENT_NAME/$GITHUB_REF" = workflow_dispatch/refs/heads/dev ] && [ "${GITHUB_ACTOR:-}/${GITHUB_TRIGGERING_ACTOR:-}" = Jayson-Stangel/Jayson-Stangel ] || { echo 'diagnostic requires exact owner manual dev context' >&2; exit 1; }
    [[ "${GITHUB_RUN_ID:-}/${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*/[1-9][0-9]*$ ]] || exit 1
    [ "${SYMPHONY_CANDIDATE_CACHE_APPROVED_REVISION:-}" = "$GITHUB_SHA" ] || { echo 'diagnostic approved workflow source differs' >&2; exit 1; }
    scope="$scope-controller-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$diagnostic"
    flags=(--cache-from "type=gha,scope=$scope")
    ;;
  *) echo 'unsupported bounded controller cache diagnostic' >&2; exit 1 ;;
esac
cold_flags=()
case "${SYMPHONY_CANDIDATE_CACHE_COLD:-false}" in
  false) ;;
  true) [ -n "$diagnostic" ] || exit 1; [ "$phase" = export ] || cold_flags=(--no-cache) ;;
  *) exit 1 ;;
esac
input_flags=()
if [ -n "${SYMPHONY_CANDIDATE_INPUT_IDENTITY:-}" ]; then
  [[ "$SYMPHONY_CANDIDATE_INPUT_IDENTITY" =~ ^[a-f0-9]{64}$ ]] || { echo 'invalid selected build input identity' >&2; exit 1; }
  input_flags=(--build-arg "SYMPHONY_INPUT_IDENTITY=$SYMPHONY_CANDIDATE_INPUT_IDENTITY")
fi
# Default serial invocation is unchanged. The opt-in pair uses only one loaded
# image; its second solve exports max cache from the exact same local builder.
state=${SYMPHONY_CANDIDATE_CACHE_STATE:-}
if [ "$phase" != serial ] || [ -n "$diagnostic" ]; then
  [ -n "$state" ] || { echo 'missing owned cache state directory' >&2; exit 1; }
  [ -d "$state" ] && [ ! -L "$state" ] || { echo 'cache state is not a regular owned directory' >&2; exit 1; }
  if [ "$phase" != export ]; then
    [ ! -e "$state/load.json" ] && [ ! -e "$state/request.json" ] || { echo 'cache load state already exists' >&2; exit 1; }
    output_flags=(--load --metadata-file "$state/load.json")
    if [ "$phase" = serial ]; then output_flags+=(--cache-to "type=gha,scope=$scope,mode=max"); fi
  else
    [ -f "$state/request.json" ] && [ ! -L "$state/request.json" ] || { echo 'cache request missing' >&2; exit 1; }
    [ ! -e "$state/export.json" ] || { echo 'cache export metadata already exists' >&2; exit 1; }
    builder=$(python3 - "$state/request.json" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection" "$scope" "${SYMPHONY_CANDIDATE_INPUT_IDENTITY:-}" <<'PYCHECK'
import json,re,sys
value=json.load(open(sys.argv[1]))
assert value['context']==sys.argv[2:], 'cache export context differs from loaded image'
assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value['builder']), 'invalid exact builder identity'
print(value['builder'])
PYCHECK
)
    output_flags=(--output type=cacheonly --builder "$builder" --cache-to "type=gha,scope=$scope,mode=max" --metadata-file "$state/export.json")
    # Cache-only exports no image, performs no load/tag/push and does not carry
    # a cold --no-cache flag. Its local-hit assumption requires native proof.
    flags=()
  fi
else
  output_flags=(--load --cache-to "type=gha,scope=$scope,mode=max")
fi
tag_flags=(); [ "$phase" = export ] || tag_flags=(-t "$image")
if [ "$phase" = export ]; then
  cache_export_event cache-export-start
  export_started=true
fi
docker buildx build "${output_flags[@]}" ${cold_flags[@]+"${cold_flags[@]}"} ${tag_flags[@]+"${tag_flags[@]}"} --platform "linux/$arch" --target "$provider" -f "$source/runner/Dockerfile" \
  --build-arg "SYMPHONY_VERSION=$version" --build-arg "SYMPHONY_REVISION=$revision" \
  ${input_flags[@]+"${input_flags[@]}"} ${flags[@]+"${flags[@]}"} "$source"
if [ "$phase" != export ] && [ -n "$state" ]; then
  python3 - "$state" "$provider" "$architecture" "$source" "$image" "$version" "$revision" "$selection" "$scope" "${SYMPHONY_CANDIDATE_INPUT_IDENTITY:-}" <<'PYBIND'
import json,re,sys
from pathlib import Path
state=Path(sys.argv[1]);metadata=json.loads((state/'load.json').read_text())
ref=metadata['buildx.build.ref'];parts=ref.split('/')
assert len(parts)==3 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',parts[0]), 'missing actual builder reference'
image=metadata['containerimage.config.digest']
assert re.fullmatch(r'sha256:[a-f0-9]{64}',image), 'missing actual loaded image identity'
with (state/'request.json').open('x') as output:
 json.dump(dict(context=sys.argv[2:],builder=parts[0],build_ref=ref,image_id=image),output)
PYBIND
fi
