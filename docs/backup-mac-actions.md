# Backup Mac Actions runbook

JAY-763 provides an optional, source-bound fallback for existing Mac checks in a
disposable Apple Silicon guest. [PR #143](https://github.com/Jayson-Stangel/symphony/pull/143)
merged into `dev` at `9bed66a9a0eafd8da7e661be6a39899e6a3e6a2d` after real Mac
process and native helper jobs passed. Hosted execution remains the default when
no exact permit matches. No persistent runner is left registered. This runbook
grants no new execution, merge, production or stable-rollout authority.

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
A separate user account alone is not the isolation boundary required here.

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
| `release-native.yml` / `native` | `Native process fixtures (macos)` | Existing explicit `python3 scripts/ci/release_tests.py native` qualification. |

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
| `make-all.yml` | `platform-contracts` |
| `package-checks.yml` | `native-host-package` |
| `release-native.yml` | `native` |
| `native-manager-helpers.yml` | `helper` |
| `current-manager-package.yml` | `package` |
| `runner-release.yml` | `packaged-candidate`, `packaged-release-platform` |
| `burrito-release.yml` | `build`, `smoke`, `controller-diagnostics` |

Only these ten logical targets can be named; one run permit admits at most eight
distinct consumer/job pairs, each with a distinct label and isolated one-job
runner. Engine/helper/package commands and artifact identities remain in their
existing jobs. Linux candidate/cache/transfer matrices and native host policy are
unchanged. Signing/notary and protected publication jobs/guards are untouched.
The allowlist is routing scope, not approval to run a candidate or production work.
All original caller guards and required source/artifact qualifications still apply.

The top-level controllers allowed by the validator are `make-all.yml`,
`package-checks.yml`, `runner-integration.yml`, `runner-release.yml` and
`burrito-release.yml`. Reused jobs bind the top-level `github.workflow_ref` and
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

JAY-742/PR140 owns local signing and protected distribution verification. This
change neither copies its unmerged ingress nor routes its signing/verification
jobs. Any additional credential-free verification integration must use its
reviewed interface and exact separately approved source. No new candidate
execution or duplicate qualification is authorized by adding selectors.

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
The supported staged proposal is: inspect that run and current source, obtain
explicit approval to cancel that exact run and rerun all its jobs at the next known
attempt, prepare/read back the permit for that next attempt, then perform only the
approved cancellation/rerun. The rerun must use the same reviewed workflow that
already contains these selectors; an older pre-selector run cannot bootstrap this.
Revalidate current PR base/head/merge binding before starting. A moved baseline
requires fresh review/approval rather than borrowing an older passing check.
Ordinary GitHub check association is retained by rerunning the actual workflow,
subject to the unchanged required gates. Do not copy a green check
from another source, create a synthetic success, silently omit the Mac job or
waive a failed/pending required check to bootstrap this PR. If Mac capacity
prevents the required checks, the PR stays unmerged until real qualifying proof
is available. A different trusted CI topology requires a reviewed design and
explicit approval for its external effects, not an improvised second dispatcher.

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

A future approved launcher must retain owned resource identities before mutation.
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
and one-job registration; both guests were stopped/deleted before the next job.

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

These observations do not establish installed-product acceptance, arbitrary-fork
safety or permission for a later source/run. JAY-763 remains unfinished while the
runbook follow-up is pending. Production promotion, stable rollout, signing and
release ownership retain their separate approval and validation boundaries.
