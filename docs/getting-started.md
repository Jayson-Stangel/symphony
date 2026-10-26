# Getting started

This guide connects an existing repository to Symphony and runs one disposable
Linear issue into a reviewable PR. One installation can serve multiple repositories;
each has its own workflow, credentials, runner, and workspaces.

On macOS, start with the [release bundle](packaged-install.md), then continue at
[initialize a repository](#initialize-a-repository) below. A bundle needs no Symphony
source checkout or host build/agent tools. The source installation alternative is
included below. For an existing installation, see [operations](operations.md).
To develop Symphony or test `dev`, use the [development guide](development.md).

## Prerequisites

- An installed Symphony command: a macOS [release bundle](packaged-install.md),
  or the [source installation](#install-a-stable-runtime) below.
- A running Docker-compatible engine: Docker Desktop or OrbStack on macOS, or Docker
  Engine or rootless Podman on Unix. Set `SYMPHONY_CONTAINER_RUNTIME=docker` or `podman`
  to select one explicitly.
- A target repository with a pushed commit and an `origin` remote the worker can clone.
- Access to a Linear project and a repository-scoped GitHub token.
- A Codex login, or a supported native Claude subscription login for the
  [Claude runner](claude.md).

Only the source installation requires host Git and [mise](https://mise.jdx.dev/).
Source setup installs the Erlang/OTP, Elixir, and Node.js versions pinned in
`elixir/mise.toml`. Target repositories do not need Elixir or mise. Container images
supply the agent CLI, GitHub CLI, Git, and browser tools; host Codex and GitHub CLI
are optional for host debugging and manual access checks.

## Install a stable runtime

For a macOS release bundle, follow [packaged installation](packaged-install.md)
and skip to [project initialization](#initialize-a-repository); do not run `setup`.

For the source alternative, choose a dedicated location that will remain available
to the global command:

```bash
git clone --branch main https://github.com/Jayson-Stangel/symphony.git ~/src/symphony
~/src/symphony/scripts/symphony install
```

`install` creates private configuration storage, links `~/.local/bin/symphony` to
this checkout, and links the shared Symphony skills into `~/.codex/skills/`. It is
safe to rerun and refuses to replace an existing unrelated file or symlink. Ensure
`~/.local/bin` is on your `PATH`, then build the runtime and check the installation:

```bash
symphony setup
symphony doctor
```

On macOS, `symphony setup --install-prerequisites` can install missing mise through
Homebrew. Git requires the Xcode Command Line Tools; Homebrew must already be installed.
On other systems, install Git and mise yourself before running setup. Setup does not
install a container engine.

Keep this stable checkout separate from development. Once installed, apply stable
changes through [the updater](operations.md#update-the-stable-installation).

## Initialize a repository

Run project commands from the target repository, rather than the Symphony source:

```bash
cd /path/to/my-service
symphony init --linear-project YOUR_LINEAR_PROJECT_SLUG --agent codex
# For Claude, use --agent claude instead.
```

Use the slug from your Linear project's URL. Initialization creates `WORKFLOW.md`
from the supplied operating contract and prints the path to a private credential
file outside the repository. Existing workflows and credential files are preserved.

`--agent codex|claude` selects the initialization instructions; it does not start
or switch a runner or change the workflow. Without it, init follows a retained
team environment, runner metadata or observed runner provider, then defaults to
Codex for a new project. The provider remains a startup choice: pass the same
`--agent` to `run` or the installed updater. To resume an initialized project,
rerun init with its Linear slug and desired `--agent`; existing workflow,
credentials and deliberate skill overrides are preserved. Read the printed
intended provider, stored desired image and observed runner separately. If they
differ, follow the explicit compatible-image selection and updater instructions
before authentication; see [provider transitions](packaged-install.md#choose-or-change-the-provider).

Review and commit `WORKFLOW.md` as repository policy. Before starting a listener:

- Confirm the tracker project and states. The supplied workflow uses `Todo`,
  `In Progress`, `Rework`, and `Merging` as active states; `Blocked` and `Human Review`
  need corresponding tracker statuses for its handoff policy.
- Check the clone URL, setup hooks, validation commands, review requirements,
  and merge rules against your repository.
- Restrict the first run to one disposable issue. Under the existing `tracker:`
  mapping, set `required_labels: ["symphony-smoke-test"]` and give only that issue
  the matching label. Under `agent:`, set `max_concurrent_agents: 1` for a serial pilot.

The listener can dispatch matching issues immediately. See the
[workflow template](../elixir/WORKFLOW.md) for worker instructions and the
[configuration reference](../elixir/README.md#configuration) for tracker adapters,
workspace setup, models, reasoning effort, and concurrency.

## Configure credentials

Open the exact credential path printed by `init`. By default it is
`~/.config/symphony/projects/<repository-identity>.env`, where the identity combines
the repository basename and a hash of its normalized `origin` URL. Do not infer the
path from the basename alone. A custom `SYMPHONY_CONFIG_HOME` or runner namespace
changes this location; use the path printed in the same environment where you will
run Symphony. Repeated `init` preserves an existing credential file. Add the values
locally:

```dotenv
LINEAR_API_KEY=YOUR_LINEAR_API_KEY
GH_TOKEN=YOUR_REPOSITORY_SCOPED_GITHUB_TOKEN
```

Keep this file private and outside version control. The template is created with
owner-only permissions. The Git credential may also be named `GITHUB_TOKEN`.

Use a Linear personal API key with access to the selected project. For GitHub, use a
[fine-grained token](https://github.com/settings/personal-access-tokens/new) restricted
to the managed repository. Grant `Contents` and `Pull requests` read/write; add `Issues`
only for issue comments or labels, and `Workflows` only for changes to
`.github/workflows/`. Avoid unrelated repository or administrative access.

Agent authentication is separate from tracker and Git credentials. Complete the
native login flow in your terminal; keep login codes and credentials out of issues,
workpads, and chat.

## Authenticate and run

For the default Codex runner, run these commands from the initialized repository:

```bash
symphony auth codex login
symphony auth codex check
symphony runner doctor --agent codex
symphony run --agent codex --port 4010
```

The first login prepares the runner image and stores the native session in a
Docker-managed volume separate from the host Codex home. `auth check` and
`runner doctor` make no model requests. The runner doctor verifies native login
and branch/commit operations in a disposable local Git repository; remote clone
and push access still depend on the project's Git credential.

For Claude, follow the [Claude setup instructions](claude.md#set-up-a-fresh-project),
using `auth claude`, `runner doctor --agent claude`, and `run --agent claude`.

Open [the local dashboard](http://127.0.0.1:4010) and inspect the pilot issue's
workspace, tracker updates, validation results, and pull request. Use a different
`--port` for each concurrently running project. Remove or widen the label filter
only after reviewing the pilot.

## Finish the pilot

The container listener runs independently of the launching terminal. To let current
work finish while preventing new dispatch:

```bash
symphony runner drain
symphony runner status
```

Wait until the dashboard shows zero active workers, then stop the runner:

```bash
symphony runner stop
```

Stopping retains the container, workspaces, logs, and native login. Restart Claude
with `symphony run --agent claude --port PORT`, or Codex with
`symphony run --port PORT`, using the original port. Then use
`symphony runner resume` to clear the explicit drain when ready. Read [operations](operations.md) for persistence, recovery,
updates, and deliberate local skill overrides.
