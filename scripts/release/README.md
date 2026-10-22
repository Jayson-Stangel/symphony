# Runner and manager release artifacts

The installed manager consumes `release.json`, independent of Git and the Symphony
source checkout. The manager version/source revision identifies the actual bundle
and helper. Each provider/platform record independently identifies the original
runner source and immutable Linux OCI image digest. `arm64` uses Linux ARM64;
`x86_64` uses Linux AMD64. Missing records are errors. Never substitute another
architecture, provider or mutable tag. `tool_versions` records versions observed
inside the built image (Codex, or Claude CLI and `claude_sdk`, plus Git, Node and Playwright). The
Dockerfile pins agent/browser packages; startup never installs agent packages.

`artifacts.py` is a build-time Python utility, not a host installation dependency:

```sh
python3 scripts/release/artifacts.py assemble --version 0.0.3 \
  --revision FULL_SOURCE_SHA --channel candidate --output internal-release.json records/*.json
# The packaged-manager prerequisite supplies package.sh and the native host helper.
bash scripts/release/package.sh internal-release.json dist
python3 scripts/release/artifacts.py assemble --version 0.0.3 \
  --revision FULL_SOURCE_SHA --channel candidate \
  --bundle dist/symphony-0.0.3-macos.tar.gz --output dist/release.json records/*.json
python3 scripts/release/artifacts.py validate dist/release.json
```

The archive's internal manifest omits `bundle.sha256` to avoid a circular checksum.
The external manifest includes it. `bundle.filename` is exactly
`symphony-VERSION-macos.tar.gz`. Both manifests carry the same source and image
identities. The universal macOS host bundle supports Apple Silicon and Intel;
the runner images are Linux containers, not macOS executables.

## Candidate qualification without publication

Run `runner-release` manually. It builds both providers on native ARM64 and AMD64
Linux hosts, temporarily pushes each image to a loopback-only disposable registry,
removes the selected local image, pulls the exact distribution digest and runs
credential-free Git, browser, skill, engine-startup and agent-version checks.
The actual default entrypoint starts with an empty in-memory tracker and networking
disabled, serves a state snapshot with no workers (`auth_required` for Claude; Codex
readiness qualifies policy transport without proving login), then
stops within its grace period. No host home or account material is mounted. The Linux image's
`timeout` bounds smoke commands, including when invoked from macOS. The registry is owned by the ephemeral CI
job; there is no GHCR or GitHub Release publication during candidate builds.
Build cache layers can remain: this is a fresh selected-image pull, not proof of
a pristine Docker installation.

Download Actions artifacts `runner-candidate-{provider}-{architecture}` and
`runner-candidate-metadata` before their 30-day expiration, and retain their run
URL and checksums with acceptance evidence. Each provider artifact contains a
Docker archive, checksum, observed release record and registry log. To replay on
a compatible native host, start an isolated registry at `127.0.0.1:5000`, verify
the archive checksum, `docker load` it, push its
`localhost:5000/symphony-PROVIDER:candidate` tag to that registry, and pull the
record's digest. Some engines normalize image manifests or drop an OCI index and
its attestations during `docker load`/`push`, changing the distribution digest.
If that happens, stop this replay path: use the original qualified registry when
available, or transfer the complete OCI graph with a digest-preserving copy that
retains raw manifest/index bytes and all referenced objects. Verify the original
record's digest after transfer. Do not rewrite a qualified record or bundle
manifest to accept the converted image. The record and image archive do not
require the Symphony source checkout.
A candidate bundle can then use those local digests while that registry is
available. Keep it isolated from stable installations and runner namespaces.

Local developer builds may use `docker build --target codex|claude` with
`--build-arg SYMPHONY_VERSION=... --build-arg SYMPHONY_REVISION=...`; commit the
candidate first so the labels identify the code tested. Treat `.Id` as an opaque local image ID, not a substitute for a registry
distribution digest.

## Approved production publication

`burrito-release` reuses `runner-release` and keeps independent engine executable
builds. Configure repository variable `SYMPHONY_PRODUCTION_RELEASES=enabled`, a
`production-release` GitHub environment with required operator reviewers, and
GHCR package write permissions for the workflow token. Make both packages
`ghcr.io/jayson-stangel/symphony-codex` and `.../symphony-claude` public before the
first completed release; new GHCR packages may default to private. A failed
anonymous digest pull fails publication. Approval remains an operator action for
the exact production scope; setting the variable alone is not approval.

