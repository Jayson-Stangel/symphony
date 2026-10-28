# Prove transport compatibility before candidate freeze

Run the focused local protocol tests before expensive candidate qualification or
Apple acceptance. The normal publisher now uses the same bounded upload-target
and outgoing-resource validators as the successful frozen registry recovery.
The adapter remains independently usable with the old candidate; importing it
lazily avoids changing that frozen recovery composition. Token acquisition,
fixed repositories, exact graph preflight, byte verification, deadlines, bounded
401 refresh and the refusal to retry uncertain writes remain authoritative.

```sh
python3 -m unittest discover -s scripts/release/tests -p test_transport_compatibility.py
python3 -m unittest discover -s scripts/release/tests -p test_graph_publication.py
python3 -m unittest discover -s scripts/release/tests -p test_registry_transport_recovery.py
python3 -m unittest discover -s scripts/release/tests -p test_staging_ingress.py
python3 -m unittest discover -s scripts/release/tests -p test_staging_glue.py
```

These credential-free tests cover the actual canonical client with synthetic
responses, exact opaque state, foreign/ambiguous destinations, interrupted
responses, explicit continuation using existing graph preflight/HEAD checks,
and exact bytes. They cannot establish real account permissions or service
behavior. No matrix, required context, automatic production gate or release
controller is added.

## Bind evidence to the inputs it exercised

Keep the original service receipts and their reviewed tool revision separately.
Record a transport input snapshot immediately before the service demonstration.
Create a **non-secret** context JSON with exactly these fields:

```json
{
  "actor": "Jayson-Stangel",
  "authentication": "github-actions",
  "repository_visibility": "private",
  "configuration_digest": "sha256:REPLACE_WITH_64_HEX_DIGITS"
}
```

Use `gh-cli` for an operator CLI demonstration. The configuration digest hashes
an inspected, non-secret record of actual repository visibility, workflow/job
permissions, selected protected environments and token-issuer/role configuration.
Do not hash credentials as a substitute for this record; do not put credentials,
Authorization headers, signed URLs or upload state in either file. The actor
and authentication fields are operator metadata, never verified token identity.

```sh
python3 scripts/release/transport_compatibility.py snapshot --context /path/to/context.json > /path/to/transport-inputs.json
python3 scripts/release/transport_compatibility.py compare --context /path/to/current-context.json --previous /path/to/transport-inputs.json
```

The tool hashes the static local Python import closure of the staging, ingress,
publication and comparison entry points, plus the consuming publication/import
workflows. It compares exact file sets as well as bytes and context. A changed
input/context exits 1; malformed or missing input exits 2. A match exits 0 and
means only that the recorded inputs match. It is not a service-capability,
qualification, signed-package, native-install or production-approval verdict.
Dynamic import changes require reviewing the inventory before reuse. Unrelated
prose or a new overall source revision alone does not invalidate unchanged
transport inputs; record both original and current source revisions honestly.

Invalidate the relevant service proof when token issuer, actor/role, permissions,
repository visibility, environments, endpoint behavior, staging/graph primitives,
workflow consumers or the exercised resources change. Token revocation/expiry,
asset deletion/replacement, expired Actions artifacts or a new service failure
also invalidate applicability even when local comparison matches. Check current
service identities and accessibility before reuse. Never infer capability from
an asserted actor, permission list, local snapshot or an HTTP mock.

## Existing real evidence and its limits

The 0.0.6 staging/import and graph repair establish actual service results at
their original sources, rather than a new demonstration at this PR's source:

* Original private draft/asset identity and protected immutable ingress were
  consumed by [local import 40596350252, attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40596350252/attempts/2),
  artifact `12776502137`, digest
  `sha256:4e811bb59217f382c58d103743642de5eaafb9c63a1ec5bb5ec20c9e1b7fa31e`.
  Package source was `ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b`.
