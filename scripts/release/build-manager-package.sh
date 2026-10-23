#!/usr/bin/env bash
# Shared candidate assembly: actual selected source, native helper and existing packer.
set -euo pipefail
controller=$(cd "$(dirname "$0")/../.." && pwd)
[ "$#" = 5 ] || [ "$#" = 6 ] || { echo 'Usage: build-manager-package.sh MANIFEST OUTPUT PLATFORM SOURCE FULL_SHA [QUALIFIED_HELPER]' >&2; exit 1; }
manifest=$1; output=$2; platform=$3; source=$4; revision=$5; qualified=${6:-}
case "$(uname -s)/$(uname -m)/$platform" in
  Darwin/arm64/macos|Linux/x86_64/linux_x86_64) ;;
  *) echo 'manager package requires matching native macOS ARM64 or Linux x86-64' >&2; exit 1 ;;
esac
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || { echo 'selected manager requires a full source SHA' >&2; exit 1; }
source=$(cd "$source" && pwd -P)
[ "$(git -C "$source" rev-parse HEAD)" = "$revision" ] || { echo 'manager package source differs' >&2; exit 1; }
manifest=$(cd "$(dirname "$manifest")" && pwd)/$(basename "$manifest")
mkdir -p "$output"
output=$(cd "$output" && pwd)
helper=$qualified
if [ -z "$helper" ]; then
  helper="$output/symphony-host"
  if [ "$platform" = macos ]; then
    bash "$source/scripts/release/build-host-helper.sh" "$helper" --native
  else
    bash "$controller/scripts/release/build-linux-host-helper.sh" "$output" "$source" "$revision"
  fi
fi
[ -f "$helper" ] && [ ! -L "$helper" ] || { echo 'selected helper must be a regular native artifact' >&2; exit 1; }
[ "$("$helper" build-revision)" = "$revision" ] || { echo 'manager helper source differs' >&2; exit 1; }
[ "$("$helper" host-platform)" = "$platform" ] || { echo 'manager helper platform differs' >&2; exit 1; }
# Check the actual binary, including historical helpers without host-architecture.
if [ "$platform" = macos ]; then
  [ "$(lipo -archs "$helper")" = arm64 ] || { echo 'manager helper contains an unsupported macOS slice' >&2; exit 1; }
else
  readelf -h "$helper" | grep -Eq 'Machine:.*Advanced Micro Devices X86-64' || { echo 'manager helper is not Linux x86-64' >&2; exit 1; }
fi
bash "$source/scripts/release/package.sh" "$manifest" "$output" "$helper"
printf 'manager_source_revision=%s\ncontroller_revision=%s\nplatform=%s\n' \
  "$revision" "$(git -C "$controller" rev-parse HEAD)" "$platform" > "$output/package-source.txt"
