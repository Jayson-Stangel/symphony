# Codex runner updates

Develop in an isolated checkout and runner namespace. Set
`SYMPHONY_RUNNER_NAMESPACE` to keep development containers, workspaces, state,
and authentication separate from production. This build supports Codex only;
`--agent codex` remains available for existing scripts. Claude configuration and
other provider selections fail before dispatch.

The launcher retains the existing container labels, metadata, storage identities,
transition journal, immutable image verification, and updater ownership checks.
`update --development-runner --agent codex` refreshes an isolated Codex runner.
A running legacy listener without the drain API must finish active work and be
paused explicitly with `update --pause-runner` before replacement. The updater
refuses unknown ownership or inconsistent metadata and retains the prior runner
for rollback if candidate startup fails.

Drain state persists across restarts. A replacement runner starts drained and
cannot dispatch until the updater verifies readiness and resumes it. Resume
requires zero active workers. Legacy provider pause and write-intent files are
retained as opaque evidence; this Codex-only runtime does not interpret them.
Review tracker and remote write state before an approved rollout because active
issues can become eligible again when the new runtime starts.

The shared tool write classification and global pause policy are removed. Codex
receives the Linear tool's ordinary success/failure response, including GraphQL
errors. Symphony does not automatically retry Linear HTTP requests. A model may
issue a fresh request after a failure, as in the original Codex implementation.
Redacted operation diagnostics and signed evidence asset URLs remain available.

Approval to merge into dev does not authorize promotion to main or deployment.
Any production change requires approval of its exact PR inventory, and stable
rollout requires separate explicit authorization. This document does not grant it.