After the normal review, promotion and release approval gates, create the matching
`vVERSION` tag. All four native candidate image qualifications must pass before
the protected publication jobs can push those exact tested images. Publication
then removes login credentials, pulls each GHCR digest anonymously, reruns smoke,
and records the actual public digest. The manager archive and checksum are built
from those four records. A legacy schema-1 bridge binds them to its own revision;
schema 2 preserves each actual runner revision and qualified payload origin. The packaged-manager prerequisite
must be integrated: absent `package.sh`, production fails closed. No Burrito engine
binary is needed to build or run the container manager bundle.

The GitHub release job runs only after every executable smoke and runner/bundle
job succeeds. Stable download/update consumers select only a completed public
GitHub release (never draft or prerelease), download its `release.json`, and pin
all remaining downloads to that same tag. Partial registry uploads from a failed
job are not offered as stable releases. Do not overwrite artifacts of a completed
release to change its identity; publish a newly approved version instead.

These checks prove artifact identity and model-free container behavior. They do
not prove native login, authenticated inference, clean macOS installation, or a
complete listener update. Those require the install/update stories' separately
recorded observations and explicit disposable-account authorization.


## Independent manager and runner components

`scripts/release/manager-version` is the manager release version and tag contract.
`elixir/mix.exs` remains the engine/runtime version reported by actual engines and
runner images. A manager semver change therefore does not itself change engine
source inputs. Never update an old image's version or source labels to claim the
new manager version; record the two component identities separately.

Schema 1 retains its strict single-revision contract. A bridge candidate may add
native component receipts while keeping every runner source equal to the bridge
manager source. Its real new helper understands schemas 1 and 2. An older
installed updater first installs that explicit schema-1 bridge with its ordinary
`--release` path, then uses the bridge updater for schema 2. Direct schema-2
selection by an older validator refuses before activation and preserves the
usable installation. Actual installed update/rollback proof is required before
this path is accepted; a shape-valid fixture is insufficient. Platform policy
and per-bundle architecture metadata belong to the supported-host contract.

Schema 2 retains top-level `source_revision` for the manager/helper and carries
original runner `source_revision`, immutable image digest, `input_identity`,
protocol compatibility, resolved dependency inventory and native qualification
origin. The installed consumer checks the selected runner's own source/input and
protocol labels. It never substitutes the manager source. Internal/external bundle
metadata must match the complete runner records, including provenance fields.

`runner_inputs.py` hashes the selected production stage graph, pinned base
references, effective declared options and actual consumed committed files/modes.
It follows supported COPY and Dockerignore semantics and includes the **whole**
current `COPY elixir` tree, including its docs, AGENTS and WORKFLOW. JAY-693's event
classification does not change what an actual image build consumes. Claude-only
stage/source changes do not enter Codex's closure. Manager/helper files outside
that closure do not enter either image. Unknown syntax, ignored metadata loss,
platform overrides, dirty/untracked consumed files or unsafe links refuse reuse.
Metadata arguments may only reach final labels, including through stage inheritance.

That identity describes declared source/recipe inputs, not a fresh resolution of
mutable external apt indexes. Each native image records its actual resolved OS
packages, Elixir/OTP and agent/browser/tool versions, pinned base references and
build options. Reuse explicitly retains that **original qualified resolution**
and immutable payload. It never claims current-head dependency resolution or old
native checks ran at the new manager head. Changing locks, a base or the effective
recipe invalidates reuse; requesting a new OS resolution requires such an actual
input change and a newly qualified image.

After an image payload upload, `component_provenance.py seal` binds its actual
archive checksum, immutable artifact ID/digest, source/controller revisions and
native job/run. The sealed small record is then uploaded separately. A reuse
selection downloads and checksum-verifies that original small record, compares
its complete content, checks the successful native job and live original payload
availability, and rejects expired/missing/foreign/corrupt evidence. Schema-2
assembly repeats this verification; a local receipt alone never authorizes reuse.
The current manager's actual bundle/integration qualification is separate from
these original native image results. Actual downstream archive/digest loading
and installed lifecycle proof remain mandatory at their consumer boundary.

