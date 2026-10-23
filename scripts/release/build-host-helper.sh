#!/usr/bin/env bash
# Build only on retained native hosts; build tools never become runtime dependencies.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:?Usage: build-host-helper.sh OUTPUT [--native]}
case "${2:-}" in ''|--native) ;; *) echo 'unknown host helper build option' >&2; exit 1 ;; esac
# Refuse before staging or invoking any compiler. Linux container ARM64
# remains supported without creating a Linux ARM64 host helper.
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64|Linux/x86_64) ;;
  *) echo 'host helper supports only macOS ARM64 and Linux x86-64' >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$output")"
output=$(cd "$(dirname "$output")" && pwd)/$(basename "$output")
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
printf 'let compiledRevision = "%s"\n' "$(git -C "$root" rev-parse HEAD)" > "$stage/host.swift"
cat "$root/scripts/package/host.swift" >> "$stage/host.swift"
case "$(uname -s)" in
  Darwin)
    swiftc -module-cache-path "$stage/cache" -O -target arm64-apple-macosx14.0 "$stage/host.swift" -o "$output"
    ;;
  Linux)
    [ "$(uname -m)" = x86_64 ] || { echo 'Linux host helper supports x86-64 only' >&2; exit 1; }
    mkdir -p "$stage/Sources/Host"
    mv "$stage/host.swift" "$stage/Sources/Host/main.swift"
    cp "$root/scripts/package/linux/Package.swift" "$stage/Package.swift"
    cp "$root/scripts/package/linux/Package.resolved" "$stage/Package.resolved"
    options=(--package-path "$stage" --configuration release --force-resolved-versions)
    if [ "${2:-}" != --native ]; then
      options+=(--swift-sdk x86_64-swift-linux-musl --static-swift-stdlib)
    fi
    swift build "${options[@]}"
    directory=$(swift build "${options[@]}" --show-bin-path)
    cp "$directory/symphony-host" "$output"
    ;;
  *) echo 'host helper requires macOS or Linux x86-64' >&2; exit 1 ;;
esac
chmod 755 "$output"
