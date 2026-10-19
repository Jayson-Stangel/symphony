#!/bin/sh
set -eu
[ "$(id -u)" = 11001 ] || { echo 'Claude runner requires UID 11001' >&2; exit 1; }
workflow=/etc/symphony/WORKFLOW.md
[ -r "$workflow" ] || { echo 'missing workflow' >&2; exit 1; }
[ -n "${SYMPHONY_GIT_TOKEN:-}" ] || { echo 'missing selected repository Git credential' >&2; exit 1; }
git config --global user.name "${SYMPHONY_GIT_AUTHOR_NAME:-Symphony}"
git config --global user.email "${SYMPHONY_GIT_AUTHOR_EMAIL:-symphony@localhost}"
git config --global credential.helper /usr/local/bin/symphony-git-credential
git config --global --replace-all url.https://github.com/.insteadOf git@github.com:
git config --global --add url.https://github.com/.insteadOf ssh://git@github.com/
exec /opt/symphony/elixir/bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --agent claude --logs-root /var/lib/symphony/logs \
  --port "${SYMPHONY_PORT:-4010}" "$workflow"
