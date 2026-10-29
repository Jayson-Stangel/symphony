# Dedicated development workspace

Keep the installed `symphony` command and its stable checkout unchanged. Use a
separate clone of `dev`, an explicit development command, separate configuration,
a Docker resource namespace and a separate loopback port. A different checkout
or configuration directory alone does not isolate Docker resources.

Prerequisites: Docker running, Git, Bash, curl and Python 3. Native Claude login
is interactive. The container builds its own pinned runtime; host Symphony setup
and global command/skill installation are unnecessary.

## Disposable CI Codex host preparation

Native image and installed-package CI consumers explicitly run
`scripts/ci/prepare-codex-apparmor.sh prepare` before protected Codex qualification,
then run its cleanup with `always()`. This administrator operation is limited to
GitHub-hosted Linux with a verified local selected Docker daemon. It checks the
shipped profile checksum/name, kernel/LSM and per-thread AppArmor interface before
loading a new profile in enforce mode. It installs the parser only after those
checks, and refuses a preexisting named policy of unknown provenance. Cleanup
removes only the invocation-owned profile; prepare/cleanup logs are retained even
when qualification fails. Explicit Bash pipefail preserves failures through log
capture.

An advertised AppArmor capability, a loaded policy name and successful runtime
application are distinct evidence. Protected qualification records the immutable
image ID and security policy identities and verifies the applied profile inside
the actual container before executing the existing workspace/process/network
effect checks. This is credential-free proof on native Linux x86-64 and ARM64
container runners, separate from supported Linux x86-64/macOS ARM64 manager hosts.
No-AppArmor Docker hosts retain the shipped selection behavior. Product launchers
do not install host policy; this CI preparation does not change a stable host or
qualify authenticated inference.

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
| Draft PR to dev or main | Full-PR classification and dependency-free metadata validation; named required wrappers report omission | Classification, metadata, policy/workflow validation and affected fast families execute. Affected Elixir, native helper/package/process and browser work is deferred; each relevant required context fails until ready qualification succeeds. |
| Ready PR, including `ready_for_review` and later pushes | Full-PR classification and metadata validation; named required wrappers report omission | Every affected fast and expensive consumer executes for the current revision. Marking ready on the same SHA never credits deferred work as executed. |
| Merge/push to dev or main | Before/after classification; named wrappers report omission | The same consumer routing from the exact before/after range; PR metadata validation is a PR event only. |
| Manual full candidate | No documentation shortcut | Portable preflight, both native host engines/helpers, selected reference-image components and current manager packages. Lifecycle joins consume the exact requested current/prior artifacts. |
| Manual `manager_only_update` candidate | No documentation shortcut | Requires an explicit previous package artifact and a complete plan with zero image builds. Reuses original immutable environments without image export/upload; installed update/rollback validation remains required. |
| Manual `engine_only` / `controller_only` diagnostic | No documentation shortcut | Only the selected engine/native-fixture or controller path executes. Omitted image/package work is not qualified by the diagnostic. |
| Version tag | No automatic production work | Tag push does not qualify, sign or publish. Explicit main preparation runs full qualification; protected main signing retains final bytes; explicit exact-tag publication imports those successful Q/S attempts and requires protected production approvals. |

The `Package qualification` context in `package-checks.yml` joins every selected
portable/native result and verifies its exact-source receipt. The former
`macos-package` and redundant ordinary `listener-and-cleanup` jobs are one gate.
Only complete explicit portable, both-host helper/package and native fixture qualification
emits `qualified_revision`; ordinary checks, partial qualification and omission emit no full-source
receipt. `Runner integration` has an ordinary `Browser qualification` gate and
an explicitly manual `Installed manager integration` lifecycle. Fixture success
is not selected-environment composition evidence. A team's authenticated task
still needs its own installed proof.

Browser routing compares the actual browser stage/COPY closure plus final
provider runtime recipes. Unrelated application source edits omit browser work;
final USER/ENV/RUN changes, browser inputs and build/test controls require it.
Source/version/input-identity metadata labels alone do not trigger a build.
Unknown syntax or history requires qualification. Both final images share one
Buildx builder and its common layers; smoke runs by immutable local image ID,
as the actual root Codex and UID/GID 11001 Claude users, without network or mounts.
Explicit integration instead tests the original restored image IDs, once per
provider/image, without extra image builds. Release smoke uses the same helper.

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

