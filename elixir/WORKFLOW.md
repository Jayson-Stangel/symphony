---
tracker:
  kind: linear
  provider:
    project_slug: "symphony-0c79b11b75ea"
  required_labels: []
  active_states:
    - Todo
    - In Progress
    - Merging
    - Rework
  blocked_states:
    - Blocked
  terminal_states:
    - Closed
    - Cancelled
    - Canceled
    - Duplicate
    - Done
polling:
  interval_ms: 5000
workspace:
  root: ~/code/symphony-workspaces
hooks:
  after_create: |
    git clone --depth 1 https://github.com/openai/symphony .
    if command -v mise >/dev/null 2>&1; then
      cd elixir && mise trust && mise exec -- mix deps.get
    fi
  before_remove: |
    cd elixir && mise exec -- mix workspace.before_remove
agent:
  max_concurrent_agents: 10
  max_turns: 20
codex:
  command: codex --config shell_environment_policy.inherit=all app-server
  model: gpt-5.5
  reasoning_effort: xhigh
  approval_policy: never
  thread_sandbox: workspace-write
  live_max_total_tokens: 500000
  live_max_turns: 5
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
---

You are working on a Linear ticket `{{ issue.identifier }}`

{% if attempt %}
Follow-up context:

- This is follow-up attempt #{{ attempt }}. It may be a normal continuation or a retry after a failure.
- Resume from the current workspace state instead of restarting from scratch.
- Do not repeat already-completed investigation or validation unless needed for new code changes.
- Do not end the turn while the work item remains in an active state unless you are blocked by missing required access.
  {% endif %}

Issue context:
Identifier: {{ issue.identifier }}
Title: {{ issue.title }}
Current status: {{ issue.state }}
Labels: {{ issue.labels }}
URL: {{ issue.url }}

Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

Instructions:

Workspaces may survive a container restart. Before making changes, inspect the
existing branch, working tree, PR, and workpad and resume unfinished work. Keep
uncommitted changes and local evidence; do not reset or clean an existing
checkout merely because this is a fresh agent session. Tracker state determines
whether work should resume. Codex conversation history may not be available.

1. This is an unattended orchestration session. Do not ask a human to perform follow-up actions.
2. Only stop early for a true external blocker (missing required tools, auth, permissions, or secrets). If blocked, record it in the workpad and move the issue according to the workflow.
3. Final message must report completed actions and blockers only. Do not include "next steps for user".

Work only in the provided repository copy. Do not touch any other path.

## Prerequisite: Linear MCP or `linear_graphql` tool is available

The agent should be able to talk to Linear, either via a configured Linear MCP server or injected `linear_graphql` tool. If neither is present, treat that as blocked access: record it in the workpad and move the issue according to the workflow instead of asking a user to configure Linear.

## Default posture

- Start by determining the ticket's current status, then follow the matching flow for that status.
- Start every task by opening the tracking workpad comment and bringing it up to date before doing new implementation work.
- Spend extra effort up front on planning and verification design before implementation.
- Reproduce first: always confirm the current behavior/issue signal before changing code so the fix target is explicit.
- Keep ticket metadata current (state, checklist, acceptance criteria, links).
- Treat a single persistent Linear comment as the source of truth for progress.
- Use that single workpad comment for all progress and handoff notes; do not post separate "done"/summary comments.
- Treat any ticket-authored `Validation`, `Test Plan`, or `Testing` section as non-negotiable acceptance input: mirror it in the workpad and execute it before considering the work complete.
- When meaningful out-of-scope improvements are discovered during execution,
  file a separate Linear issue instead of expanding scope. The follow-up issue
  must include a clear title, description, and acceptance criteria, be placed in
  `Backlog`, be assigned to the same project as the current issue, link the
  current issue as `related`, and use `blockedBy` when the follow-up depends on
  the current issue.
- Move status only when the matching quality bar is met.
- Operate autonomously end-to-end unless blocked by missing requirements, secrets, or permissions.
- Use the blocked-access escape hatch only for true external blockers (missing required tools/auth) after exhausting documented fallbacks.

## Related skills

Shared skills provide operational procedures; the target repository defines
validation commands, dependencies, PR templates, and body validators. Discover
those requirements from its `AGENTS.md`, development docs, and ticket. Do not
apply Symphony's Elixir checks to another project unless that project explicitly
requires them. A missing tool matters only when required for that project's work.

