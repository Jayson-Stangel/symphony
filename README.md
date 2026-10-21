# Symphony

Symphony turns project work into isolated, autonomous implementation runs, allowing teams to manage
work instead of supervising coding agents.

[![Symphony demo video preview](.github/media/symphony-demo-poster.jpg)](https://player.vimeo.com/video/1186371009?h=5626e4b899)

_In this [demo video](https://player.vimeo.com/video/1186371009?h=5626e4b899), Symphony monitors a Linear board for work and spawns agents to handle the tasks. The agents complete the tasks and provide proof of work: CI status, PR review feedback, complexity analysis, and walkthrough videos. When accepted, the agents land the PR safely. Engineers do not need to supervise Codex; they can manage the work at a higher level._

> [!WARNING]
> Symphony is a low-key engineering preview for testing in trusted environments.

The supplied workflow uses `Blocked` for work requiring external intervention and
`Human Review` for validated work awaiting human assessment. Workers record available
feedback and pending external reviews, then end their turn rather than poll while
waiting. Required repository checks, reviews and human approval still govern merge.
New projects inherit this policy; existing workflows are preserved by `symphony init`
and `symphony update`. Adopt the review handoff from
[`elixir/WORKFLOW.md`](elixir/WORKFLOW.md) deliberately in existing project contracts.
Configure `tracker.blocked_states` to keep
blocked tracker issues visible in the web dashboard across service restarts; move them back to an
active state after resolving the blocker. Existing workflows must add this configuration and their
tracker must provide the corresponding status.

## Running Symphony

### Requirements

Symphony works best in codebases that have adopted
[harness engineering](https://openai.com/index/harness-engineering/). Symphony is the next step --
moving from managing coding agents to managing work that needs to get done.

### Run this fork as a centralized local service

This fork includes `symphony`, a small local manager for one stable Symphony runtime serving many
repositories. It deliberately separates that runtime from the checkout where developers change
Symphony itself. Target repositories can live anywhere and do not need to use Elixir or mise.

#### Install a stable runtime

Clone the fork you intend to operate into a stable location (the path is a convention, not a
requirement), then link the command:

```bash
git clone git@github.com:Jayson-Stangel/symphony.git ~/src/symphony
~/src/symphony/scripts/symphony install
```

`install` is safe to rerun: it creates `~/.config/symphony/` with protected permissions, links
`~/.local/bin/symphony` to the stable checkout, and links the Symphony skills into
`~/.codex/skills/`. It never replaces an existing file or symlink. Ensure `~/.local/bin` is on
your `PATH`.

Set up or repair dependencies explicitly:

```bash
symphony setup --install-prerequisites
symphony doctor
```

`setup` installs the Erlang/Elixir runtime pinned in `elixir/mise.toml`. On macOS,
`--install-prerequisites` can install missing [mise](https://mise.jdx.dev/), GitHub CLI (`gh`),
and Codex; Git comes from the Xcode Command Line Tools and Homebrew is required for automatic
installation. Sign in to Codex and confirm `codex app-server` works before starting a listener.
`symphony doctor --json` is available for scripts and CI.

Keep the stable checkout in place. The global command points to it, while developer clones and
worktrees may be anywhere, dirty, or on feature branches without changing the runtime used by
managed projects.

#### Update the stable runtime

```bash
symphony update
```

The updater only fast-forwards a clean `main` checkout tracking `refs/heads/main` on a configured remote.
A local branch named `main` tracking `dev` is refused before fetching or installing.
Local commits outside fetched remote `main` are retained and refused before installation.
Its fetch prunes stale tracking branches only for that upstream remote, resolving branch-name
collisions while preserving local branches, tags, and other remotes' tracking refs. Custom fetch
mappings cannot expand that pruning scope. A typical installation of this fork tracks
`jayson/main`, not OpenAI's `origin`. Each successful update
repeats idempotent installation and pinned runtime setup, including retries after interrupted setup;
it also refreshes the global command and skill links
without changing project credentials or workflows. You do not need to run `symphony setup` after
a successful update. A combined source/runner update hands completion to the newly installed
launcher at the exact fetched revision, without a second fetch. When updating from a launcher
that predates this handoff, first run a source-only `symphony update`, then invoke the updated
command for a runner transition. Updated installed source updaters share checkout ownership
from before fetching through installation, setup and the selected runner transition; a competing
update refuses immediately. Finish already-running older launchers before adopting this behavior.
Manual Git changes and older launchers do not participate in this ownership; revision checks
continue to reject source changes they can observe.
`--config-dir` selects configuration and its derived runner storage; an explicit
`SYMPHONY_RUNNER_HOME` still takes precedence. Official runner start, stop, drain, resume,
recheck and updater actions share one process-held advisory lock per runner, including
commands using different configuration paths. A competing mutation refuses immediately;
status, logs and unrelated projects remain available. The lock lives in a private per-user
directory under `/tmp`; the original configuration-local lock also excludes older updaters. Rerun the
updated command after a mixed-version refusal. A crashed updater releases the lock after any
in-flight command exits. Unknown legacy ownerless
locks remain refused because their ownership cannot be established safely.

#### Run unattended work in an isolated container

An unattended listener must be able to write Git metadata to create branches, commits, and pushes.
Codex intentionally protects `.git` in its normal workspace-write sandbox, so use Symphony's
isolated runner rather than granting broad access to a developer's host account. The runner uses a
Docker-compatible OCI runtime: Docker Desktop or OrbStack on macOS; Docker Engine or rootless
Podman on Unix.

From an initialized repository whose external Symphony environment contains a fine-grained
`GITHUB_TOKEN` (or `GH_TOKEN`), authenticate the bundled Codex CLI. This opens a device-login
flow and saves its session in a Docker-managed volume, not the developer's `~/.codex` directory:

```bash
symphony auth codex login
symphony auth codex check
```

The session is stored in the `symphony-codex-auth` Docker volume (override with
`SYMPHONY_CODEX_AUTH_VOLUME`) and is mounted into isolated workers. It is outside repositories,
the container image, and the host Codex configuration. It remains a credential available to code
inside a worker, so use a dedicated account when one is available and remove the session with
`symphony auth codex remove` when it is no longer needed.

Then start the isolated Symphony container:

```bash
symphony runner doctor
symphony run --port 4010
```

The first command builds and starts the project-specific listener container, verifies its Codex
session, then proves that Git can create a branch and commit inside it. Use `symphony runner status`
at any time to show the container name, loopback dashboard, persistent workspace and log volumes, and
Codex session volume. `run` reuses that listener; the host CLI only launches and observes it. The
container receives a
repository-scoped GitHub token and a read-only dedicated Codex token, but it does not mount the
developer checkout, home directory, SSH keys, or local Codex login. Stop it when it is no longer
needed:

```bash
symphony runner stop
```

Workspaces and runtime logs live in repository-specific named volumes by default.
Stopping or replacing the container retains them. The listener automatically
returns after a container-engine restart unless explicitly stopped. On startup,
Symphony reads current tracker state and resumes eligible tickets from their
existing checkouts, including uncommitted changes and untracked evidence, using
fresh Codex sessions. Tickets awaiting review or blocked in the tracker retain
their workspaces without dispatch. Terminal tickets are cleaned at startup and
on later successful polls, including tickets completed while Symphony was offline.
Tracker read failures leave retained workspaces intact until reconciliation succeeds.

`runner stop` allows 30 seconds for graceful shutdown before container removal;
it retains both named volumes. Empty volumes and historical logs remain available
after per-ticket checkout cleanup. Volume names include the repository remote's
identity so repositories with the same name under different owners do not share
checkouts. Do not run multiple listeners against the same workspace volume.

Existing containers without these mounts remain ephemeral until replaced. Before
replacing one, finish its active work or back up `/workspaces` and
`/var/lib/symphony`; recreating it cannot recover files from an already removed
ephemeral container. New mounts do not migrate existing container files automatically.

Set `SYMPHONY_CONTAINER_RUNTIME=docker` or `podman` if both are installed. The runner image is a
portable Node/Git/agent base with Playwright and headless Chromium; add project-specific
dependencies in `hooks.after_create` or publish your own compatible image and set
`SYMPHONY_RUNNER_IMAGE`.

Both agent images include a version-matched Playwright/Chromium installation and its
Linux dependencies. Use `playwright --version` or `playwright screenshot --browser chromium
http://127.0.0.1:3000 screenshot.png` from a workspace. For scripted interactions, load
`require('/opt/symphony/browser/node_modules/@playwright/test')` in Node.js. Browser binaries
are shared through `PLAYWRIGHT_BROWSERS_PATH`; projects using a different Playwright version
may need to set that variable to a writable workspace directory and install their own
matching browser before running tests. The workflow template includes web app
validation and acceptance-evidence guidance. Record results and durable proof links;
publish artifacts needed to assess the change or explicitly required by the target
repository or ticket. Do not require uploading every temporary log. Review artifacts
must remain accessible after workspace cleanup and must not expose secrets. Existing
workflows are preserved; adopt these sections deliberately when changing project policy.

To verify just the browser layer without credentials or a running listener:

```bash
docker build --target browser-tools -f runner/Dockerfile -t symphony-browser-test .
docker run --rm --entrypoint node symphony-browser-test /opt/symphony/browser/smoke.cjs
docker run --rm --user 11001:11001 --entrypoint node symphony-browser-test /opt/symphony/browser/smoke.cjs
```

Use `SYMPHONY_BIN_DIR`, `SYMPHONY_CONFIG_HOME`, and `SYMPHONY_SKILLS_DIR` to relocate the command,
configuration, and skills (or pass the equivalent flags to `install`, `doctor`, or `update`). Use
`SYMPHONY_HOME` only when invoking a copy of the script outside its source checkout.

#### Initialize a repository

From a new or existing Git repository with an `origin` remote, wherever it lives:

```bash
cd /path/to/my-service
symphony init --linear-project my-service-linear-project-slug
```

On first use this creates a repository-specific `WORKFLOW.md` from Symphony's full operating
contract. Existing workflow and credentials are preserved. Review the generated workflow before
starting the listener.

Project resources use the repository basename plus the existing 12-character hash of its
normalized `origin` URL. Credentials (`projects/<repository-identity>.env`), runner metadata and update
journals, container names, workspace/state volumes and Claude login all share this identity.
Unrelated repositories named `service` therefore remain independent. SSH and HTTPS spellings of
the same remote normalize to the same identity; changing the repository origin changes identity.
Codex login remains deliberately shared within the installation's runner namespace.

For an installation that still uses basename-only credentials or container names, run the
installed updater from that repository:

```bash
symphony update --migrate-project-identity
```

This explicit migration verifies the retained runner's repository storage and workflow mount,
then renames credentials, cache, pending update/rollback journals, and retained containers.
It preserves file contents, running/stopped state, workspace/state volumes, native logins and
`WORKFLOW.md`; no agent is restarted. Retry the same command after interruption, then continue
any pending `update --agent` or `update --rollback-runner`. Conflicting old/new resources, foreign
storage and unknown metadata are refused before any move. Isolated development installations
use `update --development-runner --migrate-project-identity` with their existing namespace.

A basename-only credential file without an owned retained runner cannot prove its repository.
Review it explicitly and copy the intended values to the new `projects/<repository-identity>.env` path
reported by `init`/`run`; keep the old file until its owner is known. Existing workflows and
unproven credentials are preserved. A repository gets a new independent credential template even
if another same-basename repository has old resources or a workflow already exists. Only a retained runner with matching
repository storage requires migration before `init`; ambiguous credentials are never reused.

Symphony’s shared skills are global and namespaced—`symphony-commit`, `symphony-push`,
`symphony-pull`, `symphony-linear`, and `symphony-land`. They carry reusable operational
procedures, not repository policy, and update with the stable runtime. The workflow defines each
repository’s behavior, credentials, tracker states, workspace, hooks, and guardrails.
Workers use the target repository's validation commands and PR conventions;
Symphony's Elixir tooling is not a requirement for other projects. The runner
installs GitHub CLI from its official signed package repository to avoid obsolete
Projects (classic) API queries in older distribution packages. Shared procedures
also use REST for PR metadata updates and comment reads.
Running listeners retain their bundled tools and skills until their containers
are recreated; roll out updates between work items. Existing `WORKFLOW.md` files
are preserved by `init`, so apply workflow guidance changes explicitly there.

#### Local skill overrides

Most repositories should not have local skills. When a repository truly needs to customize a
shared skill, create an explicit copy that shadows the global skill:

```bash
symphony skill override land
# Or during initialization:
symphony init --linear-project my-service-linear-project-slug --local-skill land
```

The override is written as `.codex/skills/symphony-land/` and recorded in
`.codex/symphony-skill-overrides.yml`. It stops receiving global updates until it is removed, so
keep it small and document why it differs. This namespacing keeps Symphony-specific procedures
from changing how Codex behaves in unrelated repositories.

To return one overridden skill to the global version without discarding team changes:

```bash
symphony skill use-global land
```

The local override is moved to `.codex/symphony-skill-backups/` rather than deleted. If a legacy
project has an older copied skill that contains intentional changes, preserve it as a namespaced
override first:

```bash
symphony skill migrate-local land
```

Migration preserves the original legacy skill and creates a namespaced local copy. Review its
cross-skill references after migration, since a customized older skill may still mention generic
skill names.

#### Review an existing repository

Use the read-only project doctor before updating a repository bootstrapped by an older Symphony
version:

```bash
symphony project doctor
```

It reports legacy copied skills, the old skill manifest and `script/symphony` wrapper, workflow
references to legacy skill paths, external credential availability, and global-skill readiness. It
does not alter `WORKFLOW.md`, credentials, local skills, or repository files. Use `--json` for
automation. Review any workflow migration as an ordinary repository change: its policy may have
been customized by the team.

For a brand-new repository, make its first commit and push it before initializing so the `origin`
remote can be cloned into a Symphony workspace.

#### Configure credentials

New clone-only workflows use `workspace.clone_url` so an interrupted unpublished
clone can retry safely. Existing edited workspaces are preserved. Existing custom
`after_create` workflows keep their manual interruption recovery; see the
[workspace recovery details](elixir/README.md).

Initialization stores a private, owner-only credential template outside the repository at
`~/.config/symphony/projects/<repository-identity>.env`. The identity is the basename plus the
existing normalized `origin` hash. Use the exact credentials path printed by `init` (or by
`run` when credentials are missing), including any configuration-directory override. Add:

```dotenv
LINEAR_API_KEY=lin_api_...
GH_TOKEN=github_pat_...
```

Create the Linear key in **Settings → Security & access → Personal API keys**. Create a
[fine-grained GitHub token](https://github.com/settings/personal-access-tokens/new), restrict it
to the managed repository, and grant `Contents: Read and write` and `Pull requests: Read and
write`. Add `Issues` only if it comments or labels issues, and `Workflows` only when it changes
`.github/workflows/`. Do not grant Administration, Secrets, organization-wide access, or access to
unrelated repositories.

#### Verify and start safely

Verify repository access before starting a listener:

```bash
set -a
. ~/.config/symphony/projects/<repository-identity>.env
set +a
gh repo view <owner>/<repository>
```

Then start Symphony and its optional local dashboard:

```bash
symphony run --port 4010
```

Review polling and progress updates stay within the current Codex turn; only completed turns
count toward the worker continuation limit.

Visit `http://localhost:4010` to observe the listener. For a first run, temporarily set
`required_labels: ["symphony-smoke-test"]` in `WORKFLOW.md` and create one harmless matching
Linear issue. Inspect the workspace, branch, Linear updates, and pull request before removing that
filter for normal operation.

### Option 1. Make your own

Tell your favorite coding agent to build Symphony in a programming language of your choice:

> Implement Symphony according to the following spec:
> https://github.com/openai/symphony/blob/main/SPEC.md

### Option 2. Use our experimental reference implementation

Check out [elixir/README.md](elixir/README.md) for instructions on how to set up your environment
and run the Elixir-based Symphony implementation. You can also ask your favorite coding agent to
help with the setup:

> Set up Symphony for my repository based on
> https://github.com/openai/symphony/blob/main/elixir/README.md

---

## Model and reasoning configuration

Codex workflows also accept optional `codex.model` and `codex.reasoning_effort`
for new sessions. Both dashboards show configured reasoning levels. Omitted
values preserve native runner defaults; see [workflow configuration](elixir/README.md).

## License

This project is licensed under the [Apache License 2.0](LICENSE).

## Claude subscription workers

Codex remains the default. A qualified personal Claude profile adds isolated
Claude workers through pinned Claude Code/Agent SDK and native subscription login. Both
providers retain native organizational policy. Claude policy files do not create an
extra Symphony admission gate; native settings may configure permissions and routing.
Symphony adds no API-key/cloud fallback and does not guarantee native request endpoints
or billing from a subscription profile.
A healthy explicit `runner recheck` with zero workers acknowledges the currently
selected qualified native profile, including an account, organization or plan change.
It clears recoverable authentication/policy pauses through the existing recovery path
and uses that profile for subsequent sessions. Automatic refresh and ordinary resume
never adopt an unexpected profile. A future genuine quota reset, malformed/unsafe
pause evidence or active worker refuses recheck without changing the pinned profile
or deleting that evidence. Successful recheck clears the old recoverable pause;
it does not archive it or perform a model request.
If another readiness observation is running, recheck waits for it and then starts
a fresh bounded check. The recheck API allows 75 seconds and the launcher 80 seconds
for these two sequential checks; each native check remains limited to 35 seconds.

Native allowed/allowed-warning rate-limit events remain allowed, including overage metadata.
Rejected native rate-limit events use ordinary quota recovery regardless of bucket name;
actual native billing, authentication and permission failures retain their stop behavior.
Sonnet remains the default; nonblank model identifiers pass unchanged to the
native runtime, which validates support. Reasoning effort is configurable.
Terminal and web dashboards show the configured Claude effort.
The dashboard updates input tokens during Claude steps and confirms output tokens
at turn completion; session totals include cached input and avoid duplicate tool blocks.
It uses project-scoped credentials and
model-free readiness checks. Both agents receive tracker errors and own recovery:
read back uncertain writes before retrying, correct rejected requests, and contain
unresolved failures to the affected issue. Tracker errors do not pause unrelated
work. Redacted diagnostics retain request fingerprints and correlation/status
fields without an execution-certainty classifier or GraphQL parser dependency.
Per-attempt turn allowances use the original scheduler continuation path for
both providers. Claude native SDK turn exhaustion also closes the session and
yields to the same live tracker check before renewal in the retained workspace.
New workflows do not impose live token/turn circuit breakers.
Helper exit, timeout, malformed frames and incompatible handshakes before hello are
transient startup failures, not native policy stops. Established-session
transport/delivery and transient startup failures use bounded
cleanup and normal scheduler recovery with backoff and tracker readback. Cleanup
failure retains separate bounded primary/cleanup diagnostics and the original
outcome; it does not require container replacement or block unrelated dispatch.
Captured cancellation and owner-loss cleanup run on the existing task supervisor,
keeping scheduler controls and sibling workers responsive while each retiring issue
retains its claim and shared capacity. Captured process generations limit best-effort
cleanup. Detached descendants may
survive a crash, especially before ownership publication; baseline recovery does
not claim universal native-process quiescence. Explicit auth/policy/input stops
and durable drain retain their own semantics. See [setup, pauses and updater transitions](docs/claude.md)
for the contract and its limits. Ordinary dispatch excludes terminal history; startup and tracked-issue reconciliation
retain cleanup, with unclaimed completed workspaces waiting for the next restart.
Existing stable installations change only through
reviewed promotion and the installed `symphony update` command.
Container execution keeps its fixed workspace, bind address and outer-container
permission boundary. Requested SSH execution is rejected instead of silently removed.
`/api/v1/state` exposes `agent.execution` with safe requested/effective bindings,
also logged when container workflow settings load.

For integration testing before promotion, use a [dedicated development workspace](docs/development.md)
with separate configuration, Docker resource names and a separate dashboard port.
Development update retries rebuild from the current checkout and safely refresh
stale pending candidates while retaining their rollback source.
Update and rollback recovery reconstruct selected runtime metadata from the
verified container before completing the transition; interrupted metadata writes
do not require a new listener or manual cache repair.
If an interrupted transition's verified candidate was stopped, retrying the updater starts
that retained container and checks current admission before completing. Its workspace,
state, native login and drain markers remain intact; the prior runner remains available
for rollback until readiness succeeds.
Provider changes and rollback restore ownership for the selected runtime so
retained repositories remain usable by Git when switching back.

Codex and Claude share the configured global and per-state worker limits. Use
`agent.max_concurrent_agents: 1` for serial work or a larger value for bounded
parallel work. Healthy explicit authentication/policy rechecks restore normal parallel capacity immediately, without waiting for a model turn. The deprecated `claude.max_concurrent_agents` input is accepted
but ignored; move any desired limit into `agent`. See the
[Claude workflow settings](docs/claude.md#workflow-settings) for migration details.