`runner_reuse.py --root . --output PLAN` plans one build-or-reuse decision per
provider/platform. Its default discovery inspects at most three artifact-list
pages and 24 small native record artifacts; each selected controller and source
must belong to verified fork dev/main ancestry. `--baseline FILE` selects an
explicit manifest while retaining those origin checks. An explicitly authorized
manual candidate may use `--baseline-run ID` for that exact pre-merge native run.
This diagnostic exception does not authorize production publication.

Unchanged inputs and verified original evidence permit reuse; missing evidence
requests an explicitly reported verified-input rebuild. Unknown source closure
stops before any reuse. `--require-reuse` fails if any runner needs rebuilding.
Plans retain complete records and original origins, plus conditional build/reuse
matrices; a completed plan is not actual component qualification. Reused records
preserve every original digest/label/source and never cause export/upload just to
associate the image with a new manager head. A genuinely distinct original prior
image is optional evidence for image-changing lifecycle tests; absence is explicit.

The producer uploads actual image payloads before sealing small records. Separate
`record_filename` and `archive_filename` fields bind previous records to their real
shared payload when appropriate. Every planned build requires a matching actual
current-source native record at the terminal join. Unique registry ownership is
recorded before acquisition, and always-cleanup verifies the exact name, label
and ID before removing it. Cleanup evidence is retained even on qualification or
upload failure. Unsupported schema-2 or mixed/reused production image publication
refuses in one pre-publication gate before any matrix job can push.

## Native Docker layer cache

Candidate, listener and browser jobs use `.github/actions/build-runner`. BuildKit
retains one `mode=max` snapshot per native architecture, provider/target and ref
namespace in the bounded GitHub Actions cache service. It imports compatible
common browser/Elixir layers across providers. Dockerfile instructions, pinned
base digests, package locks, COPY contents and effective build arguments remain
BuildKit's cache keys; an absent or evicted entry builds cold. Source revision
and version labels are applied after the final production layers, so identity
changes do not invalidate dependency compilation. The final image still reports
its actual source revision and runs the same immutable-digest qualification.

Only pushes or manual runs on `dev`/`main` write the trusted namespace. PRs write
their own PR namespace and other refs write a stable hash of their ref. GitHub's
cache ref restrictions also prevent a PR merge-ref cache from replacing a base
branch cache. No cache job receives publication credentials. Cache receipts and
hits prove reuse, not agent/browser/engine behavior or artifact availability.

For a bounded nonpublishing benchmark, manually run `runner-release` with
`cache_probe=true`. It skips the normal candidate/package pipeline and runs both
providers on native Linux AMD64 and ARM64. Each job builds with `no-cache`, commits
an actual documentation-only source change and rebuilds warm, then commits a real
browser dependency-build recipe change and rebuilds again. All three actual
images retain their own source labels, push only to a disposable loopback registry
and execute the normal Git/browser/empty-tracker/graceful-stop qualification.
These synthetic source commits are diagnostics, never release candidates. After
that run completes, run the same branch again with both `cache_probe=true` and
`cache_probe_restore=true`. The second isolated run skips the cold build and
imports the first run's exported GHA cache into a fresh builder. Inspect actual
cache-import and layer-hit logs before claiming persistent cross-run reuse; a
same-job hit alone is insufficient. The changed-recipe build imports the baseline
snapshot but disables cache export, preserving the original dependency graph and
avoiding an extra large snapshot that could evict the next run's warm baseline.
Every build action also creates its own builder, so the ordinary three-phase probe exercises the cache service rather
than relying on a previously populated daemon builder.

Retained `runner-cache-*` artifacts contain source/digest records, qualification
logs and measured build/load, archive export/compression CPU/wall time, size and
checksum costs. Docker build records and CI logs retain per-layer cache outcomes;
job metadata supplies queue and critical-path times. The probe deletes its large
measurement archives after hashing and does not claim payload upload/download
or archive replay evidence. Those remain the candidate/lifecycle consumers' proof.
Compare actual results before reporting an improvement; unexecuted targets remain
open acceptance. No production publication, installed manager or existing runtime
is involved.

Ordinary event planning and original payload retrieval
----------------------------------------------------

