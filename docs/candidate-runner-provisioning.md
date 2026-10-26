# Candidate runner provisioning

A candidate package and a production release have different distribution
requirements. Production releases use anonymously readable immutable GHCR
references. Candidate components can retain the original `localhost:5000`
qualification reference; that address names a temporary registry on the build
host and is not a public download service. A fresh machine needs the exact
companion payload before provider login. A Docker `load` or matching config ID
cannot recreate the selected registry digest.

## Download and install

Download and verify the manager package using the reviewed candidate's metadata.
Follow [macOS security guidance](macos-install-security.md) for Apple Silicon,
or the Linux x86-64 path in [packaged installation](packaged-install.md).
Use separate candidate application/configuration/bin/skills paths and namespace.

After `symphony init`, fill the printed private project credentials file. For a
candidate whose selected image is an original loopback graph, the existing
`GH_TOKEN` (or `GITHUB_TOKEN`) needs **Actions read access to
Jayson-Stangel/symphony**, in addition to the repository access needed for work.
Do this before provider login; a native Codex/Claude account does not grant GitHub
artifact access. No separately exported artifact-download token is needed.

Run normal onboarding:

```sh
symphony auth codex login
# Or: symphony auth claude login
```

With a cold or pruned image cache, the manager checks the exact artifact ID in
the selected component's original qualification. It checks access, expiry,
provider/architecture name, source run/controller revision and sealed ZIP digest
before downloading the large payload. Progress reports the artifact identity,
byte count, download and verification stages. The native helper verifies the
complete ZIP checksum and streams only its exact original graph archive member;
it verifies that archive's checksum before the supported importer validates and
imports the complete manifest/config/compressed-layer closure. Host Git, gh,
Python, Node, unzip, Swift and image builds are unnecessary. Curl with CA
certificates remains an ordinary runtime prerequisite.

Reused components retain their original run, source and input identities. The
manager never searches for a similarly named replacement. An existing verified
image cache is reusable without a download credential check. Explicit or retained
team-owned environments keep their existing selection and are not replaced by
automatic default provisioning.

An unavailable, expired or inaccessible original artifact refuses onboarding
before the large transfer. Correct the private credential's Actions permission,
retry a temporary network/disk failure with the same login command, or choose an
available reviewed candidate. Each attempt uses private temporary files; failure
or interruption discards partial downloads without selecting a project image.
Allow disk space for the ZIP, compressed graph archive, extracted graph and Docker
storage. Two automatic network retries precede an actionable refusal. A candidate
is usable through this path only while its exact original artifact is accessible;
merging the implementation does not renew expired artifacts.

## Manual and offline recovery

Automatic acquisition is the normal path. To inspect the exact original
companion identity without a download or Docker/model request:

```sh
symphony runner import --agent codex
```

For offline recovery, obtain that exact original archive from its maintainer or
its identified Actions artifact and use the import command below. Do not rebuild
or relabel a substitute. A ZIP extracted manually is only a recovery option.

## Import the exact graph

Prerequisites are a running local Docker engine (Docker Desktop/OrbStack on
macOS, Docker Engine on Linux), the `registry:3` Docker image from Docker Hub,
free local port 5000, and disk space for the downloaded archive, its extracted
compressed graph, and Docker's stored image. Provision the registry prerequisite
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
layout. The launcher starts one uniquely named, ownership-labelled registry,
pulls the selected original digest, verifies runtime identity and removes only
that registry and its temporary files. It starts no listener and does not select
or replace a project's retained/team-owned environment. No host Python, Node,
Swift compiler, agent CLI or image build is required.

The importer refuses remote Docker contexts because a local bind mount must
refer to the verified graph. An occupied port, missing/corrupt archive, unsupported
manifest, mismatched platform/provider or lost ownership fails without replacing
another service or weakening verification. Resolve the reported prerequisite and
retry. If cleanup cannot confirm ownership, its diagnostic retains the exact
staging path for investigation. Never remove resources by a broad name filter.

After a successful import:

```sh
symphony auth codex login
symphony auth codex check
# From the real initialized project with its explicit origin remote:
symphony runner doctor --agent codex
```

Repeat import with the same original companion if Docker later prunes the exact
digest. The local registry is temporary and is not expected to remain running
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
graph refusal, Docker-context precedence, occupied-port cleanup and immutable
runtime verification. Native helper preparation has also been exercised against
the original October 24 Apple Silicon candidate graph, preserving digest
`sha256:e75f2deb748f5d3021645d78e47870a54ad288a6879d952f39f25cd24e9ac0a5`.
That proves archive/graph preparation only. Fresh-account downloaded-package
security acceptance, an actual Docker cold/pruned import, provider login and
unchanged Melody project readiness still require their own recorded evidence.

Automatic acquisition fixtures additionally cover exact artifact metadata,
ZIP/ZIP64 stored-member extraction (the existing workflow uses compression level
zero), access/expiry/checksum refusal, cache reuse and repeated cold/pruned
attempts. These are synthetic local fixtures, not a fresh-account downloaded
signed-package test or real authenticated GitHub/Docker/provider proof. Final
composed macOS/Linux qualification must use the actual reviewed importer and
manager inputs; publication/signing and stable rollout remain separate actions.
