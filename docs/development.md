# Dedicated development workspace

Keep the installed `symphony` command and its stable checkout unchanged. Use a
separate clone of `dev`, an explicit development command, separate configuration,
a Docker resource namespace and a separate loopback port. A different checkout
or configuration directory alone does not isolate Docker resources.

Prerequisites: Docker running, Git, Bash, curl and Python 3. Native Claude login
is interactive. The container builds its own pinned runtime; host Symphony setup
and global command/skill installation are unnecessary.

## Create the workspace

Choose a new directory; do not overwrite an existing checkout. Replace the remote
with your intended upstream if necessary:

```sh
workspace="$HOME/Repos/symphony-dev-workspace"
mkdir "$workspace"
git clone --branch dev https://github.com/Jayson-Stangel/symphony.git "$workspace/source"
git -C "$workspace/source" switch -c codex/claude-dev-workspace
mkdir -p "$workspace/bin" "$workspace/config/projects" "$workspace/config/runners"
chmod 700 "$workspace/config" "$workspace/config/projects" "$workspace/config/runners"
cat > "$workspace/bin/symphony-dev" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export SYMPHONY_HOME="$workspace/source"
export SYMPHONY_CONFIG_HOME="$workspace/config"
export SYMPHONY_RUNNER_HOME="$workspace/config/runners"
export SYMPHONY_BIN_DIR="$workspace/bin"
export SYMPHONY_SKILLS_DIR="$workspace/skills"
export SYMPHONY_RUNNER_NAMESPACE=claude-dev
export SYMPHONY_CODEX_AUTH_VOLUME=symphony-claude-dev-codex-auth
unset SYMPHONY_RUNNER_IMAGE
case "${1:-}" in
  update)
    shift
    exec "$SYMPHONY_HOME/scripts/symphony" update --development-runner "$@" ;;
  run)
    shift
    for arg in "$@"; do
      [ "$arg" != --host ] || { echo 'Use the isolated development container' >&2; exit 1; }
    done
    exec "$SYMPHONY_HOME/scripts/symphony" run --port 14030 "$@" ;;
  auth|runner|--version|--help|-h)
    exec "$SYMPHONY_HOME/scripts/symphony" "$@" ;;
  *) echo 'Use: symphony-dev auth|run|runner|update|--version|--help' >&2; exit 1 ;;
esac
SH
chmod 755 "$workspace/bin/symphony-dev"
"$workspace/bin/symphony-dev" --version
```

Use a unique namespace and port for each additional development workspace. Do
not export these development variables globally or run the stable updater from
this wrapper. The ordinary global `symphony` command remains the stable command.

## Prepare a disposable target repository

Clone a private disposable target under `projects/` and provide its test
`WORKFLOW.md`. Restrict its tracker scope and hooks to that repository. Keep
production trackers and production repository credentials out of this workspace.
The tracker operates on the remote service: a second local clone does not
isolate issues, comments or pull requests.

Credentials use `config/projects/<repository-identity>.env`: the basename plus
Symphony's existing hash of the normalized `origin` URL. `init` and a missing-credentials
`run` message report the exact path, including configuration overrides. The development
wrapper deliberately omits `init`; derive the same identity read-only from its source,
from inside the disposable target clone:

```sh
repository_identity=$(bash -c 'source "$1/scripts/symphony" --help >/dev/null; project_id "$(repo_root)"' identity "$workspace/source")
credentials="$workspace/config/projects/$repository_identity.env"
printf '%s\n' "$credentials"
```

Save a fine-grained token restricted to that disposable repository in that file,
with `GITHUB_TOKEN=...`. Grant Contents,
Issues and Pull requests read/write for a GitHub issue pilot. Set file mode 600.
Never paste the token into chat, commit it, or use the broad host GitHub login as
the worker credential.

From inside the disposable target clone:

```sh
dev="$HOME/Repos/symphony-dev-workspace/bin/symphony-dev"
"$dev" auth claude login
# Finish the native login in this terminal.
"$dev" auth claude check
"$dev" runner doctor --agent claude
"$dev" run --agent claude
```

