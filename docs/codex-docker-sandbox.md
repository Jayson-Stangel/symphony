# Codex protected commands in Docker

Codex 0.163.0 needs Linux user, mount and PID namespaces to enforce workspace
permissions. A successful app-server handshake does not establish that commands
can run. Symphony uses the public `codex sandbox` interface to check actual
allowed writes and denied writes before startup and candidate transition.

## Runtime contract

The Codex launch adapter in `scripts/symphony` is shared by listeners, doctor,
candidate preflight and image qualification. It verifies
`scripts/codex-seccomp.json` against its compiled SHA-256 before launch and queries
the selected Docker daemon for AppArmor support. An enabled AppArmor daemon also
selects the checksummed `scripts/codex-apparmor.profile` described below. Packaged
releases include that file in their normal checksummed inventory. The expected
profile is recorded in the immutable container label
`com.symphony.codex.runtime`; same-image reuse refuses an older profile and directs
the operator to the normal updater without stopping the runner.

The profile is the Docker baseline from
[moby/profiles at 4b8214fc4971e06172938a1f1554ccff6647ee80](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json),
whose original SHA-256 is
`6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`.
The only additional rules permit:

- `clone` when `CLONE_NEWUSER` or `CLONE_NEWPID` is present in argument zero,
  appropriate for the supported ARM64 and AMD64 container architectures.
- `mount`, `umount2` and `pivot_root`, which the helper needs after entering its
  own user/mount namespace. Kernel capability checks still apply. These calls
  cannot acquire `CAP_SYS_ADMIN` in the outer container namespace.

No outer capability is added. Docker's masked/read-only system paths and AppArmor
enforcement remain enabled. `no-new-privileges` prevents exec-based privilege gains.
The added calls expose additional kernel namespace/mount code, so qualification
requires process, filesystem and network effects as well as a successful command.
The profile intentionally does not allow `unshare`, `setns` or unrestricted
namespace cloning beyond the baseline. Unsupported hosts refuse execution.

The reference image removes system Bubblewrap and uses the helper bundled with
the pinned Codex release. System Bubblewrap 0.12 reports the proc mount failure
using `/proc`; Codex 0.163's fallback matcher expects `/newroot/proc`. Its bundled
helper matches that interface, allowing fallback while retaining Docker's proc
masks. Codex still creates user/PID namespaces and applies its own restrictions.

Container mode binds workspace `/workspaces`, server `0.0.0.0`, and noninteractive
approval. It preserves the configured Codex filesystem/network policy instead of
forcing full access. The initial readiness adapter supports workspace-write;
unsupported policies, custom commands, SSH workers and unqualified native config
refuse readiness. Claude's launch and authentication policy are unchanged.

## AppArmor hosts

Docker's default AppArmor profile denies all mounts, including the helper's
namespaced staging mounts. The shipped Docker-derived policy replaces that
blanket denial with exact mount flags, tmpfs/devpts types, staging paths, and two
pivot-root operations observed on native x86-64 Forge. It retains the baseline
proc/sys writes, network-family, signal and ptrace restrictions. The helper's
fresh proc mount remains denied; the pinned bundled helper uses its qualified
fallback with Docker's proc masks intact. This exposes additional kernel mount
code and requires the same effect checks as the seccomp extension.

### Identify the daemon host

Before the first Codex start, an administrator must prepare the **selected Docker
daemon host** if that daemon advertises AppArmor. From the CLI machine, use the
same runtime executable and Docker context/`DOCKER_CONTEXT`/`DOCKER_HOST` settings
as Symphony. For Docker, the reduced diagnostic is:

```sh
runtime=${SYMPHONY_CONTAINER_RUNTIME:-docker}
"$runtime" info --format 'Daemon={{.Name}} Kernel={{.KernelVersion}} Security={{json .SecurityOptions}}'
```

Look for `name=apparmor` in the security options. Establish which machine owns
that daemon before using administrator commands; do not infer it from the CLI
machine's OS or hostname. Inspect connection settings privately and do not paste
credential-bearing endpoint URLs into logs. For a remote daemon, perform the
Linux preparation below on its host, not on the CLI machine. A macOS client can
select a remote AppArmor daemon. No-AppArmor daemons, including the qualified
LinuxKit Docker Desktop configuration, need no AppArmor installation/load step;
they still require the shipped seccomp and protected-readiness checks.

This does not expand supported manager hosts: macOS ARM64 and Ubuntu 26.04 x86-64
remain supported; Linux AMD64 and ARM64 are separate required container targets.

### Locate and verify the source-free policy

Start with an archive verified against its release metadata as described in
[packaged installation](packaged-install.md). In Bash on the package's supported
manager host, use its actual application directory and resolve `current` once:

