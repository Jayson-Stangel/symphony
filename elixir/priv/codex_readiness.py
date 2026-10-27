"""Credential-free effect checks through the public, installed Codex sandbox CLI.

Input is a resolved Symphony policy, never shell text to execute. Output is a
fixed diagnostic code; native output is deliberately not forwarded.
"""
import json
import hashlib
import stat
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


def launch_receipt(data, expected_image_chain, wrapped):
    """Decode a bounded guard receipt without ever forwarding child output."""
    if not data:
        raise Refusal("sandbox_wrapper_failed" if wrapped else "sandbox_launch_failed")
    if len(data) > 16384:
        raise Refusal("sandbox_launch_receipt_invalid")
    try:
        chain = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refusal("sandbox_launch_receipt_invalid")
    if not isinstance(chain, dict):
        raise Refusal("sandbox_launch_receipt_invalid")
    if "error" in chain:
        code = chain["error"]
        if (set(chain) != {"error"} or type(code) is not str or code not in
                ("sandbox_unqualified_config", "sandbox_unsupported_command",
                 "sandbox_toolchain_unqualified", "sandbox_launch_failed",
                 "sandbox_browser_config_unsafe", "sandbox_browser_config_unwritable")):
            raise Refusal("sandbox_launch_receipt_invalid")
        raise Refusal(code)
    node = chain.get("node")
    if ({k: v for k, v in chain.items() if k != "node"} != expected_image_chain or
            not isinstance(node, dict) or set(node) != {"path", "sha256"} or
            not isinstance(node["path"], str) or not Path(node["path"]).is_absolute() or
            not isinstance(node["sha256"], str) or len(node["sha256"]) != 64 or
            any(c not in "0123456789abcdef" for c in node["sha256"])):
        raise Refusal("sandbox_launch_receipt_invalid")
    return chain


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


