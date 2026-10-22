#!/usr/bin/env bash
# Fetch a real pinned source into its own checkout. Caller history may be shallow;
# FETCH_HEAD-only objects are not a reliable input to a subsequent local clone.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
revision=${1:?Usage: prepare-candidate-source.sh FULL_SOURCE_SHA DESTINATION}
destination=${2:?Usage: prepare-candidate-source.sh FULL_SOURCE_SHA DESTINATION}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || { echo 'candidate source must use a full commit SHA' >&2; exit 1; }
[ ! -e "$destination" ] && [ ! -L "$destination" ] || { echo 'candidate source destination already exists; refusing to replace it' >&2; exit 1; }
upstream=$(git -C "$root" remote get-url origin)
# Read only the caller's verified upstream; do not mutate its refs, FETCH_HEAD,
# worktree or credentials. A standalone .git directory is also mountable by the
# previous revision's unmodified Linux helper build controller.
git init --quiet --initial-branch codex/candidate-source "$destination"
git -C "$destination" remote add origin "$upstream"
git -C "$destination" fetch --no-tags --depth=1 origin "$revision"
git -C "$destination" checkout --quiet --detach "$revision"
[ "$(git -C "$destination" rev-parse HEAD)" = "$revision" ] || { echo 'prepared candidate source revision differs' >&2; exit 1; }