The required `Runner integration / Browser qualification` check uses the same event diff with a
separate `browser` decision. Unrelated Elixir/application and prose changes omit
checkout, image build and browser execution and explicitly report qualification
as intentionally omitted, not executed. Missing/invalid/empty history requires
browser qualification; failed/cancelled classifiers or malformed outputs fail
the wrapper before any omission can pass.

Its dependency closure follows the actual consumers:

- `runner/Dockerfile` projects the `browser-tools` stage and its transitive Node
  base stage; only consumed COPY sources count, including the browser package
  manifests, lockfile and smoke script. Final provider runtime instructions are compared too; source/version metadata
  and unconsumed prose do not alone change that projection.
- `.dockerignore` and `runner/Dockerfile.dockerignore` control the build context.
- `.github/actions/build-runner/` builds that target and invokes
  `scripts/release/runner-cache-policy.sh`; `runner/node-smoke.sh` verifies Node.
- `.github/workflows/runner-integration.yml` owns ordinary browser/global controls.
  Strict YAML comparison can exclude changes confined to its audited manual-only
  jobs and dispatch inputs. New jobs or uncertain YAML stay conservative.
- `.github/workflows/change-classification.yml` and unknown shared `scripts/ci/`
  controls retain broad qualification. The known classifier and YAML comparator
  run the unconditional policy regressions and portable workflow contracts without
  browser builds. Explicitly inventoried policy regression files under
  `scripts/ci/tests/` also run cheaply; unknown tests stay broad.

Keep this set aligned with new COPY sources, build stages and invoked tools.
Application source changes alone do not change the browser-stage source closure;
final runtime instructions are also validated because smoke now tests final images.
Unsupported syntax or unsafe COPY sources require qualification; this is routing,
not historical-image provenance reuse.
Relevant changes retain root and UID/GID 11001 Chromium and CLI coverage. Release
and manual candidate paths continue their existing actual-image qualification
through `scripts/release/runner-smoke.sh` and the installed lifecycle controller,
without consulting the ordinary browser decision.

### Complete CI consumer audit (JAY-712)

The tables below cover every workflow job, reusable workflow and local action.
Paths and IDs identify implementation; display names follow the convention below. The
routing owner is `scripts/ci/classify_changes.py`; its explicit consumers are
`application` (full Elixir family), `claude`, `portable`, `package_macos`, `package_linux`, `fixtures`,
`npm`, `readiness`, `codex_config`, `contracts`, plus the
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
| `make-all / make-all` | Always-present required aggregate, routing/metadata regressions and pinned actionlint. Selected fast tests precede draft deferral; every selected ready family must succeed, omitted families must be skipped. |
| `make-all / elixir-checks` | Full Elixir quality/build/coverage/Dialyzer via `make elixir-ci`, on ready events with `application=true`. Starts directly after classification, independently of Mac process contracts. |
| `make-all / claude-checks` | Node-only toolchain, locked npm dependencies, syntax and complete Node suite via `make claude-ci`, on ready events with `claude=true`; no Erlang/Elixir setup. |
| `make-all / platform-contracts` | Selected npm process lifecycle and launcher credential/readiness/lock contracts on Ubuntu 24.04 and macOS 14, including drafts. No helper compilation, engine or updater suite. The required Application checks join rejects failed, missing, cancelled or wrongly skipped selected work. |
| `package-checks / portable` | Selected portable policy/controller tests, shell/package input checks and release version/schema validation; ordinary events execute this once, before native allocation. A caller may supply only successful exact-source preflight evidence. |
| `package-checks / qualification` | Always verifies local/caller preflight source or explicit omission; prior-source verification when explicitly requested. Cheap status/source gate. |
| `package-checks / host-policy` | Only if native work is selected and ready; calls `host-policy / select` with `all`, `macos` or `linux_x86_64`. The retained target policy stays authoritative. |
| `package-checks / native-host-package` | One allocation per affected host: native helper builds and source-independent package/selection/environment/protocol tests when that platform's helper/package inputs change; native process/updater fixtures when their inputs change. Helper-only work omits process fixtures; fixture-only work omits helper compilation and artifacts. Linux helper qualification retains clean Ubuntu 26.04 execution without development tools. |
| `package-checks / package-qualification` | Always-present strict status/decision/source join. Relevant draft deferral fails explicitly. Full exact-source receipt only for complete explicit qualification, never ordinary, omitted or partial work. |
| `runner-integration / chromium` | Always-present guard; only builds/tests for changed browser-consumed stages/COPY sources, context controls, shared build action/cache controller or CI routing/validation controls. Both final provider images retain actual default-user Chromium and CLI smoke; shared builder layers avoid rebuilding common dependencies. |

