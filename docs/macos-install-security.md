# macOS downloaded-package security

Use Apple Silicon macOS 14 or later and the archive and checksum from the same
trusted release or explicitly selected candidate run. Follow the download,
SHA-256 verification, extraction and isolated candidate settings in
[packaged installation](https://github.com/Jayson-Stangel/symphony/blob/dev/docs/packaged-install.md) before running `./install.sh`.
GitHub Actions candidate downloads may arrive as an outer ZIP; extract that ZIP
first, verify the inner `symphony-VERSION-macos.tar.gz` against its trusted
checksum, then extract the tar archive into a new directory. A checksum from an
untrusted source does not establish trust in the download.

The installer checks host compatibility, starts the native helper, verifies the
complete file inventory, then stages and activates the installation. No compiler,
Python, Node, Homebrew or `sudo` is needed. File verification remains mandatory.

If the native helper cannot start, the installer stops before activation and
reports `native helper startup failed` with its exit status. If the helper starts
but is interrupted during inventory verification, it reports that separate
stage. `Killed: 9` or exit status 137 establishes a killed process, not its cause.
Neither message by itself proves Gatekeeper rejection or a checksum defect.

If macOS shows an unidentified-developer or unnotarized executable alert for the
verified download, review the blocked Symphony helper in **System Settings →
Privacy & Security**. Only if you trust that exact download, use the specific
**Open Anyway** approval and retry `./install.sh` from the same extracted bundle.
See [Apple's instructions for opening downloaded software](https://support.apple.com/en-us/102445).
If there is no applicable approval control, macOS reports malware or damage, or
the retry still fails, stop and report the alert, macOS version, archive checksum
and failed stage to the maintainer. Do not disable Gatekeeper, recursively remove
quarantine, alter the helper or approve a checksum-mismatched payload.

An ordinary inventory rejection prints `bundle inventory verification failed`
alongside the helper's causal message, such as a checksum mismatch. Download and
verify a fresh archive. Security approval cannot repair an inventory mismatch.
The installer performs no activation before these checks; a retry preserves
existing command/skill ownership checks and may refuse a foreign path safely.

## Release acceptance still required

Ordinary credential-free packages retain the compiler's linker ad-hoc signature.
Packaging verifies integrity before execution; this establishes neither Developer
ID signing nor Apple notarization. The opt-in
[Developer ID and DMG workflow](macos-release-signing.md) requires the release
owner's protected credentials and approval for the exact candidate's Apple upload.
Only its successful distribution receipt identifies a notarized artifact. Do not
re-sign a distributed helper without rebuilding its inventory/archive/checksum.
Unsigned candidate checks remain available and do not prove Apple trust.

Inspection on October 24, 2026 of the reported candidate from the shared download
found quarantine on the downloaded archive and extracted helper. On macOS 27.0.1
(build 26A434), ARM64, the helper was ad-hoc/linker signed, had no TeamIdentifier,
and passed `codesign --verify --strict`. `spctl` assessment returned an internal
Code Signing subsystem error, so this inspection does not establish an accepted
Gatekeeper disposition or reproduce the other account's security decision.

| Inspected original artifact | SHA-256 |
| --- | --- |
| `symphony-0.0.5-macos.tar.gz` | `875fa7c8651e9b0877aef5fcff246ec1901e366adb8c7d99e48efdd30ecc095d` |
| `libexec/symphony-host` | `4656c82e820f2f3d9691cae37e0e4baf2374e721120c5e79da5363d507230223` |

These identify the original reported candidate at revision
`b5622dd26e34098cebf44e4fb7ff9108f316fd0d`, not an artifact containing the fixes.
Shell process fixtures cover startup failure, interrupted verification, inventory
refusal and retry control flow. Native package tests separately cover real helper
inventory checking and activation. Neither substitutes for a fresh account
installing the exact newly downloaded candidate with quarantine intact.

Before advertising 0.0.5 as accepted, record the exact final archive/helper
checksums, download/extraction method, macOS version, signing/notarization
assessment, whether manual approval was required, installer result and retry
behavior from a fresh account. That human/platform acceptance remains a release
blocker until observed; merging the diagnostics into `dev` does not satisfy it.
