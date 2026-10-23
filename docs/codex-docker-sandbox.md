# Codex protected commands in Docker

Codex 0.163.0 needs Linux user, mount and PID namespaces to enforce workspace
permissions. A successful app-server handshake does not establish that commands
can run. Symphony uses the public `codex sandbox` interface to check actual
allowed writes and denied writes before startup and candidate transition.

## Runtime contract

The Codex launch adapter in `scripts/symphony` is shared by listeners, doctor,
candidate preflight and image qualification. It verifies
`scripts/codex-seccomp.json` against its compiled SHA-256 before launch. Packaged
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
profile remain enabled. `no-new-privileges` prevents exec-based privilege gains.
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
