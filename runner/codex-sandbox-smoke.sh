#!/usr/bin/env bash
# Real credential-free protected-command qualification, never inference.
set -euo pipefail
image=${1:?image required}
image_id=$(docker image inspect --format '{{.Id}}' "$image")
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]]
root=$(cd "$(dirname "$0")/.." && pwd)
SYMPHONY_HOME="$root" source "$root/scripts/symphony" --help >/dev/null
# Anonymous volumes have the same destinations as the listener; --rm removes
# them. No project data, native auth, environment file or host home is mounted.
# Use the listener network settings so the native helper owns its restrictions.
runner_codex_run docker --rm --env SYMPHONY_CONTAINERIZED=1 --volume /workspaces --volume /var/lib/symphony \
  --entrypoint /usr/bin/timeout "$image_id" 25 sh -ceu '
  printf "%s\n" "---" "tracker:" "  kind: memory" "workspace:" "  root: /workspaces" "---" > /tmp/qualification-WORKFLOW.md
  exec /opt/symphony/elixir/bin/symphony --check-codex-sandbox /tmp/qualification-WORKFLOW.md
'
# Process/network controls are disposable and contain no native account data.
runner_codex_run docker --rm \
  --volume "$root/runner/codex-boundaries.py:/qualification.py:ro" \
  --entrypoint /usr/bin/timeout "$image_id" 40 python3 /qualification.py