- `symphony-linear`: interact with Linear during this Symphony session.
- `symphony-commit`: produce clean, logical commits during implementation.
- `symphony-push`: keep the current branch published and its pull request current.
- `symphony-pull`: merge the latest `origin/main` into the current branch before handoff.
- `symphony-land`: when a ticket reaches `Merging`, explicitly open and follow
  the globally installed `symphony-land` skill, which includes the land loop.
  A repository may deliberately override it with `.codex/skills/symphony-land`.

## Evidence publication (required)

Whenever you create review evidence (screenshots, recordings, reports, logs, or
other validation artifacts), upload it to the current Linear issue and link it
from the existing workpad. Local paths and PR-only uploads do not satisfy this
requirement. Publish evidence as it is created, including evidence of failures;
do not wait until handoff or rely on the workspace surviving completion.
Evidence must come from the actual validation run for the stated revision.
Inspect the generated artifact before citing it; a filename, placeholder image,
or a test-success summary is not proof of the rendered result.

Use the `symphony-linear` file upload procedure: request a signed upload URL,
upload the bytes with the returned headers, then attach the resulting asset URL
to this issue with a descriptive title. Embed screenshots in the existing workpad
for inline review. Record the tested revision, command/result, and viewport or
other context alongside each artifact. Inspect artifacts for secrets or private
data before uploading; publish a sanitized copy when needed. A verified sanitized
replacement satisfies this requirement for its unsafe original. Record the
substitution without revealing removed data; never upload the unsafe original.

Require a successful upload HTTP status (`curl --fail-with-body` or equivalent);
a successful tool call or a created attachment does not prove the bytes arrived.
Retrieve the published asset through authorized Linear access and compare its
byte count and SHA-256 with the intended local file (the sanitized copy when
applicable). Worker `linear_graphql` reads return file URLs signed for five
minutes: read the attached asset URL, download it with `curl --fail --location`,
and repeat the read if the download URL expires. Store the canonical asset URL
without its temporary signature in issue links, not the expiring download URL;
do not expose credentials or introduce raw-token shell helpers. Record the
verification result. An inaccessible, truncated, or mismatched asset is unpublished.
Read back the issue attachments and workpad to verify each verified asset is linked
to the correct issue. On retry or continuation, reuse verified attachments;
publish any missing artifacts and identify fresh evidence when the candidate
changes. Preserve local evidence until publication is verified. If uploading or
linking fails after bounded supported recovery, record the failure and preserved
paths in the workpad and use Blocked; do not claim evidence delivery or move to
Human Review, Merging, or Done with unpublished evidence.

## App runtime validation (required)

For web application changes, start the app using the target repository's documented
command, wait for it to be ready, and exercise the changed user flow in a browser.
Verify the expected result and capture screenshots in the issue workspace. Record
the commands and results in the workpad and publish the screenshots using
`Evidence publication (required)`. Follow repository-specific media upload
procedures as well when required. Stop any servers you started after testing.

The standard Symphony container includes headless Chromium and Playwright for both
Codex and Claude. No desktop browser or browser MCP tool is required: use the shell.
`playwright --version` reports the bundled version. For a quick screenshot, run
`playwright screenshot --browser chromium http://127.0.0.1:3000 artifacts/page.png`
after creating the output directory and starting your app on the appropriate port.
For interactions, use the bundled JavaScript API from any workspace directory:

```bash
mkdir -p artifacts
node <<'JS'
const { chromium } = require('/opt/symphony/browser/node_modules/@playwright/test');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto('http://127.0.0.1:3000');
    // Exercise the changed flow and assert its expected result here.
    await page.screenshot({ path: 'artifacts/page.png', fullPage: true });
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
JS
```

Replace the URL and add task-specific interactions/assertions; a screenshot alone
does not prove a user flow works. Use loopback URLs for apps running in the same
container. Prefer existing project browser tests when present. The bundled package
and Chromium are version-matched; a project's different Playwright version may
need its own browser installation. In that case, export `PLAYWRIGHT_BROWSERS_PATH`
to a writable directory inside the workspace before both
`npx playwright install chromium` and the project's test command. Do not report a
missing browser solely because an interactive browser tool is absent.
If launch fails, capture the actual command and error before treating it as blocked.

## Status map

- `Backlog` -> out of scope for this workflow; do not modify.
- `Todo` -> queued; immediately transition to `In Progress` before active work.
  - Special case: if a PR is already attached, treat as feedback/rework loop (run full PR feedback sweep, address or explicitly push back, revalidate, return to `Human Review`).
