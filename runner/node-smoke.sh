#!/usr/bin/env bash
# Assert the actual runtime and bundled package manager in finished images.
set -euo pipefail
if [ "$#" -eq 0 ]; then
  echo 'Usage: runner/node-smoke.sh IMAGE [IMAGE ...]' >&2
  exit 1
fi
for image in "$@"; do
  echo "Checking Node/npm in $image"
  docker run --rm -i --network none --entrypoint sh "$image" -eu <<'SH'
test "$(command -v node)" = /usr/local/bin/node
test "$(command -v npm)" = /usr/local/bin/npm
test "$(command -v npx)" = /usr/local/bin/npx
node -e 'const assert = require("node:assert/strict"); assert.equal(process.versions.node.split(".")[0], "24"); assert.equal(process.version, "v24.22.1"); console.log(process.version, process.arch, process.execPath)'
node -e 'const assert = require("node:assert/strict"); const fs = require("node:fs"); const h = fs.readFileSync("/usr/local/include/node/node_version.h", "utf8"); const v = ["MAJOR", "MINOR", "PATCH"].map(k => h.match(new RegExp("#define NODE_" + k + "_VERSION (\\d+)"))[1]).join("."); assert.equal(v, process.versions.node); console.log("Matching image-owned headers:", v)'
node /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js --version
test -r /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp-upstream.js
test "$(npm --version)" = 11.19.1
test "$(npx --version)" = 11.19.1
test "$(npm prefix --global)" = /usr/local
test "$(readlink -f /usr/local/bin/npm)" = /usr/local/lib/node_modules/npm/bin/npm-cli.js
test "$(readlink -f /usr/local/bin/npx)" = /usr/local/lib/node_modules/npm/bin/npx-cli.js
test ! -e /usr/bin/node
test ! -e /usr/bin/npm
SH
done