Elixir closure: `elixir/` source/tests/dependency locks/config/build tools,
production Claude modules consumed by ExUnit integration, runner entrypoints, launchers and packaged
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

The JAY-764 follow-up audits signed-output ingress and frozen registry recovery:
`local_distribution.py`, the staging modules, `ghcr_transport_recovery.py`,
`recover_ec2_registry.py`, its exact-file manifest, and their manual-only ingress
and recovery workflows select portable controller tests. These tests use local
archives and mocked service responses; ordinary CI never performs Apple operations,
private staging writes or registry publication. The registry recovery suite is
portable and runs in the ordinary release-policy executor. Changing that suite
must not leave its sole executor omitted.

Mac admission/preflight/watchdog controllers select their dependency-free policy
tests and portable workflow contracts. Changing their unit tests runs the policy
suite without native helper or browser builds. The known classifier and YAML
comparator likewise select policy regressions and portable workflow contracts.
Unknown CI controls and tests retain broad fail-closed qualification.

The release suite inventory normally selects portable, both package hosts, native
process fixtures, launcher contracts and Codex configuration tests because it
controls those executors; it does not change Elixir, Claude or browser execution.
A narrow exception selects portable only when the sole Python AST change adds
unique valid test filenames to the single top-level literal `PORTABLE` set.
Removals, executor changes and native inventory changes retain their affected
families. Browser qualification follows actual Docker stages/COPY inputs and
shared build/trust controls; an inventory edit alone is not a browser runtime input.

On ordinary PR and push events, changes confined to the audited
`burrito-release.yml` manual controller select portable workflow contracts when
both workflow versions remain `workflow_dispatch`-only, use only the eight audited
job IDs, and preserve all other global controls. Unknown jobs, trigger changes or
global changes select full qualification. Manual and tag events retain complete
qualification regardless of these ordinary-event comparisons.

### Stable application family contract (JAY-765)

The legacy `application` output now selects the complete Elixir family; `claude`
selects independent Node syntax/tests. Ordinary Elixir source edits select only
the former. Claude-only test files and auth/entrypoint/credential/smoke commands
select only the latter. Production Claude modules, lockfiles and JS fixture peers
select both: full ExUnit tests execute `port-probe.mjs` → production `helper.mjs`
and cleanup → temporary/runtime modules. A regression derives that relative-import
closure from the actual ExUnit source rather than assuming all Node code is independent.
Changes to the shared Makefile or toolchain also select both families.

Full Elixir coverage therefore retains the locked npm dependency installation
needed by these integration tests, while omitting the unrelated Node test suite.
This is necessary integration setup, not a claim that Elixir has no Claude dependency.
`make all` remains comprehensive and installs npm dependencies once; it retains
build, formatting, specs/Credo, full coverage, Dialyzer, Node syntax and Node tests.
No partial ExUnit selection is credited as full coverage.

Launcher credential/readiness/locking and Codex file/mount-policy contracts remain
their independently selected Python/native families. Installation/update/runner
lifecycle tests that read or execute shared shell launchers remain in full ExUnit;
the shell inputs continue selecting Elixir as well as their release/launcher consumers.
No launcher test was moved or dropped to obtain the family split.

Each expensive family starts after classification without depending on unrelated
Mac process execution. The required `Application checks` join accepts selected
ready work only with `success`, and omitted/deferred work only with `skipped`;
draft deferral remains non-passing. Malformed family decisions, failed/cancelled/
missing/unexpectedly skipped jobs and failed Mac admission still fail the join.
Manual release/artifact qualification retains its independent source/native gates.

