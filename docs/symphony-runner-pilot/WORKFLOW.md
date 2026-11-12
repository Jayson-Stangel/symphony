---
tracker:
  kind: linear
  provider:
    project_slug: "81955ff329e0"
    api_key: $LINEAR_API_KEY
  required_labels:
    - "Symphony Pilot JAY-934"
  active_states:
    - Todo
    - In Progress
    - Rework
  blocked_states:
    - Blocked
  terminal_states:
    - Canceled
    - Done
    - Duplicate
workspace:
  root: /workspaces
  clone_url: https://github.com/Jayson-Stangel/symphony.git
hooks:
  before_run: |
    test "$(pwd -P)" = /workspaces/JAY-934 || {
      printf '%s\n' 'The self-development pilot permits only the JAY-934 workspace.' >&2
      exit 1
    }
agent:
  max_concurrent_agents: 1
  max_turns: 8
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

Implement JAY-934 only, in its issue workspace. If `{{ issue.identifier }}` is
anything else, stop without editing files or writing to a service.

Current tracker state is `{{ issue.state }}`. Contract at {{ issue.url }}:

{{ issue.description }}

{% if attempt %}
This is attempt {{ attempt }}. Reconcile the existing workspace, workpad and
remote branch before continuing. Reuse evidence for the same inputs and resume
from the last confirmed checkpoint.
{% endif %}

1. Read the issue, its comments and the coordinator's approved handoff details.
   Obtain the exact upstream `dev` base SHA from that record. Confirm the
   handoff destination is `https://github.com/Jayson-Stangel/symphony-jay934-pilot.git`
   and the branch is `codex/jay-934-retry-deadline`. If a detail is
   missing or disagrees with this workflow, stop and record the blocker. In a
   fresh workspace, verify the approved SHA is on upstream `dev` before
   creating the branch from it. On return, inspect the existing branch and
   preserve any commits, edits and evidence.
2. Use the Linear tool to find the single `## Agent Workpad` comment. Create it
   if missing and update it with the plan, base, checkpoints and evidence.
   Move `Todo` to `In Progress` and read it back. Use exact team state IDs.
   Reconcile uncertain writes by reading the target before any retry. If
   Linear is unavailable or a write outcome remains unresolved, record the
   blocker in the workspace, make no further service writes and end the turn.
   On continuation, preserve the evidence and wait for the coordinator to
   reconcile the blocker.
3. Read repository and Elixir instructions. Restore locked dependencies from
   the issue checkout and record the actual tool versions. Reproduce the bug
   with the regression first, apply the focused fix, then run the regression
   and applicable repository gates, including `make -C elixir all`. Record
   commands, outcomes and any omissions against the exact candidate SHA.
   Retain the same checkout and branch when handling review feedback.
4. Publish only the named feature branch to the disposable repository. Verify the
   remote branch points to the tested SHA. In the workpad record that SHA,
   branch and base, validation results, gaps, and a PR body matching the
   upstream template. Check the PR body with the repository validator. Move
   the issue to `Agent Review`, verify the transition and end the turn. The
   coordinator fetches and verifies the tested commit, publishes it upstream,
   and opens the PR against `dev`.
5. If the coordinator returns the issue to `Rework`, read the upstream PR and
   review findings. Make at most one requested revision on the existing
   disposable branch, refresh the evidence, and return to `Agent Review`.
   If a further revision is needed, record the findings and hand them back.

Use `Blocked` for an external access or tooling failure that prevents the
handoff. Record the failing boundary, evidence and the needed coordinator
action, then end the turn. Keep writes limited to this issue's workpad and
authorized states, and the one disposable feature branch. The coordinator owns
upstream GitHub writes, final disposition and cleanup. Worker authority ends
at the review handoff. Keep the installed runtime, tracker routing, release
tooling and stable installation outside the workspace change.
