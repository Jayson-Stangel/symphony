# Symphony repository instructions

These rules apply to all work in this repository. Also follow directory-specific
`AGENTS.md` instructions where present.

## Protect the stable installation

- Develop Symphony only in a separate development checkout or worktree. Before
  editing or running commands that write files, verify the checkout path and the
  stable installation targeted by the global `symphony` command, including any
  `SYMPHONY_HOME` override. Never assume the current checkout is safe to modify.
- Never modify the stable installation directly while developing Symphony. This
  includes source files, Git state, built artifacts, dependencies, configuration,
  and global command or skill links. Do not copy or symlink development changes
  into the stable installation, build there, or run Git mutations there.
- `symphony update` is the only allowed mechanism for applying changes to the
  stable installation. Land changes through the normal Git review workflow first,
  then use the installed `symphony update` command when a stable update is needed.
  Do not invoke a modified development updater against the stable installation.
- Never kill, restart, replace, or otherwise manipulate running stable Symphony
  processes directly to apply or test development changes. Any required stable
  runtime transition must be handled by `symphony update`.
- If `symphony update` refuses an update or cannot perform a required installation
  or process change, stop that operation and report the limitation. Do not bypass
  its checks, repair the stable checkout manually, or fall back to another command.
- Test development changes in isolated development environments, with separate
  configuration, workspaces, and processes from the stable runtime.

## Git practices

- Inspect `git status`, the current branch, and configured remotes before making
  changes. Preserve existing user work and keep unrelated changes out of the task.
- `dev` is the integration branch; `main` is the production branch. Create focused
  feature branches from the latest `dev` on the intended upstream, using the
  `codex/` prefix unless instructed otherwise. Do not develop directly on `dev`
  or `main`. Use a separate worktree when necessary to avoid disturbing existing
  work. If `dev` is missing, report it rather than silently branching from `main`.
- Target feature pull requests at `dev`. Promote integrated changes to production
  with a pull request from `dev` to `main`; do not target `main` with feature PRs
  or bypass the promotion PR. When continuing an existing feature branch created
  from `main`, reconcile it with `dev` before publishing its PR, preserving work
  and following the history-safety rules below.
- Keep changes scoped to the task. Review the diff before staging, stage only
  intended files or hunks, and inspect the staged diff before committing. Do not
  blindly stage the entire working tree or commit secrets, generated build output,
  or unrelated edits.
- Make coherent commits with descriptive messages explaining the change. Run the
  checks appropriate to the change and report any checks that could not be run.
- Do not discard, overwrite, stash, or clean up someone else's changes. Do not use
  destructive reset/checkout/clean commands, rewrite published history, or force
  push without explicit user authorization.
- Synchronize feature branches with the intended upstream's `dev` deliberately;
  inspect divergence and follow the repository's branch synchronization
  instructions using `dev` as the integration base. Never resolve a conflict by
  blindly accepting one side or bypassing failing checks.
- Publish changes through a pull request and the required review and validation
  gates. Do not push directly to protected branches or merge with failing required
  checks. Update the stable installation only through `symphony update` after the
  change has been promoted to `main`.

## Code Review Rules

- Inspect the complete diff and affected callers, consumers, and lifecycle paths;
  continue after the first defect and report all independently actionable findings
  discovered in the pass. Group related paths by root cause and give a concrete
  supported trigger and consequence. This is a coverage goal, not a guarantee of
  exhaustive review; avoid speculative concerns and mechanical checks owned by CI.
- For orchestration changes, check dispatch, concurrency limits, retries,
  cancellation, reconciliation, and restart recovery together. A retry or restart
  must preserve workspace ownership, existing work, and durable escalation state.
- Check workspace containment, credential boundaries, and isolation of development
  or simulated execution from stable installations and live tracker/provider state.
  Follow configuration changes through load/reload, adapters, and status reporting;
  reported outcomes must agree with authoritative execution state.
