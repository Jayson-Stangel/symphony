# Runner and manager release artifacts

The installed manager consumes `release.json`, independent of Git and the Symphony
source checkout. Its version, 40-character source revision, host architecture and
provider resolve one immutable Linux OCI image digest. `arm64` uses Linux ARM64;
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

After the normal review, promotion and release approval gates, create the matching
`vVERSION` tag. All four native candidate image qualifications must pass before
the protected publication jobs can push those exact tested images. Publication
then removes login credentials, pulls each GHCR digest anonymously, reruns smoke,
and records the actual public digest. The manager archive and checksum are built
from those four records at the same revision. The packaged-manager prerequisite
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


### Explicit per-platform host architectures

New assembly derives `supported_hosts` from `host-targets.json` and declares
`bundle.architectures=["arm64"]` for macOS and
`bundles.linux_x86_64.architectures=["x86_64"]`. The global schema1 architecture
union, legacy macOS filename, immutable image and revision checks remain intact.
Validators still accept historical manifests without the additive fields; new
packaging requires the explicit policy and checks the helper's compiled CPU.
When packaging a prior source on the native ARM Mac, the current controller passes
`--native` to its unmodified helper builder, avoiding an old universal compile.
