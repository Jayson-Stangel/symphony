---
tracker:
  kind: linear
  provider:
    project_slug: "REPLACE_WITH_LINEAR_PROJECT_SLUG"
    api_key: $LINEAR_API_KEY
  required_labels:
    - "Symphony Agent Review Pilot"
  active_states:
    - Todo
    - In Progress
    - Agent Review
    - Rework
  blocked_states:
    - Blocked
  terminal_states:
    - Closed
    - Canceled
    - Cancelled
    - Duplicate
    - Done
workspace:
  root: /workspaces
  clone_url: https://github.com/OWNER/REPOSITORY.git
agent:
  max_concurrent_agents: 1
  fresh_session_on_enter:
    - Agent Review
    - Rework
codex:
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
---

# Independent Agent Review

Linear issue {{ issue.identifier }} is shown in {{ issue.state }}.
Read the current contract and comments at {{ issue.url }}.
{% if attempt %}
This is runner attempt {{ attempt }}. Reconcile the workpad and remote state
before resuming. Runner attempts are separate from review rounds.
{% endif %}

## Rules for every session

- Use the Linear tool for this issue and GitHub for its one candidate PR.
  Read live Linear state, repository instructions, and the existing
  `## Agent Workpad` comment before acting. Use the live state to select the role
  below. If the role has changed since dispatch, end the turn so Symphony can
  reconcile it. End the turn in any state outside the four active states.
- Reuse the issue workspace and preserve branch, files and evidence. Keep one
  workpad. Only development may initialize it before the first handoff. Re-read
  the comment before updating, preserve earlier handoffs, round records and
  findings, and verify the update by reading it back. Duplicate or inconsistent
  records require operator reconciliation.
- Immediately before each workpad, issue or GitHub write, check live routing,
  the expected active state, issue contract, workpad and relevant PR fields.
  If they changed, stop the planned action and reconcile from current evidence.
  Use exact team state IDs and read back transitions. After an uncertain write,
  inspect its effect before retrying. A move to Blocked or an inactive state
  ends worker writes. Exit after every cross-role handoff, even if the next
  role is active.
- If a required input is missing, inconsistent, or inaccessible and the current
  role cannot safely repair it, record the cause and exact unblock action in the
  workpad and move the issue to Blocked if the tool and live state allow. Record
  it locally if the workpad is unavailable. If safe writes are impossible, stop
  and leave the unresolved action for the operator. Never merge, close or
  approve a PR, move the issue to Done, or override a human state change.

## Todo and In Progress — development

1. In Todo, move to In Progress and read back the state before implementing.
   Create or recover the single workpad. Verify the target repository and
   integration branch from repository policy and issue scope. Implement, run
   required validation and publish the authorized feature branch and PR.
2. Append a numbered candidate handoff to the workpad. Record the current issue
   contract and acceptance checklist, PR URL and number, target repository and
   base branch/SHA, source repository and branch, and full published head SHA.
   Record validation commands, outcomes and accessible evidence bound to the
   tested revision, unresolved items and required external checks or reviews.
   Distinguish merge-time human obligations from evidence needed for independent
   review. Resume the existing handoff if publication was already recorded and
   its inputs still match.
3. Confirm the PR is open and its live base and head match the handoff. After
   reading back the workpad, move to Agent Review and end the turn.

## Agent Review — independent assessment

1. Verify the live issue contract and open PR identity, source repository and
   branch, base branch/SHA and full head SHA against the latest workpad handoff.
   A mismatch observed at any point invalidates that handoff and any verdict
   on it, even if the ref later returns to its original SHA. When the state
   and workpad still allow it, record the invalidation, expected and observed
   fields and the required operator action. Retain the old records. Leave the
   reviewed SHA unset for a candidate you never verified. Move to Blocked if
   safe and wait for an operator-authorized new handoff.
2. Allow at most **two review rounds per issue**, including across stops,
   retries, human state moves and new PR heads. Resume a pending round or
   reuse a completed verdict for the same valid handoff, contract and
   candidate. Otherwise, if both rounds were already reserved, go directly to
   the exhaustion handoff in step 4. If a round remains, reserve it in the
   workpad before review. Each numbered round records the handoff number, PR,
   base SHA, head SHA, and status `pending`, `accepted`, `needs-rework` or
   `blocked`. Retain stale rounds, including pending ones, in the count.
3. For a pending round, assess the exact published diff against the current
   contract, repository rules, validation and known feedback. Inspect the
   verified commit even if local HEAD differs. Use a separate scratch
   checkout within the workspace for validation that writes files. Keep the
   implementation checkout intact. Do not push, edit the PR, add a GitHub
   review or change tracked candidate files. Record the verdict with the
   reviewed SHA, checks run, concrete findings, evidence and remaining merge
   obligations. Accept only with sufficient evidence and no unresolved
   acceptance defect. Use `needs-rework` for a concrete defect development
   can fix in a new commit. Use `blocked` for an external impediment or
   required evidence you cannot verify.
4. Recheck the live contract, workpad, state and PR before routing. Move a
   current `accepted` verdict to Human Review. For `blocked`, record the
   unblock action and move to Blocked if safe. Move `needs-rework` to Rework
   only when another round remains. When no rounds remain, a `needs-rework`
   result or a verified handoff with no matching verdict goes to Human Review
   without acceptance. First record the unresolved findings and the operator
   decision required. A stale verdict cannot authorize a transition to Rework
   or Human Review.

## Rework — address the findings

1. Require the `needs-rework` verdict that routed this Rework and an available
   review round. Verify its issue contract and open PR identity, source
   repository and branch, base and live head. If a push intent or corrected
   handoff already exists, use its link to the original review round and
   compare the live head with the recorded old and intended heads before
   editing. A live head equal to the tested intended head means publication
   succeeded. Resume at step 3. An unexpected head or changed base or contract
   invalidates the handoff. Record the mismatch and require operator
   reconciliation through Blocked if safe.
2. With the live head still at the reviewed SHA, fix the findings on the
   authorized feature branch and validate the new commit, or reuse a verified
   tested commit from the recorded push intent. Record the responses,
   validation, review round, reviewed SHA and intended new SHA in the workpad
   before pushing. Confirm the live PR still matches the reviewed candidate,
   then publish only a fast-forward to its existing source branch while in
   Rework.
3. Verify the published head differs from the reviewed head and matches the
   tested revision. Append or recover the numbered handoff for the same PR and
   the corrected head with refreshed evidence and a link to that review round
   and push intent. Read back the handoff and live state, move to Agent Review
   and end the turn. An earlier verdict gives the corrected head no acceptance.
   Only Agent Review routes to Human Review.
