# Logging Best Practices

This guide defines logging conventions for Symphony so agents can diagnose failures quickly.

## Goals

- Make logs searchable by issue and session.
- Capture enough execution context to identify root cause without reruns.
- Keep messages stable so dashboards/alerts are reliable.

## Required Context Fields

When logging issue-related work, include both identifiers:

- `issue_id`: tracker-owned internal issue ID (stable foreign key).
- `issue_identifier`: human ticket key (for example `MT-620`).

When logging agent execution lifecycle events, include:

- `session_id`: Codex thread/turn pair or Claude turn event ID
  (`claude:<session_id>:<request_id>`). Cleanup diagnostics may use the native
  session ID; correlate the shared session segment.

## Message Design

- Use explicit `key=value` pairs in message text for high-signal fields.
- Prefer deterministic wording for recurring lifecycle events.
- Include the action outcome (`completed`, `failed`, `retrying`) and the reason/error when available.
- Avoid logging large payloads unless required for debugging.

## Scope Guidance

- `AgentRunner`: log start/completion/failure with issue context, plus `session_id` when known.
  Native allowance yield and cleanup logs use the captured, sanitized native session identity.
  Claude turn event IDs are `claude:<session_id>:<request_id>`; the shared session segment
  correlates those terminal logs with the exhausted session's turn events.
- `Orchestrator`: log dispatch, retry, terminal/non-active transitions, and worker exits with issue context. Include `session_id` whenever running-entry data has it.
- `Codex.AppServer`: log session start/completion/error with issue context and `session_id`.

## Checklist For New Logs

- Is this event tied to a tracker issue? Include `issue_id` and `issue_identifier`.
- Is this event tied to an agent session? Include `session_id`.
- Is the failure reason present and concise?
- Is the message format consistent with existing lifecycle logs?

## Tracker tool outcomes

Codex and Claude share redacted `Tracker tool outcome` diagnostics. Search by
`issue_id`, `issue_identifier` and `tool_id`; Claude includes its session ID, while
Codex includes the native thread ID (startup callbacks can precede a turn ID).
The JSON diagnostics record provider, document SHA-256, phase, elapsed time,
HTTP status, response/delivery reason and allowlisted GraphQL/transport codes.
The request fingerprint correlates calls without parsing or classifying GraphQL.
Operation kind, execution certainty and request-versus-execution response
categories are not inferred. `INVALID_INPUT` remains a diagnostic code; it is not
proof that a write did not execute. No tracker outcome suppresses delivery to the
agent or pauses project dispatch. No raw query, variables, response body,
exception text or credential is stored in these diagnostics.

`accepted` means the response was delivered, not that the external effect was
reconciled. `delivery_failed` means a reply or acknowledgement was lost and the
affected issue needs recovery. The agent owns readback and correction; HTTP 200
or a failed response alone cannot establish whether a write occurred. Legacy
intent files remain issue-local recovery evidence; their phase alone does not
prove execution or rollback. New calls do not create dispatch-gating markers.

The issue-local legacy intent notice is retained for existing state volumes only.
It reads a bounded metadata record to identify the matching issue, never infers
execution from its phase, and does not block dispatch. New calls do not create
these files. Keep the notice and ignored legacy pause kinds until retained volumes
have been inventoried and an evidence-preserving retirement can be made; this is
not a runtime gate or automatic purge. Files are never deleted by ordinary
attempts. The existing explicit reconciliation control remains operator-invoked.
