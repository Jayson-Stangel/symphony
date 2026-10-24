# Developer ID and notarized macOS distribution

This is an opt-in candidate path. Ordinary PR checks and manager packages remain
credential-free, with an explicit unsigned marker. An ad-hoc signature verifies
integrity; it does not establish Apple trust. Neither fixture tests nor unsigned
DMG construction prove notarization or fresh-account acceptance.

## Apple setup by the release owner

Do these account and credential actions yourself. Do not paste credentials into
chat, tickets, source, logs, fixtures or artifacts. This implementation does not
authorize a signing run, Apple upload, release publication or stable update.

1. Enroll in the [Apple Developer Program](https://developer.apple.com/programs/enroll/),
   select the intended team, accept its current agreements, and record its
   ten-character Team ID from Membership details. An Apple account alone is not
   Program membership. Use that same team for signing and notarization.
2. The Account Holder creates a
   [Developer ID Application certificate](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
   Generate a certificate signing request on the owner's secured Mac, upload its
   public CSR, download the certificate, and install it alongside the corresponding
   private key. A downloaded `.cer` without that private key cannot sign. Choose
   Application, not Developer ID Installer: this path distributes a DMG, not a
   privileged `.pkg`. It requires no provisioning profile or broad entitlements.
3. In Xcode's signing-certificate sheet or Keychain Access **My Certificates**,
   confirm the identity expands to its private key. Follow Apple's
   [certificate export instructions](https://help.apple.com/xcode/mac/current/en.lproj/dev154b28f09.html)
   to export certificate **and private key** as a password-protected `.p12`.
   Store it and its password separately in your approved secret manager. Record
   the valid identity's SHA-1 fingerprint, as displayed by
   `security find-identity -v -p codesigning`. This is a certificate selector,
   not an artifact-integrity hash; all artifact hashes use SHA-256.
4. For the implemented notarization authentication, enable two-factor
   authentication on the authorized developer Apple account and create an
   [app-specific password](https://support.apple.com/en-us/102654).
   Use the developer Apple account login and the selected Team ID, not your
   ordinary Apple account password. Apple's
   [notarytool guidance](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool)
   supports app-specific passwords. The account must have access to the team and
   have accepted the relevant agreements.
5. API keys are deliberately outside this implementation. For a future API-key
   path, a **team** App Store Connect API key needs Developer role or higher,
   its private `.p8`, Key ID and Issuer ID; see
   [Apple's notarize action](https://github.com/Apple-Actions/notarize) and
   [API-key documentation](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api).
   Do not assume an individual key works: Apple's current API-key article excludes
   it from `notaryTool`, while this host's newer `notarytool` help supports individual keys **without** an issuer (a team
   key requires one). Check the installed Xcode/notarytool version and live Apple
   account support before adopting that alternative. Program enrollment as an
   individual and the API key's team/individual type are different concepts.

## Protected GitHub environment

Before using `.github/workflows/macos-signing.yml`, create the **macos-signing**
environment in `Jayson-Stangel/symphony` Settings → Environments. Require trusted
release-owner approval, disable administrator bypass, and restrict deployment
branches to **dev only**. For this sole-operator team, **allow self-review** so
the operator can initiate the exact candidate run and approve its environment
deployment. Required environment approval remains mandatory; allowing self-review
does not authorize automatic credential use or bypass the exact-candidate approval.
Confirm these protections are
supported/enforced for this repository's GitHub plan. If they cannot be enforced,
do not provision the credentials or run the workflow. Keep `dev` protected by
required review and checks; environment approval must inspect the exact workflow
and executable source at the requested SHA. Workflow YAML cannot create or
enforce these repository settings by itself.

Put all following entries in that environment, never repository/organization
secrets available to PR jobs:

| Kind | Exact name | Value |
| --- | --- | --- |
| Secret | `MACOS_SIGNING_P12_BASE64` | Single-line base64 of the password-protected p12 |
| Secret | `MACOS_SIGNING_P12_PASSWORD` | That p12's nonempty export password |
| Secret | `MACOS_NOTARY_APPLE_ID` | Authorized developer Apple account login |
| Secret | `MACOS_NOTARY_APP_PASSWORD` | Its app-specific password |
| Variable | `MACOS_SIGNING_IDENTITY_SHA1` | Exact 40-hex valid Developer ID Application certificate fingerprint |
| Variable | `MACOS_TEAM_ID` | Exact ten-character team identifier |

Upload secret values using GitHub's secure settings interface or a secret-manager
integration. Base64 is encoding, not encryption. Do not print it to terminal or
put credentials on a shell command line/history. Keep encrypted backups only as
your organization's policy permits. Rotate p12 and password together, update the
fingerprint when renewing the certificate, and requalify the exact candidate.
Revoke compromised certificates in the Developer portal and compromised
app-specific passwords in the Apple account; remove/replace environment secrets.
Revoking Developer ID certificates can prevent existing software from running:
coordinate incident response and replacement distribution with the owner.

## Exact candidate, explicit approval, no publication

After review and a separately authorized merge to `dev`, run the existing
**Runner integration** workflow manually on that exact dev revision with
publication disabled. Wait for successful native helper, package, runner,
protected Codex/container, and installed-manager checks. Require the repository's
applicable full Linux application gate too. Keep the qualified run ID and attempt.

Only after the operator authorizes Apple upload and credential use for that
exact source/candidate, manually run **Runner integration** on `dev` with
`sign_macos=true`, `approved_revision` set to its full SHA and `qualified_run`
set to that successful manual integration run. Approve the matching
`macos-signing` environment deployment. Signing mode skips normal qualification,
helper/package builds and runner-image work; it calls the same-commit
**Signed macOS distribution candidate** worker with the already-qualified inputs.
It never qualifies itself or substitutes an earlier source revision.

GitHub requires a manually dispatched workflow to exist on the repository's
[default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
Here the default is `main`, while the new signing worker is deliberately on `dev`.
Use the existing registered Runner integration entry point rather than attempting
to dispatch the new worker directly or promoting code to main just to register it.
The [relative reusable call](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
resolves the worker from the same approved commit as the caller. Both retain the
manual dev-only/exact-SHA guard and the worker restricts its caller workflow.

The caller explicitly forwards only the four named Apple secrets; the worker
requires those names in `workflow_call.secrets`. GitHub requires this forwarding
even when secrets exist only in the worker's protected environment; omitting it
silently yields empty values. Environment values take precedence after the
`macos-signing` approval gate. Keep the values environment-scoped and avoid
`secrets: inherit`. See [GitHub's environment-secret forwarding rules](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow).

For example, after approval, substitute the actual full SHA and qualified run:

```sh
gh workflow run runner-integration.yml --repo Jayson-Stangel/symphony --ref dev \
  -f sign_macos=true -f approved_revision=FULL_DEV_SHA -f qualified_run=QUALIFIED_RUN_ID
```

Keep the ordinary qualification run separate with `sign_macos=false` (the
default). A controller-only workflow change still changes the checkout SHA;
qualify the new exact integrated revision before signing it. Original runner
components can be reused only when the planner proves their inputs unchanged.

The workflow refuses feature refs, PR events, a moved dev head, a different source,
failed/foreign runs, expired artifacts, mismatched helper/archive hashes and
changed original metadata. It downloads original immutable qualification evidence;
it does not rewrite qualification or claim new signatures were previously tested.

The controller imports only into a newly generated temporary keychain, uses
explicit keychain selection, preserves the default keychain and restores the
preexisting user search list. Secret files are mode-restricted and removed on
success/failure; SIGINT/SIGTERM unwind cleanup. Use only disposable GitHub-hosted
macOS runners, never persistent/shared workers. Hard power loss/SIGKILL cannot run
Python cleanup; destruction of the hosted runner is the final isolation boundary.
No signing secrets are passed to ordinary PR jobs, candidate packages, forks,
tag/release workflows, or unreviewed feature code. Raw Apple/tool diagnostics and
arguments are suppressed; only allowlisted submission/log fields are retained.
Inspect Apple submission details privately using the recorded submission ID if
the reduced log is insufficient to diagnose rejection.

The implementation signs the staged native helper with the exact certificate,
hardened runtime, secure timestamp and no added entitlements. Its `signing.json`
records original qualified helper SHA-256 and signed output identity/hash before
the file inventory and tarball are hashed. The original helper remains unchanged.
Future native payload additions fail until explicitly covered by signing policy.
The DMG contains that same bundle and instructions for running its existing
`install.sh` from Terminal; it adds no installer/updater or privileged service.
There is no Finder `.command` launcher: Apple documents a
[Finder document-assessment limitation for command-line tools](https://developer.apple.com/forums/thread/706379).

Following Apple's custom-installer guidance, the signed payload is submitted as
a temporary ZIP before the outer signed DMG is submitted using `notarytool`.
Only **Accepted** at both stages continues. Bare native binaries cannot be stapled.
Rejection, a 30-minute timeout for either wait, missing credentials, signature/team/runtime/
timestamp errors, stapling/validation failure, or Gatekeeper assessment failure
refuse readiness. A timeout can leave an Apple submission processing, but cannot
produce a ready artifact; inspect its ID privately before authorizing another run.
Each submission ID is recorded before its separate wait starts. The DMG is
stapled, validated, and assessed with `spctl` for opening. Following
[Apple's testing guidance](https://developer.apple.com/forums/thread/130560),
the bare helper is checked using `codesign -vvvv -R=notarized --check-notarization`.
No security bypass is used.

Only success uploads a `macos-notarized-candidate-SHA-RUN-ATTEMPT` Actions artifact:

- `macos-distribution.json` binds actual source, original/signed helper hashes,
  certificate/team, submission ID, pre-staple DMG signature hash and **final
  post-staple** DMG/archive hashes. `SHA256SUMS` contains those final hashes.
- `signing.json` remains inside the inventory-protected bundle with its truthful
  signing-stage status; the external receipt records the later notarization.
- Original qualified artifact IDs/digests and helper checksum remain in
  `qualification.json` and `symphony-host.sha256`. Reduced Apple logs distinguish
  submitted pre-staple bytes from the finished distribution.
- Existing `release.json` is finalized with the new macOS tarball hash and the
  **unchanged** Linux tarball/hash. Original component records, source/helper
  provenance, manager core, runner digests and metadata contracts remain intact.

No GitHub Release, tag, GHCR production publication, merge, promotion, stable
update, global link or stable process change occurs here. Existing release
workflows remain credential-free; they do not automatically publish this DMG.
Future publication must select the exact assessed artifact and requires its own
approval and gates. Do not label a separate ordinary release notarized.

## First install, updater and remaining acceptance

The DMG's stapled ticket supports the first-download container assessment.
Standalone command-line executables and tar.gz archives cannot be stapled;
the updater tarball contains the identical notarized native helper, whose ticket
can be retrieved online. Preserve the tar.gz filename and `release.json` contract;
do not replace the updater payload with a DMG. See Apple's
[custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).
Offline execution of an extracted bare helper is not established by a stapled DMG.

Credential-free native construction can be exercised after extracting a verified
ordinary candidate into a disposable directory:

```sh
python3 scripts/release/macos_distribution.py unsigned-dmg /absolute/bundle /tmp/symphony-unsigned.dmg
hdiutil verify /tmp/symphony-unsigned.dmg
```

That command neither signs nor submits to Apple. Command peers use obvious fake
certificate/p12/notary data and test order/refusal only. Native DMG mount/installer
tests use isolated application, config, bin and skill paths. Linux CI must retain
the full gate; macOS has known Linux `/proc`-only Claude coverage lines and must
not lower coverage thresholds to pass.

Before advertising trusted distribution, the owner must authorize and execute
real Developer ID/notary acceptance for the exact candidate, then use a **fresh
macOS account** and browser download with quarantine intact. Record macOS/Xcode
versions, source SHA, qualified run and attempt, final DMG/archive/helper hashes,
submission ID, signing identity/team, staple validation, both assessments,
download/extraction method, installer/doctor result, retry behavior and whether
manual approval was required. Test installed update/rollback with the final
tarball and release metadata, including online lookup. Never remove quarantine,
disable Gatekeeper or use another account's credentials/files as acceptance.
