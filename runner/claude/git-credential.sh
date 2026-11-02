#!/bin/sh
set -eu
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
  token=${SYMPHONY_GIT_TOKEN:-${GH_TOKEN:-}}
  [ -n "$token" ] || exit 0
  printf 'username=x-access-token\npassword=%s\n' "$token"
fi
