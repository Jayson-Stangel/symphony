# Symphony repository instructions

These rules apply to all work in this repository. Also follow directory-specific
`AGENTS.md` instructions where present.

## First principles for agent judgment

Apply these principles when framing work, choosing an approach, coordinating,
validating, reviewing or recovering delivery. They explain how to make decisions
within the explicit rules below; they do not grant authority, waive required
checks or replace the issue's acceptance contract.

- **Reduce uncertainty before increasing investment.** Identify the assumption
  most likely to invalidate the plan and choose the smallest authorized experiment
  that can resolve it. When the uncertainty concerns an external service, native
  tool or installed experience, exercise that actual boundary before investing in
  expensive qualification or expanding the implementation around it. Prove the
  risky mechanism, including its input/output handoff, before scaling it. A mock
  cannot establish real token access or native behavior. Keep required final
  validation after the decisive experiment.
- **Optimize for the user's completed outcome.** Define success in terms of the
  requested experience under ordinary operating conditions. Passing tests,
  merging code and publishing artifacts establish different milestones; none
  alone proves the composed outcome. Identify who will demonstrate remaining
  integration and keep manual repairs, overrides and acceptance gaps explicit.
- **Choose the simplest sufficient approach.** Before adding a subsystem,
  controller, queue, evidence protocol or persistent state, briefly explain in the
  existing workpad or task record why supported capabilities are insufficient,
  which smaller alternative was considered and why the added operating cost is
  justified.
  This is a reasoning obligation, not a new routine approval gate. Prefer a
  smaller coherent delivery that preserves required behavior and protections.
- **Treat operator attention as scarce.** Carry routine authorized work through
  completion and preserve the original human authorization across handoffs in the
  existing workpad. Verify that source rather than treating an agent's forwarded
  assertion as consent. Ask again only for missing authority, materially changed
  scope or a documented platform refusal; explain the exact unresolved boundary.
  Do not make the operator relay routine coordination or repeat unchanged approval.
- **Make claims no broader than the evidence.** Distinguish observed facts,
  reported results, assumptions, forecasts and unknowns. Bind proof to its actual
  source, environment and scope. Report queued, running, failed, cancelled,
  succeeded, reused and intentionally omitted work accurately. A partial success
  or predicted result must not become a completion claim.
- **Control expansion.** An authorized outcome does not justify unlimited
  implementation complexity. When fixes spread into adjacent capabilities or
  repeatedly repair their own validation machinery, reassess before extending the
  approach. Prefer simplifying, splitting or deferring the expansion; continue
  independent authorized work. Explain the next step's decisive evidence and keep
  unfinished requirements explicit. Repair ordinary defects within scope without
  silently narrowing acceptance or treating repeated activity as progress.
- **Keep proof proportional and reusable.** Gather evidence to resolve a named
  acceptance question. Prefer concise command results, diffs and original
  receipts; add screenshots or large exports only when they demonstrate something
  those records cannot. Reuse valid evidence with its original provenance when
  the relevant inputs remain applicable. Repeat validation for changed inputs,
  failures, uncertainty or an explicit required gate, not merely a new handoff.

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
  This reference does not incorporate that workflow's instructions wholesale.
  Do not import runner-specific tracker routing, labels, worker shutdown rules or
  Symphony-worker-only skills into direct development.
  Explicitly authorized Symphony qualification against a separate target project
  is testing the product, not using it to implement its own repair.
- Use `Backlog` for planned work and explicit holds. Starting implementation
  requires operator authorization; a Linear state or label alone does not grant
  it. Use `Todo` for authorized work queued to start and `In Progress` for active
  implementation and testing. Record direct-agent ownership and handoff evidence
  on the issue so Backlog does not become a store of completed work.
- Use `Agent Review` when subagents hand work to the main chat for acceptance
  review. Use `Rework` when that review returns actionable findings for fixes;
  return to `Agent Review` with refreshed evidence after addressing them.
- Use `Human Review` only when an operator decision or approval is actually
  needed, including a requested unmerged handoff. Identify the exact decision
  needed. If the operator already authorized the current scope's merge into
  `dev`, the main chat proceeds from successful `Agent Review` to `Merging`
  without adding a `Human Review` approval step. Material scope changes or
  instructions to stop unmerged still require operator assessment.