| Explicit manual/reusable/release workflow / jobs | Dependency/event reason (no ordinary diff shortcut) |
| --- | --- |
| `release-preflight / portable` | Full portable policy/version/schema gate for caller-selected candidates; full Git history for retained-source fixtures. |
| `host-policy / select` | Cheap validation of exactly native Linux x86-64 and macOS ARM64; platform filter preserves this policy. Container architectures remain separate. |
| `native-manager-helpers / host-policy`, `helper`, `qualification` | Explicit integration needs actual exact-source native helpers; verifies source/platform/contracts and emits a receipt only after the complete matrix succeeds. |
| `current-manager-package / host-policy`, `package`, `metadata` | Assemble selected manager packages from exact-source qualified helpers; verify original checksums before executable permission, selected metadata and both native archives before joining. |
| `component-plan / plan` | Explicit composition: source-bound original evidence discovery; selected component build/reuse decisions, never manager-change-implies-image-rebuild. |
| `runner-integration / changes`, `portable`, `helpers`, `plan` | Explicit selected-environment composition: full preflight and native helper qualification before dependent work. |
| `runner-integration / reference-node-smoke` | Only explicit `reference_node_smoke`: build and test both reference harness Docker contexts and their Node/CLI controls. No ordinary allocation. |
| `runner-integration / runners`, `manager-inputs`, `manager-package` | Build only component plan's affected provider/architecture entries, bind original immutable records and assemble matching current manager packages. |
| `runner-integration / listener-and-cleanup` | Strict selected-component/package join and actual installed lifecycle, original-payload verification, owned-resource cleanup and retained evidence. Distribution alias probe only when explicitly requested. |
| `burrito-release / production-source`, `qualification`, `host-policy` | Manual candidate preflight and supported host policy; production preparation requires main and full mode before fanout. There is no tag trigger. |
| `burrito-release / build`, `controller-diagnostics` | Each supported engine build immediately qualifies exact standalone bytes; engine-only fixtures share that allocation. Controller-only runs only its selected native fixtures after source validation. |
| `burrito-release / runners` | Manual full candidates request runner/package composition; production preparation retains stable-reference native packages without publication. Legacy unsigned publication is disabled. |
| `macos-production-signing / sign` | Explicit main-only protected signing of successful exact-source production Q; retains final assessed artifacts, no registry or GitHub release writes. |
| `production-publish / inputs`, `registry`, `release` | Explicit exact-tag import of completed Q/S attempts; source/artifact/refusal validation before protected registry transfer and separately protected final GitHub completion. |
| `runner-release / portable`, `qualification`, `component-plan` | Full or successful exact-source caller preflight; explicit mode/input/source validation, then immutable component planning. |
| `runner-release / candidate`, `candidate-metadata` | Plan-selected native provider/container architecture builds and actual-image qualification; join exact records without turning skipped/reused builds into new evidence. Both Linux container architectures and providers remain supported. |
| `runner-release / package-inputs`, `packaged-candidate`, `packaged-candidate-metadata`, `ubuntu-package-lifecycle`, `candidate-qualification` | Explicit package composition requires full package qualification, exact helper artifacts, native archives, requested prior package lifecycle and strict terminal joins. `compose_packages=false` intentionally omits this cohort. |
| `runner-release / publication-preflight`, `publish`, `packaged-release-platform`, `packaged-release` | Tag-only, production-gated publication and source-bound native package assembly; full selected artifact qualification retained. |
| `runner-release / cache-probe`, `lifecycle-diagnostic`, `recover-native-records`, `recovered-record-qualification` | Only explicit corresponding mode/run inputs; retained originals, native producer/source/failed-step provenance and cleanup gates remain intact. No automatic diagnostic runs. |
| `runner-release / retained-transfer-timing`, `transfer-benchmark`, `transfer-replay`; `transfer-timing / retained-payload-timing` | Only selected immutable run/provider/architecture diagnostics; download/measure/verify original payloads, with explicit replay and native producer guards. |
| Local action `build-runner` | Browser/candidate/probe consumer only: target, Docker recipe/COPY/ignore closure, scoped cache policy, native buildx and load, then caller-owned actual runtime qualification. |
| Local actions `configure-runner-cache`, `runner-cache-runtime` | Explicit candidate cache setup and scoped Actions cache environment; composite transitive actions and `runtime.js` route portable tests. These candidate-only cache controls and probe controllers do not allocate an ordinary browser build; `build-runner` and its actual cache policy still do. |

### Fast families and draft adoption (JAY-743)

Npm test-only changes select local configuration semantics plus the two-host
process lifecycle suite. Readiness tests select their cheap executable-identity
fixtures. Codex configuration tests select the file/mount-policy suite; its
boundary, snapshot, launch and readiness implementation inputs retain that
coverage as well as application qualification. Shared launcher changes select
credential/readiness/doctor-lock contracts on both hosts. Their synthetic settings
fixture selects the same consumers. These test-only families require no helper
compilation or unrelated engine/updater fixtures.

