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
identities. New macOS host bundles support only Apple Silicon ARM64; Linux host
bundles support only x86-64. Both Linux container architectures remain required.

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

After normal review, promotion and release approval, a matching `vVERSION` tag
uses the complete source-bound component plan. Before the protected transfer job,
all original native evidence and payload availability are reverified. Every
reused public digest must already be anonymously available with raw manifest
bytes and a registry digest header matching the exact original OCI digest.
Every referenced config is fetched and checksummed; every layer must provide a
successful anonymous HEAD with its exact digest and length. Index manifests are
followed within fixed graph/count/byte bounds. Missing or denied referenced
blobs refuse despite a valid root manifest. All transfer records, unique target
selections, original origins and destination availability are validated before
the first payload retrieval or image mutation.

The protected job transfers only missing newly built components. It loads the
original verified payload, checks the original config/source/input/platform,
pushes that exact image and refuses any digest conversion. After a new transfer,
it streams and checksums every referenced blob anonymously, without executing
foreign-architecture images or claiming a new native image qualification.
Already available original digests are never retagged or pushed. Manager-only schema2 releases run
zero image build, export, transfer or repeated native qualification commands.
The complete original records and separate immutable `distribution` map reach
both internal and external manager manifests, built with already-qualified
native helpers. Repository publication credentials reach only the registry
client, never Docker build arguments or container environments.

A strict schema1 bridge remains publishable: its plan requires all four actual
current-source components. The legacy `runners` view selects their public refs;
additive `original_components` retains every original native record unchanged.
Both views must agree in source, config, tools and exact OCI digest. New native
validators check this relation; old strict schema1 updaters keep their existing
source-equals-manager contract. Manual runner-release defaults to this explicit
bridge mode; ordinary component integration uses schema2, while the protected
automated release follows the committed release-schema policy. No
production publication has been exercised by the nonpublishing qualification.

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
upload failure. Production preflight validates all selected components and public
reuse availability before the single protected transfer job can push anything.

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

On Apple Silicon, `bash scripts/release/build-host-helper.sh /tmp/symphony-host`
builds only ARM64. Intel macOS and Linux ARM64 host helper builds refuse before
compilation; neither starts a compiler or creates an output directory. On native Linux x86-64, install the matching Swift
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


## Candidate image transfer

`export-image.py` streams `docker save` through bounded `pigz -1 -n` when present
(up to four CPUs), or standard `gzip -1 -n`. It emits the existing gzip/Docker-load
format and checksum filename selected by its caller. Multiple `--image` values
share one archive, retaining Docker's shared-layer serialization for two sources.
The archive's config hash set must match the inspected source/provider/platform
identities before publication. Original distribution digests remain provenance;
a Docker-load representation is never relabeled as an old registry digest.

The adjacent `.metrics.txt` contains JSON for each actual image identity,
compressor/version, process CPU, overlapped export/compression wall times, checksum
and archive-verification costs, and compressed bytes. Export wall time includes
pipe backpressure; those overlapping wall times must not be added together.
Only the compressed payload is staged; owned temporary files and children are
cleaned on failure. Existing outputs are refused. Actions uploads gzip payloads
with `compression-level: 0` to avoid redundant ZIP deflation. Metadata consumers
must continue downloading only native qualification records, not image payloads
or transport telemetry.

For build-free, nonpublishing native measurements, dispatch the existing
`runner-release` workflow with `transfer_diagnostic_run` set to an exact retained
same-repository candidate run ID. The diagnostic verifies the successful native
producer, original source records, archive checksums and actual config hashes.
It compares original gzip-default export/load against the selected faster
compressor, retaining current/prior images together when provided by that run.
Separate hosted upload/download steps retain their real timings. A downstream
job changes an actual archive byte to prove checksum refusal before Docker load,
restores and rechecks the original payload, loads it, and runs actual Git/browser,
empty-tracker startup and graceful-shutdown checks for every retained source.
This does not rebuild images or publish a registry/release. Missing or expired
payloads fail; a successful metadata receipt alone is insufficient.

Local transport fixtures run with:

```sh
python3 -m unittest discover -s scripts/release/tests -p test_image_export.py -v
```

