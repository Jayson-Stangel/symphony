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

An administrator must review and add the profile on the **selected Docker daemon
host** before starting or qualifying a Codex runner there:

```sh
sudo apparmor_parser -a -W /path/to/reviewed/scripts/codex-apparmor.profile
```

The fixed profile name is `symphony-codex-candidate-b6261d4c613296dd`. Its source
SHA-256 is `2eddb013e3503591aaac8e583e6dca093290287713c8aa0869b2d4458fa594aa`.
It is derived from the same pinned Moby revision as the seccomp policy; its
license is included as `scripts/codex-seccomp.LICENSE`. It assumes ABI 3.0,
standard AppArmor imports, and an unconfined Docker daemon. Host imports and the
kernel-loaded policy are administrator-controlled inputs: the source checksum
cannot prove the contents of a loaded profile. Do not reuse this name for changed
rules, replace an existing profile, or unload profiles needed by running projects
or rollback. Use a new versioned name for future rule changes.

Symphony never invokes sudo, loads policy, changes docker-default, or restarts the
daemon. A missing profile causes Docker to refuse launch. Daemon-info errors,
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
