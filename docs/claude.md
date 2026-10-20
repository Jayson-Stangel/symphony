# Claude subscription runner (qualified personal deployment)

Symphony can run a local isolated worker through pinned Claude Code and the
Claude Agent SDK. Codex remains the default. The initial Claude profile supports
one worker, configurable Claude models (Sonnet by default), Docker-compatible
Linux isolation, and native Claude Pro/Max subscription sign-in (the live qualification used Pro on Linux ARM64). Host and SSH Claude execution are unsupported.

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
  effort: high # optional; omit or use null for the model default
  max_concurrent_agents: 1
  max_agentic_turns: 50
  read_timeout_ms: 5000
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000
```

`claude.model` accepts the family aliases `sonnet` (default), `opus`, `haiku`,
and `fable`, or the current model IDs `claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-5-5`, and `claude-fable-5-1`. These match Anthropic's
[model lineup](https://platform.claude.com/docs/en/models/overview) and
[Claude Code aliases](https://code.claude.com/docs/en/model-config), checked
October 20, 2026. For example, set `claude.model: claude-opus-5-5` to select
Opus 5.5. Values are passed unchanged to the SDK; aliases follow the pinned
Claude Code runtime's resolution. Account access and included subscription
allowance still determine whether a selected model can run. Model validation
is not live qualification: the original live pilot exercised Sonnet only.
Unknown IDs, special modes such as `opusplan`, and provider-specific IDs remain
rejected by both workflow and runner validation.

`claude.effort` controls reasoning depth through the SDK's `effort` option.
Accepted levels are `low`, `medium`, `high`, `xhigh`, and `max`, as documented in
[Claude Code effort levels](https://code.claude.com/docs/en/model-config#adjust-effort-level).
Omit it or set it to `null` to preserve the native model default. Symphony does
not turn thinking off or set a fixed token budget. Workflow reload applies the
new setting to new sessions; an existing session keeps its original effort.
The terminal dashboard, web UI and `/api/v1/state` expose the configured effort
(`agent.effort`, null for model default). The displays label it as configured:
model support and account policies may cap the effective level. The web UI
shows status; edit `WORKFLOW.md` to change the setting.

`agent.max_turns` bounds continuation turns within one worker attempt for both
providers. Reaching it closes that session normally; the existing scheduler
continuation check revalidates active work and can start another attempt in the
same workspace. `claude.max_agentic_turns` bounds SDK steps within one turn. Claude settings reject
unknown keys, arbitrary commands, authentication overrides and raw SDK options.
The initial worker cap is one even if general agent limits are higher.

Root AGENTS.md and CLAUDE.md are combined without generating tracked files.
Workers must read applicable directory instructions before editing. Canonical
Symphony skills are packaged once as a local plugin; aliases are
`symphony:symphony-commit`, `symphony:symphony-push`, `symphony:symphony-pull`,
`symphony:symphony-linear`, and `symphony:symphony-land`. Declared local overrides
in `.codex/symphony-skill-overrides.yml` are selected by Elixir and copied into a
bounded private overlay. Sessions without overrides load the packaged plugin
directly. Mixed sessions link immutable packaged procedures and snapshot only
local overrides, preserving their relative helper paths. The helper validates
selected names against the packaged plugin rather than another hard-coded list.
The overlay is removed at session cleanup without changing the repository or
canonical procedures. Optional override validation still occurs at session start;
there is no setup-time cache to invalidate when repository procedures change.
Unsafe paths, symlinks and malformed manifests fail closed. Existing workpads,
including Codex Workpad comments, are reused.

## Pauses and recovery

`/api/v1/state` includes `agent.provider`, native readiness, drain state and
sanitized pause metadata. Legacy token fields remain for API compatibility;
Claude usage totals include cached input and do not imply dollar costs.

Temporary quota exhaustion uses the existing orchestrator retry queue and bounded
failure backoff. A reliable provider reset time extends the delay and pauses new
dispatch until that time. Unknown or expired reset times use ordinary retries;
no successful turn or operator recheck is required to release quota recovery.
An interrupted recovery or a normal Human Review handoff cannot leave a consumed
quota probe behind. Legacy `probe_inflight` and expired/unknown quota records no
longer gate dispatch, including after restart; work is revalidated against the
current tracker state and resumes in its preserved workspace. A repeated quota
rejection defers the next attempt again.

Model-free readiness failures remain observations and are not written as durable
account pauses. Startup returns while checking; one supervised check refreshes
the observation every 30 seconds after completion, and on refresh/resume/recheck.
Repairing authentication or readiness is recognized automatically without restart
or recheck when no separate durable runtime pause exists. Explicit drain remains
in force. The last completed observation remains visible during periodic refresh;
explicit resume/recheck waits asynchronously for verification without blocking
snapshots, drain or worker exits.
Unchanged health observations preserve the configured tracker poll cadence.
Admission transitions and explicit controls request prompt reconciliation,
coalescing with any poll cycle already in progress. Drain cancels a waiting resume.

The external check uses GNU timeout to kill its own process group after at most
35 seconds; native version/authentication checks retain 15-second limits. If the
supervised task or scheduler dies, a model-free OS check may survive until that
deadline, so a restart can briefly overlap the old bounded check. No model request
is made. This does not promise immediate OS-process quiescence on owner loss.

Model-free readiness observations govern new admission. Existing native sessions
retain their captured startup identity/configuration and actual runtime auth/policy
stops; an unhealthy observation does not introduce cross-worker cancellation or a
durable global turn gate. Native session admission still validates the attestation.

Authentication and shared policy failures reported by actual native sessions still
require explicit operator recovery. Older readiness failures and actual session
startup failures wrote indistinguishable unscoped pause records. Those ambiguous
legacy records remain intact and still need explicit recheck after repair; the
automatic-observation repair does not resolve that migration limitation. Paid-overage signals remain policy blockers. Established-session protocol, EOF/helper timeout and tracker
delivery failures retry the affected attempt with scheduler backoff after confirmed
cleanup, preserving its workspace and requiring tracker readback before writing.
No tracker mutation is replayed by the runner. Explicit permission/input and
native turn-limit stops remain blocked. Transient startup protocol or native initialization failures also retry once the
helper acknowledges owned-native cleanup for that exact session/request. The
cleanup wait is bounded; a missing or foreign acknowledgement retains the original
startup cause and creates the existing cleanup-unconfirmed stop. Explicit startup
auth/policy/schema/identity failures remain gated. A helper crash before ownership
handoff still requires recovery because native cleanup cannot be proven. Explicit drains
remain durable and prevent queued retries from dispatching.

Both providers follow the original Codex tool recovery model: tracker responses,
including GraphQL errors, partial results and transport failures, return to the
agent. Error bodies are preserved for correction. The shared handler normalizes
malformed/raised results and oversized responses into bounded actionable tool
failures; outcome classification does not control dispatch or hide errors.
Symphony disables automatic HTTP retries for Linear requests.

Every initial/retry prompt includes recovery instructions, including with a
custom workflow. The agent reads current issue/workpad and target state, checks
whether a failed write already took effect, corrects rejected requests using the
schema, and reconciles partial mutations target by target before retrying. If
readback remains inconclusive, it avoids the unresolved write and dependent
writes, keeps evidence, and continues safe independent implementation/testing.
It blocks only its issue when remaining required work depends on that outcome.

New tool calls use redacted logs rather than durable dispatch-gating intents.
After a crash or restart, the next attempt reconciles live tracker state before
writing. Existing `claude-tools` files remain legacy evidence: matching issues
receive a recovery notice; other issues can run. Legacy `tool_outcome_uncertain`
and worker `protocol_error` pause records no longer block project dispatch or
mask later auth/quota/policy pauses. No automatic replay or rollback is inferred.
This bounded issue-local reader remains compatibility for retained state volumes,
not a dispatch gate. Retire it only through evidence-preserving cleanup after
legacy volumes have been inventoried; ordinary attempts never delete legacy files,
and there is no automatic expiry or purge. The existing explicit reconciliation
control remains an operator action. No new intent records are written.

`Tracker tool outcome` logs correlate issue and tool IDs with provider, document
SHA-256, phase, elapsed time, HTTP status, response/delivery reason, allowlisted
GraphQL codes (including `INVALID_INPUT`) and transport reason. The hash
correlates requests without parsing GraphQL or inferring operation kind, execution
certainty or whether an error preceded a write. `accepted` records successful delivery (Claude
acknowledgement or a Codex RPC write); `delivery_failed` records a lost reply or
acknowledgement. Neither phase asserts the external effect was reconciled. Query text, variables, error bodies and credentials are not
included in these diagnostics. These fields help reconcile a write but do not
prove whether an unconfirmed external effect occurred.

An intentional scheduler stop when an issue leaves active states is cancellation,
not a helper protocol failure. Confirmed cleanup can finish a non-quota operator
recheck. Quota recovery uses scheduler retries and has no consumed-probe latch.

```sh
symphony runner drain
symphony runner recheck  # requires zero active workers and healthy native policy
symphony runner resume
```

These operations do not perform model probes. They expose at most the next normal
work attempt. State snapshots (`agent.admission`) and successful resume/recheck
responses (`admission`) report effective dispatch eligibility, its reason,
provider/profile and optional quota retry time. Ordinary resume refuses retained
Claude authentication/policy pauses; explicit recheck follows operator repair.
Quota cooldown resumes polling with admission false until the reset. Switching to
Codex ignores Claude pause files without deleting their evidence. Tracker errors do not require runner recheck or operator clearance.
The legacy `confirm_tools_reconciled` control API option remains available to
remove old intent evidence after external verification; ordinary runner rechecks
preserve that evidence and do not require the option.

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
candidate only after native policy readiness and verifies its effective admission
result before removing the stopped prior container. A retained quota cooldown is
reported explicitly as intentionally paused until its reset; it is not operational
dispatch. Authentication/policy pauses refuse ordinary resume and retain rollback
recovery. A successful HTTP response alone does not establish admission. Verified
rollback sources with the older provider-aware API require native readiness, no
drain and no retained pause. Rollback retains Git commits, untracked files, state
and logs.

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

Token usage appears as Claude reports assistant steps, including cached input.
Repeated tool blocks sharing a message ID count once. Step-level output counts
are SDK placeholders, so output stays at its last confirmed value until the turn
result arrives. Result `modelUsage` reconciles cumulative session totals, including
auxiliary calls, without adding earlier turns again. Before any usage arrives,
both the dashboard total and running-session cells display `unavailable`.

Qualification uses offline OTP/Node/shell tests and bounded owner-approved live
checks. Multi-hour credential refresh, every quota bucket, and commercial
third-party distribution require additional evidence; a short pilot does not
establish them.

### Progress and stall detection

Claude progress uses fixed descriptions derived from SDK event types: a response
received, a requested tool, waiting for a tool, retrying, or compacting context.
Known built-in tools map to broad command/read/edit/search categories; custom
tools and missing details use generic tool or waiting descriptions. Prompts,
reasoning, tool arguments, credentials, custom tool names and raw SDK payloads
are never used as progress text. Both dashboards use the same summary.

The last-event timestamp records liveness. Claude's stall clock advances only
for session startup or a new assistant content block (including tool requests),
with repeated block UUIDs ignored within each turn. Tool heartbeats, elapsed-time
updates, status notifications, retries and usage notifications do not reset that
clock. A long-running tool can therefore hit the configured stall timeout even
while it reports heartbeats; choose the timeout to allow expected tool durations.
Codex activity accounting is unchanged.
