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
workflows and exact fixture JSON. The fixture executor is a required entrypoint;
missing or symlinked executor/data inputs fail closed. It compares exact file sets as well as bytes and context. A changed
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

## Approved fixture execution remains a separate decision

The default-off mode of the existing manual import caller now has a focused
implementation. See [Early transport fixture](transport-fixture.md) for the exact
165-byte ZIP, 917-byte graph, three separate Actions-token roles, source/owner
admission, one artifact, confirmed checkpoint/continuation, cleanup and retention.
An operator CLI token is used only for the separately approved original private
staging allocation and cleanup; it cannot substitute for those Actions roles.

Bind the final reviewed live dev source, all executor/data/workflow hashes and
service-effects budget before asking for one explicit live approval. The presence
of these commands, a matching input snapshot, local tests and a dev merge grant
no dispatch, staging/registry-write, protected production or native authority.
New service-assigned IDs come only from the one approved original allocation,
never from a guessed or replacement tag lookup. Preserve historical receipts at
their actual source; do not backdate a snapshot or call an operator read fresh
Actions capability. The production publisher's protected caller remains separate.

No new required context or automatic production gate is added. The fixture issue
stays unfinished until the contract's actual service evidence and cleanup have
been accepted; software integration alone is not completion.
