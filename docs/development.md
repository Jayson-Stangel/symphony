# Dedicated development workspace

Keep the installed `symphony` command and its stable checkout unchanged. Use a
separate clone of `dev`, an explicit development command, separate configuration,
a Docker resource namespace and a separate loopback port. A different checkout
or configuration directory alone does not isolate Docker resources.

Prerequisites: Docker running, Git, Bash, curl and Python 3. Native Claude login
is interactive. The container builds its own pinned runtime; host Symphony setup
and global command/skill installation are unnecessary.

## Create the workspace

Choose a new directory; do not overwrite an existing checkout. Replace the remote
with your intended upstream if necessary:

```sh
workspace="$HOME/Repos/symphony-dev-workspace"
mkdir "$workspace"
git clone --branch dev https://github.com/Jayson-Stangel/symphony.git "$workspace/source"
git -C "$workspace/source" switch -c codex/claude-dev-workspace
mkdir -p "$workspace/bin" "$workspace/config/projects" "$workspace/config/runners"
chmod 700 "$workspace/config" "$workspace/config/projects" "$workspace/config/runners"
cat > "$workspace/bin/symphony-dev" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export SYMPHONY_HOME="$workspace/source"
export SYMPHONY_CONFIG_HOME="$workspace/config"
export SYMPHONY_RUNNER_HOME="$workspace/config/runners"
export SYMPHONY_BIN_DIR="$workspace/bin"
export SYMPHONY_SKILLS_DIR="$workspace/skills"
export SYMPHONY_RUNNER_NAMESPACE=claude-dev
export SYMPHONY_CODEX_AUTH_VOLUME=symphony-claude-dev-codex-auth
unset SYMPHONY_RUNNER_IMAGE
case "${1:-}" in
  update)
    shift
    exec "$SYMPHONY_HOME/scripts/symphony" update --development-runner "$@" ;;
  run)
    shift
    for arg in "$@"; do
      [ "$arg" != --host ] || { echo 'Use the isolated development container' >&2; exit 1; }
    done
    exec "$SYMPHONY_HOME/scripts/symphony" run --port 14030 "$@" ;;
  auth|runner|--version|--help|-h)
    exec "$SYMPHONY_HOME/scripts/symphony" "$@" ;;
  *) echo 'Use: symphony-dev auth|run|runner|update|--version|--help' >&2; exit 1 ;;
esac
SH
chmod 755 "$workspace/bin/symphony-dev"
"$workspace/bin/symphony-dev" --version
```

Use a unique namespace and port for each additional development workspace. Do
not export these development variables globally or run the stable updater from
this wrapper. The ordinary global `symphony` command remains the stable command.

## Prepare a disposable target repository

Clone a private disposable target under `projects/` and provide its test
`WORKFLOW.md`. Restrict its tracker scope and hooks to that repository. Keep
production trackers and production repository credentials out of this workspace.
The tracker operates on the remote service: a second local clone does not
isolate issues, comments or pull requests.

Credentials use `config/projects/<repository-identity>.env`: the basename plus
Symphony's existing hash of the normalized `origin` URL. `init` and a missing-credentials
`run` message report the exact path, including configuration overrides. The development
wrapper deliberately omits `init`; derive the same identity read-only from its source,
from inside the disposable target clone:

```sh
repository_identity=$(bash -c 'source "$1/scripts/symphony" --help >/dev/null; project_id "$(repo_root)"' identity "$workspace/source")
credentials="$workspace/config/projects/$repository_identity.env"
printf '%s\n' "$credentials"
```

Save a fine-grained token restricted to that disposable repository in that file,
with `GITHUB_TOKEN=...`. Grant Contents,
Issues and Pull requests read/write for a GitHub issue pilot. Set file mode 600.
Never paste the token into chat, commit it, or use the broad host GitHub login as
the worker credential.

From inside the disposable target clone:

```sh
dev="$HOME/Repos/symphony-dev-workspace/bin/symphony-dev"
"$dev" auth claude login
# Finish the native login in this terminal.
"$dev" auth claude check
"$dev" runner doctor --agent claude
"$dev" run --agent claude
```

