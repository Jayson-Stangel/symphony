#!/usr/bin/env bash
# Credential-free production-image proof; no native account or model requests.
set -euo pipefail
image=${1:?Usage: runner/claude/smoke.sh IMAGE}
root=$(cd "$(dirname "$0")/../.." && pwd)
SYMPHONY_HOME="$root" source "$root/scripts/symphony" --help >/dev/null
fixture=$(mktemp -d)
name="symphony-claude-smoke-$(date +%s)-$$"
cleanup() {
  docker logs "$name" 2>/dev/null || true
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$fixture"
}
trap cleanup EXIT
chmod 755 "$fixture"
cat > "$fixture/WORKFLOW.md" <<'WORKFLOW'
---
tracker:
  kind: memory
workspace:
  root: /workspaces
polling:
  interval_ms: 1000
observability:
  dashboard_enabled: false
---
No issues exist in this isolated smoke test.
WORKFLOW
chmod 644 "$fixture/WORKFLOW.md"
# The inert value satisfies the entrypoint's Git configuration contract; network
# isolation and the empty tracker prevent any repository or inference requests.
runner_claude_run docker -d --name "$name" --network none \
  --tmpfs /home/claude:rw,nosuid,nodev,uid=11001,gid=11001,mode=700,size=32m \
  --tmpfs /var/lib/symphony:rw,nosuid,nodev,uid=11001,gid=11001,mode=700,size=32m \
  --tmpfs /workspaces:rw,nosuid,nodev,uid=11001,gid=11001,mode=700,size=32m \
  -e SYMPHONY_GIT_TOKEN=credential-free-smoke-placeholder \
  --mount "type=bind,src=$fixture/WORKFLOW.md,dst=/etc/symphony/WORKFLOW.md,readonly" \
  --mount "type=bind,src=$root/runner/claude/test,dst=/opt/symphony/claude/test,readonly" \
  "$image" >/dev/null
[ "$(docker exec "$name" id -u)" = 11001 ]
# Run the checked-in Linux tests against the image's production helper modules.
docker exec "$name" node --test /opt/symphony/claude/test/process-lifecycle.test.mjs
# Poll synchronously through HTTP: a PID alone cannot establish listener liveness.
docker exec -i "$name" python3 - <<'PY'
import json
import time
from urllib.request import urlopen

for attempt in range(60):
    try:
        with urlopen('http://127.0.0.1:4010/api/v1/state', timeout=2) as response:
            state = json.load(response)
        if state.get('agent', {}).get('readiness', {}).get('code') == 'checking':
            time.sleep(1)
            continue
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit('listener did not serve a state snapshot')
assert 'error' not in state, state
assert state['counts'] == {'running': 0, 'retrying': 0, 'blocked': 0}, state
agent = state['agent']
assert agent['provider'] == 'claude', agent
assert agent['readiness']['ready'] is False, agent
assert agent['readiness']['code'] == 'auth_required', agent
print(json.dumps({'counts': state['counts'], 'agent': agent}, sort_keys=True))
PY
[ "$(docker inspect --format '{{.State.Running}}' "$name")" = true ]
docker stop --time 20 "$name" >/dev/null
# CLI.wait_for_shutdown currently maps supervisor :shutdown to exit 1. Accept
# that convention or normal exit 0, never a forced SIGKILL (137) or other failure.
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
echo "Listener stop exit code: $exit_code"
case "$exit_code" in 0|1) ;; *) exit 1 ;; esac
[ "$(docker inspect --format '{{.State.OOMKilled}}' "$name")" = false ]
echo 'Production listener, unauthenticated gate, Linux cleanup and TERM shutdown without forced kill passed.'
