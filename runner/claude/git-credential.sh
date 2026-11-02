#!/bin/sh
set -eu
provider=claude
if [ "${1:-}" = --codex ]; then provider=codex; shift; fi
[ "${1:-}" = get ] || exit 0
protocol= host=
while IFS= read -r line; do
  case "$line" in
    protocol=*) protocol=${line#protocol=} ;;
    host=*) host=${line#host=} ;;
    '') break ;;
  esac
done
if [ "$protocol" = https ] && [ "$host" = github.com ]; then
  if [ "$provider" = codex ]; then
    # The launcher normalizes its selected project credential into both aliases.
    # A Claude-specific override must not replace that selection in Codex.
    token=${GH_TOKEN:-${GITHUB_TOKEN:-}}
  else
    token=${SYMPHONY_GIT_TOKEN:-${GH_TOKEN:-}}
  fi
  [ -n "$token" ] || exit 0
  printf 'username=x-access-token\npassword=%s\n' "$token"
fi
