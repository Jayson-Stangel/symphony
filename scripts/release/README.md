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

`runner_reuse.py` plans one exact build-or-reuse decision per provider/platform
against an explicit baseline. It neither builds nor deploys. Unchanged inputs and
verified original evidence permit reuse; missing evidence requests an explicitly
reported verified-input rebuild. Unknown source closure stops before any reuse.
A `--require-reuse` diagnostic fails if any rebuild would be needed. Reused records
preserve every original digest/label/source and never cause export/upload just to
associate the image with a new manager head.

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
only when its browser closure requires a build. Reuse keeps the original native
qualification and records that decision explicitly. A missing, failed or
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

Payload retrieval is preparation for the existing packaged-manager lifecycle
consumer. It does not itself prove Docker config identity, runtime behavior or
current-manager integration. Ordinary runner integration must retain its real
behavioral checks until the native package lifecycle prerequisite is available
and that consumer is composed with these archives and the actual new manager.
