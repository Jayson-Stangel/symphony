#!/usr/bin/env bash
# Controller build callback: actual source context, native platform, bounded GHA cache.
set -euo pipefail
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
trap 'rm -f "$policy"' EXIT
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
input_flags=()
if [ -n "${SYMPHONY_CANDIDATE_INPUT_IDENTITY:-}" ]; then
  [[ "$SYMPHONY_CANDIDATE_INPUT_IDENTITY" =~ ^[a-f0-9]{64}$ ]] || { echo 'invalid selected build input identity' >&2; exit 1; }
  input_flags=(--build-arg "SYMPHONY_INPUT_IDENTITY=$SYMPHONY_CANDIDATE_INPUT_IDENTITY")
fi
docker buildx build --load --platform "linux/$arch" --target "$provider" -f "$source/runner/Dockerfile" \
  --build-arg "SYMPHONY_VERSION=$version" --build-arg "SYMPHONY_REVISION=$revision" \
  ${input_flags[@]+"${input_flags[@]}"} "${flags[@]}" --cache-to "type=gha,scope=$scope,mode=max" -t "$image" "$source"