* [Registry repair 40613288188, attempt 1](https://github.com/Jayson-Stangel/symphony/actions/runs/40613288188/attempts/1)
  used controller `02ffcdc1fc3e7b53db1e6102bc3b569d76e3ce06`, the unchanged
  candidate's transfer primitives and the recovery adapter. All four original
  graph closures were independently downloaded and hashed. The retained journal
  is artifact `12784766469`, ZIP SHA256
  `c15d260479b062919c3d9692a6e1e97d0a61c5a790c59eaf4d2bdd7c7a7d5cfb`.
* [Original publisher 40610142828, attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40610142828/attempts/2)
  subsequently succeeded with those existing graphs. It did not exercise a fresh
  upload through the newly connected canonical client. The original failed
  upload Location was not retained; its precise shape remains unknown.

Do not manufacture a pre-run snapshot for these historical operations. Reconcile
original checked-out files, permissions and original receipts before reusing any
part of that evidence. The new local tests prove routing to the same validator;
they do not turn the old operations into a new-source real-service run.

## Proposed smallest real-service decision (not authorized by this document)

Owner: the retrospective coordinator for JAY-772, with the transport owner
executing only the approved fixture. Account/repository: `Jayson-Stangel` /
`Jayson-Stangel/symphony`. Registry destination: the existing allowlisted
`ghcr.io/jayson-stangel/symphony-codex` package, **one untagged synthetic manifest**,
one synthetic config and one synthetic compressed layer. No provider execution,
image build, signing, Apple submission, production release/tag, main promotion,
stable installation or package-visibility/settings change. Do not expand the
repository allowlist to create a second transport.

Prepare the exact fixture bytes, reviewed controller revision, input snapshot,
authentication role, manifest/config/layer digests and immutable resource budget
in the approval request before any write. Use a tiny fixture, at most 16 KiB for
the graph and 16 KiB for one opaque staging ZIP. Pin both inventories locally.
The synthetic graph is transport data, never a runnable or qualified product.

A first bounded **operator CLI** demonstration can exercise `staging_archive.stage_archive`,
`local_distribution.download_staging`, `graph_publication.publish_selected` and
`registry_closure.registry_available(..., full=True)` without introducing a
workflow. Its approval scope is one private draft with one asset, original-ID
readback, original asset byte/digest verification, one untagged graph transfer,
then anonymous full closure. Use the existing fixed-origin transport and the
operator's existing approved GH credential in memory; never log/export it.
This proves that operator role only, not an Actions job-token role.

Stop once after the first confirmed blob commit but before manifest publication
in an explicitly reviewed fixture callback. Preserve only content digests and
confirmed original IDs, not upload URLs/state. After separately observing current
remote digest/size state, allow **one explicit continuation** of the same graph
through the normal publisher: it revalidates all inputs, HEADs committed blobs,
and opens fresh sessions for absent blobs. Unknown POST/PUT results stop the
fixture; no blind retry, guessed replacement ID or resume of secret upload state.
A second failure stops for diagnosis, not another unchanged run.

Cleanup is part of that decision: delete only the positively identified fixture
draft and its one asset by the recorded original release ID, then verify their
absence. No tag should have been created; verify that rather than delete an
unknown ref. Do not delete existing package versions or shared blobs. The tiny
untagged content-addressed graph/blobs may remain in the existing package and
must be explicitly approved as retained fixture state (at most 16 KiB content,
excluding service metadata). Abandoned upload sessions are service-owned state;
report unknown residuals instead of claiming transactional rollback. If deletion
or result verification fails, record the exact unresolved ID/effect and stop.

For **hosted job-token/private-draft/immutable artifact** compatibility, reuse the
original protected-ingress evidence only if its actual inputs/role remain valid.
Otherwise a separate exact hosted fixture proposal is required: one producer and
one independent reader, one immutable Actions artifact with original artifact ID,
run/attempt/source/service digest, bounded retention and explicit cleanup. Reuse
`staging_ingress` trusted source contracts; do not spoof `GITHUB_*`, weaken main
production admission or route opaque fixture bytes into native acceptance. The
current production workflow is not a cheap fixture: it also schedules native
admission. This PR does not add or dispatch a replacement controller. A CLI-only
result must leave this hosted-role gap explicit.

Record actual request counts/elapsed time and confirmed results separately from
local tests and historic evidence. No token/cost saving is inferred. The issue
remains unfinished if its required service proof has not been demonstrated or
explicitly satisfied by valid original evidence. All final artifact, native,
source-bound qualification and production approval obligations remain in force.
