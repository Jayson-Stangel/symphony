# Frozen 0.0.6 registry transport recovery

The original publisher run 40610142828/1 failed while validating GHCR's upload
Location. Its retained log does not contain the Location, so its actual shape
remains unknown. The separate adapter accepts bounded opaque upload sessions on
the fixed HTTPS GHCR repository only, including singular/plural upload endpoints.
It repairs both the returned Location and outgoing request validation. Unknown
paths/query shapes fail closed, with sanitized structural evidence retained.

This workflow changes no customer package. Package source remains
`ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b`; the transport controller has its own
reviewed feature revision and `GITHUB_SHA`. It imports original qualification
40588897389/3 through successful local import 40596350252/2 and pins artifact
12776502137's service digest and all 17 accepted signed-file hashes. It reuses
the frozen candidate's importer, provenance and raw graph publisher. Completed
external records use `verify_origin`; current-run source checks are not spoofed.

After normal PR checks and independent review, the release coordinator must
present the exact controller commit, four original graph destinations/digests,
tag, job budget, effects and cleanup before requesting production execution.
Pushing **exactly** `v0.0.6-transport-repair-1` at that reviewed feature commit
starts one Ubuntu job behind the existing `production-release` environment.
This is separately approved production recovery, not ordinary PR validation.
No tag push, environment review or registry write is authorized by this runbook.
Existing tag-triggered workflows must be inventoried before the tag is pushed.

The job has contents/actions read and packages write, shared production
concurrency, a 120-minute job bound, and the inherited per-repository one-hour
authorization deadline. Only the original bounded two-attempt 401 authorization
refresh remains; it is journaled. Workflow attempts after attempt 1 are refused.
There is no automatic retry after an unknown Location or failed transfer.

Every accepted input and pending graph is verified before registry mutation;
preexisting refs receive full anonymous closure checks. Newly transferred refs
receive full anonymous closure checks before success. The hash-chained bounded
journal records both source identities, structural Location hashes, response
statuses and four graph results. Raw URL/state/token values are never retained.
Partial remote blobs or abandoned upload sessions may remain after failure;
do not delete unknown state or claim transactional rollback.

The coordinator independently verifies all four anonymous graph closures after
the job, then separately authorizes the bounded retry of the **unchanged** ec2
publisher with the original import and both production reviews. The publisher's
normal public-reference/native/package checks remain. This recovery does not
create a GitHub release, move main or the production tag, rebuild/sign packages,
submit to Apple, update stable installations, or prove fresh-account acceptance.
