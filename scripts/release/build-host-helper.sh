#!/usr/bin/env bash
# Build on macOS in release CI; installed Macs never need Swift or Xcode.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:?Usage: build-host-helper.sh OUTPUT [--native]}
mkdir -p "$(dirname "$output")"
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
printf 'let compiledRevision = "%s"\n' "$(git -C "$root" rev-parse HEAD)" > "$stage/host.swift"
cat "$root/scripts/package/host.swift" >> "$stage/host.swift"
if [ "${2:-}" = --native ]; then
  swiftc -module-cache-path "$stage/cache" -O "$stage/host.swift" -o "$output"
else
  for arch in arm64 x86_64; do
    swiftc -module-cache-path "$stage/cache" -O -target "$arch-apple-macosx14.0" "$stage/host.swift" -o "$stage/$arch"
  done
  lipo -create "$stage/arm64" "$stage/x86_64" -output "$output"
fi
chmod 755 "$output"
