#!/usr/bin/env bash
# Credential-free candidate packer. MANIFEST is the JAY-686 release contract.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
manifest=${1:?Usage: package.sh MANIFEST OUTPUT_DIR [HOST_HELPER]}
output=${2:?Usage: package.sh MANIFEST OUTPUT_DIR [HOST_HELPER]}
helper=${3:-}
mkdir -p "$output"
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
bundle="$stage/bundle"; mkdir -p "$bundle/scripts" "$bundle/libexec" "$bundle/elixir" "$bundle/.codex/skills"
if [ -n "$helper" ]; then cp "$helper" "$bundle/libexec/symphony-host"
else "$root/scripts/release/build-host-helper.sh" "$bundle/libexec/symphony-host"; fi
"$bundle/libexec/symphony-host" validate-manifest "$manifest"
if "$bundle/libexec/symphony-host" get "$manifest" bundle.sha256 >/dev/null 2>&1; then echo 'packer requires internal manifest without circular bundle checksum' >&2; exit 1; fi
revision=$("$bundle/libexec/symphony-host" get "$manifest" source_revision)
[ "$revision" = "$(git -C "$root" rev-parse HEAD)" ] || { echo 'bundle manifest source differs from checkout HEAD' >&2; exit 1; }
git -C "$root" diff --quiet HEAD -- scripts/symphony scripts/symphony-package.sh scripts/symphony-package-update.sh scripts/package/host.swift scripts/release/package.sh scripts/release/build-host-helper.sh elixir/WORKFLOW.md .codex/skills || { echo 'release inputs have uncommitted changes; commit them before packaging' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" build-revision)" = "$revision" ] || { echo 'host helper source differs from selected release' >&2; exit 1; }
cp "$manifest" "$bundle/release.json"
cp "$root/scripts/symphony" "$root/scripts/symphony-package.sh" "$bundle/scripts/"
if [ -f "$root/scripts/symphony-package-update.sh" ]; then cp "$root/scripts/symphony-package-update.sh" "$bundle/scripts/"; fi
cp "$root/elixir/WORKFLOW.md" "$bundle/elixir/"
for skill in commit push pull linear land; do cp -R "$root/.codex/skills/$skill" "$bundle/.codex/skills/"; done
cat > "$bundle/install.sh" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
"$root/libexec/symphony-host" verify-files "$root"
exec "$root/scripts/symphony" install "$@"
INSTALL
chmod 755 "$bundle/install.sh" "$bundle/scripts/symphony" "$bundle/libexec/symphony-host"
(cd "$bundle" && find . -type f ! -name files.sha256 -print | LC_ALL=C sort | while IFS= read -r path; do shasum -a 256 "$path"; done > files.sha256)
"$bundle/libexec/symphony-host" verify-files "$bundle"
filename=$("$bundle/libexec/symphony-host" get "$manifest" bundle.filename)
tar -C "$bundle" -czf "$output/$filename" .
shasum -a 256 "$output/$filename"
