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
  final execution. Only the registry job receives `packages:write`. Final GitHub
  publication and the protected Ubuntu private-staging ingress job receive
  job-local `contents:write`; the latter needs that capability to read private
  drafts, not to publish them. The signer and Mac verifier retain read-only
  token permissions. Review the ingress capability risk described below before
  approving its exact source and execution.

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
All existing final-byte native, fresh-account, both-provider setup/task,
installed update/rollback and public-reference acceptance obligations above still
apply.

### Private staging capability and transport evidence

The supported import uses three jobs: protected main-only Ubuntu staging ingress,
read-only Mac verification, and the read-only Mac runner selector. The Ubuntu
job has **job-local `contents:write` and `actions:read`**, behind the existing
`macos-production-signing` environment review. Its code performs reads only:
it downloads the original private draft asset by ID, verifies source, nonce tag,
age, size and digest, and retains the opaque ZIP plus a closed manifest in an
immutable Actions artifact. It never unpacks or executes the uploaded ZIP and
does not reference signing/notary secrets, a PAT or public staging.

The token's capability is broader than those reads: `contents:write` can mutate
repository contents and releases. Exact reviewed main source, the protected
environment approval, and the prohibition on executing uploaded code therefore
remain required. This is an explicit permission decision within the approved
promotion/import scope; successful transport evidence does not authorize another
dispatch, qualification, Apple submission, publication or stable rollout.