The first login builds the namespaced development image. Login is separate from
host Claude, the qualification lab, and stable; it does not copy credentials.
Check/doctor make zero model requests. `run` polls the configured tracker and can
dispatch open work immediately. Start with one small disposable issue. Visit
<http://127.0.0.1:14030>, inspect provider/readiness, and verify the workpad, tests
and test PR. Confirm included allowance and no paid usage in the account UI.

To finish a test without accepting more work:

```sh
"$dev" runner drain
# Wait for the dashboard to show zero running workers, then:
"$dev" runner stop
```

Stop retains the qualified container, workspaces, state, logs and native login.
Use the same development command for status/logs/recovery. Never stop or remove
containers by a broad name filter.

## Refresh the development build

Preserve local edits. Inspect status and divergence before integrating upstream:

```sh
git -C "$HOME/Repos/symphony-dev-workspace/source" status --short
git -C "$HOME/Repos/symphony-dev-workspace/source" fetch origin dev
git -C "$HOME/Repos/symphony-dev-workspace/source" merge --ff-only origin/dev
# From the disposable target clone, with an existing development runner:
"$dev" update --agent claude
```

The wrapper supplies `--development-runner`. This requires a nonempty namespace
and uses the current development source without fetching `main`, changing global
links or running host setup. It builds a revision-tagged namespaced candidate,
preflights it, drains active work, retains the prior container, and resumes only
after readiness succeeds. On a retry, development mode rebuilds and preflights
from the current checkout. If its image differs from the pending candidate, the
updater verifies ownership, drains/stops that candidate, and replaces it while
retaining the original rollback source and volumes. It commits the new image
identity atomically before starting it, so an interrupted refresh can be retried.
A failed build or preflight leaves the pending candidate unchanged; a failed new
candidate keeps the rollback source available. No temporary workflow/model
changes are needed to escape a stale development candidate. Stable update
recovery continues to use its recorded image. `runner status` identifies
restarting containers explicitly instead of reporting them as running.
A stopped source is supported. A fresh project uses
`run` instead. If the updater refuses, inspect the reported condition; do not
delete metadata or force-replace active work. For a retained failed transition:

```sh
"$dev" update --rollback-runner
```

