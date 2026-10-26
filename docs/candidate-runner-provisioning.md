# Candidate runner provisioning

A candidate package and a production release have different distribution
requirements. Production releases use anonymously readable immutable GHCR
references. Candidate components can retain the original `localhost:5000`
qualification reference; that address names a temporary registry on the build
host and is not a public download service. A fresh machine needs the exact
companion payload before provider login. A Docker `load` or matching config ID
cannot recreate the selected registry digest.

## Download and install

Download the manager archive, its trusted SHA-256 checksum, and the original
runner companion artifact for each provider you intend to use. Use the same
reviewed candidate's metadata; reused components can originate in earlier runs.
The package's complete original component record remains authoritative. Do not
substitute another run's similarly named image or label an older image as a new
build. Follow [macOS security guidance](macos-install-security.md) for downloaded
Apple Silicon packages, or the normal Linux x86-64 package installation path.
Configure separate candidate application/configuration/bin/skills paths and a
runner namespace as described in [packaged installation](https://github.com/Jayson-Stangel/symphony/blob/dev/docs/packaged-install.md).

After installation, before login, display the selected companion identity:

```sh
symphony runner import --agent codex
# Repeat with --agent claude if needed.
```

This command makes no Docker or model request. It prints the exact source run
URL, artifact ID/name, archive filename and archive SHA-256 bound to the selected
package. Open that run's artifacts in GitHub, download the specified original
artifact and extract its ZIP. GitHub may require a signed-in account with access
to the repository. An expired or inaccessible artifact blocks this candidate's
onboarding: obtain the original verified payload from its maintainer, or select
an available reviewed candidate. Do not reconstruct or rebuild a replacement
and claim it has the selected identity.

## Import the exact graph

Prerequisites are a running local Docker engine (Docker Desktop/OrbStack on
macOS, Docker Engine on Linux), the `registry:3` Docker image from Docker Hub,
local daemon host-network support, and disk space for the downloaded archive,
its extracted compressed graph, and Docker's stored image. Provision the registry prerequisite
through your normal Docker image access policy:

```sh
docker pull registry:3
symphony runner import --agent codex \
  --archive /absolute/path/to/codex-arm64.docker.tar.gz
```

Use `codex-x86_64.docker.tar.gz` on Linux x86-64. Use the corresponding `claude`
filename/provider for Claude. Paths with spaces are supported when quoted.
The installed native helper verifies the archive checksum against the selected
package's original qualification, validates graph membership and every selected
manifest/config/compressed-layer digest, and stages a private read-only registry
layout. The launcher starts one uniquely named, ownership-labelled registry
in the local Docker daemon's network namespace. Both its transfer and debug
listeners bind only IPv4 loopback with kernel-allocated ports. It publishes no
host port and never needs exclusive use of port 5000, including on Docker
Desktop. Each concurrent import owns its own container and endpoints; startup
allocation failures have at most three attempts and readiness has a bounded wait.
The launcher pulls the exact original manifest digest through that temporary
endpoint, verifies runtime identity and removes only that registry and its
temporary files after success, failure or interruption. It does not start a
Symphony listener or select or replace a project's retained/team-owned environment. No host Python, Node,
Swift compiler, agent CLI or image build is required.

Docker records the transfer repository in `RepoDigests`. A private atomic receipt
under the candidate application's `runner-imports/` directory remembers the
allocated loopback reference for subsequent login and doctor commands. The
native helper accepts relocation only for an original loopback candidate, with
the same provider repository and exact original manifest digest. Config,
compressed-layer, source, input and platform checks remain required; a matching
Docker config ID alone cannot authorize an import. Production registry selection
is unchanged. Import information always prints the original companion identity,
and reimport after pruning uses that original metadata.

The importer refuses remote Docker contexts because a local bind mount must
refer to the verified graph. Missing host-network support or unsafe Docker
bind-mount access is actionable as a local engine prerequisite. A missing/corrupt
archive, unsupported manifest, mismatched platform/provider or lost ownership
fails without replacing another service or weakening verification. Resolve the reported prerequisite and
retry. If cleanup cannot confirm ownership, its diagnostic retains the exact
staging path for investigation. Never remove resources by a broad name filter.

After a successful import:

```sh
symphony auth codex login
symphony auth codex check
# From the real initialized project with its explicit origin remote:
symphony runner doctor --agent codex
```

For manual recovery after Docker prunes the exact digest, repeat import with
the same original companion. The local registry is temporary and is not expected to remain running
between commands. General doctor, provider authentication, project sandbox/Git
readiness, listener startup and a completed authenticated task are separate
acceptance stages.

## Production and acceptance boundary

Production publication planning and execution refuse candidate-only loopback or
unsupported distribution references before mutation. The existing protected
publication pipeline must prove anonymously available original immutable blob
closures before advertising a production release as installable. This import
flow does not authorize registry publication, version tags, production promotion
or stable rollout.

Fixture tests cover cold/pruned import orchestration, path quoting, checksum and
graph refusal, Docker-context precedence, bounded allocation recovery, interruption
cleanup and immutable runtime verification. The opt-in native Docker regression
(`SYMPHONY_TEST_DOCKER_IMPORT=1`) imports a synthetic original graph concurrently
while an unrelated real host listener occupies port 5000, verifies it remains
available, rejects a corrupt archive and checks owned registry cleanup. This runs
in the ordinary Linux native package PR job and can run locally on macOS with
Docker Desktop. Synthetic graph transfer is separate from real-provider or
fresh-account downloaded-package qualification. Native helper preparation has also been exercised against
the original October 24 Apple Silicon candidate graph, preserving digest
`sha256:e75f2deb748f5d3021645d78e47870a54ad288a6879d952f39f25cd24e9ac0a5`.
That proves archive/graph preparation only. Fresh-account downloaded-package
security acceptance, an actual Docker cold/pruned import, provider login and
unchanged Melody project readiness still require their own recorded evidence.
