#!/bin/sh
set -eu

install -d -m 700 /root/.ssh /root/.codex /workspaces

if [ ! -s /run/symphony/ssh/authorized_key.pub ]; then
  echo "missing authorized key at /run/symphony/ssh/authorized_key.pub" >&2
  exit 1
fi

install -m 600 /run/symphony/ssh/authorized_key.pub /root/.ssh/authorized_keys
git config --global user.name "${SYMPHONY_GIT_AUTHOR_NAME:-Symphony}"
git config --global user.email "${SYMPHONY_GIT_AUTHOR_EMAIL:-symphony@localhost}"

if [ -n "${GH_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf git@github.com:
  git config --global --add url."https://x-access-token:${GH_TOKEN}@github.com/".insteadOf ssh://git@github.com/
fi

exec /usr/sbin/sshd -D -e
