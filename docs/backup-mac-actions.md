# Persistent Symphony Mac CI capacity

The current JAY-782 implementation is a normal repository-scoped GitHub Actions
runner in one dedicated persistent macOS14 ARM64 VM. Actions supplies the queue
and runs one job at a time. Successful jobs retain the VM and registration.
No per-job operator grant, custom queue, copied private driver or cleanup receipt
is part of this route. Older one-off preparation below is historical, not an
activation prerequisite. This document/source change does not activate anything.

## Current routing

Set repository variable `SYMPHONY_PERSISTENT_MAC_RUNNER=symphony-macos14` only during
separately approved activation. Existing selectors then reuse their current
owner/repository/source/event checks and output the fixed native runner label;
they do not wait for a per-job capacity reply. Empty preserves the existing route.
Keep the old disposable `SYMPHONY_BACKUP_MAC_PERMIT` absent while this route is on.

| Existing consumer | Persistent capacity |
| --- | --- |
| `make-all.yml / platform-contracts` | Ordinary selected Mac process contracts; required Application checks unchanged |
| `package-checks.yml / native-host-package` | Ordinary selected Mac native helper/fixture qualification; Package qualification unchanged |
| `native-manager-helpers.yml / helper` | Existing credential-free Mac helper used by runner-integration |
| `current-manager-package.yml / package` | Existing credential-free Mac package used by runner-integration |
| `runner-release.yml / packaged-candidate` | Existing nonpublishing manual Mac candidate package |
| `burrito-release.yml / build, controller-diagnostics` | Existing nonproduction manual engine/controller jobs |

Production preparation, publication, signing/import and privileged release-package
selectors keep their current route. Linux selection, all native job bodies,
required checks, timeouts, cancellation and artifact identities remain unchanged.
Historical native provenance still checks original source/steps/artifact digests;
only exact routing-only workflow fingerprints are added. No old VM-deleted proof
is reinterpreted as proof about this retained VM. Labels/ifs are routing, not an
access barrier; registration is to this repository only. Current GitHub access
controls remain in force. This change adds no fork-policy or approval project.

## Minimal activation plan — held for separate approval

Target this Mac's dedicated Tart home
`/Users/jayson/Library/Application Support/Symphony Mac CI Persistent`, VM
`symphony-ci-macos14`, separate from pristine base/default Tart storage, stable
Symphony and personal host state. Clone/prepare it only under a later approved
activation. Record its real MAC address for the start command. Keep no host
filesystem shares, clipboard, credential/socket forwarding or ambient production
signing credentials. Use the existing native macOS14 ARM64 tools in the VM.

Register the normal runner **inside that VM** with `config.sh`, using only
`--url https://github.com/Jayson-Stangel/symphony`, `--name symphony-macos14`,
`--labels symphony-macos14`, `--no-default-labels`, and the normal persistent work
directory `_work`. Supply a separately provisioned repository registration token
through the runner's supported prompt, never source, argv examples or logs. Do not
use organization/shared registration or `--ephemeral`. This source task does not
provision credentials or run config.sh.

Install the reviewed `persistent_mac_runner.py` and executable
`persistent-mac-job-completed.sh` outside `_work`, at
`/Users/admin/.local/share/persistent_mac_runner.py` and
`/Users/admin/.local/share/persistent-mac-job-completed.sh`. In the native runner
root `/Users/admin/.local/share/symphony-ci-runner-25a`, configure its normal `.env`:

```text
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/Users/admin/.local/share/persistent-mac-job-completed.sh
TMPDIR=/Users/admin/.local/share/symphony-ci-runner-25a/_work/_temp
```

The dedicated temp directory must exist. Keep the runner's default process
cleanup enabled. Native checkout post actions remove checkout credentials; the
small completed hook detaches DMG images backed by this runner's work/temp and
removes its checkout workspace. The runner then cleans tracked orphan processes
in FinalizeJob and native temp after log uploads. Do not delete active temp from
the hook or scan/delete shared host temp. Retain only normal runner credentials,
configuration, tools and approved caches; normal maintenance also bounds logs.

Use native `./svc.sh install` and `./svc.sh start` in the guest user account. The
native macOS service is a user LaunchAgent: its guest login/session must actually
exist. Its RunAtLoad/RunnerService behavior is supplied by Actions, not a new
supervisor. The hook unloads and disables this native service on an observed
unsafe cleanup failure; stopping only Runner.Listener would let its native wrapper
restart it. Disabled state requires deliberate operator repair, not automatic
reset or re-enable. If native service stop cannot be established, the hook tries
to halt the dedicated guest and reports uncertainty through normal logs.

After approved installation, the host start/check command is:

```sh
python3 scripts/ci/persistent_mac_runner.py start \
  --tart '/Users/jayson/Library/Application Support/Symphony Mac CI/tools/tart.app/Contents/MacOS/tart' \
  --mac-address ACTUAL_REVIEWED_VM_MAC
```

It checks the separate VM path/owner/name/MAC, starts it if off, or preserves it
if already running. It waits briefly for native guest exec, then starts/checks
the normal service once, preserving an active instance. An exited but loaded
service uses native kickstart without `-k`; an unloaded service uses bootstrap
without `load -w` or enable, preserving native disabled state.
A disabled service refuses startup. No host command is required between jobs.
The physical Mac must be powered, awake and network-connected. An offline VM
cannot boot itself from a queued job; this explicit start path does not promise
wake-on-queue. Host startup automation would be a separate activation choice.

## Native mechanism and proof limits

