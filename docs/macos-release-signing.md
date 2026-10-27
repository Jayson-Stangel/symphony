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

`evidence/keychain-diagnostics.json` records fixed stage labels for the search-list
snapshot, temporary search-list setup/readback, P12 decoding/import, keychain
creation/settings/unlock, partition-list setup and pinned identity check.
The temporary signing keychain is added ahead of the saved search list before
import, then verified visible. The explicit pinned-identity selector remains in
place; the default keychain and trust settings are not changed. Each operation
records running/success/failure;
command failures include only a numeric exit status or a timeout/start-failure
category. No command arguments, tool output, passwords, account names, keychain
paths or identity listings enter this receipt. For example,
`keychain.import-p12` with exit status `44` identifies the failing command, not
a diagnosis that the password or certificate is wrong.

Cleanup records P12/keychain absence and search-list restoration separately. All
cleanup steps are attempted even if an earlier step fails. An original setup or
signing error remains the reported failure when cleanup also fails; inspect the
receipt for secondary cleanup failures. Evidence-write failure cannot block
cleanup and prevents a successful readiness result. The existing refusal artifact
retains this JSON even when no Apple submission receipt exists. A running stage
without an outcome is incomplete evidence, not success. These diagnostics do not
authorize a retry or additional credential use.

`evidence/package-diagnostics.json` records fixed assembly, helper-integrity,
manifest/input validation, source/metadata, payload-copy, payload-signing, file
inventory and archive stages. Nested signing records separate signing, physical
verification, signature inspection/policy, entitlement, leaf-certificate, native
inventory and source-revision checks. Shell failures retain only the fixed stage
and numeric exit status/source line; Python failures retain a fixed outcome category.
Failed `signature.sign` operations also retain one fixed reason: certificate chain,
trust or expiry; denied interaction; unavailable identity; unavailable or missing
timestamp; generic Security framework error; or unclassified. Classification scans
command output in memory and discards it. A category is a diagnostic clue, not
proof of its underlying cause; unknown output remains unclassified.
The source line maps to the approved commit’s assembly or package script, so even
a stage containing several checks can be traced without exposing command text.
Line `0` means the source location is unknown; it must not be treated as a
verified command location. No
arguments, command output, exception text, paths or identities are recorded.
Parent and child stages appear separately, so a failed `assembly.package` can be
traced to the failed package or signature stage. A running stage without a
terminal result is incomplete (for example, a timeout), never proof of success.

These diagnostics are enabled only by the protected controller, which passes the
public evidence destination into its packaging subprocess. Ordinary unsigned
packages do not create evidence. Missing, unwritable or incomplete package
evidence refuses progression to Apple submission; evidence errors cannot replace
an existing command failure. The existing refusal artifact retains both JSON
receipts. These records do not authorize signing, Apple credential use or retries.

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

## Protected production preparation and publication

Production uses the same signing controller as the dev candidate above, through
separate manual entry points. The dev signer and its `macos-signing` environment
remain **dev only**. A version tag no longer publishes an ordinary unsigned
archive. Production publication requires a completed exact-source preparation,
completed production signing and separately approved protected publication.
None of these steps is authorized by implementing or merging this tooling.

The committed manager version is 0.0.6 with schema2. The standalone engine still
has its independent 0.0.4 version. Its release filenames identify the manager
release and its source receipts identify the actual build source; those filenames
do not change the engine version. Standalone Burrito binaries are independently
smoke-tested, but **are not covered by manager-helper notarization**. The stapled
DMG and updater tar contain the same signed native manager helper; the bare
helper's online ticket lookup remains necessary. Do not advertise offline trust
for the tarball or Apple trust for the standalone engine.

### Operator configuration checklist

Configure these only after reviewing the exact implementation and authorizing
these settings changes; workflow source cannot create or enforce them.

- Preserve `macos-signing`: `dev` branch only, required release-owner review,
  sole-operator self-review as previously approved, administrator bypass disabled.
- Create **`macos-production-signing`** with a **`main` branch only** deployment
  rule, required trusted release-owner review and administrator bypass disabled.
  Use the established sole-operator self-review policy. Confirm the GitHub plan
  enforces these protections before provisioning credentials. Never allow tags,
  PRs, forks or feature branches into this environment.
- Place the four existing credential types in that environment using secure
  GitHub settings or a secret manager: `MACOS_SIGNING_P12_BASE64`,
  `MACOS_SIGNING_P12_PASSWORD`, `MACOS_NOTARY_APPLE_ID`,
  `MACOS_NOTARY_APP_PASSWORD`. Add variables `MACOS_SIGNING_IDENTITY_SHA1` and
  `MACOS_TEAM_ID`, verifying the intended valid certificate/team. Reuse the
  existing operator-managed identity; no new enrollment/export is inherently
  needed. Keep values out of chat, issue bodies, source, fixtures and logs. Do not
  move them into repository/organization scope accessible to PR jobs.