- Use `Merging` for accepted work proceeding through an authorized merge into
  `dev`. Use `Blocked` for an external impediment, with the precise blocker and
  unblock action recorded. Pending automated checks or agent review are not
  reasons to request human approval. Do not use the ambiguous `In Review` status
  for this project; distinguish `Agent Review` from `Human Review` explicitly.
- Use `Done` as the sole successful completion status for this project. Do not
  use `Integrated` or create a separate completed state for a merge into `dev`.
  Preserve cancellation and duplicate dispositions for work that was not completed.
- Before marking an implementation issue `Done`, read its current contract,
  verify all acceptance criteria with the required evidence, confirm review and
  validation are satisfied, and observe its PR merged into `dev`. An open PR
  handed off for operator review remains in `Human Review`. If the operator
  explicitly defines a review-only, research or other non-merge issue, verify
  that issue's authorized endpoint instead; completion never grants merge authority.
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

- Before merging, inspect actual dependencies and in-flight operations for a
  concrete conflict: overlapping writes, a changed interface or consumed build
  input, invalidated qualification evidence, or contention for an exclusive
  resource. Sharing `dev`, a repository or a coordination chat alone does not
  require peer clearance. Documentation-only CI classification alone does not
  establish build-input safety. If no material coupling is found, record the
  assessment briefly and proceed within existing authority.
- Obtain explicit clearance only from owners whose work is affected by that
  conflict. Present the exact PR/head, proposed base, dependency or resource
  impact and timing. Resolve material uncertainty with those owners; silence is
  not clearance. Renew clearance only when a change invalidates the assessed
  dependency, impact or timing, not merely because a head or base SHA changed.
  Separate production revision approvals below still apply in full.
- A freeze must name the affected inputs or resource, protected operation, owner,
  reason and release condition in the existing workpad. Hold only actions that
  can invalidate that operation; allow independent reads, local work and unrelated
  publication. A pinned immutable run is not invalidated by a ref moving unless
  its actual admission or source policy depends on that ref remaining unchanged.
  Release the hold when its stated condition is met; do not make serial runner
  use a repository-wide source-push freeze without a demonstrated dependency.
- The main chat owns the operator's request, current Linear contract, scope,
  acceptance decisions and final handoff. Even within a broad goal, choose a
  bounded next delivery with an observable acceptance endpoint and stop condition;
  discoveries do not automatically expand it. Delegate independent development
  and testing with explicit ownership, base, deliverable, proof and action limits.
  Use separate worktrees where needed; avoid competing writes and duplicate
  qualification. Small documentation or administrative changes may be handled
  directly without an unnecessary delegation cycle.
- Subagents implement, test and return a reviewable diff or PR with exact source
  revisions, commands/results, acceptance evidence and remaining gaps. A subagent
  completion message is a handoff, not acceptance or merge approval. Subagents
  must not merge or expand external effects beyond their delegated authority.
- The main chat reviews the actual diff, current issue, tests, required CI,
  available review feedback and resulting behavior. It checks the final combined
  change against the contract, including necessity, simpler alternatives, proof
  order and operating cost. Green tests and a clean defect review are insufficient
  justification for added machinery. Reject unjustified complexity or recommend
  simplification or deferral, preserving unfinished acceptance and obtaining any
  required contract decision rather than accepting a subagent's summary alone.
  Use an independent review subagent for substantial code or safety-sensitive
  changes; its findings supplement the main chat's responsibility.
- Return actionable findings with concrete expected behavior and proof. Resolve
  decisive preparation, artifact and handoff failures with focused checks before
  another expensive run. Apply the expansion principle when the approach itself
  keeps failing; after corrections, refresh affected validation and review against
  the final head while preserving all applicable required gates.
