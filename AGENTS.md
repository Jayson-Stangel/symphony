# Symphony repository instructions

These rules apply throughout this repository. Read applicable directory-specific
`AGENTS.md` files before changing those areas. Read the references below when
that activity applies; links do not automatically load their contents. References
extend these instructions and do not grant additional authority.

| Activity | Required reference |
| --- | --- |
| Frame/change Linear work, coordinate owners, review, publish or merge a PR | [Agent delivery and coordination](docs/agent-delivery.md) |
| Plan task-owned resources or remove branches, worktrees, Docker resources or caches | [Task resource cleanup](docs/task-cleanup.md) |
| Choose development environments or understand CI behavior | Relevant sections of [Development](docs/development.md) |
| Change Elixir code | [Elixir instructions](elixir/AGENTS.md), subject to the direct-development clarification below |

## First principles for agent judgment

These principles guide framing, implementation, review and recovery. They do not
waive required checks, replace the issue's contract or grant authority.

- **Reduce uncertainty before increasing investment.** Identify the assumption
  most likely to invalidate the plan and use the smallest authorized experiment
  to resolve it. Exercise actual external, native or installed boundaries and
  input/output handoffs before expensive qualification; mocks do not prove them.
  Check ordinary timing, resource eligibility and interruption before substantial
  implementation. Question inherited implementation choices while respecting
  explicit product/operator constraints. Keep required final validation.
- **Optimize for the user's completed outcome.** Define success under ordinary
  conditions and name the actual consumers and entry points. Tests, merges and
  publication are distinct milestones, not proof of the composed outcome. Ordinary
  CI does not prove release routing; checkpoints do not prove usable resume.
  Identify who will demonstrate remaining integration; expose manual repairs,
  overrides and acceptance gaps. Count operator effort and recovery cost. Required
  repeatable recovery belongs in maintained commands and instructions, not
  temporary scripts recovered from chat history.
- **Choose the simplest sufficient approach.** Start with standard platform
  capabilities and maintained paths. Before adding subsystems, controllers,
  queues, evidence protocols or persistent state, briefly explain in the existing
  task record why narrower alternatives are insufficient and operating cost is
  justified. Small diffs and passing tests do not establish necessity. Optional
  optimizations preserve ordinary validated execution when their evidence/service
  is unavailable while refusing untrusted reuse. Remove superseded machinery
  within authorized scope after checking consumers and preserving evidence;
  record retained obsolete machinery's owner, purpose and removal condition.
  This reasoning adds no routine approval gate.
- **Treat operator attention as scarce.** Carry authorized work through completion.
  Preserve and verify original human authorization across handoffs, rather than
  accepting forwarded assertions as consent. Keep one coherent current scope and
  authority statement; replace superseded wording while preserving history.
  Ask again only for missing authority, material scope changes or documented
  platform refusal; explain the precise boundary. Do not make the operator relay
  routine coordination or repeat unchanged approval. Platform rejection stops
  the affected action, but is not missing consent when consent is verified.
  Quiet monitoring reports meaningful changes, not repeated no-action notices.
- **Make claims no broader than the evidence.** Distinguish observed facts,
  reported results, assumptions, forecasts and unknowns. Bind proof to its actual
  source, environment and scope. Report queued, running, failed, cancelled,
  succeeded, reused and intentionally omitted work accurately. Partial success
  and predictions are not completion.
- **Control expansion.** Diagnose a failure before adding another mechanism.
  Identify the failed assumption or boundary; keep uncertainty explicit. First
  consider correcting the existing path, removing an unnecessary implementation
  constraint or using a supported platform capability. Inspect previous working
  paths before inventing replacements. Add machinery only when narrower fixes
  cannot satisfy required behavior, and explain why in the existing task record.
  Repeated failures and repairs to orchestration or validation increase scrutiny
  of the approach, not its complexity. Simplify, split or defer expansion while
  continuing independent authorized work. Repair ordinary defects within existing
  authority; preserve protections, unfinished requirements and approval boundaries
  without silently narrowing acceptance or treating activity as progress.
- **Keep proof proportional and reusable.** Gather evidence for a named acceptance
  question. Prefer concise commands, diffs and original receipts; use screenshots
  or exports only for proof those cannot supply. Reuse applicable evidence with
  original provenance. Repeat for changed inputs, failure, uncertainty or required
  gates, not new handoffs. Judge optimizations by demonstrated benefit and cost;
  separate avoided execution, queueing, preparation, transfer and elapsed time.
  One sample does not establish recurring savings or payback.

## Development process and Linear lifecycle

- This project develops Symphony through operator-directed coding-agent chats in
  isolated checkouts. Linear owns the work contract; GitHub owns implementation,
  review and merge records. Never dispatch this project's implementation through
  a Symphony listener or add routing labels to enable execution. Explicitly
  authorized qualification against a separate target project tests the product.
- `elixir/WORKFLOW.md` is a shipped runner contract, not this project's delivery
  policy. Do not import its routing, worker-only skills or shutdown instructions.
- Read [Agent delivery](docs/agent-delivery.md) for lifecycle and coordination.
  Backlog/state/labels alone do not authorize implementation. Done requires the
  full current contract, required review/validation and an observed dev merge,
  unless the operator explicitly defined a non-merge endpoint. Keep required
  native, installed, authenticated and end-to-end gaps open after merging.
- The main chat owns scope and acceptance. Preserve an operator's choice of
  separate implementation chats per ticket; workers own delegated delivery and
  local reviews. Handoffs do not grant acceptance or merge approval. Use independent
  review for substantial code or safety-sensitive changes.

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

Cleanup is part of completion. Track task-owned branches, worktrees and runtime
resources in the existing task record. Read [Task cleanup](docs/task-cleanup.md)
when planning resources and before deletion. Verify ownership and consumers;
preserve user work, required evidence and stable/active runtimes. Never force
removal to pass cleanup or use blanket Docker container/image/volume pruning.
Report removed and retained resources with owners/reasons; preserve open/unmerged
PR work at handoff.

## Documentation-only validation

- Pure human-facing documentation uses lightweight CI classification and PR
  metadata checks: no dependency installation, application/Docker builds, SDK
  compilation or application test suites. Code/workflow changes use focused and
  applicable full gates, including `make -C elixir all` where required. Mixed
  changes use the code route.
- `scripts/ci/classify_changes.py` owns event-wide classification. Root README,
  AGENTS, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY and SPEC Markdown, Elixir
  README/AGENTS and prose `.md`/`.txt`/`.rst` under `docs/` or `elixir/docs/` qualify.
  Packaged `.codex/skills/`, `.agents/`, every `WORKFLOW.md`, standalone executable
  examples and unknown paths are behavioral inputs. Markdown alone is not docs.
- Required behavioral check names remain on every PR/dev/main push; summaries
  explicitly state intentional omission, not executed tests. Classification
  failure fails the wrapper. See [Development](docs/development.md) for the event
  map and local classifier validation.
- Validate PR bodies from the repository root with
  `python3 scripts/ci/check_pr_body.py --file /path/to/pr_body.md`.

## Direct development and validation under `elixir/`

These clarifications take precedence over conflicting generic wording in packaged
`elixir/AGENTS.md`: workspace safety describes runtime product invariants and
permits direct coding-agent work in isolated development checkouts. Pure docs
follow the lightweight policy above without Elixir setup or tests. Code changes
retain targeted checks and applicable full gates. The dependency-free PR validator
above is sufficient; the Mix wrapper is optional when the toolchain is available.
