#!/usr/bin/env bash
# Build only on a supported native host; installed users need no Swift toolchain.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:?Usage: build-host-helper.sh OUTPUT [--native]}
case "${2:-}" in ''|--native) ;; *) echo 'unexpected host helper build option' >&2; exit 1 ;; esac
# The two retained native hosts are distinct from both Linux runner architectures.
# Refuse before staging or invoking Swift; never compile an Intel macOS slice.
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) target=arm64-apple-macosx14.0 ;;
  Linux/x86_64) target=x86_64-unknown-linux-gnu ;;
  *) echo 'host helper supports only macOS ARM64 and Linux x86-64' >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$output")"
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
printf 'let compiledRevision = "%s"\n' "$(git -C "$root" rev-parse HEAD)" > "$stage/host.swift"
cat "$root/scripts/package/host.swift" >> "$stage/host.swift"
swiftc -module-cache-path "$stage/cache" -O -target "$target" "$stage/host.swift" -o "$output"
chmod 755 "$output"
