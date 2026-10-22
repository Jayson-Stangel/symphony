#!/usr/bin/env bash
# Native Linux x86-64 BUILD controller; none of these tools are runtime dependencies.
# An explicit source lets this controller build an unmodified prior revision.
set -euo pipefail
controller_root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:?Usage: build-linux-host-helper.sh OUTPUT_DIRECTORY [SOURCE_ROOT FULL_SOURCE_SHA]}
[ "$#" = 1 ] || [ "$#" = 3 ] || { echo 'explicit source requires its exact full revision' >&2; exit 1; }
[ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { echo 'run this build on native Linux x86-64' >&2; exit 1; }
if [ "$#" = 3 ]; then
  [ -n "$2" ] || { echo 'explicit helper source root must not be empty' >&2; exit 1; }
  source_root=$(cd "$2" && pwd -P)
  expected=$3
else
  source_root=$controller_root
  expected=$(git -C "$source_root" rev-parse HEAD)
fi
revision=$(git -C "$source_root" rev-parse HEAD)
[[ "$expected" =~ ^[a-f0-9]{40}$ ]] && [ "$revision" = "$expected" ] || { echo 'selected helper source revision differs from requested revision' >&2; exit 1; }
[ -z "$(git -C "$source_root" status --porcelain --untracked-files=all -- scripts/package/host.swift scripts/package/linux scripts/release/build-host-helper.sh)" ] || { echo 'selected helper source inputs are dirty' >&2; exit 1; }
mkdir -p "$output"
output=$(cd "$output" && pwd)
sdk_stage=$(mktemp -d "${TMPDIR:-/tmp}/symphony-linux-sdk.XXXXXX")
trap 'rm -rf "$sdk_stage"' EXIT
sdk_archive="$sdk_stage/swift-static-linux.artifactbundle.tar.gz"
sdk_checksum=df0b40b9b582598e7e3d70c82ab503fd6fbfdff71fd17e7f1ab37115a0665b3b
# Swift 6.1.2's remote SDK installer can crash in FoundationNetworking/URLSession
# cleanup. Use the ordinary downloader and verify the official pinned archive
# before invoking Swift's local-file installer, avoiding that network code path.
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --connect-timeout 30 --max-time 600 --retry 3 --retry-delay 2 \
  --output "$sdk_archive" \
  https://download.swift.org/swift-6.1.2-release/static-sdk/swift-6.1.2-RELEASE/swift-6.1.2-RELEASE_static-linux-0.0.1.artifactbundle.tar.gz
if command -v sha256sum >/dev/null 2>&1; then observed=$(sha256sum "$sdk_archive")
else observed=$(shasum -a 256 "$sdk_archive"); fi
[ "${observed%% *}" = "$sdk_checksum" ] || { echo 'Swift SDK archive checksum mismatch; refusing installation/build' >&2; exit 1; }
git_common=$(git -C "$source_root" rev-parse --path-format=absolute --git-common-dir)
# Build inputs and Git metadata are read-only. SwiftPM/compiler writes stay in
# the selected revision's temporary stage; only the requested output is writable.
docker run --rm -v "$source_root:$source_root:ro" -v "$git_common:$git_common:ro" \
  -v "$sdk_archive:/sdk.artifactbundle.tar.gz:ro" -v "$output:/output" -w "$source_root" \
  -e BUILD_UID="$(id -u)" -e BUILD_GID="$(id -g)" swift:6.1.2-noble bash -euc '
    git config --global --add safe.directory "$PWD"
    swift --version
    swift sdk install /sdk.artifactbundle.tar.gz
    bash scripts/release/build-host-helper.sh /output/symphony-host
    chown "$BUILD_UID:$BUILD_GID" /output/symphony-host
  '
[ "$("$output/symphony-host" build-revision)" = "$expected" ] || { echo 'built helper revision differs from selected source' >&2; exit 1; }
file "$output/symphony-host"
# Fail on inspection errors as well as any dynamic interpreter/shared dependency.
program_headers=$(readelf -l "$output/symphony-host")
dynamic_section=$(readelf -d "$output/symphony-host")
case "$program_headers" in *INTERP*) echo 'Linux helper unexpectedly requires a dynamic interpreter' >&2; exit 1 ;; esac
case "$dynamic_section" in *NEEDED*) echo 'Linux helper unexpectedly requires a shared runtime' >&2; exit 1 ;; esac
printf '%s\n%s\n' "$program_headers" "$dynamic_section" > "$output/elf.txt"
(cd "$output" && sha256sum symphony-host > symphony-host.sha256)
printf 'source_revision=%s\ncontroller_revision=%s\nos_arch=%s\ntoolchain=Swift 6.1.2\nsdk=swift-6.1.2-RELEASE_static-linux-0.0.1\nsdk_archive_sha256=%s\n' \
  "$revision" "$(git -C "$controller_root" rev-parse HEAD)" "$(uname -sm)" "$sdk_checksum" > "$output/build.txt"
