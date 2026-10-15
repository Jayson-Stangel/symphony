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

`install` is safe to rerun: it creates `~/.config/symphony/` with protected permissions and links
`~/.local/bin/symphony` to the stable checkout. It never replaces an existing file or symlink.
Ensure `~/.local/bin` is on your `PATH`.

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
installation of this fork tracks `jayson/main`, not OpenAI's `origin`. It refreshes the launcher
without changing project credentials, workflows, or copied skills. `symphony update --skills`
reports projects whose copied skill bundle predates the current runtime; reviewing and updating
those project files remains an intentional human decision.

Use `SYMPHONY_BIN_DIR` and `SYMPHONY_CONFIG_HOME` to relocate the command and configuration (or
pass `--bin-dir` and `--config-dir` to `install`, `doctor`, or `update`). Use `SYMPHONY_HOME` only
when invoking a copy of the script outside its source checkout.

#### Initialize a repository

From a new or existing Git repository with an `origin` remote, wherever it lives:

```bash
cd /path/to/my-service
symphony init --linear-project my-service-linear-project-slug
```

On first use this creates a repository-specific `WORKFLOW.md` from Symphony's full operating
contract, copies the versioned `commit`, `push`, `pull`, `linear`, and `land` skills into
`.codex/skills/`, records their source revision in `.codex/symphony-skills.yml`, and creates the
repository launcher `script/symphony`. Existing workflow, credentials, and skill directories are
preserved. Review the generated workflow before starting the listener.

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
script/symphony --port 4010
```

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
