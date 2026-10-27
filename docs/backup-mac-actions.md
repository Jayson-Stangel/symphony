# Backup Mac Actions preparation

JAY-763 requires real GitHub Actions execution of the existing Mac checks in an
isolated Apple Silicon environment. The fallback is **not enabled**. The source-bound selectors, read-only preflight and watchdog are prepared for
review; no runner or guest has been configured and no fallback execution has
been demonstrated. They are not execution approval or completion of the issue.
Without the explicit permit, hosted execution remains unchanged.

## Verified feasibility and remaining blockers

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
1.5 TiB available storage. No disposable Mac guest or dedicated noncustomer host
has been verified. `tart`, `prlctl` and `vfkit` were not available on PATH. These
facts do not establish guest toolchain compatibility or authorize installation.
A separate user account alone is not the isolation boundary required here.

No tested source-bound, pre-execution admission mechanism is established for this
personal repository. **Do not register/start a runner or enable fallback routing
until that boundary is demonstrated and the exact setup/execution is approved.**
An organization/repository migration would be a separate operator decision; this
issue does not authorize it. A guest VM containing no credentials reduces host
exposure but does not itself prevent an unrelated workflow from consuming the job.

A possible narrower repository admission proposal is a temporary
`all_external_contributors` fork-approval policy combined with a verified sole
trusted writer, explicit writer/source freeze, no external approvals during the
bounded window, and a complete inventory of historical queued/pending/approved
runs. The initial setting was `first_time_contributors`; changing it is an
external action requiring exact approval. Assess all workflow event triggers,
external/reusable workflow references and already admitted runs before claiming
this closes the queue race. Do not cancel unrelated runs or change the setting
from this runbook. This proposal remains conditional and is not permission to
register a runner; rejection/cancellation of exact unsafe runs, any settings
change, execution, and restoration must be part of the approved scope.

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
merge commit. It refuses forks, a changed head/base, a different run/attempt,
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
requires the owner-authored same-repository PR targeting `dev`, exact current
head/base/merge parents and live run association. All selectors use only existing
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
guest. No command in this document has been run to set a variable or register a
runner. Remove/restore only this operation's exact variable value after all owned
watchdogs have confirmed teardown; a different value is a concurrency conflict.

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

## Exact host setup proposal to complete before approval

The following VM sizing is a proposed starting point, not a selected/verified
host or permission to create it. The operator must approve a complete concrete
proposal after the admission design is resolved:

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
Phase B requires separate approval for first boot, network isolation/probes,
guest accounts and any toolchain installation. Its proposal must name required
Elixir/OTP, Swift/Xcode tools, Python, Git and archive utilities, their sources and
resulting guest changes. Phase A approval cannot authorize phase B. Runner setup
and execution additionally require the exact reviewed PR/source/controller,
run/attempt/target permit, fork-policy/settings readbacks, writer/apps freeze,
queued job inventory, registrations, bounded job count and teardown approved
separately after those identities exist. The release owner owns these approval
proposals and timing; this implementation chat does not dispatch them.

- Name the physical noncustomer host, VM identifier/image digest, clean guest
  account `ci`, source/PR/head/merge SHA, immutable controller/workflow identities,
  admitted job IDs/matrix entries and exact run/attempt/job count. Proposed guest:
  one VM, 6 vCPUs, 16 GiB RAM, 100 GiB disk, macOS Sonoma 14 ARM64, destroyed after one admitted job. The proposed concurrency is one VM: each later target uses a fresh clone after prior teardown, not reuse of a contaminated guest. A complete seven-job qualification can use one fixed two-hour permit containing all seven exact targets, with separate owned leases and at most one running guest. This is a proposal, not an approved job count.
- No host account creation, shared home/workspace disks, clipboard, SSH-agent
  forwarding, host sockets, Apple ID, login keychain, stable installation or
  customer data access. No host `symphonytest` account. Verify absence from inside
  the guest, rather than treating a settings declaration as proof.
