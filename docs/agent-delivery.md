# Agent delivery and coordination

Read before creating or changing a Linear work contract or lifecycle state,
coordinating implementation owners, handing off review, or publishing or merging
a pull request. These procedures extend [AGENTS.md](../AGENTS.md); they do not
grant execution or merge authority.

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
- Use `Agent Review` when an implementation chat or subagent hands work to the
  accepting main chat for review. Use `Rework` when that review returns actionable
  findings for fixes; return to `Agent Review` with refreshed evidence after
  addressing them.
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
  [production approval boundaries](../AGENTS.md#explicit-approval-for-production).

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
  The [production approval rules](../AGENTS.md#explicit-approval-for-production)
  still apply in full.
- A freeze must name the affected inputs or resource, protected operation, owner,
  reason and release condition in the existing workpad. Hold only actions that
  can invalidate that operation; allow independent reads, local work and unrelated
  publication. A pinned immutable run is not invalidated by a ref moving unless
  its actual admission or source policy depends on that ref remaining unchanged.
  Release the hold when its stated condition is met; do not make serial runner
  use a repository-wide source-push freeze without a demonstrated dependency.
- The main chat owns the operator's request, current Linear contract, scope,
  acceptance decisions and final handoff. In coordinated work, this is the
  coordinator; implementation chats own their delegated delivery and local
  reviews, while the coordinator accepts the combined result. Even within a broad
  goal, choose a bounded next delivery with an observable acceptance endpoint and
  stop condition; discoveries do not automatically expand it. Delegate independent
  development and testing with explicit ownership, base, deliverable, proof and
  action limits.
  Use separate worktrees where needed; avoid competing writes and duplicate
  qualification. Small documentation or administrative changes may be handled
  directly without an unnecessary delegation cycle.
- Keep the coordinator focused on scope, dependencies, acceptance and current
  authority. When the operator selects separate implementation chats per ticket,
  preserve that ownership model across handoffs instead of silently moving the
  work into the coordinator's subagents. Send meaningful decisions, failures and
  completed outcomes; when monitoring is meant to be quiet, do not repeatedly
  announce unchanged state or that no operator action is needed.
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