Ordinary portable preflight uses `release-policy`: all audited portable release
modules and shell/package-input checks, with policy/config families executed by
their separate selected jobs. Ordinary native `process` excludes only the three
launcher modules moved to `contracts`. Full explicit `portable` and `native`
commands retain their original complete inventories; protected npm command/exec
qualification remains in both original Codex architecture native paths.

Strict YAML comparison can narrow `make-all.yml` changes confined to known fast
test command bodies. Other control changes keep the broad route. Classifier and
comparator changes, unknown tests and missing history remain broad.

All three behavioral workflows handle draft conversion and `ready_for_review`.
Per-PR concurrency groups supersede old ordinary runs across readiness changes;
manual, retained diagnostic and tag/release scopes remain separate. Required
contexts remain Application checks, Package qualification, Browser qualification
and PR description. Selected deferred work fails its own required context rather
than reporting a passing omission, closing the same-SHA ready window until the
replacement run actually qualifies the source.

Merging into dev enables these rules for new dev runs and PR branches containing
them. Active branches must deliberately merge current dev and refresh required
checks. In-flight runs and historical reruns retain their original workflow code.
Hosted savings identify workflow source, event, head and executed jobs, separating
avoided allocations from execution and scheduling time. A green omission wrapper
alone is not evidence that an affected suite executed.

### Path attribution and verified presentation changes

Each classification summary lists every selected component's triggering paths
and the dependency/control rule that selected it, including browser COPY source
matches and recipe/control changes. Omitted consumers explicitly report no
relevant changes. Full-event, unknown-input, missing-history and parser refusals
are listed separately as conservative fallbacks rather than invented path proof.

Existing workflow/composite YAML pairs are compared using the system Ruby Psych
syntax tree. No gems or dependency installation is needed; missing Ruby/Psych,
malformed YAML, aliases, anchors, duplicate keys, custom tags, multiple documents,
new/deleted files and unsupported shapes cannot prove presentation-only. Scalar
spelling and quoting are retained, avoiding YAML 1.1 `on`/boolean coercion.
Only workflow/action `name`, ordinary job `name` and ordinary/composite step
`name` fields can be erased from comparison. Commands, conditions, IDs, matrices,
permissions, action revisions, artifacts, inputs and all remaining YAML must
match. Reusable caller names and provenance-bearing release/helper/package job
or step names remain semantic because evidence consumers address them exactly.
Those cases conservatively follow their ordinary dependency routes. Comments
and equivalent YAML formatting can also prove unchanged structure.

Proven presentation-only paths do not select expensive runtime consumers, even
when mixed with changes to another component. The `policy` decision keeps CI regression, actionlint and gate/protection-contract
validation active for these naming changes. Pure prose emits `policy=false` and
omits those suites and setup, retaining classification/metadata checks and the
always-present status wrappers.
The required-context ruleset is migrated explicitly when names change; the
classifier cannot infer live protection changes or authorize them. Shared
classifier/comparison/controller edits still select their full audited consumers.

### CI display naming and gate migration

All owned workflow, job and step displays use sentence case with spaces and
retain tool/proper-name capitalization. Matrix variants use a parenthesized
platform, or provider and architecture, suffix. Internal YAML IDs stay stable
lower-kebab-case; the renamed `package-qualification` ID replaces the obsolete
Mac-only label. Filenames, action references and immutable artifact names remain
machine identifiers. Display names do not substitute for source/artifact identity.

The required contexts are `Application checks`, `Package qualification`,
`Browser qualification`, and `PR description`, all bound to the GitHub Actions
integration. Migration first adds these alongside the old contexts, then removes
the old requirements only after the replacement checks pass on the final PR head.
Strict up-to-date policy and pull-request/thread protections remain enabled.

Provenance sealing, historical package/component discovery, native record
recovery and transfer diagnostics accept finite old/current display names.
Stored original names still select an exact job in its original run/attempt;
duplicate matches and wrong native hosts fail. The native manager validator
accepts those same original/current producer names. Existing records are not
rewritten. Release step names used as evidence anchors remain explicit and stable.