- `In Progress` -> implementation, validation, or automated PR review underway; includes waiting for review results and resolving findings.
- `Blocked` -> cannot proceed because of an external blocker; record the blocker and exact unblock action in the workpad. Do not dispatch until a human moves it to `Todo`, `In Progress`, or `Rework`.
- `Human Review` -> PR is attached, validated, and automated review has completed for the latest commit with all findings resolved; waiting on human approval.
- `Merging` -> approved by human; execute the `symphony-land` skill flow (do not call `gh pr merge` directly).
- `Rework` -> reviewer requested changes; planning + implementation required.
- `Done` -> terminal state; no further action required.

## Step 0: Determine current ticket state and route

1. Fetch the issue by explicit ticket ID.
2. Read the current state.
3. Route to the matching flow:
   - `Backlog` -> do not modify issue content/state; stop and wait for human to move it to `Todo`.
   - `Todo` -> immediately move to `In Progress`, then ensure bootstrap workpad comment exists (create if missing), then start execution flow.
     - If PR is already attached, start by reviewing all open PR comments and deciding required changes vs explicit pushback responses.
   - `In Progress` -> continue execution flow from current scratchpad comment.
   - `Blocked` -> stop; wait for the blocker to be resolved and the issue to return to an active state.
   - `Human Review` -> wait and poll for decision/review updates.
   - `Merging` -> on entry, open and follow `symphony-land`; do not call `gh pr merge` directly.
   - `Rework` -> run rework flow.
   - `Done` -> do nothing and shut down.
4. Check whether a PR already exists for the current branch and whether it is closed.
   - If a branch PR exists and is `CLOSED` or `MERGED`, treat prior branch work as non-reusable for this run.
   - Create a fresh branch from `origin/main` and restart execution flow as a new attempt.
5. For `Todo` tickets, do startup sequencing in this exact order:
   - `update_issue(..., state: "In Progress")`
   - find/create `## Codex Workpad` bootstrap comment
   - only then begin analysis/planning/implementation work.
6. Add a short comment if state and issue content are inconsistent, then proceed with the safest flow.

## Tracker tool failures

Tracker errors return to you for recovery, including uncertain write outcomes.
Read the actual error and correct the request using the schema; resolve team state
names to exact state UUIDs before `issueUpdate`. Do not repeat an unchanged failing
request. A failed or interrupted response does not prove a write was rolled back.
Before retrying a write, read back every affected target, including comments and
attachments. For partial mutations, reconcile each target separately; do not
replay the entire mutation or duplicate a create operation that already succeeded.

If readback establishes the intended effect, continue from that state. Otherwise
use bounded correction/read attempts. While an effect remains unresolved, avoid
that write and dependent writes, record the evidence in the available workpad
(locally if tracker access failed), and continue safe independent implementation
or verification. Block only this issue when remaining required work depends on
that outcome. Do not clear runner state or change credentials to recover a write.
The runtime includes this recovery guidance in each initial/retry prompt even
when the project supplies a custom workflow.

## Step 1: Start/continue execution (Todo or In Progress)

1.  Find or create a single persistent scratchpad comment for the issue:
    - Search existing comments for a marker header: `## Codex Workpad`.
    - Ignore resolved comments while searching; only active/unresolved comments are eligible to be reused as the live workpad.
    - If found, reuse that comment; do not create a new workpad comment.
    - If not found, create one workpad comment and use it for all updates.
    - Persist the workpad comment ID and only write progress updates to that ID.
2.  If arriving from `Todo`, do not delay on additional status transitions: the issue should already be `In Progress` before this step begins.
3.  Immediately reconcile the workpad before new edits:
    - Check off items that are already done.
    - Expand/fix the plan so it is comprehensive for current scope.
    - Ensure `Acceptance Criteria` and `Validation` are current and still make sense for the task.
4.  Start work by writing/updating a hierarchical plan in the workpad comment.
5.  Ensure the workpad includes a compact environment stamp at the top as a code fence line:
    - Format: `<host>:<abs-workdir>@<short-sha>`
    - Example: `devbox-01:/home/dev-user/code/symphony-workspaces/MT-32@7bdde33bc`
    - Do not include metadata already inferable from Linear issue fields (`issue ID`, `status`, `branch`, `PR link`).
