# Symphony

Symphony turns project work into isolated, autonomous implementation runs, allowing teams to manage
work instead of supervising coding agents.

[![Symphony demo video preview](.github/media/symphony-demo-poster.jpg)](https://player.vimeo.com/video/1186371009?h=5626e4b899)

_In this [demo video](https://player.vimeo.com/video/1186371009?h=5626e4b899), Symphony monitors a Linear board for work and spawns agents to handle the tasks. The agents complete the tasks and provide proof of work: CI status, PR review feedback, complexity analysis, and walkthrough videos. When accepted, the agents land the PR safely. Engineers do not need to supervise Codex; they can manage the work at a higher level._

> [!WARNING]
> Symphony is a low-key engineering preview for testing in trusted environments.

## Running Symphony

### Requirements

Symphony works best in codebases that have adopted
[harness engineering](https://openai.com/index/harness-engineering/). Symphony is the next step --
moving from managing coding agents to managing work that needs to get done.

### Run this fork as a centralized local service

This fork includes `symphony-project`, a local bootstrapper for running one centrally installed
Symphony runtime against many repositories. It keeps credentials outside of each target repository,
but creates a repository-specific `WORKFLOW.md`, skill bundle, and convenience launcher.

#### One-time machine setup

Install these prerequisites:

- Git, GitHub CLI (`gh`), and a GitHub account with access to the repositories Symphony will work in.
- [mise](https://mise.jdx.dev/) to supply the Erlang/Elixir runtime used by Symphony.
- The [Codex CLI](https://developers.openai.com/codex/cli/) signed in and able to run
  `codex app-server`.
- A Linear personal API key for the Linear workspace that supplies work.

The bootstrapper runs the packaged `escript` through the Symphony checkout's `elixir/mise.toml`.
Target repositories do **not** need to adopt Erlang, Elixir, or mise themselves. Install the runtime
once after cloning or updating this repository:

```bash
git clone git@github.com:<your-account>/symphony.git ~/src/symphony
mise -C ~/src/symphony/elixir install
mkdir -p ~/.local/bin
ln -sf ~/src/symphony/scripts/symphony-project ~/.local/bin/symphony-project
```

Ensure `~/.local/bin` is on your shell `PATH`, then verify the command:

```bash
symphony-project --help
```

Set `SYMPHONY_HOME` if the executable is copied somewhere instead of symlinked from the checkout.
Set `SYMPHONY_CONFIG_HOME` to relocate the default per-user configuration directory
(`~/.config/symphony`).

#### Create credentials

For each target repository, `init` creates a private credential file at
`~/.config/symphony/projects/<repository>.env` (or the equivalent under
`SYMPHONY_CONFIG_HOME`). This file is deliberately outside the repository and is created with
owner-only permissions.

Add the following values:

```dotenv
LINEAR_API_KEY=lin_api_...
GH_TOKEN=github_pat_...
```

Create the Linear key in **Settings → Security & access → Personal API keys**. Create the GitHub
token at [GitHub Settings → Fine-grained personal access tokens](https://github.com/settings/personal-access-tokens/new).
Restrict the GitHub token to **only the repository that Symphony will manage**, and grant:

- `Contents: Read and write` — clone, branches, and pushes.
- `Pull requests: Read and write` — create, update, and merge pull requests.
- `Issues: Read and write` — only when Symphony will comment on or label issues.
- `Workflows: Read and write` — only when it must change files under
  `.github/workflows/`.

Do not grant Administration, Secrets, Packages, organization permissions, or access to every
repository. A classic token with `repo` is a fallback only; add `workflow` only when changing
workflow files. GitHub App support is planned as a stronger, bot-attributed alternative to a
personal token.

#### Initialize a repository

From either a new or existing Git repository, ensure it has an `origin` remote, then run:

```bash
cd ~/Repos/my-service
symphony-project init --linear-project my-service-linear-project-slug
```

The command is safe to re-run. On first initialization it:

- creates a repository-specific `WORKFLOW.md` using the full Symphony operating contract as the
  baseline;
- creates the external credential template described above;
- copies the versioned `commit`, `push`, `pull`, `linear`, and `land` skills into
  `.codex/skills/`, recording their Symphony revision in `.codex/symphony-skills.yml`; and
- adds `script/symphony`, a convenient repository-local launcher.

It preserves an existing `WORKFLOW.md` and existing credential file. Review the generated workflow
before the first run: its clone URL, Linear project slug, workspace location, active states,
concurrency, and token/turn guardrails should match the target project.

For a brand-new repository, commit and push at least one initial commit before initialization so
the `origin` remote is cloneable:

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:<owner>/<repository>.git
git push -u origin main
symphony-project init --linear-project <linear-project-slug>
```

#### Verify and start safely

First, verify authentication without starting an agent run. From the target repository, load the
credential file and use GitHub's CLI to confirm repository access:

```bash
set -a
. ~/.config/symphony/projects/<repository>.env
set +a
gh repo view <owner>/<repository>
```

Then start Symphony with its optional local dashboard:

```bash
script/symphony --port 4010
# Equivalent: symphony-project run --port 4010
```

Visit `http://localhost:4010` to observe the listener. This is a live service: it polls Linear and
can claim any issue matching the workflow's active-state and label rules. For a first run, add a
temporary `required_labels: ["symphony-smoke-test"]` entry to `WORKFLOW.md` and create one harmless
test issue with that label. Stop the listener with `Ctrl-C`, inspect the workspace, branch, Linear
updates, and pull request, then remove the temporary filter when ready for normal operation.

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
