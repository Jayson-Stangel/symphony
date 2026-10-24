"""Credential-free effect checks through the public, installed Codex sandbox CLI.

Input is a resolved Symphony policy, never shell text to execute. Output is a
fixed diagnostic code; native output is deliberately not forwarded.
"""
import json
import os
import shlex
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


class Refusal(Exception):
    pass


def interrupted(_signum, _frame):
    raise Refusal("sandbox_interrupted")


def contains(root, path):
    return path == root or root in path.parents


def diagnostic(data):
    if b"namespace" in data or b"unshare" in data:
        return "sandbox_namespace_denied"
    if b"proc" in data and (b"mount" in data or b"Operation not permitted" in data):
        return "sandbox_proc_setup_failed"
    return "sandbox_helper_failed"


def launch_command(value):
    # This is a finite argv interface, not a shell interpreter. In particular,
    # expansions/operators are refused even when a shell might quote them.
    text = value["command"]
    if value["remote"] or any(c in text for c in "\n\r;|&<>`$()"):
        raise Refusal("sandbox_unsupported_command")
    try:
        args = shlex.split(text)
    except ValueError:
        raise Refusal("sandbox_unsupported_command")
    wrapper = None
    if len(args) >= 4 and Path(args[0]).name == "bash":
        shell, script, *args = args
        if script.startswith("-") or Path(script).is_absolute():
            raise Refusal("sandbox_unsupported_command")
        source = Path(value.get("launch_root", value["workspace"])).resolve()
        script = (source / script).resolve()
        if not contains(source, script) or not script.is_file():
            raise Refusal("sandbox_launch_unavailable")
        shell = shutil.which(shell)
        if shell is None:
            raise Refusal("sandbox_launch_unavailable")
        wrapper = (shell, script.relative_to(source))
    if not args or Path(args[0]).name != "codex" or args[-1:] != ["app-server"]:
        raise Refusal("sandbox_unsupported_command")
    options = args[1:-1]
    if options not in ([], ["--config", "shell_environment_policy.inherit=all"],
                       ["-c", "shell_environment_policy.inherit=all"]):
        raise Refusal("sandbox_unsupported_command")
    if "/" in args[0] and not Path(args[0]).is_absolute():
        raise Refusal("sandbox_unsupported_command")
    return args[0], options, wrapper


def copy_project(source, target):
    # Preserve only links whose resolved destinations stay in the project, and
    # rewrite absolute internal links into the disposable copy. Never traverse
    # a project link into the runtime's native login/configuration directories.
    links = []
    def ignored(directory, names):
        return [name for name in names if name == ".git" or
                (Path(directory) / name).resolve() == target]
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [name for name in dirs if name not in ignored(directory, dirs)]
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                try:
                    resolved = path.resolve()
                except RuntimeError:
                    raise Refusal("sandbox_launch_unavailable")
                if not contains(source, resolved) or contains(target, resolved):
                    raise Refusal("sandbox_launch_unavailable")
                links.append((path.relative_to(source), resolved.relative_to(source)))
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignored, symlinks=True)
    for link, destination in links:
        copied = target / link
        copied.unlink()
        copied.symlink_to(os.path.relpath(target / destination, copied.parent))