Those fixtures exercise actual gzip/tar bytes and subprocess failure propagation;
they do not replace native production-image benchmark/replay evidence.

To consume an already completed benchmark without repeating export/compression,
set `transfer_replay_run` to that immutable benchmark run ID instead. The replay
checks the successful matching provider/architecture producer and available
payload. It uses the existing candidate lifecycle's isolated loopback `registry:3`
route: push the retained candidate/previous tags, require each observed digest to
equal the original qualified record exactly, pull that original digest, and run
Git/browser/startup/graceful-stop against the digest. A changed distribution
digest fails; config-ID equality alone is insufficient. Only `localhost:5000`
references are permitted, and registry cleanup verifies the owned container label
and actual absence. No public registry publication, credentials or image rebuild
is involved. The input source-run revision and original image revisions remain
separate, truthful provenance.

For one bounded measurement of a retained original gzip candidate payload, use
`runner-release.yml` with `transfer_timing_run`, `transfer_timing_provider` and
`transfer_timing_architecture`. Its existing dispatch entry calls the reusable
`transfer-timing.yml`; candidate builds, benchmarks and runtime replays skip.
This diagnostic currently requires the legacy combined image/record artifact
layout, such as run `39857032359`; split record/payload layouts fail closed
until their normal integration. It verifies the successful completed producer
and matching native Ubuntu runner label before downloading its artifact.
It downloads only that original artifact, checks its original checksum and
source/config identities, and measures real checksum, identity inspection and
archive verification CPU/wall time. It never loads, rebuilds or re-exports an
image, starts a registry, or schedules another provider/architecture. Receipts
name the retained archive SHA and byte count. These are measurements of the
existing original payload, not reconstructed timings for a deleted benchmark
baseline with potentially different compressed bytes. A representative dispatch
for the retained Claude x86-64 payload is:

```sh
gh workflow run runner-release.yml --ref codex/faster-image-transfer \
  -f transfer_timing_run=39857032359 \
  -f transfer_timing_provider=claude -f transfer_timing_architecture=x86_64
```

### Explicit per-platform host architectures

New assembly derives `supported_hosts` from `host-targets.json` and declares
`bundle.architectures=["arm64"]` for macOS and
`bundles.linux_x86_64.architectures=["x86_64"]`. The global schema1 architecture
union, legacy macOS filename, immutable image and revision checks remain intact.
Validators still accept historical manifests without the additive fields; new
packaging requires the explicit policy and checks the helper's compiled CPU.
On macOS it checks every Mach-O slice before helper execution; supplied universal
helpers are refused even when their executing ARM slice reports the right CPU.
When packaging a prior source on the native ARM Mac, the current controller passes
`--native` to its unmodified helper builder, avoiding an old universal compile.

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
contract alone does not establish availability of an alias. Plan-aware protected
publication verifies actual anonymous immutable availability and transfers only
missing newly built components; candidate tests remain loopback-only.
Ordinary current-manager helper scheduling
------------------------------------------

`runner-integration` gates expensive work on the shared portable preflight. The
`native-manager-helpers` reusable workflow then builds and qualifies the exact
current-source Linux x86-64 static helper and macOS ARM64 helper concurrently
with changed-component planning/building. It uses the shared two-host policy,
runs each host's native package contracts and the clean Ubuntu helper proof, and
retains source-bound checksums in same-run artifacts. Current-manager assembly
requires that successful source revision, verifies the downloaded checksum before
restoring executable permissions, and rechecks actual source/platform/CPU through
the shared builder. It does not compile a second current helper after images
finish. The terminal current-manager guard requires portable and helper success
as well as the actual package/component decisions; cancelled or skipped native
qualification cannot produce a successful behavioral receipt.

A manual ordinary-integration diagnostic may set `distribution_alias=true` to
exercise the schema2 distribution map in the existing installed lifecycle. It
uses fixed disposable localhost repositories and each original OCI digest,
without changing original component records, sources, labels, input identities
or checksums. The native Linux x86-64 controller pushes/pulls each provider's
alias, verifies the same original digest and config, then checks the actual
installed manager's selected container reference. Both native ARM payloads retain
their original qualification; this x86-64 lifecycle does not claim ARM alias
runtime proof. Alias references and the deliberately incompatible protocol2
negative fixture are cleanup-owned diagnostics, never new reusable baselines.


