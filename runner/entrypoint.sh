#!/bin/sh
set -eu

workflow=/etc/symphony/WORKFLOW.md
[ -r "$workflow" ] || { echo "missing workflow at $workflow" >&2; exit 1; }
install -d -m 700 /root/.codex /workspaces /var/lib/symphony
python3 /usr/local/lib/symphony/node-headers.py --prepare-home /root
install -d -m 700 /root/.codex/skills
for skill in commit push pull linear land; do
  rm -rf "/root/.codex/skills/symphony-$skill"
  cp -R "/opt/symphony/skills/$skill" "/root/.codex/skills/symphony-$skill"
done
git config --global user.name "${SYMPHONY_GIT_AUTHOR_NAME:-Symphony}"
git config --global user.email "${SYMPHONY_GIT_AUTHOR_EMAIL:-symphony@localhost}"

# Bind only the launcher's selected repository credential, never an ambient
# provider-specific override. This existing Git route survives tracker filtering.
export SYMPHONY_GIT_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
git config --global credential.helper /usr/local/bin/symphony-git-credential
git config --global --replace-all url.https://github.com/.insteadOf git@github.com:
git config --global --add url.https://github.com/.insteadOf ssh://git@github.com/

exec /opt/symphony/elixir/bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --logs-root /var/lib/symphony/logs \
  --port "${SYMPHONY_PORT:-4010}" "$workflow"
