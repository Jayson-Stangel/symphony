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

Treat `~/src/symphony` as the stable runtime checkout, separate from any developer checkout used
to modify Symphony itself. Clone the fork you want to run on its `main` branch, then invoke its
installer:

```bash
git clone git@github.com:Jayson-Stangel/symphony.git ~/src/symphony
~/src/symphony/scripts/symphony-project install --install-prerequisites
```

The installer is safe to re-run. It refuses to replace an existing global command, creates
`~/.config/symphony/` with protected permissions, links `~/.local/bin/symphony-project` to the
stable checkout, and installs the Erlang/Elixir versions declared in that checkout. It checks for
Git, [mise](https://mise.jdx.dev/), GitHub CLI (`gh`), and Codex. The
`--install-prerequisites` option installs missing mise, `gh`, and Codex on macOS; Git itself comes
from the Xcode Command Line Tools and may require a separate interactive confirmation.

The installer does not require target repositories to use Erlang, Elixir, mise, or any particular
parent directory. It runs the packaged `escript` through the stable checkout's `elixir/mise.toml`.
Sign in to Codex after installation and ensure it can run `codex app-server`.

Ensure `~/.local/bin` is on your shell `PATH`, then verify the installation:

```bash
symphony-project doctor
symphony-project --help
```

Keep `~/src/symphony` in place: the global command deliberately points to that checkout. Developer
worktrees may live anywhere and may be dirty or on feature branches without changing the runtime
used by managed projects.

#### Update the stable runtime

Update the stable command and runtime deliberately:

```bash
symphony-project update
```

The updater requires its source checkout to be clean, on `main`, and configured with an upstream.
It fetches that tracked remote and fast-forwards only—on this fork, a standard installation tracks
`jayson/main`, not OpenAI's `origin`. It then revalidates the global launcher and runtime without
touching any project credentials or workflows.

Use `SYMPHONY_HOME` only when the script is copied outside its checkout. `SYMPHONY_BIN_DIR` and
`SYMPHONY_CONFIG_HOME` relocate the global command and configuration. Export alternate locations
before `install` so later `update`, `doctor`, and `run` commands use the same paths.

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

From either a new or existing Git repository, wherever it lives, ensure it has an `origin` remote,
then run:

```bash
cd /path/to/my-service
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
