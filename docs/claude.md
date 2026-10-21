# Claude subscription runner (qualified personal deployment)

Symphony can run a local isolated worker through pinned Claude Code and the
Claude Agent SDK. Codex remains the default. The initial Claude profile supports
bounded worker concurrency, configurable Claude models (Sonnet by default), Docker-compatible
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
symphony auth claude check
symphony runner doctor --agent claude
symphony run --agent claude
```

The account check and doctor make no model requests and do not start a listener.
Billing-attestation files and expiry checks are not required. Native authentication
and actual provider errors determine availability; no API-key or alternate-cloud
fallback is configured. Account billing settings remain under the operator's control.

Use a fine-grained repository-scoped GitHub credential in the existing project
credentials file. The listener keeps tracker credentials; its explicitly selected
Git credential and named project-tool variables can reach the coding process. Credentials are never
copied from the host Claude or Codex home. Native login lives in a project-scoped
Docker volume; workspace and state/log volumes are separate and retained.

The Claude image uses UID/GID 11001, a read-only root filesystem, no Linux
capabilities, no-new-privileges and a private process namespace. Coding tools are
trusted to execute repository code. They can access their native account and
selected Git and project-tool credentials as that same user; this is not isolation
from hostile code or an absolute spending firewall. Query startup and native
authentication commands disable user/project/local settings sources. Their mere
presence does not block readiness. Strict MCP configuration also ignores project
`.mcp.json` and user/plugin MCP definitions. This does not enable those settings
or MCP servers. User plugin inventory and a home-config `managed-settings.json`
are also ignored native sources and do not block readiness. Native Claude Code owns
canonical organizational policy, managed drop-ins and account/trust-dependent remote
settings. Symphony does not reject these files or replace their native merge and
permission rules. Strict MCP remains enabled; native Claude reports incompatible
enterprise MCP configuration during session initialization. The model-free readiness
check validates the runtime and native login; it does not establish full native
policy or session compatibility. Native initialization failures, including strict-MCP
incompatibility, use bounded startup diagnostics and ordinary scheduler backoff
until native configuration is repaired.
The default failure backoff starts at 10 seconds and caps at five minutes.

Native subscription login is validated, and Symphony configures no API-key or
alternate-cloud fallback. Native organizational settings can nevertheless configure
credentials, endpoints, hooks and routing. A subscription profile or a `firstParty`
provider label does not prove request destinations or billing. Codex likewise retains
its native policy behavior. Unselected ambient inference credentials are stripped;
explicitly selected inference selectors remain rejected at the project-tool boundary.
These environment rules do not override native organizational policy. Unqualified
runtime versions retain their readiness checks. Recognized explicit readiness
failures before helper hello do not create durable pauses: periodic readiness and
the existing retry backoff recognize repair. This does not clear historical pause
records or relax actual post-start runtime policy/input gates.

## Project tool credentials

Use the existing project credentials env file reported by `symphony init`; select
only the variable names the target application's tools need. For example:

```sh
SYMPHONY_TOOL_ENV=AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY,AWS_SESSION_TOKEN,AWS_REGION
AWS_ACCESS_KEY_ID=your-project-tool-access-key
AWS_SECRET_ACCESS_KEY=your-project-tool-secret
AWS_REGION=us-east-1
```

An unset selected name is omitted (for example an optional session token). Select
at most 128 names; the encoded selection, values and reserved-name metadata must
fit within 64 KiB of UTF-8 bytes. Oversized envelopes are rejected before helper execution. No other ambient variable is forwarded. Active tracker secret names, native identity
and inference selectors, runtime/preload variables and Git token aliases cannot
be selected; invalid selections return `invalid_tool_environment` without values.
Keep the selected Git credential on its existing route. Variable names do not
select a cloud model provider: AWS tool access leaves native Claude first-party
routing by itself. Native organizational settings still apply. The listener transports selected values as data; Node builds
the native environment at one boundary. Cleanup receives neither the selection
nor its values. This explicit forwarding is for trusted repository tools and does
not isolate a native login or spending from arbitrary code run by those tools.

## Workflow settings

Provider selection is startup state (`--agent codex|claude`) and cannot be changed
by editing the workflow. Existing Codex settings retain their meaning.

```yaml
claude:
  model: sonnet
  effort: high # optional; omit or use null for the model default
  max_agentic_turns: 50
  read_timeout_ms: 5000
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000
```

Both providers use `agent.max_concurrent_agents` (default 10) and
`agent.max_concurrent_agents_by_state` through the same scheduler. Set the global
value to 1 for serial execution, or 3 to allow three independent worker sessions;
per-state limits can further restrict admission. Reloaded limits apply to new
starts without terminating existing workers. Normal dispatch has no additional
Claude cap. A healthy explicit authentication/policy recheck restores configured parallel capacity immediately, without waiting for a model turn.
Quota cooldown expiry uses normal capacity.
The old positive `claude.max_concurrent_agents` field is accepted for workflow
compatibility but is deprecated and **ignored**. Move any desired cap to
`agent.max_concurrent_agents`; for example, a legacy Claude value of 1 with an agent
value of 3 now allows three workers. Native account/service limits can still
reject requests; existing shared cooldown, retry, drain and owned cleanup apply.
The state API exposes the effective global value as `agent.max_concurrent_agents`.

`claude.model` defaults to `sonnet` and accepts nonblank identifiers up to 256
characters. Values pass unchanged to the SDK; the pinned native runtime owns
aliases, supported IDs and unsupported-model errors. Symphony keeps no parallel
model catalog. Account access and included subscription allowance still determine
whether a selected model can run. Passing configuration validation is not live
model qualification. Native failures continue through the existing session error
classification and retry behavior; this change does not add a new error policy.

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
same workspace. `claude.max_agentic_turns` bounds SDK steps within one turn.
Reaching this native allowance closes the exhausted session and yields normally;
the scheduler reads live tracker state and can renew the session in the retained
workspace. Human Review and terminal work are not redispatched. Recorded denied-tool
history does not override native success or max-turn results; it does not establish
an unresolved input request. Native execution failures with permission denials keep
their blocked outcome. Genuine authentication, quota and policy stops remain distinct.
Claude settings reject unknown keys, arbitrary commands, authentication overrides and raw SDK options.

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
durable global turn gate. Native session admission validates the current native login without a billing attestation.

Authentication and shared policy failures reported by actual native sessions still
require explicit operator recovery. The exact original five-field version-1 auth_required/waiting record with explicit nil profile and reset came only from pre-input observations; it is ignored read-only. Profiled auth, policy, malformed and diagnostic-bearing records retain their recovery requirements. Legacy operator_recheck, operator_recheck_inflight and probe_inflight scheduler permits are also ignored without deleting evidence. Native allowed/allowed-warning rate-limit events remain allowed, including overage metadata. Rejected native rate-limit events use ordinary quota recovery regardless of bucket name; actual native billing, authentication and permission failures retain their stop behavior. Established-session protocol, EOF/helper
timeout and tracker delivery failures use scheduler backoff after bounded cleanup,
preserving the workspace and requiring tracker readback before uncertain writes.
No tracker mutation is replayed by the runner. Explicit permission/input stops
remain blocked; native turn allowances yield through ordinary continuation.
Pre-hello helper exit, timeout, malformed frames and incompatible handshakes use
startup retry without a durable policy stop.
Retry diagnostics preserve fixed transport causes and the helper exit status. Only
the exact version-1 seven-field policy_blocked/waiting record with nil profile/reset,
code protocol_error and stage startup is ignored read-only as an attributable
historical pre-hello transport failure. Unknown fields or scoped policy records
retain explicit recheck. Startup protocol/initialization failures
keep their original retry cause after a bounded correlated stop attempt; missing or
foreign cleanup acknowledgement is a separate diagnostic. Actual startup auth,
policy/schema/identity failures retain their independent behavior. Explicit drains
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
not a helper protocol failure. Healthy explicit operator recheck clears recovered auth/policy pauses before dispatch. Recoverable turn failures then use normal scheduler retries, with no consumed-recheck latch.

```sh
symphony runner drain
symphony runner recheck  # requires zero active workers and healthy native/runtime checks
symphony runner resume
```

These operations do not perform model probes. They restore normal configured admission after their existing native/profile checks; drain remains explicit. State snapshots (`agent.admission`) and successful resume/recheck
responses (`admission`) report effective dispatch eligibility, its reason,
provider/profile and optional quota retry time. Ordinary resume refuses retained
Claude authentication/policy pauses; explicit recheck follows operator repair.
A healthy explicit `runner recheck` with zero workers acknowledges the currently
selected qualified native profile, including an account, organization or plan change.
It clears recoverable authentication/policy pauses through the existing recovery path
and uses that profile for subsequent sessions. Automatic refresh and ordinary resume
never adopt an unexpected profile. A future genuine quota reset, malformed/unsafe
pause evidence or active worker refuses recheck without changing the pinned profile
or deleting that evidence. Successful recheck clears the old recoverable pause;
it does not archive it or perform a model request.
If another readiness observation is running, recheck waits for it and then starts
a fresh bounded check. The recheck API allows 75 seconds and the launcher 80 seconds
for these two sequential checks; each native check remains limited to 35 seconds.

Quota cooldown resumes polling with admission false until the reset. Switching to
Codex ignores Claude pause files without deleting their evidence. Tracker errors do not require runner recheck or operator clearance.
The legacy `confirm_tools_reconciled` control API option remains available to
remove old intent evidence after external verification; ordinary runner rechecks
preserve that evidence and do not require the option.

The control API requires an explicit header; keep its published port on loopback.
Drain persists through restarts. Existing workers can finish, but new work is
not dispatched. Cleanup uncertainty alone never requires container replacement.
Retired cleanup markers are ignored/preserved and attributed cleanup-only pauses
are ignored read-only. Ambiguous untagged policy still uses the existing fresh
healthy explicit recheck; an old marker cannot turn that into a replacement gate.

For native reauthentication with the same account:

```sh
symphony update --pause-runner
symphony auth claude login
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
candidate only after native runtime/login readiness and verifies its effective admission
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

