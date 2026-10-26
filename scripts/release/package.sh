#!/usr/bin/env bash
# Credential-free candidate packer. MANIFEST is the JAY-686 release contract.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
source "$root/scripts/release/package-diagnostics.sh"
trap 'package_diagnostics_exit "$?" "$package_diagnostics_line"' EXIT
package_stage package.helper-integrity
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
stage=$(mktemp -d); trap 'package_diagnostics_cleanup "$?"' EXIT
bundle="$stage/bundle"; mkdir -p "$bundle/scripts" "$bundle/libexec" "$bundle/elixir" "$bundle/.codex/skills"
if [ -n "$helper" ]; then cp "$helper" "$bundle/libexec/symphony-host"
else "$root/scripts/release/build-host-helper.sh" "$bundle/libexec/symphony-host"; fi
# Inspect every Mach-O slice before invoking a supplied helper. Its executing
# ARM slice alone cannot establish that the bundle contains no retired Intel CPU.
if [ "$platform" = macos ]; then
  helper_architectures=$(lipo -archs "$bundle/libexec/symphony-host") || { echo 'cannot inspect macOS helper architectures' >&2; exit 1; }
  [ "$helper_architectures" = arm64 ] || { echo 'macOS helper must contain only arm64; universal or other CPU payload refused' >&2; exit 1; }
  # Validate physical signature integrity before helper execution. The compiler's
  # ad-hoc signature passes this check; it is not Developer ID/notarization proof.
  codesign --verify --strict "$bundle/libexec/symphony-host" || { echo 'macOS helper signature integrity check failed; refusing packaging' >&2; exit 1; }
fi
package_stage package.inputs
"$bundle/libexec/symphony-host" validate-manifest "$manifest"
python3 "$root/scripts/release/manager_inputs.py" "$manifest" --root "$root"
if "$bundle/libexec/symphony-host" get "$manifest" "$field.sha256" >/dev/null 2>&1; then echo 'packer requires internal manifest without circular bundle checksum' >&2; exit 1; fi
if python3 - "$manifest" <<'PYCHECK'
import json,sys
sys.exit(0 if 'manager_envelope' in json.load(open(sys.argv[1])) else 1)
PYCHECK
then
  python3 "$root/scripts/release/manager_envelope.py" --validate "$manifest" >/dev/null
  "$bundle/libexec/symphony-host" validate-manager "$manifest"
  revision=$("$bundle/libexec/symphony-host" manager-revision "$manifest")
  # Recompute the complete physical transition at packaging, independently of
  # any previously generated plan or core receipt.
  PYTHONPATH="$root/scripts/release" python3 - "$manifest" "$root" <<'PYMEASURE'
import sys
from pathlib import Path
from manager_envelope import decode,validate_core
from finite_transition import evaluate
core=validate_core(decode((Path(sys.argv[1]).parent/'manager-core.json').read_bytes()))
if core['compatibility']['selection_mode']=='finite-input-transition-v1':
    assert evaluate(sys.argv[2],core['original_components'])==core['compatibility']['input_transition']
PYMEASURE
  cp "$(dirname "$manifest")/manager-core.json" "$bundle/manager-core.json"
else
  revision=$("$bundle/libexec/symphony-host" get "$manifest" source_revision)
