# Operating Symphony

This guide covers packaged and source-backed installations. Run project commands
with `symphony --project PROJECT-ID` and the same configuration and namespace
used for registration. For installation, see the [macOS/Ubuntu bundle guide](packaged-install.md)
or [getting started](getting-started.md). Use `symphony --version` to identify the
installed release and `symphony --help` for the command summary.

## Inspect and control a runner

| Command | Purpose |
| --- | --- |
| `symphony doctor` | Check the installation, command/skill links, and container runtime; packaged diagnostics report release identity without host Git. |
| `symphony project doctor` | Inspect the registered workflow source and credential configuration. |
| `symphony runner doctor --agent codex` | Check the selected image's native login and disposable local Git operations. Use `claude` for Claude. |
| `symphony runner status` | Show the project's container, dashboard address, task limit, and retained volumes. |
| `symphony runner logs` | Follow container logs. |
| `symphony runner drain` | Persistently prevent new dispatch while current workers finish. |
| `symphony runner resume` | Clear an explicit drain, subject to native readiness and retained pause checks. |
| `symphony runner recheck` | Explicitly recheck repaired native authentication or policy with zero active workers. |
| `symphony runner stop` | Stop the container with a 30-second graceful shutdown window and retain its data. |

Both installation and project doctors support `--json`. The runner doctor makes no
model requests and starts no listener; it does not prove remote clone/push permission.

For a planned stop, drain first, wait for zero active workers in the dashboard, then
stop. Restart the retained Claude runner with `symphony run --agent claude --port PORT`;
for Codex use `symphony run --port PORT`. Keep the original provider and port.
A drain survives restart until explicitly resumed. `run` refuses a different
provider, image, port, or native profile for an existing runner; use the updater for
supported transitions.

