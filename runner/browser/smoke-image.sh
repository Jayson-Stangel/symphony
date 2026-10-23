#!/usr/bin/env bash
# Test the selected immutable local image as its actual default agent user.
set -euo pipefail
image=${1:?image required}
provider=${2:?provider required}
revision=${3:-}
case "$provider" in codex) uid=0 ;; claude) uid=11001 ;; *) exit 1 ;; esac
image_id=$(docker image inspect --format '{{.Id}}' "$image")
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'invalid local image ID' >&2; exit 1; }
[ "$(docker image inspect --format '{{index .Config.Labels "com.symphony.agent"}}' "$image_id")" = "$provider" ]
if [ -n "$revision" ]; then
  [ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image_id")" = "$revision" ]
fi
# No credentials, network, workspace mounts or user override. Both providers
# must work with the USER, environment and entrypoint filesystem actually shipped.
docker run --rm --network none --workdir /tmp --entrypoint /usr/bin/timeout "$image_id" 90 /bin/sh -ec '
  test "$(id -u)" = "$1"
  test "$(id -g)" = "$1"
  node --version
  playwright --version
  node /opt/symphony/browser/smoke.cjs
  playwright screenshot --browser chromium about:blank /tmp/browser-smoke.png
' browser-smoke "$uid"
