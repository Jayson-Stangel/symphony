#!/usr/bin/env bash
# Credential-free candidate packer. MANIFEST is the JAY-686 release contract.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
manifest=${1:?Usage: package.sh MANIFEST OUTPUT_DIR [HOST_HELPER]}
output=${2:?Usage: package.sh MANIFEST OUTPUT_DIR [HOST_HELPER]}
helper=${3:-}
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) platform=macos ;;
  Linux/x86_64) platform=linux_x86_64 ;;
  *) echo 'unsupported bundle build platform' >&2; exit 1 ;;
esac
field=bundle
if [ "$platform" = linux_x86_64 ]; then field=bundles.linux_x86_64; fi
mkdir -p "$output"
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
bundle="$stage/bundle"; mkdir -p "$bundle/scripts" "$bundle/libexec" "$bundle/elixir" "$bundle/.codex/skills"
if [ -n "$helper" ]; then cp "$helper" "$bundle/libexec/symphony-host"
else "$root/scripts/release/build-host-helper.sh" "$bundle/libexec/symphony-host"; fi
"$bundle/libexec/symphony-host" validate-manifest "$manifest"
if "$bundle/libexec/symphony-host" get "$manifest" "$field.sha256" >/dev/null 2>&1; then echo 'packer requires internal manifest without circular bundle checksum' >&2; exit 1; fi
revision=$("$bundle/libexec/symphony-host" get "$manifest" source_revision)
[ "$revision" = "$(git -C "$root" rev-parse HEAD)" ] || { echo 'bundle manifest source differs from checkout HEAD' >&2; exit 1; }
[ -z "$(git -C "$root" status --porcelain --untracked-files=all -- scripts/symphony scripts/symphony-package.sh scripts/symphony-package-update.sh scripts/package/host.swift scripts/package/linux scripts/release/build-linux-host-helper.sh scripts/release/package.sh scripts/release/build-host-helper.sh scripts/release/host-targets.json elixir/WORKFLOW.md .codex/skills)" ] || { echo 'release inputs have uncommitted changes; commit them before packaging' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" build-revision)" = "$revision" ] || { echo 'host helper source differs from selected release' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" host-platform)" = "$platform" ] || { echo 'helper platform differs from bundle build host' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" host-architecture)" = "$(uname -m)" ] || { echo 'helper architecture differs from bundle build host' >&2; exit 1; }
python3 - "$manifest" "$bundle/release.json" "$platform" "$root/scripts/release/host-targets.json" <<'PYMANIFEST'
import json,sys
value=json.load(open(sys.argv[1]))
hosts={host["platform"]:[host["architecture"]] for host in json.load(open(sys.argv[4]))["include"]}
assert value.get("supported_hosts") == hosts, "new bundle requires explicit supported host policy"
assert value["bundle"].get("architectures") == hosts["macos"], "new macOS bundle requires exact ARM architecture"
if "bundles" in value:
    assert value["bundles"]["linux_x86_64"].get("architectures") == hosts["linux_x86_64"], "Linux bundle architecture differs"
value["package_platform"]=sys.argv[3]
with open(sys.argv[2],"w") as output: json.dump(value,output,indent=2,sort_keys=True); output.write("\n")
PYMANIFEST
printf '%s\n' "$platform" > "$bundle/host-platform"
cp "$root/scripts/symphony" "$root/scripts/symphony-package.sh" "$bundle/scripts/"
if [ -f "$root/scripts/symphony-package-update.sh" ]; then cp "$root/scripts/symphony-package-update.sh" "$bundle/scripts/"; fi
cp "$root/elixir/WORKFLOW.md" "$bundle/elixir/"
for skill in commit push pull linear land; do cp -R "$root/.codex/skills/$skill" "$bundle/.codex/skills/"; done
cat > "$bundle/install.sh" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) host_platform=macos ;;
  Linux/x86_64) host_platform=linux_x86_64 ;;
  *) echo 'symphony: unsupported host architecture or OS; packaged installs support macOS ARM64 and Linux x86-64' >&2; exit 1 ;;
esac
IFS= read -r bundle_platform < "$root/host-platform" || { echo 'symphony: bundle is missing its host platform marker' >&2; exit 1; }
[ "$host_platform" = "$bundle_platform" ] || { echo 'symphony: bundle platform does not match this host' >&2; exit 1; }
"$root/libexec/symphony-host" verify-files "$root"
exec "$root/scripts/symphony" install "$@"
INSTALL
chmod 755 "$bundle/install.sh" "$bundle/scripts/symphony" "$bundle/libexec/symphony-host"
(cd "$bundle" && find . -type f ! -name files.sha256 -print | LC_ALL=C sort | while IFS= read -r path; do printf '%s  %s\n' "$("$bundle/libexec/symphony-host" sha256 "$path")" "$path"; done > files.sha256)
"$bundle/libexec/symphony-host" verify-files "$bundle"
filename=$("$bundle/libexec/symphony-host" get "$manifest" "$field.filename")
tar -C "$bundle" -czf "$output/$filename" .
printf '%s  %s\n' "$("$bundle/libexec/symphony-host" sha256 "$output/$filename")" "$output/$filename"