The production `runner/claude/package.json` and generated lock own the SDK/CLI
pins. Node validates the installed SDK and measured native CLI against those pins;
the qualification lab reuses the same dependencies. Elixir checks protocol/policy
version 1 and the required metadata shape, treating dependency version strings as
bounded diagnostics. A compatible dependency upgrade therefore requires no
synchronized Elixir version edit. The Dockerfile pins Node. A bounded stdio
protocol separates OTP scheduling/tracker ownership from one native SDK
conversation. Tool replies remain asynchronous; native outcomes are correlated
with the current user input. Plain assistant prose, partial EOF and malformed
frames never establish successful completion or quota resets. Captured process
generations constrain cleanup to the helper's owned native group.

The existing attempt Task owns normal session cleanup once. Exact original primary
returns/exceptions survive both successful and failed cleanup; bounded primary and
cleanup diagnostics remain separate with available issue/session correlation.
Scheduler cancellation attempts the captured adapter interrupt before Task
retirement and workspace reconciliation, even when that callback fails. It does
not retain an eternal worker/claim because descendant cleanup is unconfirmed.
Captured killed/shutdown/linked-Port `:epipe` owner loss reuses generation-safe
cleanup before normal retry. The fallback command and its own children have a
10-second GNU timeout; detached native groups may survive that deadline or a crash.
Unpublished worker loss retains the baseline Task-only retry path without an
ownership proof. No global cleanup pause, readiness latch, mandatory replacement,
watchdog, native guardian or extra supervision service is introduced.

Old `claude-cleanup-unconfirmed` files are preserved but ignored, including across
startup, workflow refresh, restart and controls. Attributed `policy_blocked` records
with code `cleanup_restart_required`/stage `cleanup`, or code
`native_ownership_unavailable`/stage `startup`, are ignored read-only. Missing
captured ownership metadata still aborts startup, then retries normally; it never
creates an unowned session. Actual expected account/profile mismatch stays a policy stop.
An old untagged policy record is indistinguishable from a real native policy stop;
it still uses ordinary healthy explicit recheck rather than automatic deletion.
Historical cleanup may have overwritten an earlier auth/quota/policy record, so
neither an attributed tag nor an untagged file proves the absence of an older real
cause. New cleanup never overwrites actual account pauses. The operator chose this
OpenAI baseline recovery tradeoff; no universal descendant-exit guarantee is claimed.

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
