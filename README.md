# Symphony

Symphony turns project work into isolated, autonomous implementation runs, allowing teams to manage
work instead of supervising coding agents.

[![Symphony demo video preview](.github/media/symphony-demo-poster.jpg)](https://player.vimeo.com/video/1186371009?h=5626e4b899)

_In this [demo video](https://player.vimeo.com/video/1186371009?h=5626e4b899), Symphony monitors a Linear board for work and spawns agents to handle the tasks. The agents complete the tasks and provide proof of work: CI status, PR review feedback, complexity analysis, and walkthrough videos. When accepted, the agents land the PR safely. Engineers do not need to supervise Codex; they can manage the work at a higher level._

> [!WARNING]
> Symphony is a low-key engineering preview for testing in trusted environments.

The supplied workflow uses `Blocked` for work requiring external intervention and reserves
`Human Review` for validated work ready for approval. Work stays `In Progress` while automated PR
review is pending or findings are being resolved. Handoff requires completed automated review of
the latest PR commit with no outstanding actionable findings; green CI alone is insufficient.
New projects inherit this gate from the supplied workflow. Existing project workflows are
preserved by `symphony init` and `symphony update`; adopt the automated PR review gate from
[`elixir/WORKFLOW.md`](elixir/WORKFLOW.md) in each existing project's delivery contract.
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

The updater only fast-forwards a clean `main` checkout from its configured upstream. A typical
installation of this fork tracks `jayson/main`, not OpenAI's `origin`. When source changes, it
automatically rebuilds the pinned runtime; it also refreshes the global command and skill links
without changing project credentials or workflows. You do not need to run `symphony setup` after
a successful update.

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
portable Node/Git/Codex base; add project-specific dependencies in `hooks.after_create` or publish
your own compatible image and set `SYMPHONY_RUNNER_IMAGE`.

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

Initialization stores a private, owner-only credential template outside the repository at
`~/.config/symphony/projects/<repository>.env`. Add:

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
. ~/.config/symphony/projects/<repository>.env
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

## License

This project is licensed under the [Apache License 2.0](LICENSE).

## Claude subscription workers

Codex remains the default. A qualified personal Claude profile adds one isolated
Sonnet worker through pinned Claude Code/Agent SDK and native subscription login.
It requires disabled-overage owner attestation, project-scoped credentials and
model-free readiness checks. See [setup, pauses and updater transitions](docs/claude.md)
for the contract and its limits. Existing stable installations change only through
reviewed promotion and the installed `symphony update` command.
For integration testing before promotion, use a [dedicated development workspace](docs/development.md)
with separate configuration, Docker resource names and a separate dashboard port.