Stable deployment requires a reviewed `dev` → `main` promotion and the installed
global `symphony update` command. Promotion and stable rollout need separate explicit
operator approval for the complete PR inventory, exact revisions and rollout targets;
development testing or landing a feature in `dev` supplies neither approval. See
[repository approval boundaries](../AGENTS.md#explicit-approval-for-production).

## Opt-in updater lifecycle proof

From a separate development checkout, prepare both production images explicitly:

```sh
docker build --file runner/Dockerfile --target codex -t updater-proof:codex .
docker build --file runner/Dockerfile --target claude -t updater-proof:claude .
python3 runner/updater-smoke.py updater-proof:codex updater-proof:claude
```

This optional script uses the actual application, entrypoints, Docker mounts,
HTTP drain/resume and update/rollback metadata in an empty Memory project. It
checks interrupted creation, interrupted rollback, cleanup recovery and retained
committed, edited, untracked and state files. It records physical mounts and UID.
Only Codex login-status and selection of prepared image variants are test peers;
Claude's fresh-volume missing-auth refusal uses its actual native check. The
prepared images remain unchanged; temporary containers, volumes and image tags
use a unique development namespace and are removed. Local diagnostic files are
retained in the printed temporary directory. No stable command, host login mount,
native positive authentication or model request is used. Successful mixed-provider
UID migration and authenticated inference remain separate qualification.

### Native standalone engine qualification

The `burrito-release` workflow can be manually dispatched on a feature branch.
A branch dispatch builds candidate artifacts and runs exactly two native standalone
smoke targets (Linux x86-64 and macOS ARM64), without publishing
a GitHub release or GHCR images. Production publication still requires its tag,
repository variable and protected environment gates. Dispatch the exact reviewed
head and record the workflow/job URLs; any subsequent commit requires another
candidate run. A skipped or unavailable native runner is an acceptance gap.
For bounded standalone-only native acceptance, dispatch the same exact feature
head with `engine_only=true`. Both retained native engines build and execute;
runner images and publication are intentionally omitted. This is standalone
engine evidence, not a claim that image/package qualification ran.
For a focused controller regression diagnosis, dispatch the same workflow with
`controller_only=true`: this runs only the macOS ARM64 fixture tests and skips
artifact building, native engine qualification and publication. Its success is
controller evidence only, never a substitute for the full native candidate run.

Each build retains the executable, SHA-256 sidecar and `.source.json` identity
(source revision, target, artifact name and checksum). On a matching native host:

```sh
python3 scripts/release/engine-smoke.py /path/to/symphony-REV-linux_x86_64 \
  --source-revision FULL_SOURCE_SHA --target linux_x86_64 \
  --evidence-dir /tmp/engine-evidence
python3 -m unittest discover -s scripts/release/tests -p test_engine_smoke.py -v
```

Python 3, `ps` and `lsof` are test controller dependencies, separate from the engine's
bundled runtime. The controller checks native OS/CPU and artifact identity, copies
the executable outside the checkout, clears ambient credentials and runtime paths,
and uses temporary homes, extraction/cache, logs, workspaces and an empty memory
tracker. It observes actual dashboard content and typed empty JSON state for a
bounded period, attributes the loopback listener to the new process session, then
requires a zero exit after SIGTERM and disappearance of that session, listener and
tracked descendant identities. A private inherited ownership descriptor lets the
controller find detached and double-forked children after reparenting; an engine
that discards this descriptor fails qualification. Ancestry snapshots supplement
that ownership marker. Inspection is limited to the test-owned private file and
process identity metadata, without reading ambient process environments.
Forced cleanup always fails qualification. Evidence artifacts retain identity,
state, dashboard, startup/application logs and shutdown/cleanup results.

The fixture selects Claude with no native helper installed and requires truthful
`runtime_unavailable` readiness. No issue is dispatched and no authenticated model
request is made. Engine buildability, native server liveness, native-agent login,
and installed-manager lifecycle are separate claims: this check proves the first
two, and deliberately does not claim authenticated inference or issue-to-PR proof.
Regression subprocess fixtures demonstrate rejection of early/banner exits, bad
HTTP/state, false readiness, startup/shutdown timeouts and surviving, detached or double-forked children;
those fixtures are controller tests, not substitutes for native artifact execution.
The fixture HTTP server binds numeric loopback directly without reverse DNS;
startup failure logs include fixture stacks and the last connection error. This
avoids a proven hosted Intel macOS `getfqdn` stall without altering the engine.

## Candidate preflight and CI ownership

Ordinary pull requests retain the named required quality, package and metadata
checks. Component plans separately decide whether optional reference images need
new qualification. Unchanged compatible components retain their original source
and native evidence; current manager packages use current-source helpers. Manual
full candidates also qualify the two supported standalone host engines. Tags
retain the protected production environment and publication conditions; a
candidate run never grants publication approval.

Portable release metadata and package-input/shell checks run once before native
qualification. Native process and shell-updater fixtures run once per OS/CPU,
separately from provider image qualification. New unclassified release suites
conservatively enter the native group. Missing native prerequisites or skipped
fixtures fail qualification. Portable preflight produces an exact source output. Native fixtures, package/helper
checks, selected image fan-out and engine builds then start independently; no image or engine
job waits for an unrelated macOS helper queue. Final qualification joins the required
native and helper evidence. Final manifests join verified runner records; standalone
smokes consume actual built engines. No placeholder records replace artifacts.

The Burrito caller owns portable qualification and passes its successful exact-head
output to the runner workflow, avoiding a second qualification. A direct runner
candidate performs its own portable qualification. Native/helper checks run concurrently
with images in the runner workflow, and its terminal verdict requires their success. The runner workflow rejects absent,
failed, cancelled, skipped or wrong-head qualification, except its explicitly
caller-owned path; its final verdict also requires all images and metadata.
The shell/controller tests support early feedback, not native image acceptance.

PR and branch groups cancel obsolete work for the same workflow/event/ref. Tag
operations do not cancel each other. Explicit `retain_diagnostic` runner runs and
standalone controller-only diagnostics use run-specific groups so deliberately
retained evidence survives new commits. Other branches and workflow scopes have
separate groups. Existing owned-resource cleanup remains part of each test.

Run `python3 scripts/ci/release_tests.py portable` and
`python3 scripts/ci/release_tests.py native` to exercise the same suite selection.
The native group requires `lsof`; these commands report actual suite names,
pass/fail/skip counts and elapsed execution time. They are not image-cache or
native engine artifact evidence. Full candidate/job timelines remain necessary
for queue/build/export/transfer performance measurements.
## Ubuntu package candidate qualification

Build the portable helper on native Linux x86-64 with
`bash scripts/release/build-linux-host-helper.sh /tmp/linux-helper`. Installed
Ubuntu 26.04 hosts need no Swift or helper interpreter. Shared contract tests run
with `SYMPHONY_TEST_HOST_HELPER=/tmp/linux-helper/symphony-host python3 scripts/package/test-package.py -v`;
retain native OS/architecture, source SHA, SDK/toolchain and artifact checksums.

For real two-revision lifecycle qualification, manually run `runner-release` on
the exact feature head with `previous_revision` set to a real earlier candidate
that already supports Linux packages. Inspect publication conditions first: the
manual feature-ref run must retain `publish=false` and cannot publish images or
releases. It builds both actual source revisions and runs the clean Ubuntu26.04
source-independent installed manager/updater against real Docker. Candidate
controller tools are test dependencies, distinct from installed product runtime
dependencies. See [the evidence contract](packaged-install.md#native-candidate-evidence).



## CI event and documentation policy

| Event | Documentation-only | Behavioral or unknown change |
| --- | --- | --- |
| PR to dev or main (including later commits) | Full-PR classification and dependency-free PR metadata validation; named required wrappers report omission | Always-on metadata, policy regression and workflow validation; only affected application, portable, native helper/package, process/updater and browser consumers execute. Ordinary checks do not invoke reference-image planning, builds or payload retrieval. |
| Merge/push to dev or main | Before/after classification; named wrappers report omission | The same consumer routing from the exact before/after range; PR metadata validation is a PR event only. |
| Manual full candidate | No documentation shortcut | Portable preflight, both native host engines/helpers, selected reference-image components and current manager packages. Lifecycle joins consume the exact requested current/prior artifacts. |
| Manual `manager_only_update` candidate | No documentation shortcut | Requires an explicit previous package artifact and a complete plan with zero image builds. Reuses original immutable environments without image export/upload; installed update/rollback validation remains required. |
| Manual `engine_only` / `controller_only` diagnostic | No documentation shortcut | Only the selected engine/native-fixture or controller path executes. Omitted image/package work is not qualified by the diagnostic. |
| Version tag | No documentation shortcut | Candidate qualification plus separately gated publication; protected production environment and release variable checks remain in force. |

The `listener-and-cleanup` context is produced by `package-checks` on ordinary
events and requires success or intentional omission for each selected consumer.
Only complete portable, both-host helper/package and native fixture qualification
emits `qualified_revision`; partial qualification and omission emit no full-source
receipt. It adds no duplicate builds or fixture runs. The
manual `runner-integration` workflow retains its separate strict artifact/package/
lifecycle guard. Fixture success is not a newly qualified image or an installed
selected-environment composition receipt. Reference-image planning is optional distribution work. A team's installed image
selection and its dependency-bearing authenticated task need their own installed
evidence; a successful reference-image graph does not establish that proof.
Browser checks compare the actual consumed browser stage graph and COPY paths
across the complete event diff, without consulting reference-image provenance.
Pure manager changes omit browser work; actual browser inputs or its build/test
machinery still require the browser layer and both-user smoke checks. Unknown
syntax or history requires qualification. Provider-final Dockerfile metadata does
not by itself change the browser target. This is change routing, not a claim of
new or reused image qualification.

Image-input changes can require matching provider/architecture builds. Changing
only the host manager does not qualify or relabel an unchanged embedded runtime.
Missing, failed, cancelled, unexpectedly skipped or wrong-source prerequisites
fail their consuming joins. The plan records intentional reuse separately from
new execution, so a skipped build is never reported as a newly qualified image.

The classifier uses the complete PR merge-base-to-head diff, or the exact push
before/after diff. It disables rename detection so both old and new paths are
classified. Missing history, empty/invalid event ranges, unknown paths and diff
failures require behavioral qualification. Workflow-level path filters are not
used for required checks, so documentation PRs do not leave pending contexts.
The classification job lists paths and the decision in its summary. Required
wrappers reject failed, cancelled, missing or invalid classifier outputs.

The required `browser-smoke / chromium` check uses the same event diff with a
separate `browser` decision. Unrelated Elixir/application and prose changes omit
checkout, image build and browser execution and explicitly report qualification
as intentionally omitted, not executed. Missing/invalid/empty history requires
browser qualification; failed/cancelled classifiers or malformed outputs fail
the wrapper before any omission can pass.

Its dependency closure follows the actual consumers:

- `runner/Dockerfile` projects the `browser-tools` stage and its transitive Node
  base stage; only consumed COPY sources count, including the browser package
  manifests, lockfile and smoke script. Provider-final metadata and unconsumed
  prose under `runner/browser/` do not alone change that projection.
- `.dockerignore` and `runner/Dockerfile.dockerignore` control the build context.
- `.github/actions/build-runner/` builds that target and invokes
  `scripts/release/runner-cache-policy.sh`; `runner/node-smoke.sh` verifies Node.
- `.github/workflows/browser-smoke.yml`,
  `.github/workflows/change-classification.yml` and `scripts/ci/` own routing,
  required wrapper semantics and their regressions.

Keep this set aligned with new COPY sources, build stages and invoked tools.
The later application/Claude/Codex stages are not dependencies of `browser-tools`.
Unsupported syntax or unsafe COPY sources require qualification; this is routing,
not historical-image provenance reuse.
Relevant changes retain root and UID/GID 11001 Chromium and CLI coverage. Release
and manual candidate paths continue their existing actual-image qualification
through `scripts/release/runner-smoke.sh` without consulting the browser decision.

### Complete CI consumer audit (JAY-712)

This table covers every workflow job, reusable workflow and local action. The
routing owner is `scripts/ci/classify_changes.py`; its explicit consumers are
`application`, `portable`, `package_macos`, `package_linux`, `fixtures`, plus the
existing Docker-stage-aware `browser` decision. `native` and `native_platform`
are derived from helper/fixture requirements and validated again in the aggregate.
Ordinary CI uses no workflow path filters. Tag events always select complete
qualification, including moved tags with valid documentation-only ranges. A
missing/empty range selects every consumer; malformed event JSON fails classification. Failed/cancelled/missing
classification, malformed outputs, inconsistent derived flags, unexpected skips,
or failed native matrices fail the required aggregate.

| Ordinary workflow / jobs | Actual inputs and reason to run |
| --- | --- |
| `change-classification / classify`; each caller's `changes` | Always: full PR merge-base/head or exact push before/after paths, both sides of renames and deletions; dependency-free Python and Git. |
| `pr-description-lint / validate-pr-description` | Every applicable PR metadata event; PR template, body and validator. Explicit cheap exception. |
| `make-all / make-all` | Always validates routing/metadata regressions and all workflow definitions with checksum-pinned actionlint. Elixir/mise/dependencies/Node setup and `make all` only for application inputs. |
| `package-checks / portable` | Selected portable policy/controller tests, shell/package input checks and release version/schema validation; ordinary events execute this once, before native allocation. A caller may supply only successful exact-source preflight evidence. |
| `package-checks / qualification` | Always verifies local/caller preflight source or explicit omission; prior-source verification when explicitly requested. Cheap status/source gate. |
| `package-checks / host-policy` | Only if native work is selected; calls `host-policy / select` with `all`, `macos` or `linux_x86_64`. The retained target policy stays authoritative. |
| `package-checks / native-host-package` | One allocation per affected host: native helper builds and source-independent package/selection/environment/protocol tests when that platform's helper/package inputs change; native process/updater fixtures when their inputs change. Helper-only work omits process fixtures; fixture-only work omits helper compilation and artifacts. Linux helper qualification retains clean Ubuntu 26.04 execution without development tools. |
| `package-checks / macos-package`, `listener-and-cleanup` | Always present, strict status/decision/source joins with explicit omission summaries. Full exact-source receipt only for the complete qualification set, never for omitted or partial work. |
| `browser-smoke / chromium` | Always-present guard; only builds/tests for changed browser-consumed stages/COPY sources, context controls, shared build action/cache controller or CI routing/validation controls. Both users retain actual Chromium and CLI smoke. |

Application closure: `elixir/` source/tests/dependency locks/config/build tools,
Claude transport/runtime/tests, runner entrypoints, launchers and packaged
behavioral skill/WORKFLOW inputs. `elixir/mix.exs` also affects portable release
version checks. Helper/package closure: native Swift source and platform locks,
build/package controllers, installed shell/environment scripts, packaged skills
and `elixir/WORKFLOW.md`, packaged team Dockerfile templates, manager envelope/version/schema/contracts and their
transitive metadata/input validators (including conditional imports at file granularity) and fixtures. A regression derives the packer's Python import closure from the actual sources, so these shared validators cannot silently become portable-only. Shared Swift/package inputs
select both hosts; Linux SDK/controller inputs select Linux only. Build identity
changes alone are not treated as changes to every consumer's behavior.

The portable suite inventory in `scripts/ci/release_tests.py` was audited: metadata,
Docker input graphs, provenance, archive/registry joins, controller command peers,
Git histories, and shell selection with fake native helpers do not require a Mac
runner, Swift build or Docker image. These run once on Ubuntu. Real loopback/process
ownership (`test_engine_smoke.py`) and installed shell updater/locking/activation
(`test_package_update.py`) retain both-host execution. New suites default to native;
new/unrecognized controllers and workflow/action paths conservatively select all
consumers until their dependencies are reviewed. Tests imported by native helper
fixtures (including component provenance and original manager metadata) fan out
to both helper consumers even when their own unit suite is portable.

| Explicit manual/reusable/release workflow / jobs | Dependency/event reason (no ordinary diff shortcut) |
| --- | --- |
| `release-preflight / portable` | Full portable policy/version/schema gate for caller-selected candidates; full Git history for retained-source fixtures. |
| `host-policy / select` | Cheap validation of exactly native Linux x86-64 and macOS ARM64; platform filter preserves this policy. Container architectures remain separate. |
| `release-native / host-policy`, `native` | Full native fixtures for explicit engine-only qualification; package-checks now runs its native fixtures inside its helper allocation instead of calling a second matrix. |
| `native-manager-helpers / host-policy`, `helper`, `qualification` | Explicit integration needs actual exact-source native helpers; verifies source/platform/contracts and emits a receipt only after the complete matrix succeeds. |
| `current-manager-package / host-policy`, `package`, `metadata` | Assemble selected manager packages from exact-source qualified helpers; verify original checksums before executable permission, selected metadata and both native archives before joining. |
| `component-plan / plan` | Explicit composition: source-bound original evidence discovery; selected component build/reuse decisions, never manager-change-implies-image-rebuild. |
| `runner-integration / changes`, `portable`, `helpers`, `plan` | Explicit selected-environment composition: full preflight and native helper qualification before dependent work. |
| `runner-integration / reference-node-smoke` | Only explicit `reference_node_smoke`: build and test both reference harness Docker contexts and their Node/CLI controls. No ordinary allocation. |
| `runner-integration / runners`, `manager-inputs`, `manager-package` | Build only component plan's affected provider/architecture entries, bind original immutable records and assemble matching current manager packages. |
| `runner-integration / listener-and-cleanup` | Strict selected-component/package join and actual installed lifecycle, original-payload verification, owned-resource cleanup and retained evidence. Distribution alias probe only when explicitly requested. |
| `burrito-release / qualification`, `host-policy` | Full candidate preflight and supported host policy, with controller-only exception. |
| `burrito-release / engine-fixtures`, `build`, `smoke`, `controller-diagnostics` | Selected explicit engine/controller diagnostic or full candidate: both supported native engines and actual standalone acceptance; controller-only runs only native controller fixtures. |
| `burrito-release / runners`, `release` | Full candidates request matching runner/package composition; publication only for version tags, enabled production variable and production environment after successful build/smoke/runner joins. |
| `runner-release / portable`, `qualification`, `component-plan` | Full or successful exact-source caller preflight; explicit mode/input/source validation, then immutable component planning. |
| `runner-release / candidate`, `candidate-metadata` | Plan-selected native provider/container architecture builds and actual-image qualification; join exact records without turning skipped/reused builds into new evidence. Both Linux container architectures and providers remain supported. |
| `runner-release / package-inputs`, `packaged-candidate`, `packaged-candidate-metadata`, `ubuntu-package-lifecycle`, `candidate-qualification` | Explicit package composition requires full package qualification, exact helper artifacts, native archives, requested prior package lifecycle and strict terminal joins. `compose_packages=false` intentionally omits this cohort. |
| `runner-release / publication-preflight`, `publish`, `packaged-release-platform`, `packaged-release` | Tag-only, production-gated publication and source-bound native package assembly; full selected artifact qualification retained. |
| `runner-release / cache-probe`, `lifecycle-diagnostic`, `recover-native-records`, `recovered-record-qualification` | Only explicit corresponding mode/run inputs; retained originals, native producer/source/failed-step provenance and cleanup gates remain intact. No automatic diagnostic runs. |
| `runner-release / retained-transfer-timing`, `transfer-benchmark`, `transfer-replay`; `transfer-timing / retained-payload-timing` | Only selected immutable run/provider/architecture diagnostics; download/measure/verify original payloads, with explicit replay and native producer guards. |
| Local action `build-runner` | Browser/candidate/probe consumer only: target, Docker recipe/COPY/ignore closure, scoped cache policy, native buildx and load, then caller-owned actual runtime qualification. |
| Local actions `configure-runner-cache`, `runner-cache-runtime` | Explicit candidate cache setup and scoped Actions cache environment; composite transitive actions and `runtime.js` route portable tests. These candidate-only cache controls and probe controllers do not allocate an ordinary browser build; `build-runner` and its actual cache policy still do. |

The previous two ordinary Mac allocations (native fixtures and helper/package)
now become one. Portable suites no longer repeat on each native host. Combining
is equivalent because no fixture consumes a separately produced helper artifact;
when both are selected they run sequentially in the same exact-source checkout.
Release engine fixtures remain separate because they qualify an explicit engine
candidate rather than ordinary helper/package inputs. Manual selected-image
assembly still consumes matching qualified helper artifacts; no release identity,
artifact source guard, runtime cleanup or provider/platform proof is replaced.

Supersession remains scoped by workflow/event/PR-or-ref. Production tags and
retained candidate diagnostics are not cancelled. Explicit integration runs have
run-specific groups and do not cancel another operator-requested composition.
Queue, execution and wall-clock timings are separate measurements: eliminating
an allocation reduces exposure to shared-runner queues, not a queue-time SLA.
No extra manual hosted or paid diagnostic is implied by this policy.

The reference run `39993349119` contains a cancelled first attempt and a successful
retry. Its 24m42s created-to-completion total spans both attempts. The successful
Mac helper retry queued for 6m17s and executed for 68s; the Mac fixture executed
for 1m56s. The roughly 23-minute ready-to-final-helper-start interval includes
cancellation/retry, so it must not be reported as one runner queue measurement.
Compare future attempts' queue, execution and total durations separately.

Human-facing prose is explicitly allowlisted in `scripts/ci/classify_changes.py`.
Packaged agent skills and runtime `WORKFLOW.md` templates remain code inputs.
The Python standard-library PR validator enforces template headings/order,
nonempty sections, bullets, test checkboxes and removal of placeholder comments;
it needs no Elixir, Mix, downloaded dependencies or setup action.

For changes to this CI policy, run:

```sh
python3 -m unittest discover -s scripts/ci/tests -v
make -C elixir all
```

The first command replays real temporary Git histories for PRs to both integration
and production, multiple documentation commits, merge pushes, mixed changes,
renames/deletions, stale bases and missing history. It also executes the required
wrapper guard from an empty workspace and checks failure/cancellation propagation.
These are isolated event-replay results, not claims that GitHub-hosted jobs ran.
Pure documentation changes run the cheap policy suite and workflow lint, but omit application setup/build/tests.