| Workflow file (without `.yml`) | Internal job ID → display name |
| --- | --- |
| `burrito-release` | `qualification` → Validate preflight inputs; `host-policy` → Select native hosts; `runners` → Qualify runner components; `build` → Verify engine (target), including build, selected native fixtures and standalone smoke; `controller-diagnostics` → Diagnose release controller |
| `change-classification` | `classify` → Classify event inputs |
| `component-plan` | `plan` → Plan selected components |
| `current-manager-package` | `host-policy` → Select native hosts; `package` → Build manager package (platform); `metadata` → Assemble manager package metadata |
| `host-policy` | `select` → Select supported native hosts |
| `make-all` | `changes` → Classify changes; `make-all` → Application checks |
| `native-manager-helpers` | `host-policy` → Select native hosts; `helper` → Build manager helper (platform); `qualification` → Bind qualified manager helpers |
| `package-checks` | `changes` → Classify changes; `portable` → Portable preflight; `qualification` → Validate preflight inputs; `host-policy` → Select native hosts; `native-host-package` → Native helpers and fixtures (platform); `package-qualification` → Package qualification |
| `pr-description-lint` | `validate-pr-description` → PR description |
| `release-preflight` | `portable` → Portable preflight |
| `runner-integration` | `changes` → Classify changes; `chromium` → Browser qualification; `portable` → Portable preflight; `helpers` → Qualify manager helpers; `reference-node-smoke` → Reference Node images; `plan` → Plan components; `runners` → Qualify runner components; `manager-inputs` → Bind selected component inputs; `manager-package` → Build current manager packages; `listener-and-cleanup` → Installed manager integration |
| `runner-release` | `component-plan` → Validate component plan; `candidate` → Build runner (provider, architecture); `publication-preflight` → Validate publication inputs; `publish` → Publish qualified runners; `candidate-metadata` → Assemble candidate metadata; `packaged-release-platform` → Build release package (platform); `packaged-release` → Assemble release packages; `cache-probe` → Measure runner cache (provider, architecture); `packaged-candidate` → Build candidate package (platform); `packaged-candidate-metadata` → Assemble candidate packages; `ubuntu-package-lifecycle` → Installed package lifecycle; `lifecycle-diagnostic` → Diagnose installed package lifecycle; `recover-native-records` → Recover native records (provider, architecture); `recovered-record-qualification` → Validate recovered native records; `portable` → Portable preflight; `qualification` → Validate preflight inputs; `package-inputs` → Qualify package inputs; `candidate-qualification` → Candidate qualification; `retained-transfer-timing` → Measure retained transfer; `transfer-benchmark` → Measure runner transfer (provider, architecture); `transfer-replay` → Replay runner transfer (provider, architecture) |
| `transfer-timing` | `retained-payload-timing` → Measure retained payload transfer |

The graph audit removed the duplicate ordinary package result job and the
standalone browser workflow/classifier allocation. Portable source validation
stays before native fanout so failed or wrong-source preflight cannot allocate
native work; the terminal package join still catches skipped/failed/cancelled
prerequisites. Other joins retain distinct evidence: manager helper receipts bind
a completed matrix; package metadata joins both archives; candidate qualification
joins requested package, image and lifecycle evidence; publication validates that
complete result before the separately approved production boundary. Reusable
host selection remains cheap target policy, not duplicate qualification.
Reference harness, transfer, recovery and engine diagnostics remain explicit
requests. No release environment, approval boundary or native architecture proof
is flattened into an ordinary success gate.

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
run-specific groups and pass `retain_diagnostic: true` into nested runner
qualification, so neither parent nor selected component builds cancel another
operator-requested composition.
Ordinary browser runs use cancellable PR/ref groups.
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
Pure documentation changes run lightweight classification and PR metadata validation; CI regression suites, workflow lint and application setup/build/tests are intentionally omitted.

### Current manager release policy

The normal `burrito-release.yml` caller reads `scripts/release/release-schema`
through portable preflight and forwards it to component planning. Policy 2 builds
changed component inputs and preserves verified original identities for unchanged
inputs. `scripts/release/manager-version` selects the fresh manager release
(currently 0.0.5); `elixir/mix.exs` remains the independent engine version.
Tag mismatch and completed release targets refuse before expensive fanout;
publication also checks again and retains the production environment gate.
Explicit schema1 diagnostics retain their pinned finite-transition restrictions.

Full nonpublishing proof uses a reviewed feature/integration ref, with
`retain_diagnostic=true`, `engine_only=false`, and `controller_only=false`.
Record the exact source, both manager archive identities, all four original
component records and immutable image digests, manifests/checksums, native helper
qualifications and cleanup. The default full candidate omits prior-package
update/rollback and legacy migration when no prior artifact is selected; report
those omissions rather than inferring their success from archive assembly.

### Component rebuild explanations