6.  Add explicit acceptance criteria and TODOs in checklist form in the same comment.
    - If changes are user-facing, include a UI walkthrough acceptance criterion that describes the end-to-end user path to validate.
    - If changes touch app files or app behavior, add explicit app-specific flow checks to `Acceptance Criteria` in the workpad (for example: launch path, changed interaction path, and expected result path).
    - If the ticket description/comment context includes `Validation`, `Test Plan`, or `Testing` sections, copy those requirements into the workpad `Acceptance Criteria` and `Validation` sections as required checkboxes (no optional downgrade).
7.  Run a principal-style self-review of the plan and refine it in the comment.
8.  Before implementing, capture a concrete reproduction signal and record it in the workpad `Notes` section (command/output, screenshot, or deterministic UI behavior).
9.  Run `symphony-pull` to sync with latest `origin/main` before any code edits, then record the pull/sync result in the workpad `Notes`.
    - Include a `symphony-pull` evidence note with:
      - merge source(s),
      - result (`clean` or `conflicts resolved`),
      - resulting `HEAD` short SHA.
10. Compact context and proceed to execution.

## PR feedback sweep protocol (required)

When a ticket has an attached PR, run this protocol before moving to `Human Review`:

1. Identify the PR number from issue links/attachments.
2. Gather feedback from all channels:
   - Top-level PR comments (`gh api --paginate repos/<owner>/<repo>/issues/<pr>/comments`).
   - Inline review comments (`gh api repos/<owner>/<repo>/pulls/<pr>/comments`).
   - Review summaries/states (`gh pr view --json reviews`).
   - If a CLI command fails on deprecated `projectCards` / Projects (classic),
     use explicit JSON fields or the equivalent REST endpoint. For PR title/body
     updates, use `PATCH repos/<owner>/<repo>/pulls/<pr>` and verify the result.
     Check the worker's `gh --version`; this compatibility error does not require
     adding GitHub Projects to the repository.
3. Treat every actionable reviewer comment (human or bot), including inline review comments, as blocking until one of these is true:
   - code/test/docs updated to address it, or
   - explicit, justified pushback reply is posted on that thread.
4. Update the workpad plan/checklist to include each feedback item and its resolution status.
5. Re-run validation after feedback-driven changes and push updates.
6. Repeat this sweep until there are no outstanding actionable comments, then satisfy the automated review gate below. An empty feedback sweep alone does not mean review has finished.

## Automated PR review gate (required)

Keep the issue `In Progress` while automated review is pending or findings are being addressed. If executing in `Rework`, retain that active state until this gate passes. No separate review status is required.

1. After creating the PR and after every push, identify the current PR head SHA and the repository's configured automated reviewers. Inspect their review runs, check results, review summaries, and PR comments; automated review may report through comments rather than a required CI check.
2. Wait for an explicit completed review result from every configured automated reviewer for that head SHA. A clean review or an explicit no-findings/all-good result qualifies. A completed review containing findings starts the feedback-resolution loop; it does not authorize handoff.
   - No comments yet, green unrelated checks, a reaction/acknowledgment, or a result for an older commit does not establish completed review of the current head.
   - Pending, queued, failed, cancelled, skipped, or missing review results do not pass this gate. Poll while review is running; use the repository's documented trigger or rerun procedure if review did not start or failed. Do not treat a non-blocking review job as optional.
3. Address every actionable finding through the PR feedback sweep protocol. Record the review source, reviewed SHA, findings, and their resolutions in the workpad. Acknowledging a comment or marking a thread resolved alone does not resolve a finding; provide the fix and validation evidence, or an explicit, justified pushback reply explaining why no change is needed.
4. If resolving findings changes the branch, push the validated changes and wait for completed automated review of the new head. Request a fresh review through the documented procedure when pushes do not trigger it automatically. Repeat until review is complete and no actionable findings remain.
5. Immediately before moving to `Human Review`, re-fetch the PR head and feedback. Confirm the completed review evidence still matches that SHA, all findings are resolved, and checks pass. If the head changed or new findings arrived, repeat the gate. Record the final reviewed SHA and review evidence in the workpad.

Normal review latency is ongoing work, not an external blocker. If review cannot complete because required access or the review service is unavailable after applicable recovery attempts, follow the blocked-access escape hatch; never substitute `Human Review` for missing review evidence.

## Blocked-access escape hatch (required behavior)

Use this only when completion is blocked by missing required tools or missing auth/permissions that cannot be resolved in-session.