```sh
app_home="$HOME/.local/share/symphony" # replace for --app-dir or a namespace
release_dir=$(cd "$app_home/current" && pwd -P)
# Before installation, instead set release_dir to the absolute verified extraction root.
"$release_dir/libexec/symphony-host" verify-files "$release_dir"
policy="$release_dir/scripts/codex-apparmor.profile"
profile_name=symphony-codex-candidate-b6261d4c613296dd
expected=2eddb013e3503591aaac8e583e6dca093290287713c8aa0869b2d4458fa594aa
actual=$("$release_dir/libexec/symphony-host" sha256 "$policy")
test "$actual" = "$expected"
grep -F -- "profile $profile_name " "$policy"
sed -n '1,160p' "$policy" # administrator reviews the rules before loading
```

Stop on any failed command or mismatch. The profile is in both supported host
archives; neither a Symphony source checkout nor the CI helper is needed.
Installed releases are immutable: do not edit the policy or change a pinned
checksum to accept other rules. These name/hash values describe this shipped
recipe; a later versioned policy requires its matching verified release guidance.
The license is `scripts/codex-seccomp.LICENSE` in the same bundle.

For a remote daemon, securely transfer only the reviewed policy to an
administrator-controlled path on that host. There set `policy` to the transferred
file's absolute path, and set `profile_name` and `expected` to the values above.
Recheck its bytes there before loading (Ubuntu provides `sha256sum`):

```sh
printf '%s  %s\n' "$expected" "$policy" | sha256sum --check -
```

Do not run a macOS bundle helper on a Linux daemon host. The inventory and hash
bind the shipped source bytes, not the contents of an already-loaded kernel
profile or the host's administrator-controlled imports.

### Explicit administrator preparation on that Linux host

The daemon host needs an AppArmor-capable kernel with AppArmor active, mounted
securityfs, a compatible `apparmor_parser`, ABI 3.0 and the standard
`/etc/apparmor.d/abi/3.0`, `tunables/global` and `abstractions/base` imports.
The policy assumes an unconfined Docker daemon and the qualified Linux
user/mount/PID namespace behavior. An Ubuntu administrator can install missing
parser/import tools with `sudo apt-get update` followed by
`sudo apt-get install --no-install-recommends apparmor`. This is a separate
administrator decision, not a Symphony installer or runtime action.

Check the actual daemon host, stopping if any prerequisite is unavailable:

```sh
cat /sys/module/apparmor/parameters/enabled # must be Y
sudo cat /sys/kernel/security/lsm # must include apparmor
stat /proc/thread-self/attr/apparmor/exec
sudo test -r /etc/apparmor.d/abi/3.0
sudo test -r /etc/apparmor.d/tunables/global
sudo test -r /etc/apparmor.d/abstractions/base
loaded_profiles=$(sudo cat /sys/kernel/security/apparmor/profiles) || exit 1
printf '%s\n' "$loaded_profiles" | grep -F -- "$profile_name ("
```

A matching line means the name already exists: **do not add, replace or unload
it** unless administrator records independently establish the exact source/import
provenance. A known matching policy must pass the enforce-list verification
below without loading again; a complain-mode listing is a blocker. An unknown
profile requires administrator investigation; neither the name
nor an on-disk checksum proves the loaded contents. No match is expected for a
new profile only when the inventory read succeeded; an inspection/access error
is a blocker, not evidence of absence.

Only for an absent name, after reviewing and verifying the file on this host:

```sh
sudo apparmor_parser --add "$policy"
```

For both a newly added policy and an existing known matching policy, verify:

```sh
sudo grep -Fx -- "$profile_name (enforce)" /sys/kernel/security/apparmor/profiles
```