- Preserve **`production-release`** with version-tag deployment policy `v*`,
  required trusted reviewer, the established self-review policy and no admin
  bypass. Registry transfer and final GitHub completion are separate jobs and
  approvals: inspect their distinct proposed effects. Do not approve the final
  job before outstanding final-byte/native/public-reference acceptance passes.
- Protect `main` and release tags against unauthorized changes, retain required
  checks/review, and verify the exact tag peels to approved source `P`.
  `SYMPHONY_PRODUCTION_RELEASES=enabled` is required for publication but is not
  operator approval. It was already enabled when this work was planned.
- Verify workflow token access to both GHCR packages and public visibility of
  `ghcr.io/jayson-stangel/symphony-codex` and `symphony-claude`. The existing
  publisher verifies anonymous exact manifest/config/blob availability and
  preserves digests. Package visibility/token permission was not established by
  the planning chat's token (`read:packages` was unavailable); verify it for the
  final execution. Only the registry job receives `packages:write`; only final
  GitHub publication receives `contents:write`. The signer has read-only token
  permissions and disposable hosted Apple Silicon execution.

### Source freeze and exact execution order

1. Finish and review the feature PR to `dev`, then freeze its integrated source
   `D`. Refresh the complete included PR/link/purpose inventory against live
   `main`; include this PR and all other intervening work. Obtain explicit
   promotion approval for that inventory and promotion PR/head. Promote normally
   and observe the actual resulting main commit **`P`**. Equal trees between
   `D` and `P` never permit rewriting a receipt's source SHA.
2. Keep main at `P` through qualification and signing. With separate execution
   authority, manually run `burrito-release.yml` **on main**, setting
   `production_prepare=true`. It performs full existing runner/helper/package and
   standalone-engine qualification, then prepares stable-reference manifests and
   native packages **without GHCR or GitHub release writes**. Keep successful
   preparation run **`Q` and its exact attempt**. Diagnostic/engine-only,
   skipped/failed producer jobs and ordinary candidate runs are not substitutes.
3. Recompute component inputs at `P`. Production planning reuses an original only
   when its canonical public digest graph is anonymously available. Matching
   private candidates that cannot be published under the existing reuse rules
   schedule genuine current-source builds; never relabel an older original as a
   new build. Prepared metadata retains exact original provenance and final
   production digest references before packaging. Linux and macOS helpers and
   manager packages are freshly bound to `P`; an old dev qualification/signing
   run is historical evidence only.
4. After explicit Apple credential-use/submission approval for `P` and `Q`, run
   `macos-production-signing.yml` **on main** with `approved_revision=P`,
   `qualified_run=Q` and `qualified_attempt=Q_ATTEMPT`. Approve its matching
   environment deployment. The controller imports only completed successful Q
   inputs, verifies immutable artifact ZIP hashes, expiry, run/source/attempt
   provenance and original helper bytes, then runs the existing signer. A moved
   main head fails the SHA guard; refreeze/requalify/reapprove instead of silently
   signing a different source. Retain signing run **`S` and exact attempt**.
5. Download the retained `production-signed-P-S-ATTEMPT` artifact. Verify final
   DMG/archive/helper hashes and source, certificate/team, hardened runtime,
   timestamp, both Apple Accepted submissions, DMG staple/open assessment,
   helper online notarization and all cleanup receipts. Use the final DMG through
   a browser in a fresh macOS account with quarantine intact; exercise isolated
   install, version/installation doctor and reinstall. Test installed update,
   rollback and retry with the final signed tar and metadata in isolated paths.
   No quarantine removal, security bypass or stable manipulation is permitted.
   A candidate-registry lifecycle proves composition, not final public GHCR
   availability. Keep any check requiring actual public refs explicitly pending
   until the next authorized stage; do not rewrite the package to insert aliases.
6. Only with exact tag/publication approval, create `v0.0.6` at `P`, then manually
   dispatch `production-publish.yml` **on that tag**, supplying
   `approved_revision=P`, `signed_run=S`, `signed_attempt=S_ATTEMPT`. The source
   must remain an ancestor of protected `main`; later unrelated main advancement
   does not authorize another `P`. All jobs consume the frozen approved SHA and
   refuse mismatched/moved tag or completed release. The tag push itself performs
   no production build/signing/publication; explicit dispatch imports Q/S.
7. The first `production-release` approval authorizes exact immutable registry
   transfer. It rechecks retained signing/qualification inputs and original
   component availability before writes. Its returned stable manifest must equal
   the manifest used to build/sign the packages. No package is rebuilt or changed
   after signing. Complete any pending final-public-reference/update acceptance
   now, before approving the final release job. Full anonymous graph checks run
   again before GitHub completion. Already transferred immutable blobs can remain
   after a later failure; they are not a completed stable GitHub release.
