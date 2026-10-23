#!/usr/bin/env bash
# Actual default-entrypoint liveness, empty memory tracker, no network or credentials.
set -euo pipefail
image=${1:?image required}
provider=${2:?provider required}
case "$provider" in codex) uid=0; home=/root ;; claude) uid=11001; home=/home/claude ;; *) exit 1 ;; esac
fixture=$(mktemp -d)
name="symphony-release-smoke-$$-$(date +%s)"
cleanup() {
  docker logs "$name" 2>/dev/null || true
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -f "$fixture/WORKFLOW.md"
  rmdir "$fixture"
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
No issues exist in this isolated credential-free release qualification.
WORKFLOW
chmod 644 "$fixture/WORKFLOW.md"
launch=(docker run --cap-drop ALL)
if [ "$provider" = codex ]; then
  root=$(cd "$(dirname "$0")/../.." && pwd)
  SYMPHONY_HOME="$root" source "$root/scripts/symphony" --help >/dev/null
  launch=(runner_codex_run docker)
fi
"${launch[@]}" -d --name "$name" --network none --read-only \
  --security-opt no-new-privileges --pids-limit 512 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --tmpfs "$home:rw,nosuid,nodev,uid=$uid,gid=$uid,mode=700,size=32m" \
  --tmpfs "/var/lib/symphony:rw,nosuid,nodev,uid=$uid,gid=$uid,mode=700,size=32m" \
  --tmpfs "/workspaces:rw,nosuid,nodev,uid=$uid,gid=$uid,mode=700,size=32m" \
  -e SYMPHONY_CONTAINERIZED=1 \
  -e SYMPHONY_GIT_TOKEN=credential-free-smoke-placeholder \
  --mount "type=bind,src=$fixture/WORKFLOW.md,dst=/etc/symphony/WORKFLOW.md,readonly" \
  "$image" >/dev/null
docker exec -i "$name" python3 - "$provider" <<'PY'
import json
import sys
import time
from urllib.request import urlopen

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urlopen('http://127.0.0.1:4010/api/v1/state', timeout=2) as response:
            state = json.load(response)
        if state.get('agent', {}).get('readiness', {}).get('code') != 'checking':
            break
    except OSError:
        pass
    time.sleep(1)
else:
    raise SystemExit('listener did not serve a qualified state snapshot within 90 seconds')
assert 'error' not in state, state
assert state['counts'] == {'running': 0, 'retrying': 0, 'blocked': 0}, state
assert state['agent']['provider'] == sys.argv[1], state
if sys.argv[1] == 'claude':
    assert state['agent']['readiness']['ready'] is False, state
    assert state['agent']['readiness']['code'] == 'auth_required', state
else:
    assert state['agent']['readiness']['ready'] is True, state
    assert state['agent']['admission']['ready'] is True, state
    effective = state['agent']['execution']['effective']['codex']
    assert effective['thread_sandbox'] == 'workspace-write', state
    assert effective['turn_sandbox_type'] in ('default', 'workspaceWrite'), state
# Credential-free readiness does not establish native account login.
print(json.dumps({'counts': state['counts'], 'agent': state['agent']}, sort_keys=True))
PY
docker stop --time 20 "$name" >/dev/null
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$name")
case "$exit_code" in 0|1) ;; *) echo "unexpected listener stop exit: $exit_code" >&2; exit 1 ;; esac
[ "$(docker inspect --format '{{.State.OOMKilled}}' "$name")" = false ]
echo 'Default entrypoint, empty tracker, credential-free policy/auth snapshot and bounded stop passed.'