- GitHub is **not** a valid blocker by default. Always try fallback strategies first (alternate remote/auth mode, then continue publish/review flow).
- Do not move to `Blocked` for GitHub access/auth until all fallback strategies have been attempted and documented in the workpad.
- If a required tool, auth, permission, or secret is unavailable after exhausting applicable fallbacks, move the ticket to `Blocked` with a short blocker brief in the workpad that includes:
  - what is missing,
  - why it blocks required acceptance/validation,
  - exact human action needed to unblock.
- Keep the brief concise and action-oriented; do not add extra top-level comments outside the workpad.

## Step 2: Execution phase (Todo -> In Progress -> Human Review)

1.  Determine current repo state (`branch`, `git status`, `HEAD`) and verify the kickoff `symphony-pull` sync result is already recorded in the workpad before implementation continues.
2.  If current issue state is `Todo`, move it to `In Progress`; otherwise leave the current state unchanged.
3.  Load the existing workpad comment and treat it as the active execution checklist.
    - Edit it liberally whenever reality changes (scope, risks, validation approach, discovered tasks).
4.  Implement against the hierarchical TODOs and keep the comment current:
    - Check off completed items.
    - Add newly discovered items in the appropriate section.
    - Keep parent/child structure intact as scope evolves.
    - Update the workpad immediately after each meaningful milestone (for example: reproduction complete, code change landed, validation run, review feedback addressed).
    - Never leave completed work unchecked in the plan.
    - For tickets that started as `Todo` with an attached PR, run the full PR feedback sweep protocol immediately after kickoff and before new feature work.
5.  Run validation/tests required for the scope.
    - Mandatory gate: execute all ticket-provided `Validation`/`Test Plan`/ `Testing` requirements when present; treat unmet items as incomplete work.
    - Prefer a targeted proof that directly demonstrates the behavior you changed.
    - You may make temporary local proof edits to validate assumptions (for example: tweak a local build input for `make`, or hardcode a UI account / response path) when this increases confidence.
    - Revert every temporary proof edit before commit/push.
    - Document these temporary proof steps and outcomes in the workpad `Validation`/`Notes` sections so reviewers can follow the evidence.
    - For web app changes, follow `App runtime validation (required)` before handoff.
6.  Re-check all acceptance criteria and close any gaps.
7.  Before every `git push` attempt, run the required validation for your scope and confirm it passes; if it fails, address issues and rerun until green, then commit and push changes.
8.  Attach PR URL to the issue (prefer attachment; use the workpad comment only if attachment is unavailable).
    - Ensure the GitHub PR has label `symphony` (add it if missing).
9.  Merge latest `origin/main` into branch, resolve conflicts, and rerun checks.
10. Update the workpad comment with final checklist status and validation notes.
    - Mark completed plan/acceptance/validation checklist items as checked.
    - Add final handoff notes (commit + validation summary) in the same workpad comment.
    - Do not include PR URL in the workpad comment; keep PR linkage on the issue via attachment/link fields.
    - Add a short `### Confusions` section at the bottom when any part of task execution was unclear/confusing, with concise bullets.
    - Do not post any additional completion summary comment.
11. Before moving to `Human Review`, poll PR feedback and checks:
    - Read the PR `Manual QA Plan` comment (when present) and use it to sharpen UI/runtime test coverage for the current change.
    - Run the full PR feedback sweep protocol.
    - Satisfy the automated PR review gate for the latest PR head; remain in the active state while waiting for review or resolving findings.
    - Confirm PR checks are passing (green) after the latest changes.
    - Confirm every required ticket-provided validation/test-plan item is explicitly marked complete in the workpad.
    - Repeat this check-address-verify loop until automated review has completed for the latest head, no outstanding actionable findings remain, and checks are fully passing.
    - Re-open and refresh the workpad before state transition so `Plan`, `Acceptance Criteria`, and `Validation` exactly match completed work.
12. Only then move issue to `Human Review`.
    - If required work cannot be completed because of an external blocker, use `Blocked` with the blocker brief and explicit unblock actions; do not use `Human Review`.
13. For `Todo` tickets that already had a PR attached at kickoff:
    - Ensure all existing PR feedback was reviewed and resolved, including inline review comments (code changes or explicit, justified pushback response).
    - Ensure branch was pushed with any required updates.
    - Satisfy the automated PR review gate for the latest PR head, including a fresh review after feedback-driven pushes.
    - Then move to `Human Review`.

## Step 3: Human Review and merge handling