- Once acceptance, review and required checks pass, the main chat may merge the
  focused PR into `dev` only within the operator's existing merge authorization.
  If authority is missing or the request requires an unmerged handoff, leave the
  PR open and present the reviewed result. A subagent verdict or an unexplained
  `Merging` status does not supply operator approval. An explicit operator move
  to `Merging` can communicate approval for the identified current PR and scope
  targeting `dev`; verify that provenance and honor any narrower instructions.
  After an authorized merge, verify the resulting revision and close the Linear
  issue as `Done` when its contract is complete. Production promotion and stable
  rollout remain separately gated.

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

## Clean up after each task

- Cleanup is part of task completion. Track the local branches, remote branches,
  worktrees, Docker containers, images, networks and build caches created for the
  task. Use task-specific names or labels so ownership is clear. Remove disposable
  artifacts when validation finishes; report what was removed and anything retained,
  with its owner and reason. Do not leave cleanup as an unrecorded future task.
  Use concise existing task records; a separate inventory service or cleanup
  framework is not required. Routine cleanup covers only verified task-owned
  artifacts. Ambiguous ownership, shared resources or broader deletion require
  owner clearance and any missing operator authority before proceeding.
- Before Git cleanup, refresh the intended remote and inspect live PR state,
  branch tips, worktree status and unpushed commits. After a feature PR is verified
  merged into `dev`, delete its task-owned remote feature branch if it still exists,
  then remove clean, inactive task worktrees and delete the corresponding local
  branches. Verify the exact branch revision was integrated, including the PR's
  recorded head for squash merges; age or a matching branch name is insufficient.
  Confirm no peer still consumes the branch or worktree before removing it.
  Prune stale remote-tracking refs after confirming remote deletion. Preserve
  `dev`, `main`, open-PR branches, active work and unmerged or unpushed commits.
- Never force-delete branches or worktrees to make cleanup pass. Inspect every
  worktree for tracked and untracked changes, and preserve required ignored files
  and validation evidence before removal. Archive Codex-managed worktrees through
  the app's archive tool when available. Keep the current PR's branch and worktree
  until merge or an explicit handoff; record any remaining cleanup at handoff.
- Inspect Docker context, container state, labels, image references and mounts
  before deleting anything. Remove task-owned disposable containers after tests
  finish, then their unused images and networks. Use exact names or IDs and avoid
  forced removal. A stopped container or an image unused by containers may still
  belong to a configured runtime or a retained release/rollback; verify ownership
  and purpose rather than treating Docker's reclaimable count as deletion authority.
- Preserve stable and other active Symphony runtimes, their configured images,
  rollback artifacts, authentication, state and workspace volumes. These protections
  apply even when containers are stopped. Remove a task-owned disposable volume only
  after verifying it contains no credentials, user work or required evidence. Never
  use blanket `docker system prune --all --volumes` or global container/image/volume
  prune commands as routine task cleanup on a shared daemon.
- Keep build caches bounded. Prefer task-specific builders or cache scopes and
  remove them when no longer needed. On a shared builder, inspect usage and active
  builds before an explicitly scoped cache cleanup with age/retention limits; do
  not remove another task's needed cache or run broad pruning during its build.
  Verify Docker usage and Git state after cleanup and report retained resources
  or cleanup blockers without bypassing the stable-installation protections.

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

## Direct development and validation under `elixir/`

These instructions apply to direct coding-agent development and documentation
validation under `elixir/`. They take precedence over conflicting generic
full-gate, toolchain setup, or runtime-only wording in the packaged
`elixir/AGENTS.md`.

- The workspace safety rules in `elixir/AGENTS.md` describe Symphony runtime
  product invariants. They do not prohibit direct coding-agent work in an
  isolated development checkout.
- Pure human-facing documentation changes follow the root documentation-only
  validation policy above. They do not require Elixir or application setup,
  `make all`, or application test suites. Code changes retain targeted tests
  while iterating and the applicable full required gates before handoff.
- Validate PR bodies, including documentation-only PRs, from the repository root
  with the dependency-free validator:

  ```bash
  python3 scripts/ci/check_pr_body.py --file /path/to/pr_body.md
  ```

  The existing `mix pr_body.check --file /path/to/pr_body.md` wrapper is optional
  when the Elixir environment is already available; documentation validation
  does not require installing that toolchain.
