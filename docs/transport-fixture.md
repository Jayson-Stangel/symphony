# Early transport fixture

This is a default-off mode of the existing `macos-local-signing-import.yml`
manual caller. It exercises transport before candidate freeze, qualification or
signing. It does not qualify a package, execute an image, admit a production
artifact or exercise the production publisher caller. The normal production
path still requires main source and the existing protected environment; its
inputs, permissions and job bodies are unchanged apart from excluding fixture
mode. Protected branch/tag allowlists are unchanged.

Do not dispatch or create staging merely because this code exists. Approval must
identify the exact reviewed execution revision, repository/account, fixture
bytes, token roles, one-run budget, cleanup and retained state. The mode requires
manual dispatch by `Jayson-Stangel`, exact live `dev` source, explicit
`transport_fixture_only=true`, matching `approved_revision`, and attempt 1.
Changing source or repeating the run requires a new applicability assessment.
The dispatcher sets no `GITHUB_*` overrides. Both normal production import and
fixture admission keep their own trusted `staging_ingress` source contracts.

## Roles and bounded effects

Only three Ubuntu jobs run in fixture mode, sequentially, with five-minute job
limits. There are no native allocations, image/package builds or Apple effects.

| Job | Actual requested GITHUB_TOKEN permissions | Action |
| --- | --- | --- |
| `transport-source` | contents write; actions read | Read the one original private asset by ID and upload one opaque immutable artifact |
| `transport-reader` | contents read; actions read | Independently verify original run/attempt/job/artifact IDs, service digest, closed manifest and exact payload bytes |
| `transport-registry` | contents read; actions read; packages write | Verify the reader succeeded, then exercise the canonical GHCR client with one fixed tiny graph |

These permissions mirror the corresponding successful historical token roles.
The fixture runs on dev without the production environments: the environments
allow only main or v* tags. Explicit fixture approval authorizes these bounded
nonproduction effects; it neither bypasses nor proves protected production
admission. A caller/job/permission/configuration change requires reassessment.
Reading an artifact with an operator token does not prove a job token works.

The source data is `scripts/release/fixtures/transport-fixture-v1.json`. Its exact
165-byte opaque ZIP has SHA256
`482d1958552ae15e11c2de25afe9ac2401b052e6b21ccac73650db58665affe1`.
The ZIP is never unpacked by either transport endpoint. The graph has one
422-byte manifest, one 441-byte config and one 54-byte compressed layer: 917
bytes total. These are synthetic transport data, never a runnable or qualified
product. Literal bytes are retained rather than recompressed across Python
versions.

The only registry destination is the existing allowlisted repository:
`ghcr.io/jayson-stangel/symphony-codex@sha256:89592386ae9565b9fd3428236f9a0d2cabce69e281b02025d0c030dc6096e9a1`.
No mutable image tag, package visibility, production release, Git ref, main or
stable installation is changed. If the fixture manifest already exists, or all
its blobs exist, stop rather than silently omit the upload-session proof or
regenerate a fixture under unchanged approval.

## Approved execution sequence

1. Finish code review and required dev CI. Bind the proposed execution to the
   resulting exact live dev revision and reviewed fixture/input hashes before
   requesting service approval. A feature code head is not an executable dev
   identity; do not substitute a historical revision or backdate proof.
2. In an isolated checkout of that exact source, materialize the fixed ZIP from
   `transport_fixture.fixture()` outside the checkout. With separate explicit
   creation approval, the operator calls the existing
   `staging_archive.stage_archive` once using the existing authorized GH CLI
   identity. Retain its original creation response/nonce and original release
   ID before upload; retain its original asset ID, size and service digest after
   upload. Persist a safe pending-request receipt before each effect. No signing
   command, final-package validation or second controller is involved.
3. Dispatch this existing workflow once at that exact dev source with
   `transport_fixture_only=true`, `approved_revision=<exact dev SHA>`, and the
   original `staging_release`, `staging_asset`, `staging_digest` from step 2.
   Service-assigned IDs cannot be known before creation; approval covers one
   owned allocation, and execution fills these fields only from its verified
   original response. Never invent IDs or discover replacements by tag.
4. The registry helper proves the graph is absent, prevalidates every local
   byte, and uses the actual canonical publisher. After the first positively
   confirmed blob PUT (201, exact streamed byte digest and returned digest), it
   records a planned interruption. Only that checkpoint permits the one approved
   continuation: recheck remote digest/size by HEAD, revalidate the same local
   graph, and call the normal publisher again. The committed blob is reused.
   Final success requires full anonymous hashes of manifest, config and layer.
5. Inspect original job/run/source/permission records and the real result,
   including selected allocations, execution and scheduling intervals. Preserve
   the source-bound original artifact ID and service digest, plus bounded
   structural registry evidence. No credential, upload URL/state, Authorization
   header or raw exception is retained. Existing bounded 401 refresh remains;
   no new uncertain-write retry is introduced.
6. The operator deletes only the positively identified owned draft release and
   its single asset, using the original release ID and verifying absence with
   actual 404 results (a permission/network error is not absence). Verify no
   fixture Git ref exists; do not delete an unexpected ref. Preserve the exact
   cleanup outcome. No broad package/blob/version deletion is allowed.

Any actual failure stops the fixture. In particular, an unknown POST/PUT result
never triggers another create/upload/continuation. Preserve confirmed IDs and
report unknown effects for reconciliation. The planned confirmed checkpoint is
not a failed network request. If cleanup cannot be verified, leave the residual
explicit; do not claim rollback.

There is at most one draft with one asset, one immutable handoff artifact and
one untagged fixture graph. The artifact is capped at 64 KiB by the consumer and
retained one day. The registry graph/blobs remain as explicitly approved tiny
untagged content; abandoned sessions may remain after interruption/failure.
Structural evidence is capped at 64 entries/32 KiB in job logs/summary, with no
second evidence artifact. Logs/summaries follow existing repository retention.
The allocation ceiling is three Ubuntu jobs, at most 15 runner-minutes total;
actual runtime and wall-clock critical path must be measured separately. No
price/token/billing saving is inferred.

## Local proof and historical evidence

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/release/tests -p test_transport_fixture.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/release/tests -p test_staging_glue.py
```

These are pure peers, not actual service capability. The production cores remain
unchanged. Successful original private fixture run 40581169862/1 at
`251e92d7e79c62a02551fcab5906107f959d7dbd`, main import 40596350252/2 at
`ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b`, and registry repair 40613288188/1 at
`02ffcdc1fc3e7b53db1e6102bc3b569d76e3ce06` remain historical service evidence.
Staging/import bytes and permissions unchanged since those records can inform
applicability; they do not become a current run. The newly connected canonical
client still requires the approved fresh service demonstration. All final-source
qualification/signing, native acceptance and production approvals remain separate.
