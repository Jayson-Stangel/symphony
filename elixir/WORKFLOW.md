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
  # Opt in to ordinary local clone-only bootstrap with:
  # clone_url: https://github.com/openai/symphony
  # Remove hooks.after_create below when enabling clone_url; they cannot coexist.
  # clone_url does not support worker.ssh_hosts. Custom setup keeps the hook below.
  # Clones publish atomically; interrupted unpublished stages permit fresh retries.
  # See README.md for retained staging directories and arbitrary-hook recovery.
hooks:
  after_create: |
    git clone --depth 1 https://github.com/openai/symphony .
    if command -v mise >/dev/null 2>&1; then
      cd elixir && mise trust && mise exec -- mix deps.get
    fi
  before_remove: |
    cd elixir && mise exec -- mix workspace.before_remove
# Optional Claude settings use the same workspace/hooks and scheduler contract.
# claude.model defaults to sonnet; any nonblank identifier up to 256 characters
# passes unchanged to the native runtime, which owns model support validation.
agent:
  # Shared by Codex and Claude; use 1 for serial execution.
  # Deprecated claude.max_concurrent_agents is accepted but ignored.
  # Pre-hello helper transport failures retry normally; they do not pause the project.
  # Healthy explicit auth/policy rechecks restore normal configured capacity.
  # A zero-worker recheck acknowledges the current qualified native profile;
  # automatic refresh/resume never accepts an unexpected profile change.
  max_concurrent_agents: 10
  max_turns: 20
codex:
  command: codex --config shell_environment_policy.inherit=all app-server
  model: gpt-5.5
  reasoning_effort: xhigh
  approval_policy: never
  thread_sandbox: workspace-write
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

## Evidence for review

Record the tested revision, commands, observed results and material limitations in
the workpad. Link durable CI runs and PR evidence. Publish acceptance-relevant
screenshots, recordings or other artifacts when needed to assess the change or
when the target repository or ticket requires them. Temporary logs do not all need
uploading. A local path is not delivered evidence if reviewers need the artifact.

Inspect artifacts for secrets/private data before publishing; sanitize or withhold
unsafe originals and explain the evidence limit. Use the `symphony-linear` upload
procedure when storing files in Linear, verify the upload succeeded, and read back
the issue link. Do not claim evidence was delivered unless it is accessible.
Follow any stricter target-repository or ticket publication requirements. Missing
required acceptance evidence prevents handoff; an optional upload failure does
not by itself make implementation blocked.

## App runtime validation (required)

For web application changes, start the app using the target repository's documented
command, wait for it to be ready, and exercise the changed user flow in a browser.
Verify the expected result and capture screenshots in the issue workspace. Record
the commands and results in the workpad and publish the screenshots using
`Evidence for review`. Follow repository-specific media upload
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
- `In Progress` -> implementation and required validation underway; resolve available actionable feedback before handoff.
- `Blocked` -> cannot proceed because of an external blocker; record the blocker and exact unblock action in the workpad. Do not dispatch until a human moves it to `Todo`, `In Progress`, or `Rework`.
- `Human Review` -> validated PR is attached and ready for human assessment. Record any pending external review; end the worker turn while waiting.
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
   - `Human Review` -> end the turn; do not poll or occupy a worker while awaiting review.
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
6. Recheck the current head and available feedback after fixes. Record which reviews completed and which remain pending; an empty comment list does not establish completed review.

## Review handoff

Follow the target repository's required checks and review obligations, including
its AGENTS.md and ticket validation, not only GitHub branch-protection settings. Address
available actionable feedback through the sweep above. Do not impose a separate
requirement that every installed bot complete a review unless the repository or
ticket explicitly requires it. Record the reviewed head and pending external
reviews accurately, including each required reviewer or process and the source
of that requirement in the existing workpad; handoff is not approval or permission
to merge.

After implementation, required validation and available feedback are complete,
move to `Human Review` and end the turn. The existing tracker flow resumes work
when the operator moves the issue to `Rework` or approves `Merging`. Do not add a
review watcher, keep a model session polling, or mark ordinary review latency as
an external blocker. Required reviews still prevent merge until satisfied.

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
    - Follow Review handoff for the current head; record completed and pending external reviews.
    - Confirm required PR checks are passing after the latest changes; record pending optional checks/reviews.
    - Confirm every required ticket-provided validation/test-plan item is explicitly marked complete in the workpad.
    - Resolve available actionable findings and pass required checks; do not hold a worker for optional bot latency.
    - Re-open and refresh the workpad before state transition so `Plan`, `Acceptance Criteria`, and `Validation` exactly match completed work.
12. Only then move issue to `Human Review` and end the turn.
    - If required work cannot be completed because of an external blocker, use `Blocked` with the blocker brief and explicit unblock actions; do not use `Human Review`.
13. For `Todo` tickets that already had a PR attached at kickoff:
    - Ensure all existing PR feedback was reviewed and resolved, including inline review comments (code changes or explicit, justified pushback response).
    - Ensure branch was pushed with any required updates.
    - Recheck the current head and available feedback after fixes, and record any pending external review.
    - Then move to `Human Review` and end the turn.

## Step 3: Human Review and merge handling

1. In `Human Review`, end the turn without polling, coding or changing ticket content.
2. The operator uses the existing tracker flow to request `Rework` or approve `Merging`.
3. On `Rework`, inspect current feedback and follow the rework flow.
4. On `Merging`, open and follow `symphony-land`, preserving all required checks and
   human approval boundaries. Do not call `gh pr merge` directly. Handoff or pending
   bot review is not merge approval. Verify each pending required review from the
   workpad against the current head; if required external review or CI is still
   pending, record it, return to `Human Review` and end the turn.
5. After merge is complete, move the issue to `Done`.

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
- Acceptance-relevant evidence is recorded or durably linked; artifacts required for review are accessible and safe.
- Validation/tests are green for the latest commit.
- PR feedback sweep is complete and no actionable comments remain.
- The current head, completed reviews, resolved feedback and pending external reviews are accurately recorded.
- Required PR checks are green, branch is pushed, and PR is linked on the issue.
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
- In `Human Review`, end the turn; do not keep a model session waiting or polling.
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
`max_concurrent_agents` (1), `max_agentic_turns` (1–100; native allowance yields
after session cleanup to ordinary tracker-revalidated continuation),
`read_timeout_ms` (up to 60000), `turn_timeout_ms` (up to 3600000), and
`stall_timeout_ms` are accepted. Authentication, commands and raw SDK settings
are runtime-owned. Select using the isolated launcher's --agent claude; provider
selection is fixed across workflow reloads. -->

Installed updates require a clean local main tracking a configured remote's main. The
launcher's --config-dir selects derived runner storage unless SYMPHONY_RUNNER_HOME
is explicit. Transition advisory locks use the original lock pathname, excluding already-loaded
older launchers. They release after a crash and any in-flight command
exits; unknown legacy ownerless locks remain refused for operator inspection.

An installed update retries idempotent setup even when Git is current. Combined source
and runner updates hand completion to the newly installed implementation at the exact
fetched revision, preserving resolved paths and target without fetching again.

Installed source updates hold shared-checkout ownership across fetch, exec handoff, setup
and transition. Finish already-running older launchers before source-only adoption; manual
Git operations and older launchers do not participate in that ownership. Development-runner
operations retain their isolated-checkout behavior.