The retained component plan records each provider/platform candidate's original
source/input identity and run/artifact IDs, plus its actual selection outcome.
Input mismatch is separate from matching evidence refused for payload expiry,
source/workflow trust, native platform, qualification, integrity or service
availability. Production-only anonymous canonical availability has its own
outcome; a valid private artifact alone cannot satisfy it. Missing candidates
are distinguished from discovered incompatible ones.

When an old source commit is already available locally, the controller checks it
out into a disposable clone, parses the actual Docker closure without executing
old code, and compares file bytes/modes and recipe/argument/ignore/directory
controls. It records changed effective paths, including removed inputs. Missing
local history, unsupported old recipes or snapshots that cannot reproduce the
recorded identity are explicit comparison gaps. Diagnostics never fetch more
history, expand discovery limits or authorize reuse; original evidence and all
existing refusal rules remain decisive. Plans with matching verified originals
retain original records and schedule no image build/export/upload. Actual hosted
execution and payload savings must be credited from the corresponding consumer
run, not inferred from a fixture or from an unavailable original.

### Production runner content

The production `common` Docker stage compiles `elixir/lib`, `elixir/config` and
`elixir/priv` against `mix.exs`/`mix.lock`. Static dashboard files and Codex
readiness Python resources are compile-time consumers of `priv` and stay in the
image. `elixir/WORKFLOW.md`, `elixir/AGENTS.md` and canonical `.codex/skills` remain
behavioral inputs. The installed entrypoints execute `bin/symphony`; they do not
invoke repository tests, documentation or the local Makefile. Those files enter
only the explicit `claude-test` qualification stage through its full-source copy.
`runner_inputs.py` derives each production identity from this actual stage/COPY
closure and the Docker ignore rules, so harness edits do not invalidate either
provider while runtime, config, assets, dependencies and skills do.

### Production publication handoffs

The initial production publisher input job is an admission gate. It does not
upload a repacked `retained-production-inputs` artifact: neither protected
consumer used it. Registry and release jobs independently retrieve authenticated
original signing/helper/engine artifacts by service ID and digest after their
separate environment approvals. Within each job the verified local material is
used for graph transfer/comparison or release assembly without another `signed`
retrieval. Source/tag state is checked freshly at each protected boundary;
release mutation also checks it before each mutation. This removes one upload
and one retained artifact per run, while retaining cross-job original retrieval
and integrity checks. No publication timing saving is claimed without a real
separately authorized production run.

### Bounded cache export comparison

JAY-769's existing `runner-release.yml` cache probe accepts one selected provider
and native architecture plus `cache_probe_export_comparison=true`. Validation
refuses `all` dimensions and restore-only runs in comparison mode, so max/min need exactly two native
allocations. Each runs cold, warm and changed-input phases through a fresh
Buildx builder per invocation, retaining its original builder reference; a reused
local builder refuses remote-cache proof. Max/min use separate branch/trust and
architecture scopes and disable unrelated trusted cache fallback. Ordinary
builds keep their existing max export/fallback policy until comparative evidence
justifies a change. Changed-recipe diagnostics retain the existing no-export
policy and disclose it in the build receipt.

Every phase records exact committed Docker input identity/source/image labels,
BuildKit cache import/export and image export/load vertex intervals, complete
build elapsed time, actual-user runtime qualification, loopback registry push/
pull, archive export/compression/checksum and small evidence upload. Vertex
intervals overlap and must not be summed as cost or end-to-end saving; upload
step elapsed time comes from the completed Actions job receipt. The probe uses
level-one gzip consistently with delivered payload export. Large diagnostic
archives are deleted; only owned registries/builders and small receipts remain.

Historical run 39865529840 has valid warm/changed receipts, but its retained
artifacts do not establish a cold baseline. A new exact-source comparison is
needed before changing the default; no claimed saving is inferred from those
old timings or the separate 178.9-second production cache-export observation.
The bounded hosted diagnostic requires its exact source/input/job/resource plan
to be authorized before dispatch. It performs no customer release, signing,
production settings or provider/model requests.

### Native engine allocation

Each selected Linux x86-64/macOS ARM64 engine is built and immediately qualified
in one native job, before its executable is uploaded. The existing `Verify
engine (target)` evidence names now describe the combined producer, so original
production readers still require a successful complete native job. There is no
whole-build-matrix barrier or second smoke allocation/selector/download. A
failed standalone check prevents the engine artifact upload; its evidence is
retained even on failure.