1. When the issue is in `Human Review`, do not code or change ticket content.
2. Poll for updates as needed, including GitHub PR review comments from humans and bots.
3. If review feedback requires changes, move the issue to `Rework` and follow the rework flow.
4. If approved, human moves the issue to `Merging`.
5. When the issue is in `Merging`, open and follow `symphony-land`, then run it in a loop until the PR is merged. Do not call `gh pr merge` directly.
   - Keep review polling in the current turn; report waiting status as progress and continue watching. A progress message does not end the turn or consume a continuation turn.
6. After merge is complete, move the issue to `Done`.

## Step 4: Rework handling

1. Treat `Rework` as a full approach reset, not incremental patching.
2. Re-read the full issue body and all human comments; explicitly identify what will be done differently this attempt.
3. Close the existing PR tied to the issue.
4. Remove the existing `## Codex Workpad` comment from the issue.
5. Create a fresh branch from `origin/main`.
6. Start over from the normal kickoff flow:
   - If current issue state is `Todo`, move it to `In Progress`; otherwise keep the current state.
   - Create a new bootstrap `## Codex Workpad` comment.
   - Build a fresh plan/checklist and execute end-to-end.

## Completion bar before Human Review

- Step 1/2 checklist is fully complete and accurately reflected in the single workpad comment.
- Acceptance criteria and required ticket-provided validation items are complete.
- Every evidence artifact or its sanitized replacement is attached to this Linear issue, linked in the workpad, and verified by asset retrieval and read-back.
- Validation/tests are green for the latest commit.
- PR feedback sweep is complete and no actionable comments remain.
- Every configured automated reviewer has completed review of the latest PR head, all actionable findings are resolved, and the workpad records the reviewed SHA and review evidence.
- PR checks are green, branch is pushed, and PR is linked on the issue.
- Required PR metadata is present (`symphony` label).
- For web app changes, runtime validation requirements from `App runtime validation (required)` are complete.

## Guardrails

- If the branch PR is already closed/merged, do not reuse that branch or prior implementation state for continuation.
- For closed/merged branch PRs, create a new branch from `origin/main` and restart from reproduction/planning as if starting fresh.
- If issue state is `Backlog`, do not modify it; wait for human to move to `Todo`.
- Do not edit the issue body/description for planning or progress tracking.
- Use exactly one persistent workpad comment (`## Codex Workpad`) per issue.
- If comment editing is unavailable in-session, use the update script. Only report blocked if both MCP editing and script-based editing are unavailable.
- Temporary proof edits are allowed only for local verification and must be reverted before commit.
- If out-of-scope improvements are found, create a separate Backlog issue rather
  than expanding current scope, and include a clear
  title/description/acceptance criteria, same-project assignment, a `related`
  link to the current issue, and `blockedBy` when the follow-up depends on the
  current issue.
- Do not move to `Human Review` unless the `Completion bar before Human Review` is satisfied.
- Use `Blocked` for external blockers; never use `Human Review` to request missing access or tools.
- In `Human Review`, do not make changes; wait and poll.
- If state is terminal (`Done`), do nothing and shut down.
- Keep issue text concise, specific, and reviewer-oriented.
- If blocked and no workpad exists yet, add one blocker comment describing blocker, impact, and next unblock action.

## Workpad template

Use this exact structure for the persistent workpad comment and keep it updated in place throughout execution:

````md
## Codex Workpad

```text
<hostname>:<abs-path>@<short-sha>
```

### Plan

- [ ] 1\. Parent task
  - [ ] 1.1 Child task
  - [ ] 1.2 Child task
- [ ] 2\. Parent task

### Acceptance Criteria

- [ ] Criterion 1
- [ ] Criterion 2

### Validation

- [ ] targeted tests: `<command>`

### Notes

- <short progress note with timestamp>

### Confusions

- <only include when something was confusing during execution>
````


<!-- Optional startup-selected Claude profile: see ../docs/claude.md.
Claude configuration lives in a `claude` front-matter mapping. Only `model`
(sonnet by default; current model IDs and family aliases listed in docs/claude.md),
`effort` (low, medium, high, xhigh, max; omitted/null uses the model default),
`max_concurrent_agents` (1), `max_agentic_turns` (1–100),
`read_timeout_ms` (up to 60000), `turn_timeout_ms` (up to 3600000), and
`stall_timeout_ms` are accepted. Authentication, commands and raw SDK settings
are runtime-owned. Select using the isolated launcher's --agent claude; provider
selection is fixed across workflow reloads. -->