The first login builds the namespaced development image. Login is separate from
host Claude, the qualification lab, and stable; it does not copy credentials.
Check/doctor make zero model requests. `run` polls the configured tracker and can
dispatch open work immediately. Start with one small disposable issue. Visit
<http://127.0.0.1:14030>, inspect provider/readiness, and verify the workpad, tests
and test PR. Confirm included allowance and no paid usage in the account UI.

To finish a test without accepting more work:

```sh
"$dev" runner drain
# Wait for the dashboard to show zero running workers, then:
"$dev" runner stop
```

Stop retains the qualified container, workspaces, state, logs and native login.
Use the same development command for status/logs/recovery. Never stop or remove
containers by a broad name filter.

## Refresh the development build

Preserve local edits. Inspect status and divergence before integrating upstream:

```sh
git -C "$HOME/Repos/symphony-dev-workspace/source" status --short
git -C "$HOME/Repos/symphony-dev-workspace/source" fetch origin dev
git -C "$HOME/Repos/symphony-dev-workspace/source" merge --ff-only origin/dev
# From the disposable target clone, with an existing development runner:
"$dev" update --agent claude
```

The wrapper supplies `--development-runner`. This requires a nonempty namespace
and uses the current development source without fetching `main`, changing global
links or running host setup. It builds a revision-tagged namespaced candidate,
preflights it, drains active work, retains the prior container, and resumes only
after readiness succeeds. On a retry, development mode rebuilds and preflights
from the current checkout. If its image differs from the pending candidate, the
updater verifies ownership, drains/stops that candidate, and replaces it while
retaining the original rollback source and volumes. It commits the new image
identity atomically before starting it, so an interrupted refresh can be retried.
A failed build or preflight leaves the pending candidate unchanged; a failed new
candidate keeps the rollback source available. No temporary workflow/model
changes are needed to escape a stale development candidate. Stable update
recovery continues to use its recorded image. `runner status` identifies
restarting containers explicitly instead of reporting them as running.
A stopped source is supported. A fresh project uses
`run` instead. If the updater refuses, inspect the reported condition; do not
delete metadata or force-replace active work. For a retained failed transition:

```sh
"$dev" update --rollback-runner
```