8. Inspect the final job's exact files/hashes and acceptance ledger before its
   separate environment approval. The publisher refuses unexpected/duplicate or
   mismatched draft assets, uploads only missing assessed files and checks the
   complete remote inventory/digests before marking the release complete. It
   never overwrites a completed release or includes an unsigned fallback.
9. Stable rollout remains a separate approval with complete change inventory,
   exact proposed revision and runtime/project targets. Use only the installed
   `symphony update`; if it refuses, stop. Never change stable sources, links,
   build artifacts or running processes directly.

Record Q/S IDs and attempts, final P/tag, original and signed helper identities,
final post-staple DMG/tar/manifest hashes, component origins/digests, Apple IDs,
native host/tool versions, quarantine/install/update observations and limitations.
The CI fixtures test orchestration and refusals, not real Apple acceptance. The
prior JAY-720 and newer 797f455 provider/signing evidence remain immutable proof
for their original bytes. Both-provider authenticated task smokes need not be
repeated merely because the manager version changes; repeat only when consumed
provider inputs or an observed failure justify it. Final-byte native acceptance
above remains required. No real production qualification/signing/publication or
stable rollout was executed by adding this tooling.

## Local signing with GitHub delivery

Local signing is an alternative producer of the same final packages, not an
alternative production approval path. The local operator signs once, uploads
those exact bytes to private draft staging, and a credential-free Mac job
independently verifies them. The existing tag publisher consumes the admitted
immutable Actions artifact without building, packaging, signing or notarizing
again. Both `production-release` environment approvals remain required.

This path must already exist in the candidate's exact source **before** its
qualification and local signing. A previously frozen source whose tag points to
the old workflow does not acquire these commands by merging a later controller.
Do not move its tag, relabel old qualification, or claim that later controller
code was part of its signed package. Integrate/review/promote this change, freeze
the resulting source, and qualify that source before the one local signing pass.
All existing final-byte native, fresh-account, installed update/rollback and
public-reference acceptance obligations above still apply.

### Prove private staging access before qualification or signing

Before spending a new qualification/signing run, use the optional transport-only
mode to prove that the real read-only Actions job token can fetch a private draft
asset. A successful operator `gh` request does not prove that job-token access:
the two authenticated identities and permissions differ. Permission denial stops
this probe; do not make staging public or grant `contents:write` to bypass it.

With separate bounded approval, create one unique nonproduction draft at the
exact reviewed main revision and upload only the fixed public probe fixture. For
the approved operator's own Terminal, the fixture can be prepared outside the
checkout without accessing credentials:

```sh
probe_dir=$(mktemp -d)
python3 - "$probe_dir/permission-probe.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w', compression=zipfile.ZIP_STORED) as archive:
    entry = zipfile.ZipInfo('permission-probe.txt', date_time=(1980, 1, 1, 0, 0, 0))
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, b'Symphony local signing staging permission probe. Public fixture. No credentials or candidate package.\n')
PY
shasum -a 256 "$probe_dir/permission-probe.zip"
```

This is exactly 240 bytes, SHA256
`bcd7136193af68bb874051656edae4326f8dacd0deca505580a5035c6e2875c1`.
The later approved staging operation uses a new
`local-signing-FULL_REVISION-RANDOM_32_HEX` draft tag, `target_commitish` equal to
that full revision, and one asset named `permission-probe.zip`. Retain its
original release ID, asset ID, server digest and size. These are test data, not
signed candidate packages. Do not reuse a production draft, move a tag or publish
the staging draft.

Dispatch `macos-local-signing-import.yml` on `main`, supplying the exact
`approved_revision`, `staging_release`, `staging_asset`, `staging_digest` and
**`transport_probe_only=true`**, then approve its normal existing
`macos-production-signing` environment gate. The Ubuntu job uses the same
`contents:read`/`actions:read` token and source/asset checks as actual import. It
downloads only the original ID/digest-bound blob with a separate 4 KiB ceiling,
checks the fixed name/240-byte digest, and never parses, unpacks or executes its
contents. It needs no qualification, Mac runner, signing secret or Apple action.

Its receipt and `local-signing-transport-probe-...` Actions artifact prove
transport access only. The native verification job is skipped. Even an overall
green probe run is **refused** by the production importer because the mandatory
`Verify locally signed distribution` job did not succeed; no signed-package
artifact or native/Apple/cleanup receipt is fabricated. Keep the successful probe
receipt before qualification/local signing. With explicitly approved cleanup,
remove only the just-created verified probe draft/asset; preserve its service
IDs and proof. No workflow or environment permission change is implied.

