# Claude subscription runner (qualified personal deployment)

Symphony can run a local isolated worker through pinned Claude Code and the
Claude Agent SDK. Codex remains the default. The initial Claude profile supports
one worker, Sonnet, Docker-compatible Linux isolation, and native Claude Pro/Max
subscription sign-in (the live qualification used Pro on Linux ARM64). Host and SSH Claude execution are unsupported.

This profile is intended for an owner-operated installation. Permission to offer
subscription sign-in as a third-party service is a separate distribution question;
this implementation does not resolve that question.

## Set up a fresh project

To test the integration branch before a production release, follow the
[dedicated development workspace guide](development.md). It isolates Docker
resources as well as the checkout, configuration and dashboard port.

After the feature has passed review, been promoted to main, and been installed
through the existing stable `symphony update` command, run these commands from the
target repository:

```sh
symphony auth claude login
# Complete native sign-in in the terminal; never paste its codes into a workpad.
# Disable paid usage/overages in your Claude account, then explicitly attest:
symphony auth claude attest --overages-disabled
symphony auth claude check
symphony runner doctor --agent claude
symphony run --agent claude
```

The account check and doctor make no model requests and do not start a listener.
Attestation is an operator statement, expires after 30 days, and is bound to the
native account fingerprint. It is not an automated billing guarantee. Confirm
included-allowance usage in the account UI after a small pilot. Unexplained paid
usage blocks rollout. No API-key or alternate-cloud fallback is configured.

Use a fine-grained repository-scoped GitHub credential in the existing project
credentials file. The listener keeps tracker credentials; only its explicitly
selected Git credential is passed to the coding process. Credentials are never
copied from the host Claude or Codex home. Native login lives in a project-scoped
Docker volume; workspace and state/log volumes are separate and retained.

The Claude image uses UID/GID 11001, a read-only root filesystem, no Linux
capabilities, no-new-privileges and a private process namespace. Coding tools are
trusted to execute repository code. They can access their native account and
selected Git credential as that same user; this is not isolation from hostile
code or an absolute spending firewall. Ambient native settings, alternate
inference credentials, unqualified runtime versions and expired policy metadata
block readiness rather than selecting a different inference route.

## Workflow settings

Provider selection is startup state (`--agent codex|claude`) and cannot be changed
by editing the workflow. Existing Codex settings retain their meaning.

```yaml
claude:
  model: sonnet
  max_concurrent_agents: 1
  max_agentic_turns: 50
  read_timeout_ms: 5000
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000
```

`agent.max_turns` still bounds Symphony continuation turns, while
`claude.max_agentic_turns` bounds SDK steps within one turn. Claude settings reject
unknown keys, arbitrary commands, authentication overrides and raw SDK options.
The initial worker cap is one even if general agent limits are higher.

Root AGENTS.md and CLAUDE.md are combined without generating tracked files.
Workers must read applicable directory instructions before editing. Canonical
Symphony skills are packaged once as a local plugin; aliases are
`symphony:symphony-commit`, `symphony:symphony-push`, `symphony:symphony-pull`,
`symphony:symphony-linear`, and `symphony:symphony-land`. Declared local overrides
in `.codex/symphony-skill-overrides.yml` are copied into a bounded private overlay.
Unsafe paths, symlinks and malformed manifests fail closed. Existing workpads,
including Codex Workpad comments, are reused.

## Pauses and recovery

`/api/v1/state` includes `agent.provider`, native readiness, drain state and
sanitized pause metadata. Legacy token fields remain for API compatibility;
Claude usage totals include cached input and do not imply dollar costs.

Authoritative quota events can provide a reset time. One persisted probe is
allowed after that time; absent/unknown reset times require explicit operator
recheck. That probe is consumed on disk before sending model input. A failed or
interrupted probe remains blocked as `probe_inflight`, including after restart;
inspect the failure and explicitly recheck before another attempt. Native
authentication, policy and protocol failures pause the project
instead of consuming issue retries indefinitely. An unresolved tracker call
retains a durable intent marker so a crash cannot silently replay a side effect.
Reconcile the external result before clearing that marker with the explicit API
confirmation below.

