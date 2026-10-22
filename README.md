# Symphony

Symphony runs coding agents against work in your issue tracker. It polls for eligible
issues, gives each one an isolated workspace, and runs an agent to implement and
validate the change. Your repository's workflow defines when work is ready for review
and when it may be merged.

This fork builds on [OpenAI Symphony](https://github.com/openai/symphony). The
orchestrator is written in Elixir/OTP; the repositories it works on can use any
language or toolchain.

> [!IMPORTANT]
> Symphony is engineering preview software for trusted, owner-operated environments.
> Workers execute repository code and can use the credentials you give them. Start
> with a disposable issue and review its results before enabling broader dispatch.

## Why this fork

We forked Symphony to use OpenAI's orchestration model in our own day-to-day work
while adapting it to our local environment and agent runtimes. The goal is one
installation serving multiple repositories, each with an explicit workflow and
unattended workers. This fork adds the operational layer we need around that model:
installation, container execution, retained workspaces, authentication, controlled
updates, and a choice of Codex or Claude.

### What is different

| Area | This fork adds |
| --- | --- |
| Installation and operation | A global `symphony` command for setup, project initialization, diagnostics, and stable updates across multiple repositories. |
| Execution | Project-specific Docker/Podman runners with retained workspace and log volumes, separate agent logins, and bundled Git and browser tools. |
| Agent choice | Claude Code/Agent SDK workers alongside the default Codex app-server, using the same scheduler and concurrency controls. |
| Project policy and review | Namespaced shared skills with deliberate local overrides, durable tracker blockers, and a workflow that releases workers at human review. |
| Runtime changes | Drain/resume controls, runner update and rollback procedures, and separate development environments and production approval boundaries. |

### What we try to preserve

- **The original division of responsibility.** Symphony schedules and reconciles
  work; agents implement changes and recover tracker operations. Repository policy
  belongs in the versioned `WORKFLOW.md`.
- **The core execution model.** Tracker-driven dispatch, one workspace per issue,
  bounded concurrency, continuation, retries with backoff, and observable progress
  remain the foundation. Codex remains the default agent.
- **A small orchestration core.** We reuse the shared scheduler for both agents and
  leave model support, authentication, and organizational policy to their native
  runtimes. We aim to avoid parallel scheduling or policy systems.
- **Project ownership.** Initialization and updates preserve existing workflows
  and credentials; teams adopt policy changes explicitly.

[SPEC.md](SPEC.md) describes this fork's contract. The
[upstream repository](https://github.com/openai/symphony) documents OpenAI's version.

## How it works

1. **Choose the work.** Configure a tracker project, eligible states, and optional labels.
2. **Define the workflow.** A repository-owned `WORKFLOW.md` holds configuration,
   setup hooks, validation requirements, and the agent's instructions.
3. **Run the workers.** Symphony creates a checkout per issue and runs the selected
   agent inside the project's container runner, with configurable concurrency.
4. **Review the result.** Workers record progress and validation evidence in the
   tracker and prepare pull requests according to repository policy. The dashboard
   shows running work, retries, blockers, and usage.

The supplied workflow hands validated work to `Human Review` and records external
blockers in `Blocked`. Repository checks, required reviews, and human approval govern
merging. Existing workflows are preserved when Symphony is initialized or updated.

## Get started

On Apple Silicon macOS 14 or later, or Ubuntu 26.04 x86-64 when its archive is available,
download the matching manager bundle and `release.json` from a
[completed stable release](https://github.com/Jayson-Stangel/symphony/releases).
Install and start Docker Engine on Ubuntu, or Docker Desktop/OrbStack on macOS,
separately. Then follow the
[packaged installation guide](docs/packaged-install.md) to verify and install the
bundle. The manager uses prebuilt runner images; it needs no Symphony checkout,
host Git, mise, Python, Node, agent CLI, or build toolchain.

You also need an existing project checkout with an explicit `origin`, tracker
credentials, a repository-scoped GitHub credential, and a native agent login.
Preparing the target checkout is separate from installing Symphony. Codex is the
default; Claude uses native sign-in in its isolated runner.

The [getting started guide](docs/getting-started.md) covers project initialization,
credentials, authentication, and one disposable issue-to-PR pilot for either
installation type. Existing source installations keep their source updater;
bundle installation does not migrate them automatically.

### Source installation

For a source-backed installation, install Git and [mise](https://mise.jdx.dev/)
and keep Symphony in a dedicated checkout:

```bash
git clone --branch main https://github.com/Jayson-Stangel/symphony.git ~/src/symphony
~/src/symphony/scripts/symphony install
# Ensure ~/.local/bin is on PATH.
symphony setup
symphony doctor
```

Continue at [initialize a repository](docs/getting-started.md#initialize-a-repository).
The guide uses Linear; other supported trackers are described in the
[configuration reference](elixir/README.md#configuration).

For source installations, keep the stable checkout in place: the global command points to it. To change Symphony
itself or test `dev`, use a [separate development environment](docs/development.md).

## Documentation

| I want to… | Read |
| --- | --- |
| Connect a project, authenticate an agent, and run a first issue | [Getting started](docs/getting-started.md) |
| Install a macOS/Ubuntu release bundle without host build tools | [Packaged installation](docs/packaged-install.md) |
| Inspect, drain, stop, update, or recover a runner | [Operations](docs/operations.md) |
| Configure trackers, workspaces, hooks, models, or concurrency | [Configuration reference](elixir/README.md#configuration) |
| Use Claude, configure its tools, or resolve native login and quota issues | [Claude runner guide](docs/claude.md) |
| Understand or adapt the worker's operating instructions | [Workflow template](elixir/WORKFLOW.md) |
| Develop and test Symphony in isolation | [Development guide](docs/development.md) |
| Build or test the Elixir service | [Implementation guide](elixir/README.md) |
| Build and qualify versioned runner and manager artifacts | [Release artifact contract](scripts/release/README.md) |
| Understand the orchestration contract | [Specification](SPEC.md) |

Run `symphony --help` for the command summary. For service diagnostics, see
[logging](elixir/docs/logging.md) and [token accounting](elixir/docs/token_accounting.md).

## Contributing

Create focused feature branches from this fork's latest `dev` and target pull requests
at `dev`. Follow [AGENTS.md](AGENTS.md) and the
[development guide](docs/development.md) for checkout isolation and validation.

`main` is production. Promoting `dev` to `main` and updating a stable installation
require explicit operator approval for the complete change set and rollout targets.
Stable changes are applied only through the installed `symphony update` command;
see [update procedures](docs/operations.md#update-the-stable-installation).

## License and attribution

Licensed under [Apache License 2.0](LICENSE). This project is derived from
[OpenAI Symphony](https://github.com/openai/symphony); see [NOTICE](NOTICE) for the
upstream copyright notice.
