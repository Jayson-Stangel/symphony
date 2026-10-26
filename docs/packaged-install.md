# Packaged installation on macOS and Ubuntu

On Apple Silicon macOS 14 or later, or Ubuntu 26.04 x86-64, install Symphony from a completed stable
[GitHub release](https://github.com/Jayson-Stangel/symphony/releases). The manager
uses prebuilt Linux runner images and a bundled native helper; end users need no
Symphony source checkout, host Git, Python, Node, Codex, Claude, mise, Erlang,
Elixir, Swift or Xcode Command Line Tools.

Linux support is delivered as candidate code/artifacts first. Merging it does not
publish a stable Linux release. Use the isolated candidate process below until a
completed stable release actually contains the Linux archive and checksum. Native
login, authenticated inference and an issue-to-PR demonstration are separate
operator-run acceptance; credential-free CI never claims they were performed.

## Prerequisites

- Install Docker Desktop or OrbStack separately and start its engine in the macOS
  account that will run Symphony. Docker installation and Homebrew are not part
  of the Symphony installer.
- On Ubuntu, install Docker Engine separately and give the ordinary operator
  account permission to use its socket. Verify `docker info`. Symphony does not
  install Docker or change socket ownership. Ordinary runtime utilities are Bash,
  curl with CA certificates, tar/gzip, coreutils, sed, grep, awk, find and diff/cmp;
  Ubuntu's `sha256sum` verifies downloads. Docker handles all runner tooling.
- Before the first Codex start on an AppArmor-enabled Docker daemon, have its
  administrator [prepare the shipped policy on that daemon's host](#codex-on-apparmor-docker-hosts).
  The installer does not load host policy. A remote daemon needs preparation on
  its own Linux host; a no-AppArmor Docker Desktop/macOS daemon does not.
- Prepare an existing target project checkout with an explicit `origin` remote
  that the worker can clone and push to. Preparing that checkout is separate from
  installing Symphony; the packaged manager reads its identity without host Git.
- Have access to a tracker project, a repository-scoped GitHub credential and a
  native Codex or Claude login. Start with a disposable issue.

If Docker Desktop is already in Applications, open it in this macOS account and
complete its first-run prompts. Wait for its engine to report running before
using Symphony. A working Docker CLI alone does not prove the engine is running.
For example, with Docker Desktop:

```sh
/Applications/Docker.app/Contents/Resources/bin/docker info
```

If the client reports that it cannot connect, open Docker Desktop and check its
engine and active context. Do not change socket or Homebrew ownership to fix an
engine-startup error. Other engines may use their own executable/context; set
`SYMPHONY_CONTAINER_RUNTIME` to that executable when needed.

## Download, verify and install

When the selected release actually includes a notarized DMG, download
`symphony-VERSION-macos.dmg` and its `macos-distribution.json`/`SHA256SUMS` from
that same trusted release. Compare the DMG's `shasum -a 256` with its final hash,
open the disk image, then run its existing installer from Terminal:
`/bin/bash "/Volumes/Symphony/Symphony/install.sh"`. Use the actual mounted volume
path if macOS gives it another name. Eject the disk after installation. Only a successful
`notarized-stapled-assessed` receipt for those exact bytes establishes that the
release tooling passed Apple checks; ordinary candidates are explicitly unsigned.
No new stable notarized release is implied by this documentation. The installed
updater continues to use tar.gz archives. See
[signing setup and acceptance](macos-release-signing.md) for remaining owner steps.

From one completed stable release, download `symphony-VERSION-macos.tar.gz` and
its accompanying `release.json`. Replace `VERSION` below with that same release's
version. Verify the archive's printed SHA256 against `bundle.sha256` in the
metadata; stop if they differ. Use a new download directory:

```sh
shasum -a 256 symphony-VERSION-macos.tar.gz
# Compare the result with bundle.sha256 in the same release's release.json.
mkdir symphony-download
tar -xzf symphony-VERSION-macos.tar.gz -C symphony-download
cd symphony-download
./install.sh
export PATH="$HOME/.local/bin:$PATH" # if the installer reports PATH is missing
symphony --version
symphony doctor
```

If the verified macOS helper cannot start, the installer stops before activation
and reports that stage separately from inventory failure. Follow the
[macOS security approval and retry guide](macos-install-security.md); a killed
process alone does not prove Gatekeeper caused it. Keep the archive and inventory
checks intact. Current ad-hoc signing does not establish notarized-download acceptance.

Installation needs no sudo and does not run `symphony setup` or build an image.
New macOS archives carry only an ARM64 Apple Silicon helper and select the Linux
ARM64 runner. Intel macOS host installation is unsupported. Missing artifacts or
failed pulls stop without choosing a different provider or architecture.
`--version` and `doctor` report release identity without a Symphony `.git` directory.

On Ubuntu 26.04 x86-64, choose the Linux archive from the **same** completed
release and compare its SHA-256 to `bundles.linux_x86_64.sha256` in `release.json`:

```sh
sha256sum symphony-VERSION-linux_x86_64.tar.gz
mkdir symphony-download
tar -xzf symphony-VERSION-linux_x86_64.tar.gz -C symphony-download
cd symphony-download
./install.sh
export PATH="$HOME/.local/bin:$PATH"
symphony --version
symphony doctor
```

The static Linux helper needs no Swift, SDK or crypto shared libraries. Ubuntu
x86-64 selects the Linux AMD64 runner. Linux ARM64 host installation is explicitly
unsupported. Linux ARM64 container runners remain required for Apple Silicon;
new standalone engines support only Linux x86-64 and macOS ARM64. A wrong-platform bundle, missing image or unavailable
Docker engine fails clearly without a build fallback. All following project,
credential, native-login, runner, update and recovery commands apply to both hosts.
The installed updater hashes downloads through the bundled helper and requires no
Perl/shasum or other interpreter.

The installer stores immutable releases under
`~/.local/share/symphony/releases/VERSION-REVISION`. Its `current` link selects the
manager and bundled skills. The global command and managed skill links go through
that release. Rerunning the same unmodified version verifies its inventory.
Existing user-owned command paths or skill links are refused before activation;
a source installation is not repointed or migrated automatically.

## Codex on AppArmor Docker hosts

Before starting Codex, follow the [source-free policy preparation and readiness
recipe](codex-docker-sandbox.md#apparmor-hosts) if the **selected Docker daemon**
advertises AppArmor. A working Docker CLI, an installed package, and a listed
profile name do not establish protected-command readiness. The administrator
must review, integrity-check and explicitly load the exact shipped policy on the
daemon host; Symphony does not perform that administrator action.

The verified extracted bundle contains `scripts/codex-apparmor.profile`. After a
default installation it is under
`~/.local/share/symphony/current/scripts/codex-apparmor.profile`, resolving into
`releases/VERSION-STORAGE_REVISION`; use the chosen application directory if you
installed with `--app-dir` or a namespace. Pin that physical release directory
before checking its inventory and policy. Do not edit installed release files.
The detailed recipe covers tools/imports, an existing profile, reboot persistence,
actual protected checks, and troubleshooting without weakening the sandbox.

These public [installation instructions](https://github.com/Jayson-Stangel/symphony/blob/dev/docs/packaged-install.md)
and [sandbox guide](https://github.com/Jayson-Stangel/symphony/blob/dev/docs/codex-docker-sandbox.md)
are the documentation entrypoint for a downloaded release; reading them requires
no source checkout. The archive ships the policy and its checksum inventory, not
these two web guides or the disposable CI preparation helper. Use guidance for
the release you verified; do not treat unmerged development documentation as a
published release or a fresh installation qualification.

## Team-owned environments

Use `symphony environment select --agent codex IMAGE` from the project checkout
to pin a compatible team environment before authentication and startup. See
[team-owned environments](team-environments.md) for requirements, templates,
explicit changes and update/recovery behavior.

## Readiness stages and smoke checks

`symphony doctor` reports **installation** readiness: installed files, command and
skills, and availability of the runtime CLI. Its JSON `scope` is `installation`;
`provider_project_readiness` remains `untested`. This does not prove daemon access,
image availability, the project command, authentication, tracker access or a completed task.

Start the selected engine before native login. Stable releases pull published
immutable digests. For candidates with a loopback reference, normal provider
login [automatically acquires the exact original companion](candidate-runner-provisioning.md)
using the project GitHub credential with repository Actions read access. The
same path recovers a pruned cache. Unavailable or expired originals refuse
onboarding. Missing Docker sockets/contexts stop before image pulls; image
availability and native login failures are separate stages.

From the existing project checkout with its explicit local `origin` remote and
unchanged trusted `WORKFLOW.md`, run:

```sh
symphony runner doctor --agent codex --sandbox-only
```

This checks the engine, selected image and real protected project launch command.
It starts no listener, makes no model request, and mounts no native login or
Symphony tracker credentials. It does execute the trusted project's setup wrapper
in a disposable copy; setup can download dependencies, and project source may
contain sensitive files. Review that source first. A direct Codex command needs
no custom wrapper. Supported wrappers preserve their required setup; see the
[launch contract](codex-docker-sandbox.md#project-launch-wrappers).

After native login, `symphony runner doctor --agent codex` adds native authentication
and isolated local Git preflight. The Claude equivalent requires its native login.
These checks do not establish remote Git authorization, Linear access, dependency
installation for a real task, listener operation or authenticated inference. Those
remain separate operator-run acceptance stages.

## Connect and run a project

Follow [project initialization](getting-started.md#initialize-a-repository) from
the target checkout:

```sh
cd /path/to/existing/project
symphony init --linear-project YOUR-LINEAR-PROJECT-SLUG
```

Edit the exact private credentials-file path printed by `init`, adding
`LINEAR_API_KEY` and a repository-scoped `GH_TOKEN`. The default configuration root
is `~/.config/symphony`; `SYMPHONY_CONFIG_HOME` and a runner namespace can change it.
A file in another configuration root will not be used. Keep credentials outside
Git and chat. Repeated `init` preserves existing workflows, overrides and credentials.

Before starting a listener, review the workflow's tracker project, active states,
hooks and validation commands. Require a label assigned only to the disposable
issue and set `agent.max_concurrent_agents: 1`. Starting a listener can immediately
execute matching issues with authenticated model requests.

For Claude:

```sh
symphony auth claude login
symphony auth claude check
symphony runner doctor --agent claude
symphony run --agent claude --port 4010
```

For the default Codex provider instead:

```sh
symphony auth codex login
symphony auth codex check
symphony runner doctor --agent codex
symphony run --port 4010
```

Complete native sign-in yourself. Login is separate from the tracker/Git credential
file and remains in Docker-managed auth storage. No host agent CLI, SSH keys or
host home copy is needed. Auth check and runner doctor make no model requests;
they do not prove remote repository access or a real issue-to-PR run.

Open the loopback dashboard at `http://127.0.0.1:4010`, then inspect the disposable
issue's tracker updates, validation and PR before widening dispatch. Use a distinct
port per running project. Read [Claude setup](claude.md) for its native profile,
[getting started](getting-started.md) for credential scopes, and
[operations](operations.md) for retained workspaces and controls.

## Stop and restart

```sh
symphony runner drain
symphony runner status
# Wait for zero active workers in the dashboard, then:
symphony runner stop
# Restart the same provider and port; for Codex omit --agent claude.
symphony run --agent claude --port 4010
symphony runner resume
```

Stopping retains workspaces, state/logs and native login. An explicit drain remains
in place until resumed. Use the installed updater for provider/image transitions;
`run` refuses to change an existing runner's provider, image, port or native profile.

## Repository identity

The project must already be a Git checkout with a remotely accessible, explicit
`origin` URL. The packaged manager
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

Different repositories with the same basename have distinct configuration,
containers, workspace and state identities.

## Isolated candidate installation

Use approved candidate artifacts only in separate application/configuration,
command and skill paths with a separate runner namespace. Keep this environment
for every candidate command and choose a distinct dashboard port:

```sh
export SYMPHONY_RUNNER_NAMESPACE=candidate-install
export SYMPHONY_APP_HOME="$HOME/.local/share/symphony-candidate"
export SYMPHONY_CONFIG_HOME="$HOME/.config/symphony-candidate"
export SYMPHONY_BIN_DIR="$HOME/.local/bin/symphony-candidate"
export SYMPHONY_SKILLS_DIR="$HOME/.codex/skills-candidate"
./install.sh
export PATH="$SYMPHONY_BIN_DIR:$PATH"
```

Fill the private project GitHub credential before candidate provider login;
automatic companion acquisition additionally needs repository Actions read
access. Follow the [candidate runner guide](candidate-runner-provisioning.md)
for access, expiry, disk/network retries and offline recovery. `symphony runner
import --agent codex` (or `claude`) displays the exact identity without a download;
`--archive PATH` remains manual/offline recovery. A config-ID-only `docker load`
cannot substitute for the original digest graph.

Candidate validation does not authorize production publication or a stable rollout.
Use the [development guide](development.md) for source development. Automatic
migration of source installations is deferred. Older project identity migration
must be completed through the original source updater before adopting packaged
storage.

## Building and checking a candidate

On an Apple Silicon macOS development/CI host, create an internal release manifest
with the release tooling, then build the ARM64 helper and archive:

```sh
bash scripts/release/package.sh /path/to/internal-release.json /tmp/candidate-output
python3 scripts/package/test-package.py -v
```

The optional third packer argument supplies a prebuilt host helper. Use
`build-host-helper.sh OUTPUT` always builds only the matching retained host.
`--native` selects the native Linux development build; Linux releases use the
pinned static musl build. macOS builds contain only ARM64, with either option.
Python and Swift are build/test dependencies, never installed-user dependencies.
The archive inventory verifies manager, helper, workflow and skill bytes before
staging. `install.sh --stage-only` verifies and stages an immutable release without
activating command or skills; the packaged updater uses that same staging
boundary. An existing project's pinned image remains selected when another
project activates a shared manager release, until the installed updater performs
that project's explicit runner transition.

Tests with a stub container CLI prove command selection, conflict/failure paths,
source independence and host-tool exclusion. Native helper compilation and
layout checks do not prove an actual fresh macOS user installation. Container lifecycle observations, native authentication,
authenticated inference and clean-Mac evidence must be recorded separately.

Historical fresh-user qualification used Apple Silicon, Docker Desktop and native
Claude login for a real issue-to-PR run and packaged update/recovery. Its recorded
Linux container and universal-helper evidence remains historical; it does not
expand the current two-host policy. An Intel walkthrough was operator-deferred.
OrbStack execution was not part of that fresh-user qualification.

## Updating a packaged installation

Use the installed command from an initialized project. Ordinary `update` changes
the shared manager and skills; `--agent` also transitions only that project's
runner. Other projects keep their qualified image and retained storage until
their own explicit transition.

```sh
symphony update                         # latest completed production manager
symphony update --agent codex           # manager and this project's runner
# Select a specific completed release (replace VERSION):
symphony update --release VERSION --agent codex
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

For a Claude project, substitute `--agent claude` for `--agent codex` in the
update/retry/rollback examples. Use the same provider as the retained project when
restoring a completed release. `--rollback-release` without `--agent` restores only the shared manager;
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

## Native candidate evidence

`Package checks` builds the ARM64 macOS and static Linux x86-64 helpers and executes
package, helper and updater regressions on the two retained native hosts.
The `runner-release` manual candidate path accepts `previous_revision`, the full
SHA of a prior real Linux-capable candidate (not a fabricated revision string).
It builds source-bound runner images and native archives for both revisions,
retains both platform checksums, and runs `package-lifecycle.py` on native Linux.
The clean Ubuntu 26.04 client gets archives and isolated data only, with optional
host tools poisoned. The controller uses CI Python/Docker as test utilities.

The lifecycle proof uses real memory-tracker listeners and loopback dashboards,
physical mounts, same-basename projects, stop/restart, manager-only activation,
selected-runner updates, post-create failure/retry and updater-controlled rollback.
It checks dirty tracked files, untracked evidence, state, credentials, workflows,
local overrides and the unchanged second project's selected image. Native login
entrypoints/Codex login status are explicitly stubbed; this is not authenticated
execution. Candidate CI creates no production tag, GHCR publication or GitHub
release. Its evidence is valid only for the recorded source SHA and artifact
checksums; a skipped or unavailable native platform remains an acceptance gap.


## Retained host policy and legacy migration

`scripts/release/host-targets.json` selects exactly Linux x86-64 and macOS ARM64
standalone/helper qualification. Both providers still publish Linux ARM64 and
AMD64 container runners. New manifests declare `supported_hosts` and exact
per-bundle `architectures`; the schema1 global `host_architectures` union remains
`["arm64", "x86_64"]` across those two distinct hosts. An ARM-only macOS bundle
is never advertised as universal. Legacy filename and image/source checks remain.

New installers and launchers refuse Intel macOS and Linux ARM64 before invoking
a native helper or activating a release. Source development operation is separate.
An unchanged legacy Intel macOS updater can still select and stage the generic
macOS archive: its installed validator ignores the additive host policy. The new
launcher then refuses before helper execution or activation, retaining the current
command and project data. This is a known limit of legacy selection, not a claim
that the old updater avoids downloading a wrong-architecture artifact. Apple
Silicon upgrade/rollback acceptance requires execution through the installed old
updater with real source-bound old/new ARM helpers; fixture metadata is not image
lifecycle or authenticated inference evidence.
