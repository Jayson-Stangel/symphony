#!/usr/bin/env bash
# Bounded credential-free qualification of the exact digest selected by a release.
set -euo pipefail
image=${1:?immutable image required}
provider=${2:?provider required}
architecture=${3:?architecture required}
revision=${4:?source revision required}
case "$provider/$architecture" in codex/arm64|codex/x86_64|claude/arm64|claude/x86_64) ;; *) echo 'unsupported provider/architecture' >&2; exit 1 ;; esac
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || { echo 'an immutable registry digest is required' >&2; exit 1; }
[ "$(docker image inspect --format '{{index .Config.Labels "com.symphony.agent"}}' "$image")" = "$provider" ]
[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" = "$revision" ]
expected_arch=$architecture
[ "$architecture" != x86_64 ] || expected_arch=amd64
[ "$(docker image inspect --format '{{.Architecture}}' "$image")" = "$expected_arch" ]
[ "$(docker image inspect --format '{{index .Config.Labels "com.symphony.architecture"}}' "$image")" = "$expected_arch" ]
# No home, repository, credentials or Docker socket mounts. The engine enforces
# a command time limit inside the Linux image, including from a macOS host.
docker run --rm --network none --entrypoint /usr/bin/timeout "$image" 90 /bin/sh -ec '
  git --version
  node --version
  playwright --version
  test -x /opt/symphony/elixir/bin/symphony
  for skill in commit push pull linear land; do test -r "/opt/symphony/skills/$skill/SKILL.md"; done
  workspace=$(mktemp -d)
  git -C "$workspace" init -q
  git -C "$workspace" -c user.name=Release -c user.email=release@localhost commit --allow-empty -qm smoke
  test -n "$(git -C "$workspace" rev-parse HEAD)"
  node /opt/symphony/browser/smoke.cjs
'
case "$provider" in
  codex) docker run --rm --network none --entrypoint /usr/bin/timeout "$image" 30 codex --version ;;
  claude) docker run --rm --network none --entrypoint /usr/bin/timeout "$image" 30 /usr/local/bin/node /opt/symphony/claude/node_modules/@anthropic-ai/claude-code/cli.js --version ;;
esac
