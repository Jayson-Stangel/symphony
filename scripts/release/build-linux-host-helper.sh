#!/usr/bin/env bash
# Native Linux x86-64 build controller. Docker/Swift are development dependencies.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
output=${1:?Usage: build-linux-host-helper.sh OUTPUT_DIRECTORY}
[ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { echo 'run this build on native Linux x86-64' >&2; exit 1; }
mkdir -p "$output"
output=$(cd "$output" && pwd)
git_common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
# Linked worktrees point outside their source root; mount only their common Git
# metadata at its original path so revision and dirty-input checks remain real.
docker run --rm -v "$root:$root" -v "$git_common:$git_common:ro" -v "$output:/output" -w "$root" \
  -e BUILD_UID="$(id -u)" -e BUILD_GID="$(id -g)" swift:6.1.2-noble bash -euc '
    git config --global --add safe.directory "$PWD"
    swift --version
    swift sdk install https://download.swift.org/swift-6.1.2-release/static-sdk/swift-6.1.2-RELEASE/swift-6.1.2-RELEASE_static-linux-0.0.1.artifactbundle.tar.gz \
      --checksum df0b40b9b582598e7e3d70c82ab503fd6fbfdff71fd17e7f1ab37115a0665b3b
    bash scripts/release/build-host-helper.sh /output/symphony-host
    chown "$BUILD_UID:$BUILD_GID" /output/symphony-host
  '
file "$output/symphony-host"
# A self-contained static artifact must have no requested dynamic interpreter/DSO.
if readelf -l "$output/symphony-host" | grep -q INTERP || readelf -d "$output/symphony-host" | grep -q NEEDED; then
  echo 'Linux helper unexpectedly requires a dynamic runtime' >&2; exit 1
fi
(cd "$output" && sha256sum symphony-host > symphony-host.sha256)
printf 'source_revision=%s\nos_arch=%s\ntoolchain=Swift 6.1.2\nsdk=swift-6.1.2-RELEASE_static-linux-0.0.1\n' \
  "$(git -C "$root" rev-parse HEAD)" "$(uname -sm)" > "$output/build.txt"