The dashboard is published on loopback, normally at
[http://127.0.0.1:4010](http://127.0.0.1:4010). Keep the control API on loopback.
See the [dashboard reference](../elixir/README.md#web-dashboard) and
[logging guide](../elixir/docs/logging.md) for diagnostics.

## Persistence and recovery

Each project has named volumes for workspaces and state/logs. Stopping or replacing
a runner retains them. A listener returns after a container-engine restart unless
explicitly stopped. Do not run multiple listeners against the same workspace volume.

On startup, Symphony reconciles retained workspaces with current tracker state.
Eligible issues can resume in their existing checkouts, including edits and
untracked evidence, through fresh agent sessions. Review and blocked issues retain
their workspaces without dispatch. Terminal issues are cleaned at startup; later
polls reconcile tracked issues without scanning all terminal history. Tracker read
failures preserve workspaces until reconciliation succeeds.

Use `tracker.blocked_states` for durable tracker blockers and move resolved issues
back to an active state to resume work. Runtime-only input stops do not survive an
orchestrator restart. See [workspace and blocker behavior](../elixir/README.md#how-it-works).

Recoverable agent failures use scheduler retries with backoff. Agents receive tracker
tool errors and reconcile uncertain writes before retrying. For Claude native login,
quota, profile changes, and retained pause records, follow
[pauses and recovery](claude.md#pauses-and-recovery). A readiness check does not make
a model request or prove that quota is available.

## Update the stable installation

For this repository, `dev` is integration and `main` is production. Before promotion
or stable rollout, obtain explicit operator approval for the complete included PR
set and exact revisions. Rollout approval must also name runtime/project targets;
review or testing in `dev` does not authorize either action. Follow the
[production approval rules](../AGENTS.md#explicit-approval-for-production).

After approval, use the installed global command. The installation type selects
the updater; existing source installations remain source-backed.

### Idle update procedure

Before invoking the updater, pause admission on every runner in the affected
installation, let active work finish, and normally stop all those runners with
`symphony runner stop` from their respective project contexts. Keep new work on
hold until the update and readiness checks are complete. This is an operator
procedure; the updater does not enforce an installation-wide stopped check.

Use the installed updater with the same project, provider, configuration and
selected environment. `update --agent` performs candidate preflight, including
temporary containers and native authentication-status checks. It then replaces
the stopped target, starts it, checks readiness and resumes admission before
returning. Do not separately restart that target. A retained quota cooldown is
reported as intentionally paused. Other projects stay stopped until their normal
restart and readiness checks under the intake hold; preserve their provider and
port, and explicitly resume any retained drain only when ready.

A manager-only update leaves runners stopped and does not adopt a new workflow
mount. Packaged same-provider image pins and explicit environment selections
remain in effect; a new manager default does not silently replace them. Busy updates and
live-session continuity are deferred to
[JAY-817](https://linear.app/jayson-stangel/issue/JAY-817) and
[JAY-818](https://linear.app/jayson-stangel/issue/JAY-818), respectively.

### Packaged releases

A packaged update selects a completed stable GitHub release and verifies its
manifest, archive checksum and file inventory before activation. It uses prebuilt
images without host Git or a local build. From the target project:

```sh
symphony update                           # shared manager and skills only
symphony update --agent claude            # manager and this project's Claude runner
# For Codex instead: symphony update --agent codex
# Choose a specific completed release: symphony update --release VERSION --agent claude
```

Keep the project's existing provider when updating. Other projects retain their
pinned images and volumes until their own explicit transition. A failed download
or verification leaves the current manager and listener usable.

```sh
symphony update --agent claude            # retry a pending selected transition
symphony update --rollback-runner         # recover an unfinished transition
symphony update --rollback-release --agent claude  # restore a completed prior release
```

A pending transition must be completed or rolled back before selecting another
release. Retry its `--agent` update without new `--release`, `--candidate-manifest`
or `--candidate-bundle` flags; the journal retains the exact selected release.
`--rollback-release` without `--agent` restores only the shared manager;
project image pins stay unchanged. See [packaged update and recovery](packaged-install.md#updating-a-packaged-installation)
for selection, ownership and retained-state rules. Do not repoint `current`,
remove journals, or replace retained containers manually.

### Source installations

```sh
symphony update
```

A source update fast-forwards a clean `main` checkout that tracks a configured
remote's `refs/heads/main`, then repeats installation and pinned runtime setup.
It preserves project credentials and `WORKFLOW.md`. A branch tracking `dev`, local
commits outside remote `main`, or a dirty stable checkout is refused. The tracked
remote may be named `origin` or another name; it must be the intended fork.

A source-only update leaves existing runner containers and their bundled tools
and skills in place. For an approved runner rollout, run from each target repository:

```bash
symphony update --agent codex
# For a Claude runner instead:
# symphony update --agent claude
```

Follow the [idle update procedure](#idle-update-procedure) before invoking this
command. The updater preflights the candidate, retains the stopped previous
container for rollback, verifies volume ownership, and starts the replacement
through native readiness and admission checks before completing. Source and
runner changes use the exact fetched revision through the installed launcher's
update handoff.

If a transition fails, retain its evidence and use the installed updater to retry
or roll back:

```bash
symphony update --rollback-runner
```

Pending transition journals preserve the selected image and rollback source. Retry
can recover metadata from the verified container and start a retained stopped
candidate, subject to current readiness. Rollback retains workspace and state data.
For provider transitions and older listener formats, use the
[transition guide](claude.md#provider-and-image-transitions).

Source updates and runner mutations use ownership locks; competing operations
refuse immediately. Status and logs remain available. If the updater refuses,
resolve the reported precondition or report the limitation. Do not repair the
stable checkout, delete ownership evidence, force-replace a container, or restart
stable processes manually to bypass it. Stable changes go through `symphony update`.

These update commands preserve project workflows, credentials and native login;
they do not turn a source installation into a packaged installation. Use separate
paths and a namespace for [isolated candidate testing](packaged-install.md#isolated-candidate-installation).

### Managed runner task limit

Managed Codex and Claude listeners inherit the container engine's process and
thread limit by default. To set a project-specific cap, add
`SYMPHONY_RUNNER_PIDS_LIMIT=2048` to the private project `.env` printed by
`symphony init`. Use a positive decimal integer without leading zeros. Remove
the line or leave its value empty to inherit the engine default again.

Follow the [idle update procedure](#idle-update-procedure) and run
`symphony update --agent claude` or `symphony update --agent codex` for the
selected project. The packaged updater can apply this change with the same
selected image. When the project has no override, upgrading an existing Claude
runner drops Symphony's former 512-task cap and the replacement inherits Docker's
default. The checkout migration applies the same rule. Before draining a healthy
listener, the updater checks that the selected image can boot with an explicit
cap.
`symphony runner status` shows the desired setting, the launch setting, and
the running container's observed `pids.max` when the engine can read it.

An interrupted update retries with its original cap. Use
`symphony update --rollback-runner` to restore the retained container and its
previous limit. Review the desired value in the project `.env` before the next
update.

## Workflow reload identity

The successful `/api/v1/state` response includes a `workflow` object with
`sha256`, `status`, `accepted_at`, and `checked_at`. The SHA-256 identifies the exact
complete bytes read and successfully parsed and validated by the workflow store,
including whitespace and prompt-only edits. It is not a hash of normalized
settings or a later disk read. `accepted_at` records acceptance of changed bytes;
`checked_at` records the latest reload check, both as UTC ISO 8601 timestamps.
Unchanged valid bytes preserve `accepted_at`. Status is `loaded`, `rejected`, or
`unavailable` when the workflow store is absent (with null hash and timestamps).

A rejected parse, configuration validation, or read keeps the previous accepted
workflow and hash. Recovery to valid bytes clears rejection. This object exposes
no prompt, configuration, source path, or rejection details. A newly started
session reads the currently accepted prompt. Live-session continuity across an
update is outside the current release acceptance, as described in the
[idle update procedure](#idle-update-procedure).

For a registered project, state additionally reports the watched branch,
accepted commit, last successful source check, and source error. Compare the
committed root workflow at that revision with the reported accepted SHA-256.
See the [packaged workflow source guide](packaged-install.md#register-a-new-project-from-a-remote-workflow)
for stale sources, rejection and admission behavior.

## Credentials and project identity

Use the exact credential path printed by `init` or a missing-credentials `run`
message. Project resource names combine the repository basename and a hash of its
normalized `origin` URL. SSH and HTTPS forms of the same remote share an identity;
changing `origin` changes identity. Repositories with the same basename under
different owners have separate credentials, metadata, containers, and storage.

Codex login is shared within the installation's runner namespace in the
`symphony-codex-auth` volume by default. Claude login is project-scoped. Both are
separate from host agent homes and retained across runner replacements. Workers can
access their native login and selected Git/tool credentials; use trusted code and
appropriately scoped accounts. Remove unused logins with `symphony auth codex remove`
or `symphony auth claude remove`, subject to active-container checks.

The container does not mount the developer checkout, home directory, SSH keys, or
host agent login. Tracker credentials remain with the listener; agents use
Symphony's tracker tools. For explicitly forwarded project-tool credentials, see
[the Claude tool environment](claude.md#project-tool-credentials).

### Migrate an older project

Follow the [checkout migration procedure](packaged-install.md#migrate-a-checkout-based-project)
to register the existing repository ID and preserve its resources. A pending
migration can restore the exact stopped checkout listener with
`symphony --project PROJECT-ID update --rollback-runner`.
Projects with an older basename-only identity must first use their prior
installed manager's repository identity migration.

## Shared skills and local overrides

The stable installation supplies `symphony-commit`, `symphony-push`, `symphony-pull`,
`symphony-linear`, and `symphony-land`. Shared skills define reusable procedures;
the target repository's workflow defines policy, validation, and PR conventions.
Existing listeners retain bundled procedures until their runner image is updated.

Most projects can use the shared skills directly. For an intentional customization:

```bash
symphony skill override land
```

This creates `.codex/skills/symphony-land/` and records it in
`.codex/symphony-skill-overrides.yml`. It shadows the global version and stops
receiving its updates. Keep the difference small and document why it is needed.

To return to the shared version:

```bash
symphony skill use-global land
```

The local copy moves to `.codex/symphony-skill-backups/` rather than being deleted.
Use `symphony skill migrate-local land` to preserve intentional changes from a
legacy generic skill as a namespaced override. Migration retains the original;
review cross-skill references that may still use generic names.

## Runner tools and installation paths

Both images bundle Git, GitHub CLI, and a version-matched Playwright/Chromium
installation. Add project dependencies through `hooks.after_create`, or use a
compatible custom image selected with `SYMPHONY_RUNNER_IMAGE` in a source
installation. Packaged installations require the release manifest's immutable
image and reject that override.

For browser automation inside a workspace, use the bundled CLI or Node package:

```bash
playwright --version
playwright screenshot --browser chromium http://127.0.0.1:3000 screenshot.png
node -e "const { chromium } = require('/opt/symphony/browser/node_modules/@playwright/test'); console.log(chromium.name())"
```

Browser binaries use `PLAYWRIGHT_BROWSERS_PATH`. A project using a different
Playwright version may need its own writable browser directory and matching install.
Publish review artifacts that remain accessible after workspace cleanup and do not
expose secrets. The [workflow template](../elixir/WORKFLOW.md) describes acceptance
evidence; temporary logs do not all require upload.

| Variable | Purpose |
| --- | --- |
| `SYMPHONY_BIN_DIR` | Global command directory; default `~/.local/bin`. |
| `SYMPHONY_CONFIG_HOME` | Configuration root; default `$XDG_CONFIG_HOME/symphony` or `~/.config/symphony`. |
| `SYMPHONY_SKILLS_DIR` | Shared skill directory; default `$CODEX_HOME/skills` or `~/.codex/skills`. |
| `SYMPHONY_RUNNER_HOME` | Runner metadata and journals; default under the configuration root. |
| `SYMPHONY_CODEX_AUTH_VOLUME` | Override the namespace's Codex login volume. |
| `SYMPHONY_HOME` | Source checkout when invoking a launcher copied outside its checkout. |
| `SYMPHONY_APP_HOME` | Packaged application storage; default `~/.local/share/symphony`. |
| `SYMPHONY_RUNNER_NAMESPACE` | Namespace for isolated configuration, containers and storage; preserve it for every command. |

`install`, `doctor`, and `update` also accept `--bin-dir`, `--config-dir`, and
`--skills-dir`. An explicit `SYMPHONY_RUNNER_HOME` overrides derived runner storage.
For development, isolate the configuration, Docker namespace, auth volumes, and
port together using the [development guide](development.md).

Source-only `run --host` is for native Codex debugging. Packaged installations
require container execution. For source debugging, use `doctor --host` for the
pinned host runtime and authenticate the commands selected by your workflow.
Install host GitHub CLI when its procedures require it. Claude requires its container.