def copy_project(source, target, browser_state=None, defer_external=False):
    # Preserve only links whose resolved destinations stay in the project, and
    # rewrite absolute internal links into the disposable copy. Never traverse
    # a project link into the runtime's native login/configuration directories.
    links = []
    omitted = set()
    def ignored(directory, names):
        return [name for name in names if name == ".git" or
                (Path(directory) / name).relative_to(source).as_posix() in omitted or
                (Path(directory) / name) == browser_state or
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
                    relative = path.relative_to(source).as_posix()
                    if (not defer_external or contains(target, resolved) or
                            any(part in (".codex", ".git") for part in path.relative_to(source).parts)):
                        raise Refusal("sandbox_launch_unavailable")
                    omitted.add(relative)
                    if len(omitted) > 256 or sum(len(item) for item in omitted) > 32768:
                        raise Refusal("sandbox_launch_unavailable")
                    continue
                links.append((path.relative_to(source), resolved.relative_to(source)))
    # This is the helper's newly-created empty temporary root, never a task
    # directory. copytree copies source directory metadata too; keep the private
    # destination boundary even when an ordinary read-only source uses 0775.
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if (target.resolve() != target or not stat.S_ISDIR(before.st_mode) or
                before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o700 or
                any(target.iterdir())):
            raise Refusal("sandbox_launch_unavailable")
        try:
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignored, symlinks=True)
        finally:
            opened = os.fstat(descriptor)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_uid)
            if identity(opened) != identity(before):
                raise Refusal("sandbox_launch_unavailable")
            os.fchmod(descriptor, 0o700)
            current = target.lstat()
            if not stat.S_ISDIR(current.st_mode) or identity(current) != identity(before):
                raise Refusal("sandbox_launch_unavailable")
    finally:
        os.close(descriptor)
    for link, destination in links:
        copied = target / link
        copied.unlink()
        copied.symlink_to(os.path.relpath(target / destination, copied.parent))
    return sorted(omitted)



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
        if os.path.lexists(parent / ".codex/config.toml"):
            raise Refusal("sandbox_unqualified_config")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    browser_state = None
    if value.get("snapshot_profile") in ("trust-ro-v1", "trust-ro-v2"):
        retained_context = attest_context(value)
        retained_config = retained_context["config_bytes"]
        # Validate the real explicit destination without writing outside the
        # disposable diagnostic. The actual launch validates again post-wrapper.
        browser_env = browser_environment(value, os.environ, initialize=False)
        browser_state = Path(browser_env.get("CHROME_CONFIG_HOME", browser_env.get("XDG_CONFIG_HOME")))
        try:
            value["expected_image_chain"] = image_chain(retained_context["executable"])
        except Refusal:
            raise Refusal("sandbox_toolchain_unqualified")
    else:
        if any(os.path.lexists(p) for p in (codex_home / "config.toml", Path("/etc/codex/config.toml"),
                                           Path("/etc/codex/requirements.toml"))):
            raise Refusal("sandbox_unqualified_config")
        retained_config = None
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
        allowed = Path(allowed).resolve()
        denied = Path(denied).resolve()
        omitted_links = []
        if wrapper:
            # Never mutate the source checkout. The trusted setup script runs in
            # a disposable project copy under the real effective writable root.
            omitted_links = copy_project(launch_root, allowed, browser_state, defer_external=retained_config is not None)
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
        if retained_config is not None:
            (home / "config.toml").write_bytes(retained_config)
            (home / "config.toml").chmod(0o600)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "TMPDIR", "LANG", "LC_ALL", "CHROME_CONFIG_HOME", "XDG_CONFIG_HOME")}
        env.update(HOME=str(home), CODEX_HOME=str(home))
        browser_environment_map = []
        if retained_config is not None:
            for key in ("CHROME_CONFIG_HOME", "XDG_CONFIG_HOME"):
                if key in env:
                    original = env[key]
                    env[key] = str(allowed / (".symphony-probe-" + key.lower()))
                    browser_environment_map.append({"probe": env[key], "source": original})
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
        args += ["--", sys.executable, "-I", "-c", payload, str(allowed), str(denied), str(protected), "git-write" if git_write else "protected-git"]
        reached = denied / "launcher-reached"
        if wrapper:
            # The trampoline is an argv target for exec "$@" wrappers. It
            # resolves Codex only after their PATH/runtime setup, and preserves
            # that environment for the actual sandbox. Its marker is diagnostic
            # only: the native positive and negative effects still decide readiness.
            trampoline = allowed / ".symphony-launch.py"
            trampoline.write_text("""import os,pathlib,shutil,sys,hashlib,stat
if any(os.environ.get(k) for k in ('NODE_OPTIONS','LD_PRELOAD','LD_LIBRARY_PATH','DYLD_INSERT_LIBRARIES')):
    sys.exit(78)
home=pathlib.Path(os.environ.get('CODEX_HOME',str(pathlib.Path.home()/'.codex')))
if home.resolve()!=pathlib.Path(EXPECTED_HOME) or home.is_symlink():sys.exit(78)
config=home/'config.toml'
if EXPECTED_CONFIG is None:
    if os.path.lexists(config):sys.exit(78)
else:
    info=config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o022 or info.st_size>65536:sys.exit(78)
    descriptor=os.open(config,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        opened=os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):sys.exit(78)
        raw=stream.read(65537)
    if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=EXPECTED_CONFIG:sys.exit(78)
for ancestor in (pathlib.Path.cwd(),*pathlib.Path.cwd().parents):
    if os.path.lexists(ancestor/'.codex/config.toml'):sys.exit(78)
exe=shutil.which(sys.argv[2])
if not exe:sys.exit(127)
pathlib.Path(sys.argv[1]).write_text('reached')
os.execvpe(exe,[exe,*sys.argv[3:]],os.environ)
""".replace("EXPECTED_HOME", repr(str(home))).replace("EXPECTED_CONFIG", repr(hashlib.sha256(retained_config).hexdigest() if retained_config is not None else None)))
            if value.get("snapshot_profile") not in ("trust-ro-v1", "trust-ro-v2"):
                args = [wrapper[0], str(script), sys.executable, "-I", str(trampoline), str(reached), *args]
        read_fd = write_fd = None
        launch_chain = None
        if value.get("snapshot_profile") in ("trust-ro-v1", "trust-ro-v2"):
            read_fd, write_fd = os.pipe()
            probe_value = {k: v for k, v in value.items() if k != "launch_program"}
            probe_value.update(operation="probe", workspace=str(allowed), launch_root=str(allowed), browser_policy=policy,
                               browser_source={"workspace": str(workspace), "launch_root": str(launch_root), "browser_policy": value["policy"],
                                               "home": os.environ.get("HOME", str(Path.home())),
                                               "codex_home": str(codex_home)},
                               browser_omitted_links=omitted_links, browser_environment_map=browser_environment_map,
                               expected_home=str(home), expected_codex_home=str(home),
                               expected_native_environment={}, expected_snapshot_uid=os.getuid(), receipt_fd=write_fd)
            prefix = [wrapper[0], str(script)] if wrapper else []
            args = [*prefix, sys.executable, "-I", "-c", value["launch_program"],
                    json.dumps(probe_value), *args]
        try:
            with tempfile.TemporaryFile() as output:
                try:
                    child = subprocess.Popen(args, cwd=allowed, env=env, stdout=output, stderr=output,
                                             pass_fds=(write_fd,) if write_fd is not None else (), start_new_session=True)
                except OSError:
                    raise Refusal("sandbox_wrapper_failed" if wrapper else "sandbox_launch_failed")
                if write_fd is not None:
                    os.close(write_fd)
                    write_fd = None
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
            if read_fd is not None:
                try:
                    chain_data = os.read(read_fd, 16385)
                    launch_chain = launch_receipt(chain_data, value["expected_image_chain"], bool(wrapper))
                except OSError:
                    raise Refusal("sandbox_launch_receipt_invalid")
                finally:
                    os.close(read_fd)
                    read_fd = None
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
        if wrapper and value.get("snapshot_profile") not in ("trust-ro-v1", "trust-ro-v2") and not reached.exists():
            raise Refusal("sandbox_unqualified_config" if status == 78 else
                          "sandbox_helper_missing" if status == 127 else "sandbox_launch_failed")
        if status != 0:
            raise Refusal("sandbox_git_unavailable" if status == 72 else
                          diagnostic(data) if status > 0 else "sandbox_helper_terminated")
        if any(not (allowed / p).is_file() or (allowed / p).read_text() != "sandbox-ready"
               for p in (("allowed", "git-complete", "complete") if git_write else ("allowed", "complete"))):
            raise Refusal("sandbox_required_effect_missing")
        if value.get("snapshot_profile") in ("trust-ro-v1", "trust-ro-v2"):
            launch_chain = rebase_project_node(launch_chain, allowed, workspace)
    result = {"ready": True, "code": "sandbox_ready", "protocol": 1}
    if value.get("snapshot_profile") in ("trust-ro-v1", "trust-ro-v2"):
        result["launch_chain"] = launch_chain
    return result


def rebase_project_node(chain, copied_workspace, actual_workspace):
    """Relocate only a successful probe's project-local Node byte identity.

    This is not proof the task path itself ran in the probe. The actual guard
    independently resolves and hashes that path after the real wrapper setup.
    Image frontend/native/package and global Node paths are never relocated.
    """
    if not isinstance(chain, dict) or not isinstance(chain.get("node"), dict):
        raise Refusal("sandbox_unqualified_config")
    node = chain["node"]
    if set(node) != {"path", "sha256"} or not isinstance(node["path"], str):
        raise Refusal("sandbox_unqualified_config")
    source = Path(node["path"])
    copied = Path(copied_workspace)
    actual = Path(actual_workspace)
    if (not source.is_absolute() or source.resolve(strict=True) != source or
            copied.resolve(strict=True) != copied or actual.resolve(strict=True) != actual):
        raise Refusal("sandbox_unqualified_config")
    if copied not in source.parents:
        return chain
    destination = actual / source.relative_to(copied)
    if destination.resolve() != destination or copied == actual or copied in actual.parents or destination == copied or copied in destination.parents:
        raise Refusal("sandbox_unqualified_config")
    return {**chain, "node": {**node, "path": str(destination)}}


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
