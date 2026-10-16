#!/bin/sh
set -eu

install -d -m 700 /root/.ssh /workspaces

if [ ! -s /run/symphony/ssh/authorized_key.pub ]; then
  echo "missing authorized key at /run/symphony/ssh/authorized_key.pub" >&2
  exit 1
fi

install -m 600 /run/symphony/ssh/authorized_key.pub /root/.ssh/authorized_keys

if [ ! -s /run/secrets/codex_access_token ]; then
  echo "missing Codex access token at /run/secrets/codex_access_token" >&2
  exit 1
fi

export CODEX_ACCESS_TOKEN
CODEX_ACCESS_TOKEN=$(tr -d '\r\n' </run/secrets/codex_access_token)

git config --global user.name "${SYMPHONY_GIT_AUTHOR_NAME:-Symphony}"
git config --global user.email "${SYMPHONY_GIT_AUTHOR_EMAIL:-symphony@localhost}"

if [ -n "${GH_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf git@github.com:
  git config --global --add url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf ssh://git@github.com/
fi

exec /usr/sbin/sshd -D -e