fi
package_stage package.source
[ "$revision" = "$(git -C "$root" rev-parse HEAD)" ] || { echo 'bundle manifest source differs from checkout HEAD' >&2; exit 1; }
[ -z "$(git -C "$root" status --porcelain --untracked-files=all -- scripts/symphony scripts/codex-seccomp.json scripts/codex-seccomp.LICENSE scripts/codex-apparmor.profile scripts/symphony-package.sh scripts/symphony-package-update.sh scripts/symphony-environment.sh scripts/symphony-apparmor.sh scripts/symphony-runner-import.sh runner/templates docs/team-environments.md docs/macos-install-security.md docs/candidate-runner-provisioning.md scripts/package/host.swift scripts/package/linux scripts/release/build-linux-host-helper.sh scripts/release/package.sh scripts/release/build-host-helper.sh scripts/release/host-targets.json scripts/release/manager-version scripts/release/manager_version.py scripts/release/manager_envelope.py scripts/release/manager_contract.py scripts/release/manager_inputs.py scripts/release/finite_transition.py scripts/release/manager-transition-policy.json scripts/release/release-schema elixir/WORKFLOW.md .codex/skills)" ] || { echo 'release inputs have uncommitted changes; commit them before packaging' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" build-revision)" = "$revision" ] || { echo 'host helper source differs from selected release' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" host-platform)" = "$platform" ] || { echo 'helper platform differs from bundle build host' >&2; exit 1; }
[ "$("$bundle/libexec/symphony-host" host-architecture)" = "$(uname -m)" ] || { echo 'helper architecture differs from bundle build host' >&2; exit 1; }
package_stage package.metadata
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
package_stage package.copy
cp "$root/scripts/symphony" "$root/scripts/codex-seccomp.json" "$root/scripts/codex-seccomp.LICENSE" "$root/scripts/codex-apparmor.profile" "$root/scripts/symphony-package.sh" "$root/scripts/symphony-environment.sh" "$root/scripts/symphony-apparmor.sh" "$root/scripts/symphony-runner-import.sh" "$bundle/scripts/"
if [ -f "$root/scripts/symphony-package-update.sh" ]; then cp "$root/scripts/symphony-package-update.sh" "$bundle/scripts/"; fi
mkdir -p "$bundle/runner/templates" "$bundle/docs"
cp "$root/runner/templates/"*.Dockerfile "$bundle/runner/templates/"
cp "$root/docs/team-environments.md" "$root/docs/macos-install-security.md" "$root/docs/candidate-runner-provisioning.md" "$bundle/docs/"
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
# Separate executable startup from inventory validation. A signal alone does not
# establish a Gatekeeper decision; retain the failing stage and exit status.
helper_execution_failure() {
  printf 'symphony: installation stopped before activation: native helper %s failed (exit status %s).\n' "$1" "$2" >&2
  echo 'symphony: executable startup or execution did not complete; this is not proof of a checksum mismatch or a macOS security decision.' >&2
  if [ "$host_platform" = macos ]; then
    echo 'symphony: verify the downloaded archive SHA-256 against the trusted candidate/release checksum first.' >&2
    echo 'symphony: if macOS blocked this verified helper, open System Settings > Privacy & Security and review the specific blocked Symphony executable; use Open Anyway only if you trust that exact download, then retry ./install.sh.' >&2
    echo 'symphony: if approval is unavailable or execution still fails, retain the macOS alert and exit status and contact the release maintainer. Do not disable Gatekeeper or remove quarantine recursively.' >&2
  else
    echo 'symphony: confirm the archive matches this host and download checksum; report the exit status to the release maintainer.' >&2
  fi
}
if "$root/libexec/symphony-host" host-platform >/dev/null; then :
else
  status=$?
  helper_execution_failure startup "$status"
  exit "$status"
fi
if "$root/libexec/symphony-host" verify-files "$root"; then :
else
  status=$?
  case "$status" in
    126|127|1[3-9][0-9]|2[0-5][0-9]) helper_execution_failure inventory-verification "$status" ;;
    *) echo 'symphony: bundle inventory verification failed; installation stopped before activation. Download a fresh archive and verify its trusted SHA-256; do not approve or execute a modified payload.' >&2 ;;
  esac
  exit "$status"
fi
exec "$root/scripts/symphony" install "$@"
INSTALL
chmod 755 "$bundle/install.sh" "$bundle/scripts/symphony" "$bundle/libexec/symphony-host"
# Only the opt-in distribution controller supplies an isolated keychain. Sign
# the staged copy, preserving qualified input bytes, before inventory/archive
# hashing. The ordinary path remains credential-free and explicitly unsigned.
package_stage package.payload-signing
if [ -n "${SYMPHONY_MACOS_KEYCHAIN:-}" ]; then
  [ "$platform" = macos ] || { echo 'macOS signing requested on another platform' >&2; exit 1; }
  python3 "$root/scripts/release/macos_distribution.py" sign-payload "$bundle"
else
  printf '%s\n' 'unsigned candidate; compiler ad-hoc signatures are not Developer ID or notarization' > "$bundle/distribution-status.txt"
fi
package_stage package.inventory
(cd "$bundle" && find . -type f ! -name files.sha256 -print | LC_ALL=C sort | while IFS= read -r path; do printf '%s  %s\n' "$("$bundle/libexec/symphony-host" sha256 "$path")" "$path"; done > files.sha256)
"$bundle/libexec/symphony-host" verify-files "$bundle"
package_stage package.archive
filename=$("$bundle/libexec/symphony-host" get "$manifest" "$field.filename")
tar -C "$bundle" -czf "$output/$filename" .
printf '%s  %s\n' "$("$bundle/libexec/symphony-host" sha256 "$output/$filename")" "$output/$filename"
