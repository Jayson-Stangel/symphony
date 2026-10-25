# Symphony repository instructions

These rules apply to all work in this repository. Also follow directory-specific
`AGENTS.md` instructions where present.

## Development process and Linear lifecycle

- This project develops Symphony; it does not use Symphony to develop itself.
  Implementation runs through operator-directed coding-agent chats in isolated
  development checkouts, with Linear as the work contract and GitHub as the
  implementation, review and merge record.
- Do not dispatch this project's issues through a Symphony listener, add routing
  labels to enable execution, or start a listener to implement repository work.
  `elixir/WORKFLOW.md` is a shipped runner workflow. Reuse its useful delivery
  practices (acceptance checklists, validation, feedback sweeps and rework), but
  follow this file for this project's coordination, status and authority rules.
  Do not import runner-specific tracker routing, labels, worker shutdown rules or
  Symphony-worker-only skills into direct development.
  Explicitly authorized Symphony qualification against a separate target project
  is testing the product, not using it to implement its own repair.
- Use `Backlog` for planned work and explicit holds. Starting implementation
  requires operator authorization; a Linear state or label alone does not grant
  it. Keep issue status aligned with actual progress and use the team's existing
  active or review states when appropriate. Record direct-agent ownership and
  handoff evidence on the issue so Backlog does not become a store of completed
  work.
- Use `Done` as the sole successful completion status for this project. Do not
  use `Integrated` or create a separate completed state for a merge into `dev`.
  Preserve cancellation and duplicate dispositions for work that was not completed.
- Before marking an implementation issue `Done`, read its current contract,
  verify all acceptance criteria with the required evidence, confirm review and
  validation are satisfied, and observe its PR merged into `dev`. If the contract
  explicitly ends at an open reviewed PR or another non-merge deliverable, verify
  that authorized endpoint instead; completion never grants merge authority.
- A merged PR alone does not complete an issue with outstanding required native,
  installed, authenticated or end-to-end acceptance. Keep those gaps explicit and
  leave the issue unfinished until satisfied or the operator explicitly changes
  the contract. Distinguish fixtures, CI, native runtime and installed proof.
- Record the PR link, exact reviewed/tested revision, merge revision when
  applicable, and acceptance results before closeout. Read back Linear after a
  status change. When reconciling stale Backlog issues, check live issue and PR
  evidence and move only verified completed work to `Done`.
- `Done` describes completion of the issue's contract, not production promotion,
  release publication or stable installation. Those actions retain their separate
  approval and validation boundaries below.

## Main-chat coordination and subagent delivery

- The main chat owns the operator's request, current Linear contract, scope,
  acceptance decisions and final handoff. For implementation work, it delegates
  focused development and testing to subagents, with explicit ownership, base
  revision, deliverable, required proof and action limits. Use separate worktrees
  where needed; parallelize independent work without competing writes or duplicate
  builds and qualification runs. Small documentation or administrative changes
  may be handled directly without an unnecessary delegation cycle.
- Subagents implement, test and return a reviewable diff or PR with exact source
  revisions, commands/results, acceptance evidence and remaining gaps. A subagent
  completion message is a handoff, not acceptance or merge approval. Subagents
  must not merge or expand external effects beyond their delegated authority.
- The main chat reviews the actual diff, current issue, tests, required CI,
  available review feedback and resulting behavior. It checks the final combined
  change against the contract rather than accepting a subagent's summary alone.
  Use an independent review subagent for substantial code or safety-sensitive
  changes; its findings supplement the main chat's responsibility.
- Return actionable findings to the owning subagent with concrete expected
  behavior and proof. Reassess the approach when failures expose a mistaken
  assumption; do not merely repeat tests until green. After fixes or integration
  changes, refresh the relevant validation and review against the final head.
- Once acceptance, review and required checks pass, the main chat may merge the
  focused PR into `dev` only within the operator's existing merge authorization.
  If authority is missing or the request requires an unmerged handoff, leave the
  PR open and present the reviewed result. Neither a subagent verdict nor a
  `Merging` status supplies operator approval. After an authorized merge, verify
  the resulting revision and close the Linear issue as `Done` when its contract
  is complete. Production promotion and stable rollout remain separately gated.

## Explicit approval for production

- Before merging any promotion into `main` or performing any stable rollout,
  obtain the operator's explicit approval for that action and the exact PRs
  included. Approval to review, land, or test changes in `dev` does not authorize
  promotion to `main` or changes to the stable installation.
- Present the complete included PR list with links and purposes, the promotion
  PR and exact head revision, and the proposed stable revision and runtime/project
  targets when rollout is requested. Include every change since the target
  `main` or deployed stable revision, not only the PR that motivated the request.
  Check this inventory against live Git history before acting.
- A request to "roll out the fix" does not authorize other changes accumulated
  in `dev`. If the promotion or rollout includes any PR outside the explicitly
  approved set, stop and obtain approval for the complete set. Announcing extra
  scope, a PR description asserting approval, passing checks, and lack of an
  objection are not operator approval.
- Promotion and stable rollout are separate approval boundaries. One explicit
  instruction may authorize both for the presented exact PR set and revisions;
  approval of promotion alone does not authorize rollout. If the included PRs,
  revisions, or rollout targets change, present the updated scope and obtain
  renewed approval before acting. Preparing a draft promotion PR does not grant
  permission to merge it.

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

## Documentation-only validation

Pure human-facing documentation changes use the lightweight CI classification and
PR metadata checks. They do not require dependency installation, application or
Docker builds, SDK compilation, or test suites. For code and workflow changes,
run the focused checks and full required gates, including `make -C elixir all`
where applicable. A mixed change follows the code route.

`scripts/ci/classify_changes.py` is the shared event-wide policy. Root README,
AGENTS, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY and SPEC Markdown, the Elixir
README/AGENTS, and prose `.md`/`.txt`/`.rst` beneath `docs/` or `elixir/docs/`
are documentation. Examples embedded in those documents follow that route.
Standalone executable examples and unknown paths take the conservative code route.
Packaged `.codex/skills/`, `.agents/` content and every `WORKFLOW.md` are behavioral
inputs even though they use Markdown. Do not classify all Markdown as documentation.

Behavioral required check names remain present on every PR and dev/main push;
PR metadata validation remains required on PR events. Their summaries
say explicitly when behavioral qualification was intentionally omitted; that is
not a claim that tests executed. Classification failure fails the required wrapper.
See `docs/development.md` for the event map and local classifier validation.
