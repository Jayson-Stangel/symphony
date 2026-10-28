---
name: release
description: Prepare a Symphony manager release through reviewed dev integration, approved main promotion, exact-source qualification and signing, native acceptance, and protected publication. Use when asked to release, tag, or retag Symphony.
---

# Release

Implementing release tooling, merging into `dev`, promotion, qualification,
Apple credential use/submission, tag/publication and stable rollout are distinct
authority boundaries. Track existing explicit approval; do not infer authority
for a later effect from a prior step. Follow repository `AGENTS.md` and the exact
[production runbook](../../../docs/macos-release-signing.md#protected-production-preparation-and-publication).

1. Inspect status, remotes and instructions. Verify this fork's integration
   `dev`, production `main`, global command's stable target and any
   `SYMPHONY_HOME` override. Use a focused `codex/` branch from latest verified
   `dev` in an isolated development checkout. Never modify stable or unrelated
   work, global links or stable processes.
2. Confirm a fresh manager version against live tags/releases and the requested
   scope. Set canonical `scripts/release/manager-version`; preserve committed
   schema2 and the independent engine version in `elixir/mix.exs` unless engine
   scope requires changing it. Equal version numbers or trees do not establish
   equal source. Never republish or overwrite a completed release; select a fresh
   version. Never move a published tag without explicit approval.
   `manager_version.py` binds the tag to the manager
   version and refuses completed-release reuse.
3. Run focused tests and applicable full gates including `make -C elixir all`;
   prepare a reviewed feature PR to `dev`. Land only with required gates/review
   and actual merge authority. A requested open/unmerged handoff stays open.
   Packaged `.codex/skills/` edits are behavioral inputs, not documentation-only:
   the Codex Docker stage copies these skills. Recompute component input
   identities and qualify changed components; never promise old component reuse.
4. Freeze integrated source `D`. Prepare the dev-to-main promotion PR and refresh
   every included PR/link/purpose against live main. Present the promotion PR and
   exact head and obtain explicit approval for the complete scope before merge.
   Renew approval if included changes/revisions change. Observe the actual
   promoted main commit `P`; never relabel a dev candidate as `P`, even if trees
   match. Preserve old qualification/signing evidence for its original bytes.
5. With exact-source execution approval, run **`burrito-release.yml` on main**
   using `production_prepare=true`, `engine_only=false`, `controller_only=false`.
   Keep main at `P` through preparation/signing. Require full runner/native
   helper/package and both engine smoke qualifications, then retained
   production-reference native packages and the prepared component publication
   plan. This stage performs no production registry/GitHub release writes.
   Record successful preparation **`Q` and its exact attempt**, source, artifact
   IDs/digests, selected component originals and omissions. Ordinary diagnostics
   or skipped/failed full producer jobs cannot satisfy Q.
6. Assess reuse from actual effective inputs and immutable original evidence.
   Production planning only reuses originals whose canonical public GHCR digest
   graphs are available. Unpublished/private matching candidates require real
   current-source builds under existing policy; never flip an old reuse record
   into a new build. Refresh native helpers/packages/source-bound Q at `P`.
   Preserve independent component/engine versions and original provenance.
7. Obtain explicit credential-use/Apple-submission approval for `P` and `Q`.
   Verify operator-configured environment protections/secret names against the
   runbook without reading secret values. Keep existing `macos-signing` dev-only;
   production uses separate main-only **`macos-production-signing`** with required
   review and no administrator bypass. Dispatch `macos-production-signing.yml`
   on main with `approved_revision=P`, `qualified_run=Q`,
   `qualified_attempt=Q_ATTEMPT`. Retain successful signing **`S` and exact
   attempt**. Main movement, wrong Q/artifacts, signing/notary/assessment/cleanup
   failure or incomplete evidence refuses readiness; never use unsigned fallback.
8. Verify retained final `production-signed-P-S-ATTEMPT` bytes and receipts:
   original/signed helper hashes, certificate/team/runtime/timestamp, both Apple
   Accepted submissions, helper online ticket lookup, final DMG staple/Gatekeeper
   assessment, post-staple hashes and complete cleanup. Perform separately
   authorized fresh-account browser/quarantine install/version/doctor/reinstall
   and isolated installed update/rollback/retry with final signed tar/metadata.
   Do not remove quarantine or bypass security. Record native gaps explicitly;
   fixtures and old provider smokes do not prove final-byte Apple acceptance.
   Preserve already valid provider evidence; do not repeat authenticated feature
   tasks absent changed consumed inputs or a discovered failure.
9. Only after exact tag/publication approval, create the matching annotated
   version tag at `P` and push it. Tag push itself does not publish or qualify.
   Explicitly dispatch **`production-publish.yml` on that tag** with
   `approved_revision=P`, `signed_run=S`, `signed_attempt=S_ATTEMPT`. The approved
   source must remain on protected main; live peeled tag/source checks must pass.
   Do not substitute another Q/S or rerun attempt silently.
10. Inspect final assessed artifacts/native evidence before approving the first
    protected `production-release` job's immutable registry transfer. It rechecks
    origins and anonymous availability, preserves digests and requires its
    returned manifest to equal the prepared signed selection. Packages are not
    rebuilt after signing. Finish any pending final-public-reference native or
    updater checks before the **separate final release job approval**. Verify
    exact remote asset inventory/digests and no stale/unsigned draft assets before
    completed GitHub release publication. Partial registry transfer is not a
    completed stable release and is not automatically rolled back.
11. Record exact final P/tag, Q/S attempts, final hashes, component provenance,
    executed checks/native results and remaining limitations. Standalone engine
    binaries retain independent native smoke/source evidence; manager-helper
    notarization does not establish Apple trust for those engine assets.

Stable rollout remains separately approved. Present the complete inventory since
the deployed revision, proposed stable revision and runtime/project targets;
then use only the installed `symphony update`. Promotion, tagging, publication or
signing approval alone never authorizes stable changes. If the updater refuses,
stop and report its limitation rather than bypassing or repairing stable directly.