Stable deployment requires a reviewed `dev` → `main` promotion and the installed
global `symphony update` command. Promotion and stable rollout need separate explicit
operator approval for the complete PR inventory, exact revisions and rollout targets;
development testing or landing a feature in `dev` supplies neither approval. See
[repository approval boundaries](../AGENTS.md#explicit-approval-for-production).

## Opt-in updater lifecycle proof

From a separate development checkout, prepare both production images explicitly:

```sh
docker build --file runner/Dockerfile --target codex -t updater-proof:codex .
docker build --file runner/Dockerfile --target claude -t updater-proof:claude .
python3 runner/updater-smoke.py updater-proof:codex updater-proof:claude
```

This optional script uses the actual application, entrypoints, Docker mounts,
HTTP drain/resume and update/rollback metadata in an empty Memory project. It
checks interrupted creation, interrupted rollback, cleanup recovery and retained
committed, edited, untracked and state files. It records physical mounts and UID.
Only Codex login-status and selection of prepared image variants are test peers;
Claude's fresh-volume missing-auth refusal uses its actual native check. The
prepared images remain unchanged; temporary containers, volumes and image tags
use a unique development namespace and are removed. Local diagnostic files are
retained in the printed temporary directory. No stable command, host login mount,
native positive authentication or model request is used. Successful mixed-provider
UID migration and authenticated inference remain separate qualification.

### Native standalone engine qualification

The `burrito-release` workflow can be manually dispatched on a feature branch.
A branch dispatch builds candidate artifacts and runs all four existing native
smoke targets (Linux x86-64/ARM64, macOS Intel/Apple Silicon), without publishing
a GitHub release or GHCR images. Production publication still requires its tag,
repository variable and protected environment gates. Dispatch the exact reviewed
head and record the workflow/job URLs; any subsequent commit requires another
candidate run. A skipped or unavailable native runner is an acceptance gap.
For a focused controller regression diagnosis, dispatch the same workflow with
`controller_only=true`: this runs only the macOS Intel fixture tests and skips
artifact building, native engine qualification and publication. Its success is
controller evidence only, never a substitute for the full native candidate run.

Each build retains the executable, SHA-256 sidecar and `.source.json` identity
(source revision, target, artifact name and checksum). On a matching native host:

```sh
python3 scripts/release/engine-smoke.py /path/to/symphony-REV-linux_x86_64 \
  --source-revision FULL_SOURCE_SHA --target linux_x86_64 \
  --evidence-dir /tmp/engine-evidence
python3 -m unittest discover -s scripts/release/tests -p test_engine_smoke.py -v
```

Python 3, `ps` and `lsof` are test controller dependencies, separate from the engine's
bundled runtime. The controller checks native OS/CPU and artifact identity, copies
the executable outside the checkout, clears ambient credentials and runtime paths,
and uses temporary homes, extraction/cache, logs, workspaces and an empty memory
tracker. It observes actual dashboard content and typed empty JSON state for a
bounded period, attributes the loopback listener to the new process session, then
requires a zero exit after SIGTERM and disappearance of that session, listener and
tracked descendant identities. A private inherited ownership descriptor lets the
controller find detached and double-forked children after reparenting; an engine
that discards this descriptor fails qualification. Ancestry snapshots supplement
that ownership marker. Inspection is limited to the test-owned private file and
process identity metadata, without reading ambient process environments.
Forced cleanup always fails qualification. Evidence artifacts retain identity,
state, dashboard, startup/application logs and shutdown/cleanup results.

The fixture selects Claude with no native helper installed and requires truthful
`runtime_unavailable` readiness. No issue is dispatched and no authenticated model
request is made. Engine buildability, native server liveness, native-agent login,
and installed-manager lifecycle are separate claims: this check proves the first
two, and deliberately does not claim authenticated inference or issue-to-PR proof.
Regression subprocess fixtures demonstrate rejection of early/banner exits, bad
HTTP/state, false readiness, startup/shutdown timeouts and surviving, detached or double-forked children;
those fixtures are controller tests, not substitutes for native artifact execution.
The fixture HTTP server binds numeric loopback directly without reverse DNS;
startup failure logs include fixture stacks and the last connection error. This
avoids a proven hosted Intel macOS `getfqdn` stall without altering the engine.

## Candidate preflight and CI ownership

Ordinary pull requests retain the named required quality, package and metadata
checks. Manual full candidates add standalone engines and both Linux image
architectures for both providers. Tags retain the protected production environment
and publication conditions; a candidate run never grants publication approval.

Portable release metadata and package-input/shell checks run once before native
qualification. Native process and shell-updater fixtures run once per OS/CPU,
separately from provider image qualification. New unclassified release suites
conservatively enter the native group. Missing native prerequisites or skipped
fixtures fail qualification. Package/helper checks produce an exact source receipt;
image fan-out starts only after these succeed. Engine builds and runner builds
then run independently. Final manifests join verified runner records; standalone
smokes consume actual built engines. No placeholder records replace artifacts.

The Burrito caller owns package qualification and passes its successful exact-head
output to the runner workflow, avoiding a second qualification. A direct runner
candidate performs its own qualification. The runner workflow rejects absent,
failed, cancelled, skipped or wrong-head qualification, except its explicitly
caller-owned path; its final verdict also requires all images and metadata.
The shell/controller tests support early feedback, not native image acceptance.

PR and branch groups cancel obsolete work for the same workflow/event/ref. Tag
operations do not cancel each other. Explicit `retain_diagnostic` runner runs and
standalone controller-only diagnostics use run-specific groups so deliberately
retained evidence survives new commits. Other branches and workflow scopes have
separate groups. Existing owned-resource cleanup remains part of each test.

Run `python3 scripts/ci/release_tests.py portable` and
`python3 scripts/ci/release_tests.py native` to exercise the same suite selection.
The native group requires `lsof`; these commands report actual suite names,
pass/fail/skip counts and elapsed execution time. They are not image-cache or
native engine artifact evidence. Full candidate/job timelines remain necessary
for queue/build/export/transfer performance measurements.
