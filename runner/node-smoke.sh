#!/usr/bin/env bash
# Assert the actual runtime and bundled package manager in finished images.
set -euo pipefail
if [ "$#" -eq 0 ]; then
  echo 'Usage: runner/node-smoke.sh IMAGE [IMAGE ...]' >&2
  exit 1
fi
for image in "$@"; do
  echo "Checking Node/npm in $image"
  # Reference harness images share the runtime pin, but do not ship the worker
  # development toolchain. Require the full toolchain for actual provider images.
  provider=$(docker image inspect --format '{{index .Config.Labels "com.symphony.agent"}}' "$image")
  toolchain=false
  case "$provider" in codex|claude) toolchain=true ;; esac
  docker run --rm -i --network none -e "SYMPHONY_CHECK_TOOLCHAIN=$toolchain" --entrypoint sh "$image" -eu <<'SH'
test "$(command -v node)" = /usr/local/bin/node
test "$(command -v npm)" = /usr/local/bin/npm
test "$(command -v npx)" = /usr/local/bin/npx
node -e 'const assert = require("node:assert/strict"); assert.equal(process.versions.node.split(".")[0], "24"); assert.equal(process.version, "v24.22.1"); console.log(process.version, process.arch, process.execPath)'
node -e 'const assert = require("node:assert/strict"); const fs = require("node:fs"); const h = fs.readFileSync("/usr/local/include/node/node_version.h", "utf8"); const v = ["MAJOR", "MINOR", "PATCH"].map(k => h.match(new RegExp("#define NODE_" + k + "_VERSION (\\d+)"))[1]).join("."); assert.equal(v, process.versions.node); console.log("Matching image-owned headers:", v)'
if [ "$SYMPHONY_CHECK_TOOLCHAIN" = true ]; then
node /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js --version
test ! -e /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp-upstream.js
command -v cc
command -v c++
command -v make
command -v python3
python3 /usr/local/lib/symphony/node-headers.py --prepare-home "$HOME"
python3 - <<'PY'
import json, os, runpy, tempfile
from pathlib import Path
store = Path('/usr/local/share/symphony/node-gyp')
manifest = json.loads((store / 'manifest.json').read_text())
view = Path.home() / '.cache/node-gyp' / manifest['version']
assert (view / 'include/node').resolve() == Path('/usr/local/include/node')
assert int((view / 'installVersion').read_text()) == manifest['installVersion']
assert not (store.stat().st_mode & 0o222)
assert view.parent.stat().st_uid == os.getuid()
prepare = runpy.run_path('/usr/local/lib/symphony/node-headers.py')['prepare_home']
with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary)
    cache = home / '.cache/node-gyp'
    cache.mkdir(parents=True)
    previous = cache / '24.21.0'
    previous.symlink_to('/operator-owned/previous-view')
    assert prepare(home) == 'prepared'
    assert previous.readlink() == Path('/operator-owned/previous-view')
    assert (cache / manifest['version']).resolve() == store / manifest['version']
print('Image-owned stock node-gyp header cache:', view)
PY
fi
test "$(npm --version)" = 11.19.1
test "$(npx --version)" = 11.19.1
test "$(npm prefix --global)" = /usr/local
test "$(readlink -f /usr/local/bin/npm)" = /usr/local/lib/node_modules/npm/bin/npm-cli.js
test "$(readlink -f /usr/local/bin/npx)" = /usr/local/lib/node_modules/npm/bin/npx-cli.js
test ! -e /usr/bin/node
test ! -e /usr/bin/npm
SH
done
