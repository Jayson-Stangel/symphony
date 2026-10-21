#!/usr/bin/env bash
# Exact lock function from 5a10e32fb8f7506af099a4aa7da12f31c9ad9df0:scripts/symphony.
set -euo pipefail
die() { printf 'symphony: %s\n' "$*" >&2; exit 1; }

runner_transition_lock() {
  local path=$1 owner
  [ ! -L "$path" ] || die 'unsafe updater lock'
  if [ -d "$path" ]; then
    [ -f "$path/owner" ] || die 'updater lock has no verifiable owner; inspect it before recovery'
    owner=$(cat "$path/owner")
    case "$owner" in ''|*[!0-9]*) die 'invalid updater lock owner' ;; esac
    if kill -0 "$owner" 2>/dev/null; then die 'another updater is active'; fi
    rm "$path/owner"; rmdir "$path"
  fi
  mkdir "$path" || die 'another updater acquired the project lock'
  printf '%s\n' "$$" >"$path/owner"
}