The pinned Actions runner2.338.0 source
[`JobDispatcher`](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Listener/JobDispatcher.cs#L32)
serializes jobs. Its completed hook is an awaited post step in
[`JobExtension`](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Worker/JobExtension.cs#L523),
before native orphan cleanup. Hook failure alone does not stop the listener;
native service disable/bootout is used on an observed unsafe cleanup failure.
Native [`RunnerService`](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Misc/layoutbin/RunnerService.js)
can restart a failed listener, which is why that whole service is stopped.

Cancellation normally reaches native cleanup, but a killed/crashed worker or
initialization failure can omit the completed hook; native cleanup also reports
some failures without quarantining itself. This implementation does not claim
universal fail-closed containment for every crash, nor add a supervisor to obtain
it. Inspect native logs/state after such failures before further use. A later
activation check must prove actual guest login/service startup, two queued
eligible jobs on the same VM, cancellation, observed cleanup failure preventing
another job, normal native cleanup, repository scope and retained provenance.
Local inert fixtures/source inspection are not real guest or provider proof.
No VM boot, registration, settings, live CI, installation or publication is
authorized by this runbook.

---

## Historical one-off/disposable preparation

The following material describes the earlier bounded one-off experiments and
source interfaces. Its service-freeze, grant and teardown requirements are not
requirements of the persistent native-runner route above.

# Backup Mac Actions runbook

JAY-763 provides an optional, source-bound fallback for existing Mac checks in a
disposable Apple Silicon guest. [PR #143](https://github.com/Jayson-Stangel/symphony/pull/143)
merged into `dev` at `9bed66a9a0eafd8da7e661be6a39899e6a3e6a2d` after real Mac
process and native helper jobs passed. The historical fallback selected hosted
execution when no exact permit matched. Reusable capacity is retained in a private durable
toolchain store; only disposable clones are ever registered. Both original-source
post-PR149 Mac jobs also passed on 2026-10-28, with independently reconciled
teardown. This runbook grants no new execution, merge, production or stable-rollout
authority.

## Owned-first ordinary qualification

JAY-778 makes the requested credential-free Mac selector prefer a prepared
owned guest on the first ordinary run attempt. The Linux selector verifies the
owner, controller, PR head/base and tested merge source, then waits at most five
minutes for an authenticated, exact-run capacity reply. A valid existing permit
continues to work. Linux-only and omitted Mac qualification do not wait.

The capacity reply is an owner-created commit status under
`owned-mac/v1/<run>/<attempt>/<consumer>/<job>`, attached to the tested source
commit. It is visibly capacity readiness, never a required test result. The
selector retains the original immutable status ID, rejects mismatched or malformed
matching replies, and reads with `statuses: read` through every reusable caller.
The first real ordinary run must prove this GET using its actual workflow token;
local fixtures do not prove API permissions or that an owned session ran.

The normal host operation is one reviewed foreground command:

```sh
python3 <reviewed-owned-mac-driver>/owned_mac_session.py --plan <exact-session-plan.json> --admission <private-current-admission.json>
```

The reviewed finite driver is currently prepared as an operator artifact, not a
repository-installed or background service. Its exact bytes, private durable base,
current helper hashes, source/run/attempt/consumer, actual Linux selector job ID,
expiry and fresh admission are pinned in the session plan before execution. The
operator starts it for one already identified run while that selector is waiting.
It acquires one shared nonblocking durable capacity lock and requires the separate
ordinary selector probe step to have completed its real token GET. Before any
clone, VM boot or registration, it publishes and verifies an authenticated
PREPARING marker for this session. The service-created claim must precede the
selection step start plus 240 seconds; no resource preparation begins after a
late or uncertain claim. CI bounds each status GET to 20 seconds and requires
a final observation after that claim window before a no-response hosted fallback.
It then prepares one real offline ephemeral
runner and publishes READY only after preparation. A PREPARING marker followed by
crash, timeout or unverified cleanup refuses hosted fallback; only a later matching
READY or verified clean terminal response can resolve that preparation. It starts the listener
only for the actual exact queued private-label job, after the existing watchdog is
armed. A late reply after hosted selection causes offline cleanup, never duplicate
execution. There is no discovery loop, automatic chain, dispatcher or permanent
registration. A stopped base without this foreground operation is not unattended
capacity.

An authenticated busy/offline/clean preparation failure, or no reply before the
bounded deadline, selects hosted execution with an explicit reason. API errors,
uncertain cleanup and invalid matching replies fail closed. Once a private label
is selected there is no automatic hosted retry; actual test failures remain
failures. Busy refers to this request owning no resources and must preserve the
other healthy session. A crashed predecessor retains an unclean capacity record
until explicitly reconciled.

The existing writer/App/external-approval admission, exact one-job lease, private
token transport, guest/host deadlines and full cleanup readbacks remain required.
Initially only one owned session is admitted. Protected local-signing import keeps
its existing explicit permit route; credentialed signing and tag publication are
not newly admitted. The `runner-release/candidate` native-runner field selects
Linux builders and is not a Mac consumer. Actual first-attempt owned execution and
a genuine pre-execution hosted fallback still require ordinary CI evidence; prior
recovery successes do not establish this new policy's acceptance.

## Verified feasibility and admission boundary

The initial audit on 2026-10-27 found `Jayson-Stangel/symphony` is a public,
personal (`User`) repository. The authenticated operator has repository admin
permission; the repository runner inventory was empty. This establishes the
ability to administer repository runners, not safe job admission.
[GitHub runner groups](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access)
provide organization/enterprise access policies. A repository runner's labels,
including a random per-run label, do not restrict which workflow can request it.
A malicious workflow can race for an available runner. Workflow `if` conditions and environment reviews cannot serve as host-side
admission controls when an admitted workflow can change those checks. Fork approval
policy alone does not account for already approved/queued historical runs.

[Ephemeral/JIT registration](https://docs.github.com/en/actions/reference/security/secure-use#using-just-in-time-runners)
bounds the number of assigned jobs; it neither isolates the machine nor identifies
the permitted job. A hostile job taking the single slot is still a hostile job.
Do not register a runner on an operator account or customer's Mac Mini.

The local machine is Apple Silicon with hypervisor support, macOS 27.0.1,
Swift 6.4 and macOS SDK 27. It has 18 logical CPUs, 128 GiB RAM and approximately
1.5 TiB available storage. Initial discovery found no installed VM tooling. The
release owner subsequently reported a booted macOS 14.8.7 ARM64 guest, Swift 6.0.3,
durable Python 3.13.18 and runner bundle 2.338.0, strict public HTTPS/DNS, no host
shares, and verified shutdown. These preparation observations are not Actions
job, admission, artifact or installed-product acceptance.
A separate user account alone is not the isolation boundary required here. The
2026-10-28 durable-store and CI results below supersede the initial preparation
gaps; they do not turn this guest into a physical-Mac Docker acceptance host.

### Operator-selected standard NAT first pass

On 2026-10-27 the operator explicitly selected Tart's out-of-box standard NAT
for the first pass and authorized necessary guest internet/toolchain setup.
This replaces the custom Softnet design for that pass. No custom privileged
networking, SUID helper, host DHCP preference edit or network-policy daemon is
required. Unused custom networking drafts are not execution-ready or part of CI.

Standard NAT permits possible host gateway/LAN reachability; it does not prove
host/LAN network denial. That known boundary is accepted for this first pass,
not an additional custom-network prerequisite. Retain the disposable guest,
no host filesystem/credential/socket forwarding, exact trusted-source admission,
one-job registration, bounded supervision and teardown. No arbitrary fork or
unapproved workflow may reach a runner. This choice grants no registration/job,
merge, promotion, signing or publication authority beyond the exact approvals.

GitHub's [macOS 14 retirement notice](https://github.com/actions/runner-images/issues/13518)
warns of longer peak-time queues during deprecation and retirement on
2026-11-02. Its 2026-10-26 brownout ended at 2026-10-27 00:00 UTC; that schedule
does not establish that the observed later queues are a brownout. Review the
normal hosted default in a separate migration before retirement. This fallback
preserves actual Sonoma qualification and does not silently switch to macOS 15.

The approved first pass used a temporary `all_external_contributors` fork-approval
policy, an operator-established trusted writer/App source freeze, no external
approvals, and a complete inventory of historical queued/pending/approved runs.
The owner confirmed restoration to `first_time_contributors`, zero registered
runners and an absent permit after both successful disposable-guest jobs.
This is evidence for those exact windows, not a standing admission guarantee.

Before each later window, refresh workflow triggers, external/reusable references,
writer/App permissions and already admitted runs. Obtain authority for the exact
source, controller, run/attempt/targets, policy change, registration and cleanup.
Labels and a guest without credentials alone cannot close the queue race. Keep
unrelated workflows outside the private label; cancel or change settings only
within the operator's approved scope. Any organization migration remains a
separate decision.

The freeze must include installed integrations, not just human collaborators.
Initial settings inspection found active code/workflow writers ChatGPT Codex
Connector, Bolt.new, Claude and Linear Code. Netlify can write checks/statuses and
PRs; Supabase is suspended and must remain suspended during the window. Inventory
current installation permissions, automation principals and already approved
workflow runs again before approval. No app suspension or workflow cancellation
is authorized by preparation. A promise by this chat alone cannot freeze other
writers: the operator/release owner must establish the exact enforced window and
read back its service state before any listener starts.

A pre-job hook is not an admission boundary. The offered runner v2.338.0 source
(commit `197ca027b90de199464dee3bb9ff198a1db60967`) prepares actions before the
hook, then queues both hook and normal steps. After hook failure, the step loop
still evaluates subsequent step conditions; a hostile `if: always()` step can
therefore execute. This is a source-audit conclusion, not a live test:
[initialization](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Worker/JobExtension.cs#L299),
[queueing](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Worker/JobRunner.cs#L229),
[step condition evaluation](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Worker/StepsRunner.cs#L220).
Do not infer isolation from hook exit-code documentation or claim an unverified
hook environment-variable exploit.

## Inspect a proposed ordinary PR run

From a development checkout, use the existing authenticated `gh` CLI:

```sh
python3 scripts/ci/mac_runner_preflight.py \
  --run-id RUN_ID --attempt ATTEMPT --pr PR_NUMBER \
  --head FULL_REVIEWED_PR_HEAD --tested-source FULL_CURRENT_PR_MERGE_SHA \
  --workflow .github/workflows/make-all.yml
```

For package CI select `.github/workflows/package-checks.yml`. Values must be the
reviewed full lowercase SHAs, not a branch name or abbreviated revision. The
preflight performs bounded GET requests only. It reads repository identity/admin
permission, the selected run attempt, the current PR, and the proposed tested
merge commit. Feature PRs must target `dev`; only same-repository `dev` into
`main` promotion CI is additionally supported, with live main/dev ref freshness
and owner author/actor checks. This is not promotion or merge approval. It refuses
forks, a changed head/base, a different run/attempt,
a different workflow, a completed/cancelled run, or a merge commit that does not
have exactly the current base and reviewed head as parents. It never reads or
prints a token, registers a runner, changes a job, reruns CI or posts a status.

The report deliberately returns `execution_ready: false` and exit status 2 even
when identity reads agree. Matching metadata is a point-in-time observation; it
is not approval, a lease, controller-revision proof or enforcement against races.
The run API's PR `head_sha` and the expected tested merge SHA are distinct. A run
record alone does not prove what source a job actually checked out or which
reusable workflow revision executed. Retain the real job checkout/controller
identities during eventual execution. Recheck live state immediately before any
future separately approved action. Unavailable/invalid GitHub reads fail closed.

## Existing job and selector interfaces

Initial source base: `b4c90f3a08f82027bffaafcdd7b94a4d8886192d`. Reinspect the
current reviewed source before preparing an execution approval.

| Existing workflow/job | Display/check semantics | Existing Mac work |
| --- | --- | --- |
| `make-all.yml` / `platform-contracts` | `Fast process contracts (macos-14)` feeds required `Application checks` | `python3 runner/test_npm_cache_lifecycle.py` when npm inputs change; `python3 scripts/ci/release_tests.py contracts` when process contracts change. |
| `package-checks.yml` / `native-host-package` | `Native helpers and fixtures (macos)` feeds required `Package qualification` | Build the ARM64 helper; assert exact revision/platform; run existing package, selected-image, environment, manager-protocol and Codex-config suites; run selected native process/updater fixtures in the same allocation. |
| `native-manager-helpers.yml` / `helper` | `Build manager helper (macos)` joins both native helpers before `qualified_revision` | Build the real helper with `scripts/release/build-host-helper.sh --native`; run the existing exact-source shared contracts; retain the helper/checksum artifact. |

The package workflow uses `release_tests.py process` for ordinary PR/non-tag push
runs and `release_tests.py native` for its explicit qualification callers. Its
helper artifact is `native-host-helper-macos-${github.sha}`; current-manager
helpers retain `current-manager-helper-macos-${run_id}-${run_attempt}`. Preserve
these existing artifacts and source bindings rather than replacing them with
local logs. Retain original GitHub service artifact IDs, digests, size, expiration,
run/attempt, job/check URLs and the real outcome.

The ordinary PR selectors route only the requested Mac process/helper jobs.
They keep `matrix.os`/`matrix.platform`, job identifiers, displays,
commands, classification and required wrapper assertions intact. Only
the Mac `runs-on` value changes for an exact selected permit. No permit may be
installed until source/workflow/head/job admission is established. Linux values and jobs remain unchanged. The shared `host-policy.yml`
and `scripts/release/select-hosts.py` are consumed by explicit qualification and
release workflows too: a global replacement of `macos-14` would expose additional
work without authorization. Each consumer instead has an explicit narrow selector; the shared policy is unchanged.

The same per-consumer mechanism covers these credential-free qualification jobs:

| Consumer workflow | Exact logical `job_key` targets |
| --- | --- |
| `macos-local-signing-import.yml` | `verify` (existing separately protected native verification) |
| `make-all.yml` | `platform-contracts` |
| `package-checks.yml` | `native-host-package` |
| `native-manager-helpers.yml` | `helper` |
| `current-manager-package.yml` | `package` |
| `runner-release.yml` | `packaged-candidate`, `packaged-release-platform` |
| `burrito-release.yml` | `build`, `controller-diagnostics` |
| `macos-local-signing-import.yml` | `verify` (protected main-only inspection; no signing credential) |

Only these nine logical targets can be named; one run permit admits at most eight
distinct consumer/job pairs, each with a distinct label and isolated one-job
runner. Engine/helper/package commands and artifact identities remain in their
existing jobs. Linux candidate/cache/transfer matrices and native host policy are
unchanged. Signing/notary and protected publication jobs/guards are untouched.
The allowlist is routing scope, not approval to run a candidate or production work.
All original caller guards and required source/artifact qualifications still apply.

The top-level controllers allowed by the validator are `make-all.yml`,
`package-checks.yml`, `runner-integration.yml`, `runner-release.yml` and
`burrito-release.yml`, plus the protected `macos-local-signing-import.yml`
controller. Reused jobs bind the top-level `github.workflow_ref` and
`github.workflow_sha`, while each target separately names its consumer workflow.
A permitted branch push or manual run must match the live exact source/run/attempt
and owner actor/triggering actor; tag events are refused. PR routing additionally
requires the owner-authored same-repository feature PR targeting `dev`, or the
narrow promotion CI route with same-repository `head.ref == dev` targeting `main`.
Both routes bind the exact reviewed head, current base, tested merge parents,
controller, run/attempt and live association; promotion CI additionally refreshes
both live `main` and `dev` branch refs and refuses stale snapshots. Feature PRs
cannot use a `main` base. The live branch route must still match the reviewed
webhook event, even when two branches temporarily have identical SHAs. This
supports required CI only: it grants no merge, promotion, production execution
or stable rollout authority. All selectors use only existing
read-only contents/actions permissions; live public PR read access must be proven
with the actual workflow token during approved execution, not an administrator
fixture token. No extra pull-request scope is added to reusable call chains.

JAY-742/PR140 and the later private-ingress repair own local signing and protected
distribution verification. The shipped import selector keeps `macos-15-intel` as
its hosted default and routes only the credential-free `verify` job when an exact
permit selects the isolated ARM guest. Its protected main-only Ubuntu ingress job
has job-local `contents: write` to retrieve the original private asset; the Mac
inspection job explicitly retains `contents: read` and `actions: read`. No elevated
Ubuntu token is forwarded to the guest. The immutable same-run ingress artifact
and original release/asset IDs remain part of native provenance. This routing
interface does not authorize signing, environment approval, import, publication,
a new candidate or duplicate qualification. See [the signing runbook](macos-release-signing.md).

## Source-bound routing permit

The operator installs `SYMPHONY_BACKUP_MAC_PERMIT` only after approval and service
admission/isolation readback. It is a repository variable, not a secret or an
admission authority. An already-admitted malicious workflow can read its labels;
the service freeze must prevent that workflow from reaching a runner in the first
place. Exact source checks reduce mistakes and stale routing; they cannot replace
the external writer/fork/queue boundary.

The following is a schema example, not an approved manifest. Replace every
placeholder with the exact approved current identity. PR example:

```json
{
  "schema": 1,
  "repository": "Jayson-Stangel/symphony",
  "event_name": "pull_request",
  "controller_workflow": ".github/workflows/package-checks.yml",
  "workflow_ref": "Jayson-Stangel/symphony/.github/workflows/package-checks.yml@refs/pull/PR_NUMBER/merge",
  "workflow_sha": "FULL_CONTROLLER_SHA",
  "run_id": 123,
  "run_attempt": 2,
  "pr_number": 456,
  "pr_head": "FULL_REVIEWED_PR_HEAD",
  "base_sha": "FULL_CURRENT_DEV_BASE",
  "source_sha": "FULL_CURRENT_PR_MERGE_SHA",
  "actor": "Jayson-Stangel",
  "triggering_actor": "Jayson-Stangel",
  "pr_author": "Jayson-Stangel",
  "expires_at": "YYYY-MM-DDTHH:MM:SSZ",
  "targets": [
    {
      "consumer_workflow": ".github/workflows/package-checks.yml",
      "job_key": "native-host-package",
      "runner_label": "symphony-backup-mac-32_LOWERCASE_HEX_DIGITS"
    }
  ]
}
```

For an approved branch push/manual run, `pr_number`, `pr_head`, `base_sha` and
`pr_author` must be JSON null. The exact controller ref is then a branch ref;
`source_sha` must match the live run head and actual `github.sha`. The permit
requires expiry within two hours at selector evaluation. The independent
watchdog enforces that absolute deadline after selection too: queued labels do
not expire themselves. It also terminates the owned VM/runner if the permit is
changed/removed. One shared permit can name each approved target once; registering
one runner per distinct label prevents two target jobs from silently sharing a
persistent guest. Never relabel a broadly available runner.

After exact approval only, `gh variable set SYMPHONY_BACKUP_MAC_PERMIT -R
Jayson-Stangel/symphony < /absolute/private/approved-permit.json` reads the manifest
from stdin. Retain the prior variable state and exact raw-value SHA-256; read back
through the GitHub variable API and compare. Do not put an admin token in the
guest. The first live windows used owner-controlled permits and registrations;
these examples grant no new authority. Remove/restore only this operation's exact
variable value after all owned watchdogs have confirmed teardown; a different value is a concurrency conflict.

## Already blocked runs cannot be repaired by changing their workflow

An existing hosted job's `runs-on` is part of the workflow revision selected by
that run. Rerunning it after editing a branch does not inject the new selector.
Registering a runner with a `macos-14` label is not an acceptable shortcut: it
admits any matching job and confuses the hosted/backup execution boundary.

A workflow containing the reviewed fallback must first exist in the exact source
under test. A natural same-source run provides its service run ID, but its selector
may already have emitted the hosted value before an operator can install a permit.
For ordinary CI, use the smallest authorized rerun: install/read back the exact
next-attempt permit, then rerun the successful Mac-selector job and its dependent
jobs on that same workflow handle. The original workflow must already contain
these selectors. Re-running only a failed Mac job retains the earlier selector
output; it does not establish new routing. A selector/dependent rerun may include
Linux matrix siblings and the required wrapper; retain their original successful
results separately and do not claim they were reused if GitHub reran them.
Unrelated Browser qualification need not be restarted.

GitHub supports [re-running a job and its dependent jobs](https://docs.github.com/en/rest/actions/workflow-runs#re-run-a-job-from-a-workflow-run).
Observe the actual new attempt and exact private label before exposing a guest.
If the POST result is uncertain, reconcile the same handle; do not repeat it or
treat the previous completed attempt as closure of the requested one. A complete
workflow rerun remains a separately reviewed recovery choice when the narrower
route cannot satisfy the dependency graph; it is not the default first step.
For an active job, confirm it is queued/unassigned with zero steps before any
approved cancellation. Leave a healthy assigned hosted job alone.

When hosted Mac capacity is impaired, use the already approved local path after
that check rather than waiting for predictable acquisition failure. Hosted stays
the repository default. Before rerunning an old `dev` push, inventory all active
statuses and check the workflow's `dev` concurrency group: it can cancel another
owner's newer run. Coordinate completion or hold; never cancel unrelated work.
An original-source rerun can remain bound to its historical commit after `dev`
advances; do not move `dev` backwards or relabel proof as the new tip. PR reruns
still require fresh live base/head/merge-parent checks, including both refs for
promotion CI.

Ordinary check association is retained on the actual GitHub run. Never copy a
green check, synthesize success, omit a required Mac job or waive a failed/pending
check. If capacity or admission cannot satisfy the gates, leave the check honest
and record the precise blocker. A different CI topology needs its own review.

## Selected host setup and per-window approval

The physical Mac and stopped-clone/guest setup were selected in the operator
chat. Record the current exact guest identity and source/job scope before runner
registration; this runbook is not an execution approval.

Phase A's exact approved boundary is a **stopped clone only**: install the pinned
Tart tool, pull the pinned public Sonoma image, and create one stopped clone with
6 vCPUs, 16 GiB RAM and 100 GiB disk. Do not boot it. Phase A authorizes no
Softnet installation/invocation, root/network helper, network/mount probes,
account or toolchain installation, GitHub settings change, token creation,
runner registration or Actions job. Do not infer first-boot or network authority
from approval to pull and size the stopped clone.
The proposed public Sonoma image manifest is
`sha256:e2ebdfc4d354b336fe00d729c11a8136a019b8046f41cc183a2fe51b85d18f49`,
with config digest
`sha256:16e9d30a585f4c572f14cfb2fa73baf3932ead0f476f5e5967571da082f9ea15`.
Its raw disk is 50 GB and transfer size approximately 22.563 GB. The actual Apple
`VZMacHardwareModel.isSupported` probe returned true on this M5 Max for the pinned
image hardware model. This is hardware compatibility evidence, not guest boot,
network isolation or toolchain proof.
The operator subsequently authorized guest boot, standard NAT internet and
necessary scoped toolchain setup. Record Elixir/OTP, Swift/Xcode tools, Python,
Git and archive utilities, their public sources and resulting guest changes.
The earlier stopped-clone approval alone did not grant these later effects. Runner setup
and execution additionally require the exact reviewed PR/source/controller,
run/attempt/target permit, fork-policy/settings readbacks, writer/apps freeze,
queued job inventory, registrations, bounded job count and teardown approved
separately after those identities exist. The release owner owns these approval
proposals and timing; this implementation chat does not dispatch them.

- Name the physical noncustomer host, VM identifier/image digest, clean guest
  account, source/PR/head/merge SHA, immutable controller/workflow identities,
  admitted job IDs/matrix entries and exact run/attempt/job count. Proposed guest:
  one VM, 6 vCPUs, 16 GiB RAM, 100 GiB disk, macOS Sonoma 14 ARM64, destroyed after one admitted job. The proposed concurrency is one VM: each later target uses a fresh clone after prior teardown, not reuse of a contaminated guest. Derive the exact target count from the approved run inputs/DAG; the target set depends on the workflow and selected inputs. One fixed bounded permit names only that approved set, with distinct per-job leases and at most one running guest. Do not stitch qualification across attempts.
- No host account creation, shared home/workspace disks, clipboard, SSH-agent
  forwarding, host sockets, Apple ID, login keychain, stable installation or
  customer data access. No host `symphonytest` account. Verify absence from inside
  the guest, rather than treating a settings declaration as proof.
- Choose and verify a Mac VM runtime/image before installation. Candidate runtime
  Tart 2.41.1 archive SHA-256:
  `e680a734febccfce7d4e910c1f7f13fc3b00f19a181c945e2d746de3214c32c2`.
  The operator-selected first pass uses standard Tart NAT with the pinned runtime;
  no Softnet installation or execution is required.
- Launch the owned guest with `tart run --no-graphics --no-audio
  --no-usb-accessories --no-clipboard VM_NAME`, without custom `--net-*`, shared
  directories, extra disks, Rosetta sharing or forwarding flags. Record ordinary
  DNS and strict public TLS success, and the known host/LAN reachability boundary.
  Do not describe standard NAT as host/LAN denial or silently add another network.
- The unused Softnet proposal would require its own review before any use: the
  official helper can persistently overwrite host InternetSharing DHCP defaults.
  No such helper or DHCP edit was executed for the selected first pass. It is not
  a prerequisite for CI and must not be introduced as an implicit repair.
- Verify guest macOS/ARM64, Python 3.11+, Swift/Xcode command-line tools, Git,
  Bash, `lsof`, disk/resources and exact pinned Actions runner package/checksum.
  Use the current supported release, not an assumed version. No signing/notary,
  package publication or model/account credentials go into this guest.
- Present registration scope, exact runner name/labels, admission enforcement
  evidence, token handling and permissions. Registration credentials must never
  appear in CLI arguments, logs, ticket text or chat. Use the pinned runner's
  `ACTIONS_RUNNER_INPUT_TOKEN` environment input rather than `--token` argv,
  supplied from private process memory without shell tracing/history or dumps.
  [v2.338.0 command input handling](https://github.com/actions/runner/blob/197ca027b90de199464dee3bb9ff198a1db60967/src/Runner.Listener/CommandSettings.cs#L101)
  reads recognized input variables, masks recognized secret values and removes
  those variables from the runner process environment. Recheck the exact pinned
  package behavior before setup; this source audit is not registration proof.
  No token is created during preparation.
- Define startup/acquisition/execution deadlines, one-job lifecycle, cancellation
  ownership and no automatic retry. No login service, persistent runner or broad
  shared queue. Include cleanup after rejection, capacity failure and cancellation.

This document grants no new authority. The operator's recorded guest setup and
standard NAT instruction apply; registration and job execution still require
the exact source/controller/run/attempt/targets, admission window and teardown.

## Durable capacity and restoration

On 2026-10-28 the operator explicitly requested durable retention. The release
owner created and verified the private store at
`~/Library/Application Support/Symphony Mac CI`, outside temporary directories,
the stable installation and default `~/.tart`. The store remains mode `0700` and
contains pinned Tart/archive, public image cache, a clean **stopped, unregistered**
base, bootstrap/receipt material and `bootstrap/reference-25a/` source references.
There is no persistent runner service, registration, token, permit or job workspace.
Per-job cleanup must reject this store, all its ancestors/descendants, stable
paths and default `~/.tart`; it may remove only the exact disposable UUID home.

The accepted persistence helper uses `clonefile(2)` with errors propagated; it
cannot silently fall back to a large ordinary copy. It refuses an existing or
partial destination, links and unexpected special files, and preserves partial
output for review instead of overwriting/deleting it. Disk verification is
**size-only**: 26 of the current 28 inventoried files have full SHA-256 checks;
the two disk images do not. Clean guest state relies on the controlled stopped
base and setup receipts, not an offline inspection of the disk for credentials.
APFS clones share storage. This protects routine retention, not device failure,
user deletion or arbitrary mutation; a separate backup policy is not established.
The earlier temporary assets disappeared for an unknown reason.

Current local verification entry point, for this prepared host only:

```sh
python3 "$HOME/Library/Application Support/Symphony Mac CI/bootstrap/persist-runtime.py" verify
```

This performs local integrity/signature/state inspection, not a boot or source
qualification. macOS security assessment may consult its assessment service.
Read the retained inventory and current helper before using it; historical
bootstrap/controller copies are provenance, not permission to replay old handles.
The 2026-10-28 observed README SHA-256 is
`a28153e44188ee5749fc4cdce0c44ecfe52ee371a749ce15217b12a343795e1f`;
inventory SHA-256 is
`13332296c4eccf26611aeaa81dc7dc74f5233223cdbc1b34c548e5600b511e4f`.
Both are observations of that revision, not immutable promises after a later
reviewed store update. Its `source_bound_qualification` field is `false`.

If restoration is needed, use a fresh private staging root and explicit
`TART_HOME` with `TART_NO_AUTO_PRUNE=1`. Verify the exact archive hash, Cirrus team
`9M2P8L4D89`, notarization and public image digest before a stopped clone:

| Public artifact | Verified pin |
| --- | --- |
| [Tart 2.41.1 archive](https://github.com/cirruslabs/tart/releases/download/2.41.1/tart.tar.gz) | 22,959,557 bytes; SHA-256 `e680a734febccfce7d4e910c1f7f13fc3b00f19a181c945e2d746de3214c32c2`; archive top level is `LICENSE` and `tart.app`, without links. The release API redirected to the same pinned asset under `openai/tart`; identity was verified by bytes and signing team. |
| Sonoma image | `ghcr.io/cirruslabs/macos-sonoma-base@sha256:e2ebdfc4d354b336fe00d729c11a8136a019b8046f41cc183a2fe51b85d18f49` |
| [Python 3.13.18 ARM64 standalone](https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.13.18%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz) | SHA-256 `8392b6ea51d94391befb2c31ebe75463f045d0b69dfcf5e1bb3234e1f5fa3405` |
| [Actions runner 2.338.0 ARM64](https://github.com/actions/runner/releases/download/v2.338.0/actions-runner-osx-arm64-2.338.0.tar.gz) | SHA-256 `df4cebda25c86a886ed204e49fee63f5c2e7cec5f447b5c98440a826bbdf9df2` |

Use the app-bundle binary at `tart.app/Contents/MacOS/tart`. After the approved
bounded standard-NAT builder boot, provision public tools through `tart exec -i`
stdin, verify tools/no host shares/no registration state, then stop/reap and
inventory the base before cloning job guests. Do not blindly execute a historical
bootstrap with expired paths. Standalone Node/npm are not prerequisites of these
Mac process-contract jobs: the npm-lifecycle suite uses Python subprocess fixtures;
actual npm semantics execute on Ubuntu. Preserve the runner bundle's own action
runtime. `lsof` remains required by real native socket-ownership fixtures.
Tool upgrades require a newly reviewed base and inventory, not in-place drift.

## Fresh guest and single-job registration sequence

Seal the stopped, standalone public-toolchain base **before any registration**;
verify the runner directory has no `.runner`, `.credentials*` or `_work` job
state. Never reuse a post-job guest. Keep its public image/tool provisioning and
base-file identities in a private receipt.

For every exact queued target derive a fresh canonical UUID, matching VM/runner
name/sole label and private Tart home as required below. Use the supported Tart
export/import path, or an explicitly reviewed standalone native clone plus owned
same-filesystem relocation into that job's private Tart home. Cross-home movement
is source-backed layout handling, not an exposed cross-home Tart command. Refuse
symlinks, existing destinations, suspended/overlay/external disks or changed base
identity. Run supported `tart set VM_NAME --random-mac` while stopped, then read
back its new MAC/ownership and keep at most one guest running.

Start that guest under a bounded foreground standard-NAT process. Configure its
runner **offline**, using private stdin/process-memory token input and a PATH
that prepends the prepared Python 3.11+ bin. The verified v2.338.0 flags are:

```sh
./config.sh --unattended --ephemeral --no-default-labels --disableupdate \
  --url https://github.com/Jayson-Stangel/symphony \
  --name EXACT_UUID_NAME --labels EXACT_UUID_NAME --work _work
```

The token is supplied only through `ACTIONS_RUNNER_INPUT_TOKEN`; never put it in
`--token`, another guest command argument, shell history, logs or a base image.
Do not use `--replace`, a login service or a persistent listener. Read the actual
runner ID and exact queued/unassigned provider job ID/sole label, prepare its
private lease, and require the host watchdog's `armed` receipt **before** the
bounded `run.sh --once` listener starts.

Never boot or register the durable base itself. Bind the host controller and
selector/preflight/watchdog helper files to the exact tested source; an older
checkout with a matching function body can still lack newly shipped targets.
The watchdog stops/deregisters/deletes the exact guest. The foreground Tart owner
must independently reap its exact process and bind expected VM absence to that
matching final watchdog receipt/lease, not call a fixed base-VM supervisor on an
already deleted job guest or infer cleanup from a missing directory. Require
verified teardown before the next fresh clone. All qualification targets remain
on the same approved source/run/attempt and preserve original GitHub artifacts.

## Bounded owned watchdog

The host-side watchdog is a cleanup supervisor, not a runner installer, job
scheduler or source approval service:

```sh
python3 scripts/ci/mac_runner_watchdog.py \
  --lease /absolute/private/session-lease.json \
  --receipt /absolute/private/new-receipt.json
```

Prepare one private mode-0600 lease per approved target. Fields are `schema: 1`,
`session_id` (canonical UUID), absolute `tart_home`, `tart_binary` and `gh_binary`,
`vm_name`, `vm_mac`, `runner_id`, `runner_name`, `runner_label`, `run_id`,
`run_attempt`, provider `job_id`, `head_sha`, `source_sha`, `consumer_workflow`,
`job_key`, raw `permit_sha256`, UTC `expires_at`, `acquisition_seconds` (at most
300) and `total_seconds` (at most 3600). VM/runner/label and the dedicated
`tart_home` basename must be `symphony-backup-mac-` plus that UUID's 32 lowercase
hex digits. The private mode-0700 dedicated Tart home contains a mode-0600
`.symphony-backup-mac-session` marker with the canonical UUID; the `vms` and exact
VM directory must be owned/private, without symlinks. Pin the actual VM MAC read
from Tart, exact provider runner ID and exact queued/unassigned provider job ID;
a logical `job_key` cannot substitute for the numeric service job ID.

Configure the approved runner as ephemeral and offline first. Before starting its
listener, launch the independent host watchdog and wait for its `armed` receipt.
It verifies the exact offline/idle runner registration, queued unassigned job,
live source/run/permit/raw digest, VM ownership and deadline. Only then may the
separately approved foreground guest listener start. Keep admin credentials on the
host; they are needed for variable readback/deregistration and never enter CI.
The guest runner also needs its own bounded foreground deadline; host polling does
not justify an unbounded login service or retry loop. No watchdog command starts
or registers a runner or installs Tart/Softnet.

If the runner reports busy before the job API exposes its assignment, the
watchdog refreshes the same exact job within a maximum 15-second confirmation
window. Busy state alone is not assignment proof. A wrong runner, missing or late
confirmation, provider error, cancellation or earlier overall deadline causes
refusal and owned cleanup. Idle flaps cannot reset the confirmation window.

The watchdog checks expiry/permit changes and job outcome, handles TERM/INT,
and independently attempts exact owned Tart stop/delete and runner deregistration
with readback. A failed provider read is not a cleanup success. Its fresh atomic
mode-0600 receipt records the outcome and each owned cleanup result. Every tool/API
call has a 20-second timeout; an in-flight call can overshoot the observed deadline
by that bound, followed by separately bounded cleanup (budget approximately 180
seconds). Keep the service policy/writer freeze until cleanup readback; expiry
alone does not end the freeze. `cleanup_complete` covers only the exact VM
stopped/deleted and exact runner registration absent. The foreground Tart
process, task-private runtime/cache files and any separately authorized host
setup retain their own teardown ledger. Standard NAT does not
use the unused custom Softnet/DHCP path; the watchdog receipt does not attest
cleanup of unrelated or unselected helper installations. Do not treat
SIGKILL, host power loss or a dead watchdog as clean teardown: verify service
runner absence and exact guest state manually before ending the freeze, and
retain an explicit recovery gap if verification is unavailable. No broad process
kill or resource-name cleanup is supported.

## Owned cleanup and acceptance ledger

Each approved controller must retain owned resource identities before mutation.
On success/failure/cancellation, stop only its runner/guest, deregister the exact
owned runner ID and verify absence through GitHub, destroy the exact guest/disk,
remove its privileged helper if installed by this operation and verify no owned
process/profile/service/network resource remains. A job-end workflow step or
hook cannot guarantee cleanup after cancellation or VM failure; teardown needs
a separately owned bounded path. Never kill stable Symphony or remove resources
by a broad name filter. A failed teardown is an explicit blocker with sanitized
resource identities and an operator recovery action.

The following proof was recorded on 2026-10-27 for reviewed head
`c731aa0da506247b86cf56190d9e8bf4bd7c6473`, PR merge source
`c573f97676ac7ee67716e78ada2d6d17417d9e32`, and base
`b4c90f3a08f82027bffaafcdd7b94a4d8886192d`. Each job used its own fresh guest
and one-job registration; the first guest was stopped/deleted before the second
job and the second was stopped/deleted at closeout.

| Proof | Observed result |
| --- | --- |
| Exact PR/run/source refusal fixtures | 156 CI policy tests and 33 focused watchdog tests passed; independent review had no remaining findings. These are fixture/review results. |
| Guest and network | Owner verified Sonoma ARM64, Python 3.13, runner 2.338, public DNS/TLS, no host shares and shutdown. Standard NAT host/LAN reachability was accepted, not denied. |
| Existing Mac process job | [Application checks attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40543408836/attempts/2), job `122329324392`, runner `22`: success. |
| Native helper/package job | [Package checks attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40543408767/attempts/2), job `122333078964`, runner `23`: success; helper build, contracts and process fixtures passed. |
| Original GitHub artifact | Service artifact `12752751816`, `native-host-helper-macos-c573f97676ac7ee67716e78ada2d6d17417d9e32`; owner independently verified archive checksum, inner checksum, ARM64 Mach-O and exact source marker without executing the downloaded artifact on the host. |
| Watchdog and owned cleanup | Owner receipts report completed/success and exact VM/runner cleanup for both jobs (50.769 and 313.783 seconds). Independent final readbacks: runner inventory zero, permit 404, fork policy `first_time_contributors`; foreground processes reaped and clean base preserved. |
| Failure/cancellation recovery | The first App attempt exposed a busy/job-assignment API race. Its run was cancelled, exact runner/permit removed and guest reaped. The bounded confirmation fix then passed real execution. Unavailable-runner and other refusal cases remain fixture proof. |
| Required implementation checks | Application checks, Package qualification, Browser qualification and PR description all passed before integration. |
| Integration | PR #143 merged into `dev` at `9bed66a9a0eafd8da7e661be6a39899e6a3e6a2d`; this NAT runbook follow-up remains a separate review item. |

The original artifact archive was 182,942 bytes with SHA-256
`cac201d8253350aee23223e6173ed9a8c0cd1d46473ac3000144636235014c4d`.
The inner helper SHA-256 was
`9efc8c9eb4c143ecb68f6d7c8a0937ab23b4a010e8bcbe88fb74dba62d0a1bb7`.
Private owner receipts retain resource identities and independent verification;
public logs must not expose host addresses, routes or credentials.

## Original-source recovery proof on 2026-10-28

The later operator-authorized window tested the original PR149 post-merge source
`25a4307ca53094050d78771225d85e260d49f19d`, despite later `dev` advancement. The
initial Mac jobs had no runner or steps. The approved recovery reran the source-bound
selectors/dependents on the same handles, using fresh disposable guests and the
verified Python 3.13.18 / runner 2.338.0 base. The operator explicitly held other
coding writers, installed writer Apps and external Actions approvals idle.

| Real result | Original GitHub evidence |
| --- | --- |
| Application workflow and Mac process job: success | [Run `40719836698`, attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40719836698/attempts/2), Mac job `122965337886`, runner ID `47`. |
| Package workflow and native Mac helper/fixture job: success | [Run `40719836696`, attempt 2](https://github.com/Jayson-Stangel/symphony/actions/runs/40719836696/attempts/2), Mac job `122968522281`, runner ID `48`. |
| Original native artifact | Service ID `12836330563`, `native-host-helper-macos-25a4307ca53094050d78771225d85e260d49f19d`, 191,750 bytes; service SHA-256 `e65c21fb8704ba0799dccbf7df043728cbef7d235de759720ddff7a05b7e69b5`. |

The workflow/job conclusions and artifact service metadata were independently
read back from GitHub. The release owner additionally verified the downloaded
archive digest, inner checksum, ARM64 format and exact source marker. That native
content verification is owner-reported here, not a second execution of the
artifact by the documentation author. No new job or test was run for this refresh.

The first session policy-restoration readback reported **unverified**. Fresh
independent API reads subsequently confirmed the original `first_time_contributors`
policy, zero runners and actual permit HTTP 404. Preserve the initial report and
its reconciliation; neither a stale failure nor a prediction is final proof.
Both guest/process/registration cleanups were independently reconciled, the
durable store passed post-job verification, and only then was the writer freeze
released. Do not rerun successful tests or repeat a settings write solely because
an earlier readback was uncertain. Private owned receipts retain the reconciliation;
public logs must omit credentials, host addresses and routes.

## Remaining readiness and acceptance contract

Later proof supersedes the initial unexecuted-CI and temporary-retention gaps.
Keep the following distinctions when reviewing JAY-763; merging this prose alone
cannot close a still-required acceptance gap.

| Scope | Current proof and next boundary |
| --- | --- |
| Existing Mac checks, real architecture, ordinary required wrappers and artifacts | Demonstrated in both recorded windows, with exact source/run/attempt and original artifacts. Reuse those receipts for those sources; never relabel them as a newer revision. |
| Guest lifecycle and settings restoration | Real successful teardown and failed-attempt recovery were demonstrated. The 2026-10-28 readback reconciliation is part of that proof. No persistent runner remains. |
| Retained capacity | Durable store created/verified and reverified after job cleanup. Before each future window verify integrity/state and provision fresh job clones. It is not qualification, backup against device loss or standing execution authority. |
| Wrong/unapproved source and hostile workflow exclusion | Strict source/identity/refusal fixtures and readonly preflight exist; real approved source/token execution is proven. General adversarial service exclusion is not demonstrated by those successes. This personal repository still relies on the exact operator-established writer/fork/approval/queue window. No hostile fork workload was executed to manufacture proof. |
| Remaining JAY-763 acceptance | The integration coordinator must compare the current issue's refusal/admission criterion with this narrow demonstrated boundary. If an additional live negative-admission proof remains required, record the smallest concrete experiment and its separate authority; do not silently mark the criterion satisfied from fixtures or claim this PR supplies that live proof. |
| Next CI window | Refresh source/helpers, controller/run/attempt/targets, App/writer permissions and explicit freeze, all active statuses/concurrency, actual guest identities and settings readbacks. Use local-first recovery promptly for unassigned capacity queues; never duplicate an assigned healthy hosted job. |
| Physical-Mac installed/container acceptance | Separate from these CI guests. Reuse the owning chat's valid installed evidence; any new acceptance target must have its account, Docker and provider access checked before a deadline promise. Do not forward host Docker sockets into this guest or treat it as a physical-host substitute. |
| Hosted Sonoma retirement | The recorded November 2 retirement needs its own reviewed hosted-default migration. This fallback does not silently change test OS or weaken required checks. |

This PR refreshes the runbook and readiness handoff only. The sole integration
coordinator owns acceptance and authorized `dev` integration. No new privileged
runner/account/settings or real-account/model operation, production promotion,
signing, Apple submission, publication or stable update is authorized here.

### Retired engine routes

The combined engine `build` job retains actual standalone qualification and its
`Verify engine (target)` evidence name. Engine-only controller fixtures run in
that same native allocation with full Git history. The old `smoke` target and
unconsumed `release-native.yml` / `native` target are no longer valid new permits.
Historical source-bound permits and receipts remain evidence for their original
controller/source/run; do not reinterpret them under this updated allowlist.
This inventory change grants no native job, candidate, signing or publication
authority. Existing local-import verification retains its protected approval
boundary and gains no new access from its explicit table entry.