The controller copies exact checked bytes into a fresh temporary directory,
uses isolated home/config/cache/extraction/workspace paths, and launches from
outside the source tree with an explicit environment and system-only PATH. It
refuses a PATH exposing Erlang/Elixir/Mix/Zig so the preceding build cannot mask
a missing standalone runtime tool. Python/lsof remain controller dependencies;
startup, attributable health, missing-provider readiness, graceful stop and
owned-descendant cleanup remain actual executable checks. Engine-only controller
fixtures run in the same native build allocation, retaining full Git history and
the identical suite. The controller-only
mode selects only its own Mac route. Unselected build/controller routes allocate
no selector job; the obsolete tag-only no-op trigger is removed. Saved queue
intervals overlap other work and are not additive deployment-time savings.

The former `release-native.yml` reusable workflow has no remaining consumer and
is removed together with its `native` route; the separate `smoke` route is also
retired. New source-bound permits target the combined `build` job. Historical
runs, source revisions, permits and receipts retain their original meaning and
are not reinterpreted through the new allowlist. Native compatibility fixtures
still require full Git history and the same retained source ancestry authorities.

### One final package cohort for production preparation

Validated inputs select `final_only=true` only for production preparation with
package composition enabled and no prior-package, legacy migration or manager-only
lifecycle request. That path skips the candidate platform archives, their metadata
join and the unused candidate Mac selector. Qualified component records and the
original native helpers still feed one immutable production distribution plan,
then exactly one final manager archive per native host. The existing finalizer
binds actual archive hashes; `production_release.verify_packages` then checks the
exact final bytes, prepared manifest, source and stable references before upload.

`Candidate qualification` is a terminal join: in final-only mode it requires the
candidate cohort to be intentionally skipped and the final package join to have
succeeded. Failed, cancelled, missing or incomplete final producers cannot pass.
The preparation metadata gate independently requires successful component/helper
inputs and the exact build-versus-reuse result, avoiding a dependency cycle.
Raw prior-source revision composition remains refused before fanout; select a
verified historical package artifact for supported update/rollback acceptance.
Requested candidate/prior-package/legacy/manager-only lifecycle modes retain their
candidate archives and real lifecycle consumers; final production preparation
still cannot succeed after a failed requested lifecycle. No signing, publication
or production settings are invoked by these routing changes. Production workflow
execution and final-byte native acceptance remain separate from fixture tests.

### Pending original baseline qualification

The ordinary prior-package selector still requires successful historical installed
lifecycle proof. A separate default-empty `bootstrap_previous` descriptor permits
only one explicit owner-triggered, nonproduction combined candidate on `dev` to
use original run `40588897389`, attempt 3, source `ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b`,
package artifact `12774063336` with service digest
`sha256:6347428eaa08c972eb43090af41460c0d73c9d84776351f2b91c80d407bdc446`.
The descriptor also pins the approved current candidate source and historical
skipped lifecycle job `122501814533`. This is a pending test subject, never a
claim that the skipped historical lifecycle passed. Production/main/tag and
ambiguous, mismatched, failed or expired subjects refuse.

Before executing its installer, the actual Ubuntu lifecycle verifies original
service and inner archive hashes, manifest/core/source/component identity and
helper architecture. Owned Mac provenance requires its actual source's native
OS/CPU/version guard, successful ordered helper/package steps and a bounded
original helper receipt whose bytes match the packaged helper. A private label
or capacity-ready status alone proves neither native execution nor qualification.
Owned-Mac source validation accepts only explicitly audited whole-workflow
fingerprints and exact relied-on step configurations. Unknown workflow text
refuses, including quoted keys, aliases, duplicate jobs and shell/error overrides.
This intentionally requires another owner review when those workflows change;
it is not a general YAML or shell equivalence checker. The final combined source
fingerprint must be reviewed after integration before candidate execution.

The existing `scripts/release/package-lifecycle.py` consumer must repeat-install
that exact original baseline as an ordinary user, assert its installed source,
pass doctor, and run both isolated projects with attributable listeners, original
images and preserved canaries before activating the new candidate. Full update,
rollback, refusal/retry and cleanup must then pass. The existing terminal join
also requires fresh same-run baseline proof; missing or failed proof refuses.
Fresh proof names both original and current identities and preserves the old
`skipped` result. The descriptor grants no execution authority: exact candidate
source, complete change inventory, prior subject and native effects still require
operator approval before the single combined candidate. No version bump or
production promotion is needed for this nonpublishing dev path.
