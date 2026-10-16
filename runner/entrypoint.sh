#!/bin/sh
set -eu

workflow=/etc/symphony/WORKFLOW.md
[ -r "$workflow" ] || { echo "missing workflow at $workflow" >&2; exit 1; }
install -d -m 700 /root/.codex /workspaces /var/lib/symphony
install -d -m 700 /root/.codex/skills
for skill in commit push pull linear land; do
  rm -rf "/root/.codex/skills/symphony-$skill"
  cp -R "/opt/symphony/skills/$skill" "/root/.codex/skills/symphony-$skill"
done
git config --global user.name "${SYMPHONY_GIT_AUTHOR_NAME:-Symphony}"
git config --global user.email "${SYMPHONY_GIT_AUTHOR_EMAIL:-symphony@localhost}"

if [ -n "${GH_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf git@github.com:
  git config --global --add url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf ssh://git@github.com/
fi

exec /opt/symphony/elixir/bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --logs-root /var/lib/symphony/logs \
  --port "${SYMPHONY_PORT:-4010}" "$workflow"
