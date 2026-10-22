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
supported. Identity must be explicit: repository or external Git configuration
using includes, URL rewrite rules, external `remote.origin` definitions, or
per-worktree configuration is refused before choosing credentials or storage.
Ordinary global user name/email and credential-helper settings remain supported.
The manager inspects `~/.gitconfig`, `$XDG_CONFIG_HOME/git/config` (defaulting to
`~/.config/git/config`) and standard Apple/Homebrew system config locations; it
honors explicit `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` and the boolean
`GIT_CONFIG_NOSYSTEM`. It never opens include targets or prints configuration
contents. Where multiple Git distributions have system configurations, select the
intended one explicitly to avoid ambiguity.

Remove identity indirection and use the repository's explicit origin, or select
non-indirect global/system files for the same environment used with Git. Explicit
`/dev/null` config selection is supported. Unset Git discovery/relocation and
config-injection overrides such as `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`,
`GIT_CEILING_DIRECTORIES`, `GIT_CONFIG_COUNT` and `GIT_CONFIG_PARAMETERS`; the
packaged manager refuses these rather than silently assigning a different
repository identity. Cloning the target project and managing Docker installation
are outside the installer.

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
