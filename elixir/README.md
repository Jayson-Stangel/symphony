# Symphony Elixir

The workflow template asks workers to record actual validation results and durable
acceptance evidence. Publish artifacts needed for review or explicitly required by
the repository/ticket; temporary logs do not all require upload. Evidence must match
the stated revision and remain accessible after workspace cleanup. Existing project
workflows are preserved; adopt `Evidence for review` deliberately when changing policy.

This directory contains the current Elixir/OTP implementation of Symphony, based on
[`SPEC.md`](../SPEC.md) at the repository root.

> [!WARNING]
> Symphony Elixir is prototype software intended for evaluation only and is presented as-is.
> We recommend implementing your own hardened version based on `SPEC.md`.

## Screenshot

![Symphony Elixir screenshot](../.github/media/elixir-screenshot.png)

## How it works

1. Polls the configured tracker for candidate work (included adapters: Linear, GitHub Issues, Jira
   Cloud, Asana, and GitLab)
2. Creates a workspace per issue
3. Launches Codex in [App Server mode](https://developers.openai.com/codex/app-server/) inside the
   workspace
4. Sends a workflow prompt to Codex
5. Keeps Codex working on the issue until the work is done

During app-server sessions, the selected tracker adapter may advertise provider-native tools. The
Linear serves `linear_graphql`, GitHub Issues serves `github_api`, Jira Cloud serves
`jira_rest`, Asana serves `asana_api`, and GitLab serves `gitlab_api`. Symphony executes those
tools with configured host-side auth and removes declared tracker-token environment variables from
the Codex child, so the agent does not need a second tracker login.

Codex and Claude return tracker errors to the agent, following the original Codex
recovery flow. The agent reads back uncertain writes before retrying and corrects
rejected requests. After confirmed session cleanup, lost delivery, malformed turn
frames, unexpected EOF and helper timeouts retry the affected attempt with backoff.
The next attempt reads tracker state before writing; Symphony never automatically
retries a mutation. Unconfirmed cleanup, explicit input/permission requirements and
authentication/policy stops remain gated. Transient startup failures retry only after a
matching helper cleanup acknowledgement. Explicit startup policy/identity failures
and missing cleanup confirmation remain fail-closed. Every initial/retry
prompt includes tracker recovery instructions, including for custom workflows.
Claude's excluded user/project/local settings and project MCP files do not block
startup. Native auth commands use the same disabled settings sources as coding
sessions; managed/remote policy and unqualified installed-plugin metadata retain
the explicit unsupported-settings limit. See [native settings](../docs/claude.md).
See [tracker diagnostics](docs/logging.md). Those logs retain request fingerprints,
correlation IDs and allowlisted status/codes without parsing GraphQL or inferring
execution certainty. Existing issue-local legacy notices preserve old evidence
without gating dispatch; no new intent files or automatic purge are introduced.

If a claimed issue moves to a terminal state (`Done`, `Closed`, `Cancelled`, or `Duplicate`),
Symphony stops the active agent for that issue and cleans up matching workspaces.
Successful polls also clean retained local terminal workspaces that no longer have a
running agent, such as a ticket completed after human review. Startup performs
the same terminal cleanup; failed tracker reads preserve retained checkouts.

The container launcher mounts persistent repository-specific volumes at
`/workspaces` and `/var/lib/symphony`. Restart recovery uses the tracker and
existing files, with fresh Codex sessions; in-memory retry counts and session
history are not restored. Completed workspace setup is reused without rerunning
`after_create`. If shutdown interrupts a local `after_create` hook, a marker in
`workspace.root/.symphony/bootstrap/<workspace-key>` causes subsequent attempts to fail
with `workspace_bootstrap_interrupted`, preserving the partial checkout. Inspect
and repair that checkout before removing the marker, or remove the partial
checkout and marker to allow a fresh bootstrap. Arbitrary setup hooks are not
automatically replayed against partial contents. Normal hook failures still
remove new partial workspaces so a later attempt can retry.
For local clone-only setup, prefer `workspace.clone_url: "https://github.com/owner/repo.git"`
without `hooks.after_create`. Git clones into an exclusively owned random directory
under `workspace.root/.symphony/clones`, then renames the completed checkout into
place. Existing workspaces are reused, including edits. An interrupted unpublished
clone does not block a fresh attempt. Its stage is retained rather than replayed or
deleted; inspect and remove abandoned stages only when no bootstrap process is
using them. Normal clone failures remove their own stage; timed-out clones retain
it because a native child may still be exiting. Errors omit the URL and Git output
to avoid exposing credentials. Git credentials should use the existing credential
helper rather than embedded URL secrets. `hooks.timeout_ms` bounds waiting for Git.
This option supports local workers only and cannot be combined with `after_create`;
SSH and arbitrary setup hooks continue using their existing lifecycle. Existing
workflows are not automatically migrated. Publication assumes one listener owns
each workspace root, as with existing workspace creation.

`.symphony` is reserved for local workspace metadata; an issue with that exact
identifier receives a hashed workspace key. SSH deployments retain their existing
startup and claimed-issue cleanup behavior without polling all terminal history.

If Codex reports that operator input, approval, or MCP elicitation is required, Symphony keeps the
issue claimed and exposes it as blocked in the runtime state, JSON API, and dashboard. Blocked
session details are in memory only; restarting the orchestrator clears those runtime stops, so any
still-active tracker issue can become a dispatch candidate again after restart.

For durable blockers, configure `tracker.blocked_states: ["Blocked"]` and create that status in the
tracker. These states must not overlap active or terminal states. Symphony polls them alongside
active work and shows matching issues in the dashboard and JSON API, including after a restart.
Blocked issues are not dispatched. The issue workpad holds the blocker and exact unblock action;
`Human Review` holds validated PRs awaiting human assessment. Record available feedback
and pending external reviews, then end the worker turn. Required repository checks,
reviews and human approval remain merge obligations; optional bot latency is not a
reason to occupy a coding worker. See Review handoff in [`WORKFLOW.md`](WORKFLOW.md).
Move an unblocked issue back to
`Todo`, `In Progress`, or `Rework` to resume it. Restored rows have no session history or known
blocked timestamp. `blocked_states` defaults to `[]`, can be disabled with `[]`, and reloads dynamically.
Tracker read failures preserve the last successful blocked view until polling recovers.

## How to use it

1. Make sure your codebase is set up to work well with agents: see
   [Harness engineering](https://openai.com/index/harness-engineering/).
2. Get a new personal token in Linear via Settings → Security & access → Personal API keys, and
   set it as the `LINEAR_API_KEY` environment variable.
3. Copy this directory's `WORKFLOW.md` to your repo.
4. Optionally copy the `commit`, `push`, `pull`, `land`, and `linear` skills to your repo.
   - The `linear` skill expects Symphony's `linear_graphql` app-server tool for raw Linear GraphQL
     operations such as comment editing or upload flows.
5. Customize the copied `WORKFLOW.md` file for your project.
   - To get your project's slug, right-click the project and copy its URL. The slug is part of the
     URL.
   - When creating a workflow based on this repo, note that it depends on non-standard Linear
     issue statuses: "Blocked", "Rework", "Human Review", and "Merging". You can customize them in
     Team Settings → Workflow in Linear.
6. Follow the instructions below to install the required runtime dependencies and start the service.

## Prerequisites

We recommend using [mise](https://mise.jdx.dev/) to manage Elixir/Erlang versions.

```bash
mise install
mise exec -- elixir --version
```

## Run

```bash
git clone https://github.com/openai/symphony
cd symphony/elixir
mise trust
mise install
mise exec -- mix setup
mise exec -- mix build
mise exec -- ./bin/symphony ./WORKFLOW.md
```

## Burrito releases

Symphony ships self-contained executables built with
[Burrito](https://github.com/burrito-elixir/burrito). They embed Erlang/OTP, Elixir, and Symphony,
but still expect `codex`, `git`, and the selected tracker credentials on the target machine.

Supported release targets:

- `macos_arm64`
- `macos_x86_64`
- `linux_arm64`
- `linux_x86_64`

`v*` tags publish all four targets with checksums. A manual workflow run builds the same
artifacts without creating a release.

After downloading the executable for your platform from a release:

```bash
chmod +x ./symphony-v0.0.1-macos_arm64
./symphony-v0.0.1-macos_arm64 ./WORKFLOW.md
```

## Configuration

Pass a custom workflow file path to `./bin/symphony` when starting the service:

```bash
./bin/symphony /path/to/custom/WORKFLOW.md
```

If no path is passed, Symphony defaults to `./WORKFLOW.md`.

Optional flags:

- `--logs-root` tells Symphony to write logs under a different directory (default: `./log`)
- `--port` also starts the Phoenix observability service (default: disabled)

The `WORKFLOW.md` file uses YAML front matter for configuration, plus a Markdown body used as the
Codex session prompt.

Minimal example:

```md
---
tracker:
  kind: linear
  provider:
    project_slug: "..."
workspace:
  root: ~/code/workspaces
hooks:
  after_create: |
    git clone git@github.com:your-org/your-repo.git .
agent:
  max_concurrent_agents: 10
  max_turns: 20
codex:
  command: codex app-server
---

You are working on an issue from the configured tracker {{ issue.identifier }}.

Title: {{ issue.title }} Body: {{ issue.description }}
```

Notes:

- If a value is missing, defaults are used.
- `tracker.kind` selects an adapter. Adapter-owned endpoint, scope, and auth settings belong under
  `tracker.provider`; the current Linear adapter still accepts the older flat `endpoint`,
  `api_key`, `project_slug`, and `assignee` aliases for compatibility.
- `tracker.required_labels` is optional. When set, an issue must have every
  configured label to dispatch or continue running. Label matching ignores
  case and surrounding whitespace. A blank configured label matches no issue.
- Safer Codex defaults are used when policy fields are omitted:
  - `codex.approval_policy` defaults to `{"reject":{"sandbox_approval":true,"rules":true,"mcp_elicitations":true}}`
  - `codex.thread_sandbox` defaults to `workspace-write`
  - `codex.turn_sandbox_policy` defaults to a `workspaceWrite` policy rooted at the current issue workspace
- Optional `codex.model` and `codex.reasoning_effort` select a model and reasoning
  level for new sessions. For example:

  ```yaml
  codex:
    command: codex app-server
    model: gpt-5.5
    reasoning_effort: xhigh
  ```

  Explicit fields override native command/config defaults for the new thread.
  Omit them or use `null` to preserve the runner's defaults and existing command
  flags. Model IDs and effort names are nonblank strings, validated for support
  by the installed Codex app-server and account; available effort levels vary
  by model and client (for example `low`, `medium`, `high`, `xhigh`, `max`, `ultra`).
  Symphony sends `model` and `config.model_reasoning_effort` on `thread/start`.
  [Codex configuration reference](https://developers.openai.com/codex/config-reference)
  and [app-server protocol](https://developers.openai.com/codex/app-server) define
  the native contract. Reload applies changes to new sessions; existing threads
  retain their selected model and reasoning effort.
- `codex.turn_timeout_ms` is the maximum silence interval while a turn is streaming. Each
  app-server update resets it; it is not a total turn runtime cap.
- Supported `codex.approval_policy` values depend on the targeted Codex app-server version. In the current local Codex schema, string values include `untrusted`, `on-failure`, `on-request`, and `never`, and object-form `reject` is also supported.
- Supported `codex.thread_sandbox` values: `read-only`, `workspace-write`, `danger-full-access`.
- When `codex.turn_sandbox_policy` is set explicitly, Symphony passes the map through to Codex
  unchanged. Compatibility then depends on the targeted Codex app-server version rather than local
  Symphony validation.
- Workflows that run package managers or other commands that resolve external hosts should set
  `networkAccess: true` in `codex.turn_sandbox_policy`; otherwise DNS/network access may be denied
  by the Codex turn sandbox.
- `agent.max_turns` caps how many back-to-back coding-agent turns Symphony will run in a single agent
  invocation when a turn completes normally but the issue is still in an active state. Default: `20`.
  Reaching this per-attempt allowance closes the native session normally and schedules the existing
  continuation check in the retained workspace; it does not block the issue. Both providers use this
  behavior. Tracker state and dispatch limits are checked again before another attempt starts.
  Only `turn/completed` completes a successful turn. Progress messages, review polling, and thread
  status notifications remain inside that turn and do not consume continuation turns.
- `codex.live_max_total_tokens` is a live circuit breaker. When a running issue session reports
  total token usage at or above this value, Symphony stops the worker and exposes the issue as
  blocked instead of retrying it. Set to `0` to disable. Default: `0`.
- `codex.live_max_turns` is a live circuit breaker on observed Codex session starts for one issue
  run. When the count reaches this value, Symphony stops the worker and exposes the issue as
  blocked instead of retrying it. Set to `0` to disable. Default: `0`.
  Generated and sample workflows leave both live circuit breakers disabled. Existing project
  workflows keep any explicitly configured limits; remove them or set them to `0` to use ordinary
  continuation behavior. Symphony does not rewrite existing project policy on update.
- If the Markdown body is blank, Symphony uses a default prompt template that includes the issue
  identifier, title, and body.
- Use `hooks.after_create` to bootstrap a fresh workspace. For a Git-backed repo, you can run
  `git clone ... .` there, along with any other setup commands you need.
- If a hook needs `mise exec` inside a freshly cloned workspace, trust the repo config and fetch
  the project dependencies in `hooks.after_create` before invoking `mise` later from other hooks.
- For the Linear adapter, `tracker.provider.api_key` reads from `LINEAR_API_KEY` when unset or
  when value is `$LINEAR_API_KEY`. The legacy flat `tracker.api_key` alias behaves the same way.
- Do not put a literal tracker token in a repo-owned `WORKFLOW.md` if Codex can read that
  workspace. Use `$VAR`/host-side secret references so Symphony can keep the token out of the
  child environment.
- For path values, `~` is expanded to the home directory.
- For env-backed path values, use `$VAR`. `workspace.root` resolves `$VAR` before path handling,
  while `codex.command` stays a shell command string and any `$VAR` expansion there happens in the
  launched shell.

```yaml
tracker:
  provider:
    api_key: $LINEAR_API_KEY
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    git clone --depth 1 "$SOURCE_REPO_URL" .
codex:
  command: "$CODEX_BIN --config 'model=\"gpt-5.5\"' app-server"
```

- If `WORKFLOW.md` is missing or has invalid YAML at startup, Symphony does not boot.
- If a later reload fails, Symphony keeps running with the last known good workflow and logs the
  reload error until the file is fixed.
- `server.port` or CLI `--port` enables the optional Phoenix LiveView dashboard and JSON API at
  `/`, `/api/v1/state`, `/api/v1/<issue_identifier>`, and `/api/v1/refresh`.

### Linear adapter profile

- Config: use `tracker.kind: linear` with `tracker.provider.endpoint` (default
  `https://api.linear.app/graphql`), `api_key` (defaults to `LINEAR_API_KEY` and accepts
  `$VAR`), required `project_slug`, and optional `assignee` (a Linear user ID or `me`,
  defaulting to `LINEAR_ASSIGNEE`).
  The legacy flat `tracker.endpoint`, `api_key`, `project_slug`, and `assignee` aliases remain
  supported. `required_labels`, `active_states`, `blocked_states`, and `terminal_states` stay under `tracker`.
- Scope and paging: candidate reads filter the configured project slug and requested state names,
  following Linear pages of 50. ID refreshes are also project-scoped and batch up to 50 IDs. Empty
  state/ID lists return `{:ok, []}` without a Linear request.
- Identity and normalization: `issue.id` is the Linear issue ID and `issue.native_ref` is currently
  `nil`. Records missing a nonblank ID, identifier, title, or state are dropped from candidate
  pages and fail ID refreshes. State keeps Linear's spelling; integer priorities are preserved and
  other priority values become `nil`; RFC 3339 timestamps are parsed and unusable timestamps become
  `nil`. Labels are trimmed, lowercased, deduplicated, and blanks are dropped; blockers come from
  inverse `blocks` relations.
- Dispatchability: the adapter marks an issue dispatchable only when optional assignee routing
  matches and a `Todo` issue has no non-terminal blocker. The generic scheduler then applies
  active/terminal states, required labels, claims, retries, and concurrency.
- Tool: the Linear adapter advertises `linear_graphql`, accepting either a raw query string or an
  object with nonblank `query` and optional object `variables`. Symphony executes it host-side
  with the session-bound endpoint/token and strips declared token environment variables from the
  Codex child. `project_slug` scopes scheduler reads, not raw tool calls; the tool can access
  whatever the configured Linear token can access.
- Evidence downloads: worker tool calls request file URLs signed for five minutes
  with Linear's `public-file-urls-expire-in` header. Workers can retrieve and hash
  uploaded artifacts without receiving the API key. Ordinary scheduler reads do
  not request signed URLs. Store canonical asset URLs in durable issue links and
  request fresh signed URLs through the tool when a download expires.
- Responsibility and errors: `linear_graphql` adds no idempotency key, retry, scope guard, or
  rate-limit policy, so workflows own idempotent mutations and handling provider errors. Read/config
  failures use `{:error, :missing_linear_api_token}`, `{:error, :missing_linear_project_slug}`,
  `{:error, :invalid_linear_endpoint}`, `{:error, :invalid_linear_assignee}`,
  `{:error, :missing_linear_viewer_identity}`, `{:error, {:linear_api_status, status}}`,
  `{:error, {:linear_api_request, reason}}`, `{:error, {:linear_graphql_errors, errors}}`,
  `{:error, :linear_unknown_payload}`, or `{:error, :linear_missing_end_cursor}`. Tool results
  are maps with `"success"`, JSON-string `"output"`, and text `"contentItems"`; invalid
  arguments, missing auth, and transport failures return `"success" => false` with
  `{"error": {"message": ...}}`, while top-level GraphQL errors preserve the response body with
  `"success" => false`.
  For portable reporting, map missing/invalid token, project, endpoint, assignee, or viewer errors
  to `tracker_config` or `tracker_auth`, request failures to `tracker_transport`, non-200 responses to
  `tracker_response` (`429` is `tracker_rate_limited`), GraphQL/unknown payload failures to
  `tracker_payload`, and missing cursors to `tracker_pagination`; logs and tool responses carry the
  human-readable provider detail.

### GitHub Issues adapter

- Config: use `tracker.kind: github` with required `tracker.provider.repo` in `owner/repo` form,
  optional `token` (defaults to `GITHUB_TOKEN` and accepts `$VAR`), and optional `api_url`
  (default `https://api.github.com`, HTTPS only). Set explicit `active_states` and
  `terminal_states`; active entries may be `open` and terminal entries may be `closed`.
- Reads and identity: polling is scoped to the configured repository; `issue.id` is the
  repository issue number, `issue.identifier` is `GH-<number>`, hidden or deleted `404` issues are
  omitted on refresh, and pull requests returned by the Issues API are not dispatchable.
- Tool and auth: `github_api` accepts a relative REST `path` plus optional `params` and JSON
  `body`; Symphony executes it host-side with the session-bound token, removes configured tracker
  credentials and provider authentication aliases from the Codex child, and leaves raw tool access
  limited by that token's GitHub permissions.

### Jira Cloud adapter

- Config: use `tracker.kind: jira` with provider `base_url`, `email`, `api_token`, and required
  `project_key`; the first three default to `JIRA_BASE_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`
  and accept `$VAR`. Set explicit Jira-native `active_states` and `terminal_states`.
- Issues and reads: candidate reads and ID refreshes stay scoped to the configured project and
  requested statuses; `issue.id` is Jira's immutable ID and `issue.identifier` is the issue key.
- Blockers: inward `Blocks` links populate `blocked_by`; issues in Jira's `new` status category
  wait until blockers reach configured terminal states, while in-progress categories keep running.
- Tool: `jira_rest` sends relative `/rest/api/3/` requests host-side with configured Basic auth,
  strips token environment variables from Codex, and can reach whatever the Jira credential can.

### Asana adapter

- Config: use `tracker.kind: asana` with required `tracker.provider.project_gid`, optional
  `endpoint` (default `https://app.asana.com/api/1.0`), and `api_key` (defaults to `ASANA_PAT` and
  accepts `$VAR`); `active_states` and `terminal_states` are project section names.
- Scope: Symphony polls tasks in the configured project, treats their section as state, and omits
  deleted or out-of-project tasks during ID refreshes.
- Tool: `asana_api` sends relative Asana REST requests host-side with the configured auth; Symphony
  strips `ASANA_PAT` and configured token variables from the Codex child, while raw tool calls are
  not limited to the configured project.

### GitLab adapter

- Configure `tracker.kind: gitlab` with `tracker.provider.project_path`, optional `api_url`, and
  `api_key` (default `GITLAB_PAT`); use `opened` and `closed` tracker states.
- Symphony reads project issues by IID and exposes route-safe `GL-<iid>` identifiers.
- `gitlab_api` forwards raw GitLab REST requests with host-side auth and keeps configured tracker
  credentials and provider authentication aliases out of the Codex child.

## Web dashboard

The observability UI now runs on a minimal Phoenix stack:

- LiveView for the dashboard at `/`
- JSON API for operational debugging under `/api/v1/*`
- Bandit as the HTTP server
- Phoenix dependency static assets for the LiveView client bootstrap
- Tracker issue identifiers link to the tracker-provided URL when it uses `http` or `https`

Both the web and terminal dashboards show the selected coding agent and model
near the top, including while idle. Claude displays its configured model alias
or ID (`sonnet` by default), plus configured reasoning effort or “Model default”.
Codex displays `codex.model` when explicitly configured; otherwise it displays
the model returned by its most recent session startup, labelled “last session”.
Before either is available it shows “Not yet reported”. Codex reasoning shows
`codex.reasoning_effort` or “Runner default”; Symphony does not guess values from
command flags or native account configuration.
The JSON state API includes `agent.model`, `agent.model_source` (`configured`
or `last_session`), and configured `agent.effort` (null for native defaults). Reported Codex identity resets when the runner restarts.

## Project Layout

- `lib/`: application code and Mix tasks
- `test/`: ExUnit coverage for runtime behavior
- `WORKFLOW.md`: in-repo workflow contract used by local runs
- `../.codex/`: repository-local Codex skills and setup helpers

## Testing

```bash
make all
```

Run the real external end-to-end test only when you want Symphony to create disposable Linear
resources and launch a real `codex app-server` session:

```bash
cd elixir
export LINEAR_API_KEY=...
make e2e
```

Optional environment variables:

- `SYMPHONY_LIVE_LINEAR_TEAM_KEY` defaults to `SYME2E`
- `SYMPHONY_LIVE_SSH_WORKER_HOSTS` uses those SSH hosts when set, as a comma-separated list

`make e2e` runs two live scenarios:
- one with a local worker
- one with SSH workers

If `SYMPHONY_LIVE_SSH_WORKER_HOSTS` is unset, the SSH scenario uses `docker compose` to start two
disposable SSH workers on `localhost:<port>`. The live test generates a temporary SSH keypair,
mounts the host `~/.codex/auth.json` into each worker, verifies that Symphony can talk to them
over real SSH, then runs the same orchestration flow against those worker addresses. This keeps
the transport representative without depending on long-lived external machines.

Set `SYMPHONY_LIVE_SSH_WORKER_HOSTS` if you want `make e2e` to target real SSH hosts instead.

The live test creates a temporary Linear project and issue, writes a temporary `WORKFLOW.md`, runs
a real agent turn, verifies the workspace side effect, requires Codex to comment on and close the
Linear issue, then marks the project completed so the run remains visible in Linear.

Run the opt-in GitHub Issues live test with a disposable/scratch repository:

```bash
cd elixir
export SYMPHONY_LIVE_GITHUB_REPO=owner/scratch-repo
export GITHUB_TOKEN=...
SYMPHONY_RUN_GITHUB_LIVE_E2E=1 mix test test/symphony_elixir/github_live_e2e_test.exs
```

Run the opt-in Jira Cloud live test against a disposable project whose credential can browse,
create, comment on, transition, and delete issues:

```bash
cd elixir
export JIRA_BASE_URL=https://your-site.atlassian.net
export JIRA_EMAIL=...
export JIRA_API_TOKEN=...
export SYMPHONY_LIVE_JIRA_PROJECT_KEY=TEST
SYMPHONY_RUN_JIRA_LIVE_E2E=1 mix test test/symphony_elixir/jira_live_e2e_test.exs
```

Run the opt-in Asana live E2E against disposable Asana resources:

```bash
cd elixir
export ASANA_PAT=...
export SYMPHONY_LIVE_ASANA_WORKSPACE_GID=...
# Required only when the workspace is an organization:
# export SYMPHONY_LIVE_ASANA_TEAM_GID=...
SYMPHONY_RUN_ASANA_LIVE_E2E=1 mix test test/symphony_elixir/asana_live_e2e_test.exs
```

Run the opt-in GitLab live E2E against a disposable project:

```bash
cd elixir
export GITLAB_PAT=...
export SYMPHONY_LIVE_GITLAB_PROJECT_ID=...
SYMPHONY_RUN_GITLAB_LIVE_E2E=1 mix test test/symphony_elixir/gitlab_live_e2e_test.exs
```

## FAQ

### Why Elixir?

Elixir is built on Erlang/BEAM/OTP, which is great for supervising long-running processes. It has an
active ecosystem of tools and libraries. It also supports hot code reloading without stopping
actively running subagents, which is very useful during development.

### What's the easiest way to set this up for my own codebase?

Launch `codex` in your repo, give it the URL to the Symphony repo, and ask it to set things up for
you.

## License

This project is licensed under the [Apache License 2.0](../LICENSE).

## Selecting Claude

`bin/symphony --agent codex|claude` selects one startup-owned coding provider;
Codex is the default. Claude runs only in its qualified nonroot container image.
Workflow reload cannot change providers. `claude.model` defaults to `sonnet` and
accepts current Opus, Sonnet, Haiku and Fable IDs and family aliases. Optional
`claude.effort` selects `low`, `medium`, `high`, `xhigh`, or `max` for new sessions;
omit it for the model default. Both dashboards show the configured reasoning
level. See [the Claude runner contract](../docs/claude.md)
for native sign-in, closed `claude` settings, shared scheduler concurrency, durable pauses,
tracker-side-effect recovery and updater-owned provider/image transitions.
The state API adds an `agent` object while retaining existing token fields.
Model-free Claude readiness runs in one supervised task at startup, on explicit
refresh/control, and every 30 seconds after completion. Failed observations recover
automatically after repair; they are not persisted as account pauses. Snapshots,
drain and worker exits remain responsive during native checks.
Unchanged health observations preserve the configured tracker poll cadence.
Admission transitions and explicit controls request prompt reconciliation,
coalescing with any poll cycle already in progress. GNU timeout bounds
the external check group to 35 seconds, including after task/scheduler loss; an old
OS check may briefly overlap restart until that deadline. Explicit runtime pauses,
quota cooldowns, drain and cleanup safety remain enforced. Ambiguous unscoped
legacy auth/policy pause files still require explicit recheck after repair.
Model-free readiness observations govern new admission. Existing native sessions
retain their captured startup identity/configuration and actual runtime auth/policy
stops; an unhealthy observation does not introduce cross-worker cancellation or a
durable global turn gate. Native session admission still validates the attestation.

Its `admission` result reports effective dispatch eligibility (`ready`, `reason`,
`provider`, `profile`, and optional `retry_at`), including drain and account pauses.
Resume refuses retained Claude authentication/policy pauses; use explicit recheck
after repair. A retained quota cooldown resumes polling with `admission.ready=false`
and its reset time. The updater reports this as intentionally paused, never as
operational. Codex ignores retained Claude pause files and leaves their evidence
untouched when switching providers.

Development updater retries refresh stale pending images from the current checkout,
preserving the rollback container until the replacement passes readiness. See
[development updates](../docs/development.md#refresh-the-development-build).

Development verification now also requires Node 24.18.0 (included in mise.toml).
`make setup` installs only the Elixir dependencies. For Claude development, run
`make claude-setup` to install the locked helper dependencies without install scripts.
`make all` installs those optional dependencies explicitly and runs the full quality
gate, including credential-free Node protocol/process tests. Native process
ownership coverage is verified on Linux; macOS cannot exercise `/proc` branches.

Linear tool calls share delivery and redacted diagnostics across Codex and Claude.
The agent owns outcome reconciliation. Legacy unresolved-call files remain evidence
for the matching issue's recovery prompt, without blocking dispatch. Authentication and shared policy
failures still pause the runner. Quota exhaustion uses existing retries with bounded
backoff and respects reliable reset times; it recovers without manual recheck. See [tracker diagnostics](docs/logging.md#tracker-tool-outcomes)
and [recovery](../docs/claude.md#pauses-and-recovery).

## Claude token observability

Claude reports input tokens during assistant steps, including cached input;
output tokens update at turn completion. Cumulative result totals reconcile
live counts without counting earlier turns twice. The dashboard shows
`unavailable` until usage is reported. See [the Claude runtime contract](../docs/claude.md#runtime-contract).

Claude progress displays fixed, privacy-preserving activity descriptions in both
dashboards, with a waiting fallback when details are missing. Liveness timestamps
can update without resetting stall detection; only new assistant content or
session startup advances Claude's activity clock. See
[progress and stall detection](../docs/claude.md#progress-and-stall-detection).

Credential-free Linux packaging verification runs in CI using the production Claude
image and entrypoint. To repeat it locally with Docker:

```bash
docker build --target claude -f runner/Dockerfile -t symphony-claude-smoke .
bash runner/claude/smoke.sh symphony-claude-smoke
```

The smoke uses a disposable nonroot container, empty memory tracker and disabled
network. It checks HTTP state with the expected missing-auth gate, Linux owned
subprocess cleanup, and TERM shutdown without a forced kill. The current CLI
returns exit 1 for supervisor shutdown; the smoke accepts that convention or exit 0.
It mounts no host credentials and makes no model requests. This proves packaging and process behavior; authenticated native
sessions and updater transition/rollback acceptance require separate evidence.

Both coding providers use the existing `agent.max_concurrent_agents` and
`agent.max_concurrent_agents_by_state` scheduler controls. Claude has no separate
one-worker ceiling for normal dispatch. Explicit authentication/policy rechecks
retain one recovery owner until success, then resume configured parallel work.
Legacy positive `claude.max_concurrent_agents` values remain
accepted but are ignored; migrate the desired limit into the `agent` section.
Changing limits affects new dispatch; existing sessions keep their ownership and
may temporarily exceed a reduced limit until they finish. The state API reports
the effective global setting as `agent.max_concurrent_agents`.