Definite tracker client rejections, including a Linear HTTP 400 for an invalid
GraphQL document, are returned to Claude so it can correct the request. Transport
failures, server errors and partial GraphQL results still pause for reconciliation.

An intentional scheduler stop when an issue leaves active states is cancellation,
not a helper protocol failure. Confirmed cleanup can finish a non-quota operator
recheck; quota probes and unresolved tracker intents remain blocked until reconciled.

```sh
symphony runner drain
symphony runner recheck  # requires zero active workers and healthy native policy
symphony runner resume
```

These operations do not perform model probes. They expose at most the next normal
work attempt. To reconcile uncertain tracker effects, use the local control API
only after checking the actual issue/comment/PR outcome:

```sh
curl --fail -H 'x-symphony-control: 1' -H 'Content-Type: application/json' \
  -d '{"confirm_tools_reconciled":true}' \
  http://127.0.0.1:4010/api/v1/agent/recheck
```

The control API requires an explicit header; keep its published port on loopback.
Drain persists through restarts. Existing workers can finish, but new work is
not dispatched. If process cleanup cannot be confirmed, readiness requires an
updater-owned container replacement; a recheck alone cannot clear that condition.

For native reauthentication with the same account:

```sh
symphony update --pause-runner
symphony auth claude login
symphony auth claude attest --overages-disabled
symphony update --agent claude
symphony runner recheck
```

Native logout/removal refuses a profile mounted by an active container and never
removes workspaces or logs. Account changes are not silently adopted by an
existing listener.

`runner stop` retains the stopped container and its metadata as a qualified
source for a later updater transition. A normal run can restart the same profile.

## Provider and image transitions

After reviewed promotion, use the installed updater from the target repository:

```sh
symphony update --agent claude
# or: symphony update --agent codex
# If a candidate cannot become ready:
symphony update --rollback-runner
```

The updater preflights the candidate, drains the qualified listener, waits for
zero workers, retains the stopped prior container, and checks exclusive volume
ownership before migrating root-owned workspace/state files to UID 11001. It
pins the candidate image ID and journals the transition. It resumes the
candidate only after native policy readiness, then removes the stopped prior
container. Rollback retains Git commits, untracked files, state and logs.

`run` refuses provider, image, port or native-profile mismatches. It does not
replace an active listener. The previous Codex listener format has no provider or
port labels and no drain API. The updater qualifies it using repository storage,
workspace/state/login mounts, the read-only workflow mount, the original entrypoint,
and the loopback port recorded in its metadata. First use `symphony update` without
flags to install the new command if the installed version predates provider support.
To migrate the runner, finish active work, then use
`symphony update --pause-runner` explicitly. This performs a graceful
stop with retained volumes; it cannot drain an old listener. Retry
`symphony update --agent codex` or `symphony update --agent claude` to replace the
stopped listener. Rollback understands its previous state API. Unknown legacy
formats or mismatched ownership are refused. Do not repair
or restart a stable installation manually when the updater refuses; report the
limitation. Rollback can stop a verified candidate whose control API never became
usable while its durable upgrade marker proves it has never resumed. The runtime
removes this marker before permitting work, including on manual resume; later
drains do not recreate it. Candidates that may have resumed must drain normally.
An unresponsive active candidate is otherwise refused unless Docker verifies it is
an empty restarting container. Never kill active work to bypass a
failed drain.

## Runtime contract

The image pins SDK 0.3.303, Claude Code 2.1.303 and Node 24.18.0. A bounded stdio
protocol separates OTP scheduling/tracker ownership from one native SDK
conversation. Tool replies remain asynchronous; native outcomes are correlated
with the current user input. Plain assistant prose, partial EOF and malformed
frames never establish successful completion or quota resets. Captured process
generations constrain cleanup to the helper's owned native group.

Qualification uses offline OTP/Node/shell tests and bounded owner-approved live
checks. Multi-hour credential refresh, every quota bucket, and commercial
third-party distribution require additional evidence; a short pilot does not
establish them.