`component-plan.yml` resolves the same committed Docker input closure and bounded
original native evidence on ordinary PR and dev/main push events. The full plan
is retained as a source-bound artifact; job outputs contain only explicit
`build`/`reuse` decisions. Browser CI builds and executes browser qualification
only when its browser closure requires a build. Browser reuse requires matching
verified root/Codex and agent-user/Claude native qualifications for that same
browser closure, retaining both original records and origins. Actual production
build options are projected into the browser stage graph; provider-only options
are excluded. Reuse records the retained qualification explicitly. A missing, failed or
cancelled plan cannot become a successful omission. Documentation-only events
continue to omit both planning and behavioral work.

`reuse_payload.py --plan PLAN --provider codex --architecture x86_64
--destination NEW_DIRECTORY` independently rechecks the original origin and
current committed closure, downloads the original immutable artifact, verifies
its archive digest, then selects and verifies the exact Docker archive member.
It retains the original record, checksum and origin beside those actual bytes.
It never builds, exports, uploads or renames an image. `--prior` requires an
available original prior record; it never fabricates one. Download, checksum and
member failures leave no completed destination or successful receipt.

Payload retrieval prepares the existing packaged-manager lifecycle consumer;
it does not itself prove Docker config identity or runtime behavior. Ordinary
runner integration assembles actual native packages from the current manager
source, verifies their source and archive checksums, then executes the installed
current manager with original immutable runner payloads. Unchanged components
bypass the native image producer entirely. Changed components use the same
production native producer and retain their actual original records.

`package-lifecycle.py --current-manager-only PACKAGES RUNNERS EVIDENCE` qualifies
one actual current manager package, source-free installation, both provider
listeners, same-basename project isolation, persistent dirty/untracked/state and
auth-volume canaries, stop/restart and owned cleanup. It retains explicit
single-manager evidence and never invents a prior package revision. The default
controller mode continues to require two real manager revisions and exercises
actual update, failure, pinned retry and rollback separately. Both modes verify
original runner config/source/protocol/input labels and restore/pull only the
original isolated-registry digest, without assigning a manager revision to an
older image.

`build-manager-package.sh MANIFEST OUTPUT PLATFORM SOURCE FULL_SHA [HELPER]`
extracts the native package assembly used by these consumers. It calls the
existing native helper builders and source's packer, verifies exact source,
platform and binary CPU, and builds historical Mac helpers with `--native`.
An explicitly supplied already-qualified helper avoids a duplicate compile.
`current-manager-package.yml` consumes the shared two-host policy and binds both
actual archive hashes; no nonexistent platform archive receives a placeholder
checksum.

Pre-merge candidate diagnostics may pass `--baseline-run ID` to the payload
consumer only in an explicitly invoked `workflow_dispatch` context. The consumer
independently verifies that selected original run; a plan's diagnostic metadata
never grants permission. Ordinary PR/push consumers always require original
source/controller ancestry in verified fork dev/main history. Actual archive
retrieval also bounds the streamed artifact bytes and total download time.

### Portable host helper (candidate qualification)

On macOS, `bash scripts/release/build-host-helper.sh /tmp/symphony-host` retains
one universal Intel/Apple Silicon executable. `--native` is a development-only
single-architecture build. On native Linux x86-64, install the matching Swift
6.1.2 toolchain and Static Linux SDK, then use the same command, or run
`bash scripts/release/build-linux-host-helper.sh /tmp/linux-helper` with Docker.
The latter pins the Swift build container, SDK integrity checksum and portable
Crypto/ASN.1 revisions. These are build dependencies, never installed runtime
requirements. Linux ARM64 manager/helper support is outside this contract.

The Linux build controller downloads the pinned SDK with `curl`, verifies its
official SHA-256 before Docker starts, then installs the local archive inside the
Swift container. This avoids the Swift 6.1.2 remote installer's observed
FoundationNetworking/URLSession crash. A current controller can build an actual
prior checkout without modifying it:

```sh
bash scripts/release/build-linux-host-helper.sh /tmp/prior-helper /tmp/prior-source FULL_SOURCE_SHA
```

The selected source and Git metadata are mounted read-only; compiler/SwiftPM
writes stay in temporary storage. The executable's compiled revision must match
the requested source. `build.txt` distinguishes source/controller revisions and
records the SDK checksum; `elf.txt` retains actual runtime-dependency inspection.
For a small nonpublishing diagnostic, manually dispatch `Package checks` with
`previous_revision`: it builds/tests both real helpers through the current
controller. This is build/helper evidence, not a substitute for the two-archive
installed-manager lifecycle qualification.

