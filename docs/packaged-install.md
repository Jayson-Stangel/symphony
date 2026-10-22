# Packaged macOS installation

This first bundle targets macOS 14 or later.

Install Docker Desktop or OrbStack separately and start its engine. Download a
completed, approved Symphony release archive and its `release.json` from the
project's GitHub release. Candidate archives are for isolated qualification only;
normal stable downloads and updates must select completed production releases.
Verify the archive against `bundle.sha256` in the accompanying metadata before
unpacking. On a standard Mac, `shasum -a 256` prints the archive's checksum.

```sh
tar -xzf symphony-VERSION-macos.tar.gz -C ./symphony-download
cd ./symphony-download
./install.sh
export PATH="$HOME/.local/bin:$PATH" # if the installer reports PATH is missing
cd /path/to/existing/project
symphony init --linear-project YOUR-LINEAR-PROJECT-SLUG
# Edit the exact private credentials-file path printed by init.
symphony auth codex login              # or: symphony auth claude login
symphony runner doctor                 # or: symphony runner doctor --agent claude
symphony run                           # or: symphony run --agent claude
symphony runner status
symphony runner logs
symphony runner stop
symphony runner start
```

Create `symphony-download` before extracting. The project must already be a Git
checkout with a remotely accessible, explicit `origin` URL. The packaged manager
finds the repository and its local origin without host Git. Normal Git-generated
configuration, quoted URLs, linked worktrees and repository paths with spaces are
supported. A project using Git `include`, `includeIf`, per-worktree configuration, or local URL rewrite rules
must first
remove those indirections and make that remote explicit in local configuration; the manager refuses ambiguous
identity rather than choosing a different repository. Cloning the target project
and managing Docker installation are outside the installer.

The printed project credentials file is outside the checkout, protected by the
user's private configuration directory. Fill `LINEAR_API_KEY` and a fine-grained,
repository-scoped `GH_TOKEN` there. Keep native agent login in the Docker-managed
authentication volume. Do not copy host agent, SSH or home configuration into
workers. The dashboard is available only on loopback at
`http://127.0.0.1:4010`; select another port with `symphony run --port PORT`.
Existing workflows, local skill overrides and credentials are preserved by
repeated init. Different repositories with the same basename have distinct
configuration, containers, workspace and state identities.

The archive carries a universal ARM64/Intel macOS host helper built in release
CI. End users need no Git, Python, Node, Codex, Claude, mise, Erlang, Elixir, Swift,
Xcode Command Line Tools or local image build. Apple Silicon selects the Linux
ARM64 runner; Intel selects Linux AMD64. Missing or unsupported artifacts and
failed image pulls stop clearly without selecting a different provider or
architecture. Version/doctor diagnostics identify the bundled version, source
revision and immutable runner digest without a Symphony `.git` directory.

The installer stores immutable releases under
`~/.local/share/symphony/releases/VERSION-REVISION`. A single `current` link selects
the manager and bundled skills. `~/.local/bin/symphony` and the namespaced managed
skills link through that current release. Installation needs no sudo. Rerunning
the same unmodified version verifies its inventory and is safe. Existing
user-owned command paths or skill links are refused before activation; an
existing source installation is never repointed or migrated automatically. Use
separate paths for an isolated candidate instead:

```sh
export SYMPHONY_RUNNER_NAMESPACE=candidate-install
export SYMPHONY_APP_HOME=/tmp/symphony-candidate/app
export SYMPHONY_CONFIG_HOME=/tmp/symphony-candidate/config
export SYMPHONY_BIN_DIR=/tmp/symphony-candidate/bin
export SYMPHONY_SKILLS_DIR=/tmp/symphony-candidate/skills
./install.sh
```

Keep that environment for every candidate command and use a distinct dashboard
port. Source-only identity migration must be completed through the original
source installer before adopting packaged storage; automatic migration is
explicitly deferred. The package helper preserves the existing inherited-file-
descriptor advisory locking and admission checks; it does not own a second
transition journal or dispatcher.

## Building and checking a candidate

On a macOS development/CI host, create an internal release manifest with the
JAY-686 tooling, then build the universal helper and archive:

```sh
bash scripts/release/package.sh /path/to/internal-release.json /tmp/candidate-output
python3 scripts/package/test-package.py -v
```

The optional third packer argument supplies a prebuilt host helper. Use
`build-host-helper.sh OUTPUT --native` only for local native-architecture checks;
release builds omit `--native` and use `lipo` for both supported host layouts.
Python and Swift are build/test dependencies, never installed-user dependencies.
The archive inventory verifies manager, helper, workflow and skill bytes before
staging. `install.sh --stage-only` verifies and stages an immutable release without
activating command or skills; the packaged updater uses that same staging
boundary. An existing project's pinned image remains selected when another
project activates a shared manager release, until the installed updater performs
that project's explicit runner transition.

Tests with a stub container CLI prove command selection, conflict/failure paths,
source independence and host-tool exclusion. Native helper compilation and
cross-architecture layout checks do not prove an actual clean Intel Mac or fresh
macOS user installation. Container lifecycle observations, native authentication,
authenticated inference and clean-Mac evidence must be recorded separately.

## Updating a packaged installation

Use the installed command from an initialized project. Ordinary `update` changes
the shared manager and skills; `--agent` also transitions only that project's
runner. Other projects keep their qualified image and retained storage until
their own explicit transition.

```sh
symphony update                         # latest completed production manager
symphony update --agent codex           # manager and this project's runner
symphony update --release 0.0.4 --agent codex
```

The updater selects one non-draft, non-prerelease GitHub release, verifies its
manifest and archive checksum, checks the bundle inventory, and stages the exact
version without activating it. Downloads stay pinned to that release tag. The
selected image must match the release revision, provider and architecture.
Packaged updates use no host Git, build tools, Python, or local image build.
Source installations retain their existing source updater.

A runner transition uses the existing project ownership, drain/idle checks,
admission checks and recovery journal. The shared manager changes only after
the selected runner passes admission. Workflows, credentials, native login,
dirty tracked files, untracked evidence, workspace/state volumes and repository
ownership remain retained. A failed download or verification leaves the current
manager and listener usable. A pending transition excludes another shared
manager update, including one using alternate configuration paths.

```sh
# Retry an interrupted transition at its already selected release, without
# consulting latest or requiring the original archive again:
symphony update --agent codex
# Restore the retained prior runner and its compatible manager after failure:
symphony update --rollback-runner
# After a completed update, transition back to the previous retained release:
symphony update --rollback-release --agent codex
```

Use the same provider as the retained project when restoring a completed
release. `--rollback-release` without `--agent` restores only the shared manager;
project image pins remain unchanged. A previous release directory is retained
and verified before recovery. An unfinished transition must first be retried or
rolled back before selecting another release. Recovery refuses unknown owners,
altered release bytes, incompatible images and unrelated current-manager links.
Do not manually repoint `current`, remove transition metadata, or manipulate
retained runner containers to bypass an updater refusal.

For isolated candidate qualification, keep the separate namespace/application/
configuration environment above and supply both build artifacts:

```sh
symphony update --candidate-manifest /tmp/candidate/release.json \
  --candidate-bundle /tmp/candidate/symphony-VERSION-macos.tar.gz --agent codex
python3 -m unittest discover -s scripts/release/tests -v
```

The candidate override requires a nonempty development namespace. It does not
publish a release or authorize a stable rollout. Portable updater fixtures prove
selection, verification, exclusion, interrupted activation, retry, and rollback
control flow. Actual installed native/container observations and clean-machine
and authenticated account acceptance remain separate evidence.
