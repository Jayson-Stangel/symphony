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
