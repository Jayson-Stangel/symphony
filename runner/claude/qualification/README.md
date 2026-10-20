# Claude qualification lab

This development harness is the first gate for subscription-backed Claude support.
It does not change the production Symphony launcher, Codex, or any stable service.
The production transport decision remains open until qualification passes.

## Candidate runtime

- Node 24.18.0, with the base image digest pinned in `Dockerfile`.
- SDK and CLI pins come from the production `../package.json` and `../package-lock.json`.
  The lab imports the same runtime pin owner; it has no separate dependency manifest.
- Native Linux ARM64/X64 executable explicitly resolved from the pinned CLI package;
  login and SDK execution use that same executable. The SDK's own bundled executable
  is not selected implicitly. Dependencies are locked; install scripts are disabled.

These are candidate pins, not a claim of tested subscription funding.

## Offline checks

From this directory:

```bash
npm --prefix .. ci --ignore-scripts --no-audit --no-fund
node --test test/*.test.mjs
for file in *.mjs; do node --check "$file"; done
bash -n lab
./lab build
./lab test
./lab inspect
./lab status
```

`lab build` uses the parent production dependency context with a Dockerfile-specific
allowlist; host dependency directories, credentials and other files are excluded.
The fixed `:runtime` image tag is resolved to an immutable image ID for each invocation.
If upgrading from the old independent lab setup, remove obsolete local
`qualification/node_modules` before local checks so Node uses the parent install.

`test` and `inspect` have no network access. `status` invokes native auth status,
never a model prompt, and prints only a normalized category. Logged-out status exits
nonzero. No native credential files or raw status payloads are printed.

The image runs as UID/GID 11001 with a read-only root, no capabilities and no-new-privileges.
Two dedicated labeled lab volumes retain native HOME/config and a harmless workspace.
The launcher refuses foreign-labeled images/volumes and pins each invocation to the
resolved image ID. No host home, source checkout, Codex auth, container socket,
tracker credentials or project env files are mounted or forwarded.

## Native login and bounded live test

The owner first confirms an eligible personal plan and disables paid usage credits/
overages in the account. Turning off automatic credit reload alone is insufficient.
Then run in a real terminal:

```bash
./lab login
./lab status
```

Choose the Claude subscription account in Anthropic's native sign-in flow. Paste any
sign-in codes only into the native terminal prompt. Host Claude sign-in does not
authenticate this isolated volume. Do not copy host credential files into it.

After explicit owner authorization:

```bash
./lab live --confirm-overages-disabled
```

This sends two short Sonnet turns in one streaming-input session. Turn one remembers
a random nonce. Turn two must recall it and call one in-process MCP tool. The tool
writes only `/lab-workspace/qualification.txt`, with fixed harmless contents. No shell,
tracker or arbitrary file tools are exposed. Filesystem settings and ambient MCP
loading are disabled. A fresh lab workspace is required. Errors stop the experiment;
there is no automatic retry, alternate model or funding fallback.

Evidence contains fixed event categories and counters, not prompts, arbitrary native
errors or credential values. The output explicitly leaves funding and cancellation
qualification pending: session success alone proves neither. The live deadline is
120 seconds; closing a session is not yet proof of process-tree cancellation.

Do not delete volumes or rerun a failed/partially completed experiment blindly. Inspect
the retained evidence and create a deliberately separate run if needed. This initial
lab intentionally exposes no destructive cleanup command.

## Remaining qualification

- Capture live final-result, auth and tool behavior against the candidate versions.
- Prove interruption of a harmless long-running child and absence of delayed writes.
- Qualify skills/instruction loading without ambient hooks or unapproved settings.
- Confirm native login survives container replacement and characterize renewal failure.
- Record account-side included-allowance and paid-usage evidence after reporting settles.
- Compare the SDK/helper complexity with the direct CLI control surface; record one ADR.

The separately authorized cancellation probe is:

```bash
./lab cancel --confirm-overages-disabled
```

It sends one turn with only Bash exposed. A permission callback allows exactly one
fixed foreground command: record a PID, sleep 15 seconds, then write a sentinel.
All other commands and background execution are denied. After the shell starts,
the harness snapshots its actual descendants, requests SDK interruption, checks
their process identities, closes the session and keeps the container alive another
16 seconds. A surviving process or delayed sentinel fails qualification. Evidence
contains only categories and counts. This probe does not establish production
supervision correctness; later Elixir integration must repeat lifecycle checks.

No account dashboard scraping, API-key tests, deliberate quota exhaustion or stable
process transitions are part of this lab. Production readiness/policy enforcement and
the complete tracker workflow belong to later implementation tickets.

## First-party references checked October 19, 2026

- [Subscription SDK usage](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Native authentication](https://code.claude.com/docs/en/authentication)
- [Streaming input](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)
- [Custom SDK tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)
- [Usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)
- [SDK and distribution scope](https://code.claude.com/docs/en/agent-sdk/overview)
- [Native binary and authentication terms](https://code.claude.com/docs/en/legal-and-compliance)
