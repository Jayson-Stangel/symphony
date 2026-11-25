# Adopt independent Agent Review

Use the [example workflow](WORKFLOW.md) in a Linear-backed GitHub project
running a Symphony build with
[`agent.fresh_session_on_enter`](../../elixir/README.md#configuration).
Codex and Claude use the same workflow. The runner keeps the workspace,
branch, PR and local evidence when it starts the next conversation.

## Configure a pilot project

1. Choose a disposable repository and Linear project for the pilot. In the
   project's Linear team, confirm these statuses exist and use their actual
   names throughout the workflow.

   | Status | Who acts |
   | --- | --- |
   | Todo, In Progress | Development worker prepares and publishes the candidate. |
   | Agent Review | Fresh reviewer checks the candidate and records a verdict. |
   | Rework | Fresh worker fixes recorded findings on the same PR. |
   | Human Review | Operator reads the handoff and owns the merge decision. |
   | Blocked | Operator stops the issue or resolves a recorded impediment. |

2. Follow [project setup](../getting-started.md#configure-credentials) for the
   isolated runner, project-scoped Linear access and a repository-scoped
   GitHub credential with Contents and Pull requests read/write. Confirm that
   the worker can read and update the issue and its workpad via the Linear
   tool. Set up native authentication for the chosen provider in that
   runner's own auth storage.
3. Copy the example to the pilot repository root as `WORKFLOW.md`. Preserve
   any existing policy you still need, including target-branch and validation
   rules. Replace the project slug, clone URL, and label with the pilot's
   values. Give only the intended issue the required label. Keep Agent Review
   and Rework in both the active and fresh-entry lists, and keep Human Review
   outside the active list. Keep Blocked in the blocked list. Commit the
   workflow before starting one provider through the
   [normal runner entry point](../getting-started.md#authenticate-and-run).
   Use a separate pilot issue and PR for each provider. Stop the first
   listener before starting the second on the same tracker scope. For a
   concurrent pilot, use distinct projects or label filters as well as
   separate runner namespaces.

## Operate the handoff

Read the issue's single workpad comment for the candidate, exact published
base and head, validation, known gaps and review history. The two-round cap
counts reservations in that comment. Resuming a matching reservation or
completed verdict consumes no extra round. A new candidate needs a separate
review. If a human changes the PR or contract, reconcile the workpad and
authorize a new handoff before restarting review. Preserve all old records.

At Human Review, check that the disposition is acceptance for the current
head or an escalation for exhausted rounds. With acceptance, verify the head
again and satisfy remaining required checks, reviews and repository merge
rules. With exhaustion, decide how to resolve the findings outside the
automated loop. To unblock a previously blocked round, record the resolution
and a fresh handoff before moving the issue back to Agent Review. Any
remaining round allowance still applies.

Move the issue to **Blocked** to stop it. Symphony applies the stop after a
successful observation of tracker state. A tool request already sent may take
effect. Check the PR and workpad, and verify shutdown in the runner before
re-entering an active state or editing the shared workspace. If the workpad
and PR disagree after an interruption, reconcile them before resuming.

## Verify with each provider

On the packaged candidate, start a labeled pilot issue in Todo with a small
reproducible acceptance requirement and a supplied defective seed. In the
pilot issue, ask development to publish the seed unchanged, record baseline
validation and leave the correction to Rework. Observe a fresh Agent Review
session report the defect for that head, a fresh Rework session fix and
validate it, and a fresh Agent Review session accept the new head. Confirm
Linear reads back Human Review and the runner stays idle through multiple
polls. Use a separate pilot issue whose second round still needs changes to
confirm the workpad cap produces an escalation and prevents a third round.

Record the installed manager and runner revisions, distinct provider session
IDs for development and each review or rework, and fresh start inputs or
equivalent evidence that the provider did not resume a conversation. Capture
tracker readbacks, PR base/head SHAs, findings, validation and workpad rounds.
Verify that each new conversation gets its role and evidence from the current
workflow, tracker and workspace. Reuse shutdown and Blocked proof from
fresh-session qualification when it covers the same runtime and policy,
retaining its original source and revision.