Normal local import keeps `transport_probe_only=false` (the default) and still
requires all qualification and native checks below.

### Sign once on the operator's Apple Silicon Mac

Use a clean isolated checkout at the exact approved `main` source and retain
successful production preparation `Q` and its exact attempt. If a hosted signing
run was attempted, first inspect its steps, artifacts and Apple receipts: failure
can happen after an Accepted submission. Do not start local signing while a
hosted pass is active or after one succeeded. The local command enforces this
serialization and refuses an existing output directory instead of retrying.

With explicit approval for the exact source, qualification, existing credential
use and both Apple submissions, run:

```sh
python3 scripts/release/local_distribution.py sign Q Q_ATTEMPT /absolute/private/local-signing --authorize-apple-upload
```

Replace `Q`, `Q_ATTEMPT` and the output path. In your own interactive Terminal,
the command asks for the existing successful P12 file path, its export password,
the Apple Developer email and app-specific password (credential values hidden),
then the public pinned certificate SHA1 and Team ID. The current intended pin is
`6696C74960AEAE2B4061B0410CD29E46E243321D`, team `UVKZ67Z7UT`; the import job
also checks them against the protected environment's public variables.
Unavailable hidden input fails closed. Never put private values in command
arguments, shell history, tickets or chat.

The established controller creates a disposable private keychain, imports the
existing identity, temporarily changes and restores its search list, signs,
submits the payload and DMG to Apple, staples/assesses, and removes its temporary
credential material. No new certificate, login-keychain identity installation or
global configuration change is needed. Retain sanitized failure/cleanup evidence
and stop after a failure; a fresh attempt needs renewed exact approval.

### Upload and independently admit the final bytes

With explicit staging-upload approval, run from the same clean source:

```sh
python3 scripts/release/local_distribution.py upload /absolute/private/local-signing/signed /absolute/private/local-signing-upload.zip --authorize-staging-upload
```

This performs fresh original-Q checks and native verification, then uploads an
allowlisted ZIP to a **draft** whose unique tag begins `local-signing-`, never
`v`. This is private transport visible to repository maintainers, not a completed
release, latest release, registry publication or stable update. The upload
receipt records the source, draft release ID, original asset ID, GitHub's SHA256
digest and size. The ZIP contains packages and sanitized receipts only—no P12,
password, keychain or arbitrary logs. Never publish this staging draft or use
`--clobber` to replace an asset.

The command writes a sibling `.receipt.json` with exact inputs for
`macos-local-signing-import.yml`. Dispatch that workflow on `main` only after
bounded approval, supplying `approved_revision`, `staging_release`,
`staging_asset` and `staging_digest` from the receipt. Approve its existing
`macos-production-signing` environment gate. The job references only public
identity/team variables, not signing or notary secrets. It runs on Intel macOS
to inspect the ARM64 package without depending on ARM64 signing-runner capacity.

Admission refuses mismatched source/Q/attempt, replaced or expired draft assets,
unsafe archives, incomplete Apple or cleanup attestations, changed source files,
or wrong signatures/tickets. It compares the signed executable to the actual
qualified helper with only validated Mach-O signing-size fields/signature bytes
excluded; every other byte and offset is bound. The signed tar's source files,
permissions and inventory must match qualification. It also mounts the DMG
read-only, checks its exact root instructions and payload against the signed tar,
then detaches only that owned mount. Native signature, pinned certificate/team,
runtime, timestamp, online notarization, staple and Gatekeeper checks must pass.
Local cleanup receipts are operator attestations; the hosted verifier cannot
inspect the operator's former temporary keychain. Intel inspection is not ARM64
execution, fresh-account installation or authenticated provider acceptance.

The successful import retains `production-local-signed-P-RUN-ATTEMPT` with an
honest `origin: local` receipt and byte-bound native verification. Subsequent
publication reads that original immutable Actions artifact, not the mutable
staging draft; later draft mutation cannot substitute accepted bytes. Retention
expiry still refuses publication. Keep the original staging upload receipt and
import run/attempt with the final acceptance ledger.

### Publish the already signed packages through the existing gates

After final native acceptance and the separate exact tag/publication approval,
dispatch `production-publish.yml` on the exact approved `vVERSION` tag, setting
`signed_origin=local`, `signed_run=IMPORT_RUN`,
`signed_attempt=IMPORT_ATTEMPT` and `approved_revision=P`. The importer requires a
successful same-source `macos-local-signing-import.yml` run and all original-Q,
native and immutable-artifact bindings. Its normal registry and final-release
jobs each revalidate original inputs after their existing protected approval.
No package bytes are rebuilt, resigned or renotarized after local signing.

Hosted signing remains supported with `signed_origin=hosted` (the default).
Neither origin grants production publication or stable rollout authority merely
by producing or uploading a signed package.