- Choose and verify a Mac VM runtime/image before installation. Candidate runtime
  Tart 2.41.1 archive SHA-256:
  `e680a734febccfce7d4e910c1f7f13fc3b00f19a181c945e2d746de3214c32c2`.
  Candidate Softnet 0.24.1 archive SHA-256:
  `8ff8ff099c2b5ac1b1128e7999c4a730ac85f486c8a4cb0239eb98432859a0f7`.
  These are proposed component identities, not an installed or validated network.
- Explicitly deny host access (`@host`), private/link-local ranges and every host
  global IP. Start from default deny and enumerate required public GitHub,
  artifact and toolchain endpoints and DNS behavior. Softnet's default gateway
  behavior is not sufficient isolation. No bridged/default-NAT fallback. Prove
  positive required endpoint access and negative host/LAN access before registering.
- Softnet requires privileged helper setup: the proposal must enumerate the exact
  helper path, root ownership and mode 4755 if selected, installation/removal
  authority and teardown receipt. It also makes a persistent host configuration
  change: pinned Softnet 0.24.1 `src/main.rs` `configure_bootpd` overwrites the
  `bootpd` dictionary in SCPreferences `com.apple.InternetSharing.default.plist`
  with `DHCPLeaseTimeSecs: 600` and `dhcp_ignore_client_identifier: true`, then
  commits and applies it. This is not merely an ephemeral helper effect. Whichever
  approved phase first invokes Softnet must explicitly authorize that DHCP
  preference overwrite and its potential effect on other host VM networking;
  the stopped-clone Phase A excludes it. Any earlier proposed isolation/network
  probe would require its own explicit approval for the same persistent effects.
  Renew approval when the payload changes. Before mutation, read and privately retain
  the exact original key value and whether the key existed. After stopping all
  owned guests, compare the current key with the exact value this operation
  wrote; restore the exact prior value or prior absence only when that ownership
  check succeeds, then commit/apply and read back. If another owner changed it,
  stop restoration and report the conflict instead of overwriting their work.
  Retain separate preference-restoration and helper-removal receipts; remove the
  owned privileged helper only after owned networking/guests have stopped.
  Do not claim admin-free setup or absence of host configuration changes. Do not
  modify sudoers or host profiles/services. If these persistent effects or
  privilege/network isolation cannot be approved and established, stop setup.
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

No live setup or jobs are authorized by this document. Approval must cover the
exact host, privileged changes, source, registration and executions, not just a
VM size or an abstract intention to try the fallback.

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

The watchdog checks expiry/permit changes and job outcome, handles TERM/INT,
and independently attempts exact owned Tart stop/delete and runner deregistration
with readback. A failed provider read is not a cleanup success. Its fresh atomic
mode-0600 receipt records the outcome and each owned cleanup result. Every tool/API
call has a 20-second timeout; an in-flight call can overshoot the observed deadline
by that bound, followed by separately bounded cleanup (budget approximately 180
seconds). Keep the service policy/writer freeze until cleanup readback; expiry
alone does not end the freeze. `cleanup_complete` covers only the exact VM
stopped/deleted and exact runner registration absent. Tart/Softnet installation,
privileged helper/network setup, the persistent InternetSharing DHCP preference
overwrite/restoration and caches require their separate host teardown ledger and
approved cleanup; the watchdog receipt cannot attest them. Do not treat
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

| Proof | Current state |
| --- | --- |
| Live repository owner/admin capability | Initial read verified; must refresh before external setup. |
| Read-only exact PR/run/source refusal fixtures | Covered by `test_mac_runner_preflight.py`; fixture proof only. |
| Enforceable admission including hostile workflow/`always()` and queued race | Unresolved; no runner should be exposed. |
| Guest isolation/toolchain and network/mount negative checks | Unproved; guest not created. |
| Real existing backup Mac process job with correct check association | Not executed. |
| Real native ARM64 helper/package job and original service artifacts | Not executed. |
| Real cancellation/unavailable-runner refusal and owned teardown receipts | Not executed. |
| Independent review and required CI for implementation PR | Required before handoff/integration; no waiver. |
| Merge into `dev` and complete issue contract | Not observed; JAY-763 must remain unfinished. |

Source-bound fixtures and documentation do not complete any of the required real
runner, isolation, artifact, cleanup or merge acceptance. Production promotion,
stable rollout and release ownership remain separate from this capacity issue.