`Package checks` executes the shared hash, typed JSON, identity, path/link and
inherited-descriptor lock contracts on native macOS and Linux. The Linux artifact
must have no ELF interpreter or required shared libraries and is additionally
executed in clean Ubuntu 26.04 without Swift, Git or scripting runtimes. The
retained artifact includes SHA-256 and clean-container evidence, bound to the
workflow source SHA. CI controller utilities are distinct from helper runtime
requirements. Cross-compiling a binary does not prove native execution.


### Linux and macOS bundle metadata

`artifacts.py assemble --platform-bundles` creates internal metadata with both
native archive slots. Final assembly supplies `--bundle MACOS_ARCHIVE` and
`--linux-bundle LINUX_ARCHIVE`, binding both SHA-256 checksums to the same source
and immutable runner identities. Schema1, `host_architectures` and legacy `bundle`
remain unchanged: the old macOS updater still selects its ordinary macOS archive.
The additive `bundles` map mirrors that macOS entry and adds `linux_x86_64`.
Each archive records `package_platform`; new Linux managers require this marker
and select the Linux field. Prior macOS manifests without the marker remain usable.

The manual `runner-release` workflow accepts an optional full `previous_revision`
SHA. When supplied, it builds actual current/prior runner images and platform
archives, then performs the native clean-Ubuntu two-revision installed lifecycle
proof. Candidate artifacts/evidence remain workflow artifacts only. Production
publication still requires a production tag, enabled release variable and the
protected environment; the production manager artifact now contains both native
archives and the backwards-compatible combined manifest. No test controller can
satisfy an unavailable native platform by replacing it with a compile or mock.

For controller-only lifecycle diagnosis, manually dispatch
`runner-release` with `diagnostic_artifact_run` set to a completed run ID
and `diagnostic_package_source_revision` set to its exact current package SHA.
Only the diagnostic job runs in this mode. It downloads that run's real checksummed
current/prior packages and x86-64 images, then uses the selected controller head.
Evidence records the controller SHA, package SHA and diagnostic artifact run
separately. This never publishes and does not replace final same-head native
build and lifecycle acceptance in `runner-release`.

The real current/prior candidate controller uses `build-runner-cached.sh` through
an explicit executable callback. Each source context retains its actual Git
revision, version, recipe and native platform; both roles receive persistent
layer imports. Current and prior exports use separate bounded snapshots so a
prior build cannot replace the current graph. The local Actions runtime bridge
masks the standard cache token and passes it only to BuildKit's cache service;
it never becomes a Docker build argument or container environment variable.
The GitHub cache quota is shared across these bounded snapshots. Missing or
partly evicted blobs can force dependency rebuilding even after an initial
`CACHED` marker; report the later errors and actual timings, not marker counts
alone. No other workflow's cache is deleted to make a probe succeed.

Manager activation checks every retained project image before changing the active
manager or starting a runner transition. The config identity, provider and native
container platform must match, and protocol 2 or an unknown protocol is refused.
Protocol 1 is compatible. A historical image without a protocol label has one
explicit migration route: its source and immutable RepoDigest must exactly match
the verified previous schema1 release. A private config-keyed receipt retains
the original canonical release directory as the migration basis. Every later
start, update and rollback revalidates that original release inventory and exact
image/source evidence; a receipt alone grants no trust. That original directory
must remain available; removal or tampering causes explicit refusal. Arbitrary
unlabeled local images cannot satisfy that route. Retained project starts apply the same checks. Projects still
using older images outside that exact previous release need a compatible bridge
runner transition before a later manager-only update; refusal preserves the
current manager and project state. Native lifecycle qualification must establish
that migration and refusal behavior separately from these portable regressions.

Schema2 may carry a separate `distribution` provider/architecture map. Each value
is an immutable image reference with exactly the original qualified OCI digest;
changing that digest is rejected by Python and the native helper. Original runner
records and proof remain unchanged. Installed consumers select the distribution
reference but verify the original source, input identity, protocol and config ID.
External and bundled manifests must carry identical distribution maps. This
contract alone does not publish, transfer or establish availability of an alias;
plan-aware availability checks and protected publication remain separate work.