Actual HTTP diagnostics observed private-draft access refused with the read-only
job token (`403`) and accepted with the explicitly approved write capability
(`200`). The final transport proof in [run40581169862](https://github.com/Jayson-Stangel/symphony/actions/runs/40581169862)
used the same archive-staging and handoff cores: a fixed 240-byte public fixture
was uploaded privately, read by the write-capability producer, retained as an
immutable artifact, and read unchanged by a separate read-only consumer. Its
SHA256 was `bcd7136193af68bb874051656edae4326f8dacd0deca505580a5035c6e2875c1`.
This proves transport for that fixture and source, not a full signed-package
import, final-source qualification, Apple acceptance, native installation or
customer lifecycle. Temporary feature proof/HTTP jobs and the unsupported
read-only private-draft probe are not production entry points.

### Retain an incomplete signing attempt without changing its bytes

Before the first notarization upload, signing compares the staged signed archive against
the original qualified package: full directories, file modes and unchanged source
bytes must match. Production, dev and local signing supply that original archive
via `--qualified-package`; missing or mismatched inputs refuse progression. Secure
timestamp service contact has already occurred during signature creation. Payload
construction uses canonical public permissions inside a private stage, extraction
preserves those permissions, and DMG instructions are explicitly mode0644. The
caller's private umask still governs outer archives/checkpoints/credentials.

Before the first Apple upload, signing retains the already signed updater tar
and the exact payload ZIP in a new sibling `OUTPUT.checkpoint` directory. The
directory is exclusive, owned and mode0700; the fixed receipt binds actual source,
input-manifest SHA256, original qualified helper and retained-file hashes. It
does not invent a qualification run identity. No P12, keychain, passwords or
arbitrary temporary files are copied. Copies and metadata are synchronized before
upload; retention failure stops progression. Apple submission IDs/status are
recorded as soon as the matching response arrives, before later log or ticket
checks can fail. The actual payload submission reads the retained ZIP, and the
eventual final tar is copied from those retained bytes without repacking.

Both post-Accepted helper checks retain fixed stage/category/exit diagnostics in
that private receipt, never raw command output or paths. An explicit unmet
notarization requirement can receive at most three verification-only checks in
60 seconds, after strict signature/certificate/runtime checks and unchanged
helper hashes. Signature, certificate, trust, lookup/network, timeout and unknown
failures stop; no propagation cause is assumed. Rechecks never sign, build or
submit again. The second helper check additionally requires the DMG's Accepted
submission. Temporary credential cleanup and keychain-search-list restoration
remain mandatory even when verification or retention fails.

Failure/cancellation leaves this checkpoint available for operator assessment;
the hosted production signer attempts to retain only its three fixed files as
a distinct private `production-signing-checkpoint-...` artifact. This is not a
ready distribution or an automatic resume path, and authorizes no further Apple
action. It adds no files to the completed public asset set. A successful build
still requires every existing signature, ticket, assessment, provenance and
cleanup check before writing the normal ready receipt.

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
uses the shared `stage_archive` path: structured creation captures the original
release ID, and reads before and after upload use that ID with exact private
draft/source/nonce and sole-asset checks. Drafts are not discovered through the
published-release by-tag endpoint. The receipt records source, original release
and asset IDs, GitHub's SHA256 digest and size. The ZIP contains packages and
sanitized receipts only—no P12, password, keychain or arbitrary logs. Never publish
this staging draft or use `--clobber` to replace an asset.

If creation or upload has an uncertain outcome, stop without an automatic retry.
Preserve the archive, source, any returned original IDs and the service ledger;
reconcile the exact draft/asset before a separately approved retry. Changing the
archive path or creating another nonce draft is not uncertainty recovery.

The command writes a sibling `.receipt.json` with exact inputs for
`macos-local-signing-import.yml`. Dispatch that workflow on `main` only after
bounded approval, supplying `approved_revision`, `staging_release`,
`staging_asset` and `staging_digest` from the receipt. Approve its existing
`macos-production-signing` environment deployments after reviewing the Ubuntu
ingress write capability and the Mac inspection effects separately. The Mac job
explicitly keeps `contents:read`/`actions:read` and references only public
identity/team variables, not signing or notary secrets. It depends on successful
staging ingress and the unchanged runner selector. By default it runs on hosted
`macos-15-intel`; an exact source/run/attempt-bound backup permit may route only
this credential-free inspection job to the isolated macOS 14 ARM64 guest. The
Ubuntu ingress is not a backup-Mac consumer. The Mac reports its actual hardware
and verifies the guest architecture/version, inspecting the uploaded ARM64
package without executing it. This routing admits no signing job or Apple
credentials to the guest.

The Mac reads only the exact Actions artifact ID, derived name and service
digest returned by the ingress job in the **same current run, attempt and
source**. Admission requires one uniquely identified successful ingress job;
artifact creation must fall within that producer job's start/completion times.
The whole workflow is still running at this handoff, so neither whole-run
completion nor the mutable run `updated_at` establishes artifact provenance.
The opaque ZIP and closed manifest must bind the original release/asset IDs,
size and digest before the existing unpacking, qualification and native checks.
There is no latest/name-only, cross-run or cross-attempt fallback.

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
expiry still refuses publication. Unlike the earlier same-run transport handoff,
publication requires the **whole import run to have completed successfully**,
including the mandatory native producer. Ingress alone never proves native
acceptance. Keep the original staging upload receipt, ingress artifact identity
and import run/attempt with the final acceptance ledger.

### Publish the already signed packages through the existing gates

After final native acceptance and the separate exact tag/publication approval,
dispatch `production-publish.yml` on the exact approved `vVERSION` tag, setting
`signed_origin=local`, `signed_run=IMPORT_RUN`,
`signed_attempt=IMPORT_ATTEMPT` and `approved_revision=P`. The importer requires a
successful same-source `macos-local-signing-import.yml` run and all original-Q,
native and immutable-artifact bindings. Its normal registry and final-release
jobs each revalidate original inputs after their existing protected approval.
No package bytes are rebuilt, resigned or renotarized after local signing.

The publisher also retains the original production draft ID. A retry uses a
bounded release inventory and requires one exact-tag draft; ambiguous/incomplete
inventory or an already completed release stops publication. New creation uses
one structured POST with no blind retry after an uncertain result. Subsequent
reads and final completion PATCH use the original ID, with unchanged asset
hash/size checks and live source/tag revalidation before each mutation. For an
existing Git tag, `target_commitish` is opaque stable service metadata, not proof
of source: the peeled immutable tag and approved-source ancestry remain binding.

Hosted signing remains supported with `signed_origin=hosted` (the default).
Neither origin grants production publication or stable rollout authority merely
by producing or uploading a signed package.
