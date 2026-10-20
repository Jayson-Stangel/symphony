# Logging Best Practices

This guide defines logging conventions for Symphony so Codex can diagnose failures quickly.

## Goals

- Make logs searchable by issue and session.
- Capture enough execution context to identify root cause without reruns.
- Keep messages stable so dashboards/alerts are reliable.

## Required Context Fields

When logging issue-related work, include both identifiers:

- `issue_id`: Linear internal UUID (stable foreign key).
- `issue_identifier`: human ticket key (for example `MT-620`).

When logging Codex execution lifecycle events, include:

- `session_id`: combined Codex thread/turn identifier.

## Message Design

- Use explicit `key=value` pairs in message text for high-signal fields.
- Prefer deterministic wording for recurring lifecycle events.
- Include the action outcome (`completed`, `failed`, `retrying`) and the reason/error when available.
- Avoid logging large payloads unless required for debugging.

## Scope Guidance

- `AgentRunner`: log start/completion/failure with issue context, plus `session_id` when known.
- `Orchestrator`: log dispatch, retry, terminal/non-active transitions, and worker exits with issue context. Include `session_id` whenever running-entry data has it.
- `Codex.AppServer`: log session start/completion/error with issue context and `session_id`.

## Checklist For New Logs

- Is this event tied to a Linear issue? Include `issue_id` and `issue_identifier`.
- Is this event tied to a Codex session? Include `session_id`.
- Is the failure reason present and concise?
- Is the message format consistent with existing lifecycle logs?

## Tracker tool outcomes

Codex and Claude share redacted `Tracker tool outcome` diagnostics. Search by
`issue_id`, `issue_identifier` and `tool_id`; Claude includes its session ID, while
Codex includes the native thread ID (startup callbacks can precede a turn ID).
The JSON diagnostics record provider, operation type, document SHA-256, phase,
elapsed time, HTTP status, GraphQL response category and allowlisted GraphQL/transport codes.
`graphql_result=request_error` identifies a definite pre-execution rejection;
`execution_error` can include partial writes or null bubbling. `INVALID_INPUT`
is retained as a diagnostic code, not treated as proof of non-execution. Outstanding
intent markers retain the latest phase across restarts. No raw query, variables,
response body, exception text or credential is stored in these diagnostics.

A read error is correctable and does not mean an uncertain write. For a mutation
or unclassified request, `uncertain` requires checking the external outcome before
explicit reconciliation. A `started` marker left by a dead worker means execution
may have begun; a `response_received` marker may mean delivery was interrupted.
Do not automatically clear either marker based solely on its phase.