The committed `scripts/release/release-schema` selects the production manager
format independently of both the manager version and engine `mix.exs` version.
It starts at 1 so the normal protected tag path can publish an actual strict
schema1 bridge. Shared preflight validates the policy and exports it to Burrito's
runner caller; publication rejects a format that differs from that committed
policy. First publish and install an approved bridge version through the ordinary
stable release path; a candidate-only bridge cannot migrate stable installations.
Only a later, separately versioned manager release changes `manager-version` and this policy
to 2, outside every runner Docker input closure. Engine version and original
runner labels remain their actual original values.

Manual full candidates may select `previous_artifact_run` from an actual successful
schema1 current-package run. Cheap common qualification first checks that exact
fork/workflow/source/attempt, every native image and package stage, prior installed
lifecycle, immutable metadata and available payload service identities. The final
Ubuntu lifecycle consumer independently repeats those checks, downloads only the
needed historical Linux package and x86 image payloads, verifies ZIP/member hashes
and actual selected config/source/provider/platform, and preserves old records.
Historical images are never rebuilt, exported or uploaded as new candidate
artifacts. `legacy_artifact_run` additionally supplies an actual old installed
package for a three-package manager-only migration; `manager_only_update` refuses
if the current source plan needs any runner build. Original archive tags and
immutable digests remain provenance even when the isolated loader creates a
local role alias. Small lifecycle summary artifacts retain original sources,
checksums, request/checkpoints and cleanup independently of full evidence.

The manual ordinary `distribution_alias` proof additionally runs a tiny synthetic
OCI closure probe after actual manager/alias integration cleanup. Its new owned
registry is separate from qualified runners. Complete blob checksums, an actual
missing storage blob, a real HTTP permission denial and a foreign digest are
negative protocol fixtures; they never become native image qualifications or
reusable records. Reuse availability reads each config and a bounded byte range
from each layer as well as HEAD metadata, so descriptor caches cannot alone prove
that the stored blob is readable. New publication transfers checksum every byte.

`package-lifecycle.py --manager-only-update` requires two distinct actual manager
packages with exactly unchanged original runner records. It proves installed
manager activation, release rollback, checksum refusal and retry, then restarts
the retained projects while checking both projects and their persistent canaries.
When `packages/legacy/release.json` is supplied, it requires three distinct real
sources: the historical schema1 manager in `packages/legacy`, the strict schema1
bridge in `packages/previous`, and the schema2 manager at the package root. It
installs and initializes both providers under the historical manager, retains
those actual unlabeled images through both manager updates, and exercises the
bridge's durable compatibility basis across the later rollback/retry/restart.
`images/legacy` contains the verified original historical archive/checksum pairs;
the root contains unchanged bridge runner payloads. The consumer loads the old
archive first and creates only a local `:previous` role alias before loading the
current root tags. Original records, archive tags, source labels and OCI digests
remain unchanged. Without the legacy input, the result explicitly limits its
claim to the two actual managers; it does not invent a historical migration.

The shared JAY-695 source-build graph supports an explicitly selected raw
`previous_revision`: its qualification resolves the
full commit and checks native/Linux package capabilities before image fanout.
Both prior helpers build alongside current helpers and images; assembly consumes
same-run, platform/prior-SHA/checksummed helper artifacts rather than compiling
old helpers after images finish. The old macOS helper uses `--native`, actual
Mach-O ARM64/source/platform/hash checks, and its real later package assembly.
Historical universal-layout fixtures do not describe this native-only target;
all current native/helper fixtures remain required on both supported hosts.
Previously qualified artifact subjects use an empty raw source-build selector
and therefore do not trigger any old helper compilation.

The component producer currently refuses raw prior-source composition during
common qualification, before old helper or image work. Its supported historical
route is the verified artifact selector above. The reusable prior-helper interfaces
remain available to the independent source-build controller without claiming that
legacy recipes satisfy the new input/protocol closure.
