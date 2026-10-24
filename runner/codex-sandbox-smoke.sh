#!/usr/bin/env bash
# Real credential-free protected-command qualification, never inference.
set -euo pipefail
image=${1:?image required}
image_id=$(docker image inspect --format '{{.Id}}' "$image")
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]]
root=$(cd "$(dirname "$0")/.." && pwd)
SYMPHONY_HOME="$root" source "$root/scripts/symphony" --help >/dev/null
apparmor=$(codex_apparmor_profile docker)
printf 'Protected qualification: image=%s apparmor=%s seccomp=%s\n' "$image_id" "$apparmor" "$(codex_seccomp_id)"
# Anonymous volumes have the same destinations as the listener; --rm removes
# them. No project data, native auth, environment file or host home is mounted.
# Use the listener network settings so the native helper owns its restrictions.
if ! runner_codex_run docker --rm --env SYMPHONY_CONTAINERIZED=1 --env "SYMPHONY_QUALIFICATION_APPARMOR=$apparmor" --volume /workspaces --volume /var/lib/symphony \
  --entrypoint /usr/bin/timeout "$image_id" 60 sh -ceu '
  if [ "$SYMPHONY_QUALIFICATION_APPARMOR" != none ]; then
    actual=$(cat /proc/self/attr/current) || { echo "AppArmor runtime context is unreadable" >&2; exit 1; }
    [ "$actual" = "$SYMPHONY_QUALIFICATION_APPARMOR (enforce)" ] || { echo "Shipped AppArmor profile was not applied by the selected runtime" >&2; exit 1; }
    printf "AppArmor runtime apply verified: %s\n" "$actual"
  fi
  printf "%s\n" "---" "tracker:" "  kind: memory" "workspace:" "  root: /workspaces" "---" > /tmp/qualification-WORKFLOW.md
  /opt/symphony/elixir/bin/symphony --check-codex-sandbox /tmp/qualification-WORKFLOW.md
  # Exercise the same native effects through an argv-preserving project wrapper,
  # with a quoted script path and wrapper-selected executable lookup. This is a
  # credential-free fixture; real Melody toolchain onboarding is separate proof.
  project="/workspaces/launch project"
  mkdir -p "$project/scripts" "$project/runtime"
  codex=$(command -v codex)
  printf "%s\n" "#!/bin/sh" "exec \"$codex\" \"\$@\"" > "$project/runtime/codex"
  chmod 755 "$project/runtime/codex"
  printf "%s\n" "#!/bin/bash" "set -eu" "test -f required-input" \
    "export PATH=\"\$PWD/runtime:\$PATH\"" "exec \"\$@\"" > "$project/scripts/with node.sh"
  printf "%s\n" fixture > "$project/required-input"
  printf "%s\n" "---" "tracker:" "  kind: memory" "workspace:" "  root: /workspaces" \
    "codex:" "  command: bash \"scripts/with node.sh\" codex --config shell_environment_policy.inherit=all app-server" \
    "---" > /tmp/wrapper-WORKFLOW.md
  SYMPHONY_LAUNCH_ROOT="$project" /opt/symphony/elixir/bin/symphony --check-codex-sandbox /tmp/wrapper-WORKFLOW.md
'; then
  # This credential-free command is diagnostic only; it cannot qualify readiness.
  # Keep the same runtime restrictions and preserve the original refusal result.
  docker info --format 'Kernel={{.KernelVersion}} Security={{json .SecurityOptions}}'
  runner_codex_run docker --rm --network none --volume /workspaces \
    --entrypoint /usr/bin/timeout "$image_id" 20 sh -ceu '
    uname -m
    cat /proc/self/attr/current 2>/dev/null || true
    cd /workspaces
    exec codex sandbox -c sandbox_mode=\"workspace-write\" -- /bin/true
  ' || true
  exit 1
fi
# Process/network controls are disposable and contain no native account data.
runner_codex_run docker --rm \
  --volume "$root/runner/codex-boundaries.py:/qualification.py:ro" \
  --entrypoint /usr/bin/timeout "$image_id" 40 python3 /qualification.py