Stop on parser errors, missing imports or a missing enforce line. Never use
`--replace` to overwrite unknown policy, change `docker-default`, disable
AppArmor/seccomp, run a privileged container, or bypass the protected check.
The [Ubuntu parser manual](https://manpages.ubuntu.com/manpages/noble/man8/apparmor_parser.8.html)
describes `--add` and its refusal when a name already exists.

This loads policy into the **current kernel for this boot**; it does not install
the file for reboot persistence. The versioned package path is not an automatic
boot-policy directory. Persistence is separate administrator-managed host setup:
use the distribution's AppArmor policy/service management (normally
`/etc/apparmor.d/` on Ubuntu), retaining the exact reviewed file and imports.
After reboot, verify the enforce listing and protected readiness again.
Do not overwrite an existing persistent policy file or unload profiles needed by
running projects, retained environments or rollback. Keep old versioned profiles
until their consumers are retired. A future changed recipe needs a new versioned
name. See [Ubuntu's AppArmor administration guide](https://documentation.ubuntu.com/server/how-to/security/apparmor/index.html)
for distribution policy management; Symphony does not configure boot persistence.

### Verify protected readiness, then distinguish authentication

Back on the CLI machine, from the intended initialized project checkout with its
reviewed `WORKFLOW.md` and selected compatible Codex environment:

```sh
symphony --version
symphony doctor --json
symphony runner doctor --agent codex
```

`doctor` checks installation/inventory and runtime executable availability
(plus selected environment validation when applicable); it does **not** qualify
the sandbox.
`runner doctor` first uses the shared launch integration and selected immutable
image for a credential-free disposable protected-command check. The exact marker
`Codex sandbox protected-command checks passed.` means it wrote the intended
workspace canary and denied outside/protected Git writes for that workflow and
image. No native login or model request is provided to this first check. It does
not independently repeat CI's sibling-process and both network-mode probes.

The command then checks native authentication and isolated Git. Without a login,
it can fail after the protected-check marker; that is not full doctor success or
provider readiness. No marker means no protected success. A complete doctor pass
still does not prove authenticated inference, tracker/repository access or a
real issue-to-PR run. Login and any live provider task require their own operator
authority; they are not needed to inspect the policy or its credential-free gate.

### Troubleshooting without weakening isolation

| Symptom | Check and action |
|---|---|
| Docker cannot apply the named profile | Confirm the selected daemon host and inspect its loaded inventory. Load only the reviewed matching policy there if absent. Advertising AppArmor is insufficient. |
| Missing/corrupt file, wrong name or hash | Reverify the matching release archive and inventory. Stop; do not edit immutable files or substitute a checksum/name. |
| Parser/import/LSM or per-thread interface failure | Have the host administrator establish compatible kernel/LSM/securityfs/parser/import prerequisites. An error mentioning `attr/apparmor/exec` alone does not establish which prerequisite failed. |
| A same-name policy is already loaded | Preserve it and establish provenance. Do not replace or unload an unknown policy or one required by active/rollback consumers. |
| Policy loaded, protected check still fails | Retain the refusal; inspect `sandbox_not_ready`, selected immutable image compatibility, workflow policy, workspace/TMPDIR paths, mount/storage behavior and host kernel/runtime diagnostics. A listed profile is not execution proof. |
| Protected marker succeeds, auth check fails | Treat policy success and native login as separate results. Do not claim whole-doctor readiness or start a listener merely to test the policy. |

The ordinary installed manager never invokes sudo, loads policy, changes
`docker-default`, or restarts the daemon. Explicit disposable CI preparation is
a separate qualification action, not a persistent-host setup command. A missing
profile causes Docker to refuse launch. Daemon-info errors,
empty output, malformed security options, or profile-integrity errors also refuse
execution. The immutable runtime label includes the AppArmor name and source
checksum, so reuse and interrupted transition recovery reject a different recipe.
Non-AppArmor daemons, including the qualified LinuxKit host, retain the previous
seccomp-only identity. Rollback starts the retained container with its original
policy rather than rebuilding it under today's policy.

This first AppArmor recipe qualifies writable workspace paths beneath
`/workspaces` and temporary paths beneath `/tmp`. Additional writable roots or an
effective TMPDIR outside those trees are unqualified and may fail readiness.
Exact remount flags must also qualify against the deployment's actual storage.
The real protected-command gate remains required; compilation or a source hash
alone does not establish compatibility.

The Forge audit-profile and narrower-profile probes used disposable data and no
account credentials. The shipped profile differs from the approved narrower
candidate only in a descriptive comment; both compile to identical policy bytes
with Forge's parser. The earlier broad audit profile is diagnostic only and is
never selected by this launcher.

## Selected and historical environments

Manager-only activation preserves the selected immutable runtime image. It does
not install this capability into an old image. A fresh Codex start or transition
requires the real protected-readiness diagnostic embedded in a compatible rebuilt
image; an older protocol-1 image lacking that diagnostic refuses preflight before
drain. Explicitly select a rebuilt compatible Codex environment through the
normal environment-selection workflow, then retry the transition. The updater
never silently replaces a selected or team-owned environment to obtain this fix.

Historical `previous` image builds run their pinned source tree's own committed
smoke/startup contract, with an explicit historical-only evidence receipt. That
original proof does not establish today's protected readiness. Current component
builds always run the current protected-command qualifier.

## Qualification and evidence limits

Run `bash runner/codex-sandbox-smoke.sh IMAGE` to verify the immutable image with
credential-free disposable storage. The first check uses the actual resolved
container workflow and tests workspace/outside/`.git` writes. A second check
verifies that an ordinary sibling's disposable environment and proc-root paths
cannot be read, sibling signal access is denied, and a controlled TCP endpoint is
denied with network disabled and reachable when explicitly enabled. Positive
controls run outside the protected helper to prove that denials are meaningful.
No account secret, host home or Docker socket is mounted.

`bash scripts/release/runner-startup.sh IMAGE codex` checks the actual entrypoint
with an empty in-memory tracker. This is a listener/component proof, not a paid
provider turn or authenticated managed-worker demonstration. Native macOS helper
checks, ARM64 containers on Docker Desktop, AMD64 emulation, and a real Linux
x86-64 host must be reported separately. An unexecuted host/matrix cell is not a
pass. CI executes the real protected-command qualifier; fixture tests alone do
not qualify an image or host.