def check(value):
    executable, options, wrapper = launch_command(value)
    if any(os.environ.get(k) for k in ("NODE_OPTIONS", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES")):
        raise Refusal("sandbox_unqualified_config")
    if wrapper is None:
        executable = shutil.which(executable)
        if not executable:
            raise Refusal("sandbox_helper_missing")
    policy = {"writableRoots": [], "readOnlyAccess": {"type": "fullAccess"},
              "networkAccess": False, "excludeTmpdirEnvVar": False, "excludeSlashTmp": False,
              **value["policy"]}
    fields = {"type", "writableRoots", "readOnlyAccess", "networkAccess",
              "excludeTmpdirEnvVar", "excludeSlashTmp"}
    if (set(policy) != fields or policy["type"] != "workspaceWrite"
            or policy["readOnlyAccess"] != {"type": "fullAccess"}
            or any(type(policy[k]) is not bool for k in
                   ("networkAccess", "excludeTmpdirEnvVar", "excludeSlashTmp"))
            or not isinstance(policy["writableRoots"], list)
            or any(not isinstance(p, str) or not Path(p).is_absolute() for p in policy["writableRoots"])):
        raise Refusal("sandbox_unsupported_policy")
    roots = [Path(p).resolve() for p in policy["writableRoots"]]
    workspace = Path(value["workspace"]).resolve()
    # Do not inherit model credentials, hooks or unknown native policy. Config
    # equivalence must be established before accepting this deliberately narrow
    # supported interface. auth.json is neither opened nor copied.
    launch_root = Path(value.get("launch_root", workspace)).resolve()
    for parent in [workspace, *workspace.parents, launch_root, *launch_root.parents]:
        if (parent / ".codex/config.toml").exists():
            raise Refusal("sandbox_unqualified_config")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if any(p.exists() for p in (codex_home / "config.toml", Path("/etc/codex/config.toml"),
                               Path("/etc/codex/requirements.toml"))):
        raise Refusal("sandbox_unqualified_config")
    if not policy["excludeSlashTmp"]:
        roots.append(Path("/tmp").resolve())
    if not policy["excludeTmpdirEnvVar"] and os.environ.get("TMPDIR"):
        roots.append(Path(os.environ["TMPDIR"]).resolve())
    # The negative target is writable by the ordinary runtime user and outside
    # every policy root. /tmp is normally allowed and is never assumed denied.
    outside_parent = next((p for p in (Path("/var/tmp"), Path("/var/lib/symphony"), Path.home())
                           if p.is_dir() and os.access(p, os.W_OK)
                           and not any(contains(r, p.resolve()) for r in roots)), None)
    if outside_parent is None:
        raise Refusal("sandbox_no_denial_target")
    with tempfile.TemporaryDirectory(prefix=".symphony-sandbox-", dir=workspace) as allowed, \
            tempfile.TemporaryDirectory(prefix=".symphony-sandbox-", dir=outside_parent) as denied:
        allowed = Path(allowed)
        denied = Path(denied)
        if wrapper:
            # Never mutate the source checkout. The trusted setup script runs in
            # a disposable project copy under the real effective writable root.
            copy_project(launch_root, allowed)
            script = allowed / wrapper[1]
            if not script.is_file() or not contains(allowed, script.resolve()):
                raise Refusal("sandbox_launch_unavailable")
        selected_git = str(workspace / ".git")
        git_write = selected_git in policy["writableRoots"]
        if value.get("containerized", False) and not git_write:
            raise Refusal("sandbox_git_unavailable")
        protected = allowed / (".codex" if git_write else ".git")
        protected.mkdir(exist_ok=True)
        git_metadata = allowed / ".git"
        git_metadata.mkdir(exist_ok=True)
        # The disposable repository receives exactly the owned-metadata grant
        # selected by the managed task policy, relocated from the real workspace.
        policy["writableRoots"] = [str(git_metadata) if p == selected_git else p
                                   for p in policy["writableRoots"]]
        # Positive controls distinguish Codex enforcement from ordinary DAC or
        # read-only mounts. Every path is owned disposable data.
        for directory in (allowed, denied, protected):
            control = directory / "control"
            control.write_text("control")
            control.unlink()
        home = denied / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "TMPDIR", "LANG", "LC_ALL")}
        env.update(HOME=str(home), CODEX_HOME=str(home))
        args = [executable, *options, "sandbox", "-c", 'sandbox_mode="workspace-write"']
        for key, name in (("networkAccess", "network_access"), ("excludeSlashTmp", "exclude_slash_tmp"),
                          ("excludeTmpdirEnvVar", "exclude_tmpdir_env_var")):
            args += ["-c", "sandbox_workspace_write." + name + "=" + str(policy[key]).lower()]
        args += ["-c", "sandbox_workspace_write.writable_roots=" + json.dumps(policy["writableRoots"])]
        payload = """import errno,pathlib,subprocess,sys
a,d,g=map(pathlib.Path,sys.argv[1:4])
(a/'allowed').write_text('sandbox-ready')
for p in (d/'outside',g/'protected'):
    try:p.write_text('escaped')
    except OSError as error:
        if error.errno not in (errno.EACCES,errno.EPERM,errno.EROFS):raise
    else:sys.exit(71)
if sys.argv[4]!='git-write':
    (a/'complete').write_text('sandbox-ready')
    sys.exit(0)
def git(*args):
    result=subprocess.run(['git',*args],cwd=a,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:sys.exit(72)
    return result.stdout.decode().strip()
git('init','-q')
git('config','user.name','Symphony readiness')
git('config','user.email','readiness@example.invalid')
(a/'tracked').write_text('baseline')
git('add','tracked')
git('commit','-qm','baseline')
git('init','--bare','-q','remote.git')
git('remote','add','origin',str(a/'remote.git'))
git('push','-q','origin','HEAD:refs/heads/main')
git('clone','-q','--branch','main','remote.git','cloned')
git('fetch','-q','origin')
git('switch','-qc','codex/readiness')
(a/'tracked').write_text('candidate')
assert (a/'tracked').read_text()=='candidate'
git('add','tracked')
git('commit','-qm','candidate')
git('push','-q','origin','HEAD:refs/heads/codex/readiness')
git('fetch','-q','origin','codex/readiness')
assert git('rev-parse','HEAD')==git('rev-parse','FETCH_HEAD')
assert git('--git-dir=remote.git','show','codex/readiness:tracked')=='candidate'
(a/'tracked').write_text('dirty')
(a/'untracked').write_text('preserved')
git('fetch','-q','origin')
assert (a/'tracked').read_text()=='dirty' and (a/'untracked').read_text()=='preserved'
(a/'git-complete').write_text('sandbox-ready')
(a/'complete').write_text('sandbox-ready')
"""
        args += ["--", sys.executable, "-c", payload, str(allowed), str(denied), str(protected), "git-write" if git_write else "protected-git"]
        reached = denied / "launcher-reached"
        if wrapper:
            # The trampoline is an argv target for exec "$@" wrappers. It
            # resolves Codex only after their PATH/runtime setup, and preserves
            # that environment for the actual sandbox. Its marker is diagnostic
            # only: the native positive and negative effects still decide readiness.
            trampoline = allowed / ".symphony-launch.py"
            trampoline.write_text("""import os,pathlib,shutil,sys
if any(os.environ.get(k) for k in ('NODE_OPTIONS','LD_PRELOAD','LD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES')):
    sys.exit(78)
home=pathlib.Path(os.environ.get('CODEX_HOME',str(pathlib.Path.home()/'.codex')))
if (home/'config.toml').exists():sys.exit(78)
exe=shutil.which(sys.argv[2])
if not exe:sys.exit(127)
pathlib.Path(sys.argv[1]).write_text('reached')
os.execvpe(exe,[exe,*sys.argv[3:]],os.environ)
""")
            args = [wrapper[0], str(script), sys.executable, str(trampoline), str(reached), *args]
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(args, cwd=allowed, env=env, stdout=output, stderr=output, start_new_session=True)
            try:
                status = child.wait(timeout=180 if wrapper else 15)
            except subprocess.TimeoutExpired:
                raise Refusal("sandbox_timeout")
            finally:
                # Kill descendants even if the immediate helper exited first.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            output.seek(0)
            data = output.read(65536)
        if (denied / "outside").exists() or (protected / "protected").exists():
            raise Refusal("sandbox_policy_enforcement_failed")
        if wrapper and not reached.exists():
            raise Refusal("sandbox_unqualified_config" if status == 78 else
                          "sandbox_helper_missing" if status == 127 else "sandbox_launch_failed")
        if status != 0:
            raise Refusal("sandbox_git_unavailable" if status == 72 else
                          diagnostic(data) if status > 0 else "sandbox_helper_terminated")
        if any(not (allowed / p).is_file() or (allowed / p).read_text() != "sandbox-ready"
               for p in (("allowed", "git-complete", "complete") if git_write else ("allowed", "complete"))):
            raise Refusal("sandbox_required_effect_missing")
    return {"ready": True, "code": "sandbox_ready", "protocol": 1}


def main():
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result = check(json.loads(sys.argv[1]))
    except Refusal as error:
        result = {"ready": False, "code": str(error), "protocol": 1}
    except Exception:
        result = {"ready": False, "code": "sandbox_check_failed", "protocol": 1}
    print(json.dumps(result))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
