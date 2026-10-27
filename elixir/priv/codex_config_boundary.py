"""Shared post-wrapper attestation; prefix into a trusted `python3 -I -c` program.

This attests the already-established immutable file mount. It does not establish
a mount, authorize project code, or defend against a privileged external writer.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys


class Refusal(Exception):
    pass


MAX_CONFIG_BYTES = 64 * 1024
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
MAX_CHAIN_BINARY_BYTES = 512 * 1024 * 1024
EMPTY_CONFIG_SHA256 = hashlib.sha256(b"").hexdigest()
SYSTEM_CONFIG_ROOT = Path("/etc/codex")
NATIVE_ENV_PREFIXES = ("CODEX_", "OPENAI_", "AZURE_OPENAI_")
INJECTION_VARIABLES = (
    "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
)
NPM_FRONTEND_SHA256 = "61b0194f3bb6534439c8d26a3ed57d0805f84b884588b761795323eeb92fcf70"
NPM_PACKAGE_SHA256 = "691468e75b276e5a824cbf8ad180f072307dcf01178d403405af6f11d6b5ce14"
NPM_LINUX_TARGETS = {
    "x86_64": ("x64", "x86_64-unknown-linux-musl", 62,
               "79676eba7965ef9b8c4f02bf6c0956e6252c25447bcdd98dedcb1fc2be6e2c7a"),
    "aarch64": ("arm64", "aarch64-unknown-linux-musl", 183,
                "6aa21ae6e4ec81fcf687d635f8a3aa37af8132eadc63f207aca0215c0f593167"),
}


def refuse():
    raise Refusal("sandbox_unqualified_config")



def browser_environment(value, environment, initialize=True):
    """Task browser state; no HOME/XDG default or new sandbox write grant.

    initialize=False validates existing ancestors without creating state. The
    readiness diagnostic uses that mode before rebasing into its own workspace.
    """
    result = dict(environment)
    workspace = Path(value["workspace"])
    name = next((key for key in ("CHROME_CONFIG_HOME", "XDG_CONFIG_HOME") if key in result), None)
    selected = result[name] if name else str(workspace / ".symphony-browser-config")
    path = Path(selected)
    policy = value.get("browser_policy", value.get("policy", {}))
    roots = [workspace, *(Path(root) for root in policy.get("writableRoots", []))]
    if not policy.get("excludeSlashTmp", False):
        roots.append(Path("/tmp").resolve())
    if not policy.get("excludeTmpdirEnvVar", False) and environment.get("TMPDIR"):
        roots.append(Path(environment["TMPDIR"]).resolve())
    if (not selected or not path.is_absolute() or os.path.normpath(selected) != selected or
            any(part in (".codex", ".git") for part in path.parts)):
        raise Refusal("sandbox_browser_config_unsafe")
    for key in ("HOME", "CODEX_HOME"):
        if environment.get(key):
            protected = Path(environment[key])
            if path == protected or path in protected.parents or protected in path.parents:
                raise Refusal("sandbox_browser_config_unsafe")
    matching = [root for root in roots if root.is_absolute() and root in path.parents]
    if not matching:
        raise Refusal("sandbox_browser_config_unsafe")
    root = max(matching, key=lambda candidate: len(candidate.parts))
    descriptor = None
    try:
        if root.resolve(strict=True) != root:
            raise Refusal("sandbox_browser_config_unsafe")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        # /tmp may be a shared authorized boundary; every descendant still must
        # be owned and non-shared. No existing directory is chmod/chown'ed.
        for part in (None, *path.relative_to(root).parts):
            if part is not None:
                if initialize:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=descriptor)
                except FileNotFoundError:
                    if not initialize:
                        break
                    raise
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            shared_boundary = part is None and root == Path("/tmp").resolve()
            if (not stat.S_ISDIR(info.st_mode) or
                    not shared_boundary and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
                raise Refusal("sandbox_browser_config_unsafe")
            if not shared_boundary and info.st_mode & 0o300 != 0o300:
                raise Refusal("sandbox_browser_config_unwritable")
        if not name:
            result["CHROME_CONFIG_HOME"] = str(path)
        return result
    except Refusal:
        raise
    except OSError as error:
        code = "sandbox_browser_config_unwritable" if error.errno in (13, 30) else "sandbox_browser_config_unsafe"
        raise Refusal(code) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def probe_browser_environment(value, environment):
    """Resolve wrapper-final state without writing to the original project.

    Omitted external links are never available to diagnostic setup. Only links
    strictly inside its final browser-state subtree can be left out; all other
    project escapes still refuse before the native process/receipt.
    """
    source = value.get("browser_source")
    if not isinstance(source, dict):
        raise Refusal("sandbox_browser_config_unsafe")
    omitted = value.get("browser_omitted_links", [])
    if (not isinstance(omitted, list) or len(omitted) > 256 or
            any(not isinstance(item, str) for item in omitted) or
            sum(len(item) for item in omitted) > 32768):
        raise Refusal("sandbox_browser_config_unsafe")
    workspace = Path(value["workspace"])
    original_workspace = Path(source["workspace"])
    original_launch_root = Path(source.get("launch_root", source["workspace"]))
    name = next((key for key in ("CHROME_CONFIG_HOME", "XDG_CONFIG_HOME") if key in environment), None)
    selected = Path(environment[name]) if name else workspace / ".symphony-browser-config"
    mappings = value.get("browser_environment_map", [])
    if (not isinstance(mappings, list) or len(mappings) > 2 or
            any(not isinstance(item, dict) or set(item) != {"probe", "source"} or
                not all(isinstance(item[key], str) for key in item) for item in mappings)):
        raise Refusal("sandbox_browser_config_unsafe")
    original_selected = None
    for item in mappings:
        placeholder = Path(item["probe"])
        if placeholder.parent != workspace:
            raise Refusal("sandbox_browser_config_unsafe")
        if selected == placeholder or placeholder in selected.parents:
            original_selected = Path(item["source"]) / selected.relative_to(placeholder)
            break
    if workspace in selected.parents:
        if original_selected is None:
            origin = original_launch_root if name else original_workspace
            original_selected = origin / selected.relative_to(workspace)
        disposable_selected = selected
    else:
        original_selected = selected
        disposable_selected = workspace / ".symphony-probe-browser-config"
    original_environment = dict(environment)
    original_environment.update(HOME=source["home"], CODEX_HOME=source["codex_home"])
    original_environment[name or "CHROME_CONFIG_HOME"] = str(original_selected)
    # This walks real source ancestors only, never creating or chmod'ing them.
    browser_environment(source, original_environment, initialize=False)
    for item in omitted:
        relative = Path(item)
        if (not item or relative.is_absolute() or str(relative) != item or
                any(part in (".", "..", ".codex", ".git") for part in relative.parts) or
                original_selected not in (original_launch_root / relative).parents):
            raise Refusal("sandbox_browser_config_unsafe")
    result = dict(environment)
    result[name or "CHROME_CONFIG_HOME"] = str(disposable_selected)
    return browser_environment(value, result)

def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        refuse()
    return value


def _absolute(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        refuse()
    path = Path(value)
    if not path.is_absolute() or str(path) != value or os.path.normpath(value) != value:
        refuse()
    return path


def _directory(path, owner=None, allow_group_write=False):
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & (0o002 if allow_group_write else 0o022):
            refuse()
        if owner is not None and info.st_uid != owner:
            refuse()
        if path.resolve(strict=True) != path:
            refuse()
        return info
    except (OSError, RuntimeError, ValueError):
        refuse()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_regular(path, maximum, owner=None):
    """Bounded, nonblocking, no-follow read with descriptor/path identity checks."""
    fd = None
    try:
        before_path = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (_identity(before_path) != _identity(before) or
                not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022 or
                before.st_size > maximum or before.st_size < 0 or
                owner is not None and before.st_uid != owner):
            refuse()
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if (len(data) > maximum or len(data) != before.st_size or
                _identity(before) != _identity(os.fstat(fd)) or
                _identity(before) != _identity(path.lstat())):
            refuse()
        return bytes(data)
    except (OSError, RuntimeError, ValueError):
        refuse()
    finally:
        if fd is not None:
            os.close(fd)


def validate_config_bytes(data, managed_workspace, expected_sha256):
    expected_sha256 = _digest(expected_sha256)
    if not isinstance(data, bytes) or len(data) > MAX_CONFIG_BYTES:
        refuse()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        refuse()
    if not data:
        if expected_sha256 != EMPTY_CONFIG_SHA256:
            refuse()
        return
    try:
        import tomllib
    except ImportError:
        refuse()
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        refuse()
    if set(document) != {"projects"} or not isinstance(document["projects"], dict):
        refuse()
    if not document["projects"] or len(document["projects"]) > 512:
        refuse()
    root = _absolute(str(managed_workspace))
    _directory(root, os.getuid())
    for name, metadata in document["projects"].items():
        if not isinstance(metadata, dict) or metadata != {"trust_level": "trusted"}:
            refuse()
        path = _absolute(name)
        if path != root and root not in path.parents:
            refuse()
        # Missing retired workspaces remain valid metadata, but each existing
        # component must still be an owned ordinary directory, never a link/file.
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if os.path.lexists(current):
                _directory(current, os.getuid())


def read_mountinfo():
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            text = stream.read(4 * 1024 * 1024 + 1)
        if len(text) > 4 * 1024 * 1024:
            refuse()
        return text
    except (OSError, UnicodeError):
        refuse()


def _unescape_mount_path(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def validate_mountinfo(text, config_path):
    matches = []
    try:
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 10 or "-" not in fields[6:]:
                refuse()
            if _unescape_mount_path(fields[4]) == str(config_path):
                matches.append(set(fields[5].split(",")))
    except (TypeError, AttributeError):
        refuse()
    if len(matches) != 1 or "ro" not in matches[0] or "rw" in matches[0]:
        refuse()


def validate_readonly_mount_tree(text, root):
    """Require an exact RO root mount and no writable nested mount."""
    validate_mountinfo(text, root)
    for line in text.splitlines():
        fields = line.split()
        mountpoint = _absolute(_unescape_mount_path(fields[4]))
        if root in mountpoint.parents:
            options = set(fields[5].split(","))
            if "ro" not in options or "rw" in options:
                refuse()


def _refuse_policy_inputs(paths, codex_home, readonly_launch_source=None):
    forbidden = [SYSTEM_CONFIG_ROOT / name for name in
                 ("config.toml", "requirements.toml", "managed_config.toml", "hooks.json", "rules")]
    forbidden.extend(codex_home / name for name in ("hooks.json", "rules"))
    seen = set()
    for path in paths:
        for ancestor in (path, *path.parents):
            if ancestor in seen:
                continue
            seen.add(ancestor)
            metadata = ancestor / ".git"
            if os.path.lexists(metadata):
                readonly_source = (readonly_launch_source is not None and
                                   (ancestor == readonly_launch_source["root"] or
                                    readonly_launch_source["root"] in ancestor.parents))
                if readonly_source:
                    _directory(metadata, readonly_launch_source["uid"], allow_group_write=True)
                else:
                    _directory(metadata, os.getuid())
            local = ancestor / ".codex"
            if os.path.lexists(local) and not stat.S_ISDIR(local.lstat().st_mode):
                refuse()
            forbidden.extend(local / name for name in ("config.toml", "hooks.json", "rules"))
    if any(os.path.lexists(path) for path in forbidden):
        refuse()


def _attest_native_environment(expected):
    if not isinstance(expected, dict) or len(expected) > 256:
        refuse()
    actual = {name: hashlib.sha256(value.encode()).hexdigest()
              for name, value in os.environ.items()
              if name != "CODEX_HOME" and name.startswith(NATIVE_ENV_PREFIXES)}
    for name, digest in expected.items():
        if not isinstance(name, str) or name == "CODEX_HOME" or not name.startswith(NATIVE_ENV_PREFIXES):
            refuse()
        _digest(digest)
    if actual != expected:
        refuse()


def _launch_directory(path, excluded_roots=()):
    # A host source bind is trusted only as a complete read-only mount tree.
    # Group-writable source modes are ordinary on shared hosts; world-writable
    # roots remain unsupported even under that explicit deployment contract.
    info = _directory(path, allow_group_write=True)
    mountinfo = read_mountinfo()
    exact_ro = False
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields[6:]:
            refuse()
        if _unescape_mount_path(fields[4]) == str(path) and "ro" in fields[5].split(","):
            exact_ro = True
    if exact_ro:
        validate_readonly_mount_tree(mountinfo, path)
        for excluded in excluded_roots:
            if path == excluded or path in excluded.parents or excluded in path.parents:
                refuse()
        return {"root": path, "uid": info.st_uid}
    _directory(path, os.getuid())
    return None


def _linux_target():
    if sys.platform != "linux" or platform.machine() not in NPM_LINUX_TARGETS:
        refuse()
    return NPM_LINUX_TARGETS[platform.machine()]


def _fingerprint_binary(path):
    """Hash bounded image/toolchain binaries without buffering hundreds of MB."""
    path = _absolute(str(path))
    fd = None
    try:
        if path.resolve(strict=True) != path or not os.access(path, os.X_OK):
            refuse()
        before_path = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (_identity(before_path) != _identity(before) or not stat.S_ISREG(before.st_mode) or
                before.st_mode & 0o022 or before.st_uid not in (0, os.getuid()) or
                not 0 < before.st_size <= MAX_CHAIN_BINARY_BYTES):
            refuse()
        checksum = hashlib.sha256()
        prefix = bytearray()
        count = 0
        while count <= MAX_CHAIN_BINARY_BYTES:
            chunk = os.read(fd, min(65536, MAX_CHAIN_BINARY_BYTES + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            checksum.update(chunk)
            if len(prefix) < 64:
                prefix.extend(chunk[:64 - len(prefix)])
        if (count != before.st_size or count > MAX_CHAIN_BINARY_BYTES or
                _identity(before) != _identity(os.fstat(fd)) or
                _identity(before) != _identity(path.lstat())):
            refuse()
        return {"path": str(path), "sha256": checksum.hexdigest()}, bytes(prefix)
    except (OSError, RuntimeError, ValueError):
        refuse()
    finally:
        if fd is not None:
            os.close(fd)


def _require_elf(prefix, machine):
    if (len(prefix) < 20 or prefix[:6] != b"\x7fELF\x02\x01" or
            int.from_bytes(prefix[18:20], "little") != machine):
        refuse()


def _pinned_package(path, expected_digest):
    if path.resolve(strict=True) != path or path.stat().st_uid not in (0, os.getuid()):
        refuse()
    data = read_regular(path, MAX_CONFIG_BYTES)
    if hashlib.sha256(data).hexdigest() != expected_digest:
        refuse()
    try:
        document = json.loads(data)
    except (UnicodeError, ValueError):
        refuse()
    return {"path": str(path), "sha256": expected_digest}, document


def image_chain(executable):
    """Exact official 0.163 Linux npm closure.

    Only the observed ordinary-file npm platform layout is qualified. Node's
    vendor fallback, linked package layouts, script trampolines and other npm
    versions deliberately refuse rather than broadening module resolution.
    """
    try:
        cpu, target, machine, native_package_digest = _linux_target()
        executable = _absolute(str(executable))
        frontend, _ = _fingerprint_binary(executable)
        if (executable.name != "codex.js" or executable.parent.name != "bin" or
                frontend["sha256"] != NPM_FRONTEND_SHA256):
            refuse()
        package_root = executable.parent.parent
        if (package_root.name != "codex" or package_root.parent.name != "@openai" or
                package_root.parent.parent.name != "node_modules"):
            refuse()
        package, metadata = _pinned_package(package_root / "package.json", NPM_PACKAGE_SHA256)
        alias = "@openai/codex-linux-" + cpu
        alias_version = "npm:@openai/codex@0.163.0-linux-" + cpu
        if (metadata.get("name") != "@openai/codex" or metadata.get("version") != "0.163.0" or
                metadata.get("bin") != {"codex": "bin/codex.js"} or
                metadata.get("optionalDependencies", {}).get(alias) != alias_version):
            refuse()
        native_root = package_root / "node_modules" / "@openai" / ("codex-linux-" + cpu)
        native_package, native_metadata = _pinned_package(native_root / "package.json", native_package_digest)
        if (native_metadata.get("name") != "@openai/codex" or
                native_metadata.get("version") != "0.163.0-linux-" + cpu or
                native_metadata.get("os") != ["linux"] or native_metadata.get("cpu") != [cpu]):
            refuse()
        native_path = native_root / "vendor" / target / "bin" / "codex"
        native, native_prefix = _fingerprint_binary(native_path)
        _require_elf(native_prefix, machine)
        return {"profile": "codex-npm-linux-v1", "target": target,
                "frontend": frontend, "native": native,
                "package": package, "native_package": native_package}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        refuse()


def attest_runtime_chain(value, executable, require_launch_chain=True):
    """Probe may capture Node; actual launch must match the successful probe."""
    if any(os.environ.get(name) for name in INJECTION_VARIABLES):
        refuse()
    try:
        image = image_chain(executable)
        if image != value.get("expected_image_chain"):
            refuse()
        chain = dict(image)
        selected = shutil.which("node")
        if selected is None:
            refuse()
        node_path = Path(selected).resolve(strict=True)
        node, prefix = _fingerprint_binary(node_path)
        _require_elf(prefix, _linux_target()[2])
        chain["node"] = node
        expected = value.get("expected_launch_chain")
        if require_launch_chain or expected is not None:
            if chain != expected:
                refuse()
        return chain
    except (Refusal, OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        # Keep the same closure checks, but identify the post-wrapper toolchain
        # stage rather than blaming retained trust configuration.
        raise Refusal("sandbox_toolchain_unqualified")


def attest_context(value, require_snapshot=True):
    try:
        if not isinstance(value, dict) or value.get("snapshot_profile") not in ("trust-ro-v1", "trust-ro-v2"):
            refuse()
        if any(os.environ.get(name) for name in INJECTION_VARIABLES):
            refuse()
        _attest_native_environment(value.get("expected_native_environment", {}))
        home = _absolute(value["expected_home"])
        codex_home = _absolute(value["expected_codex_home"])
        if os.environ.get("HOME") != str(home) or os.environ.get("CODEX_HOME") != str(codex_home):
            refuse()
        _directory(home, os.getuid())
        _directory(codex_home, os.getuid())
        paths = [_absolute(value[key]) for key in ("workspace", "managed_workspace", "launch_root")]
        for path in paths[:2]:
            _directory(path, os.getuid())
        workspace, managed_workspace, launch_root = paths
        readonly_launch_source = _launch_directory(
            launch_root, (managed_workspace, workspace, home, codex_home))
        if workspace != managed_workspace and managed_workspace not in workspace.parents:
            refuse()
        _refuse_policy_inputs(paths, codex_home, readonly_launch_source)
        config_path = codex_home / "config.toml"
        if require_snapshot:
            validate_mountinfo(read_mountinfo(), config_path)
        snapshot_uid = os.getuid()
        if value["snapshot_profile"] == "trust-ro-v2":
            snapshot_uid = value.get("expected_snapshot_uid")
            if type(snapshot_uid) is not int or not 0 <= snapshot_uid <= 4294967294:
                refuse()
        config = read_regular(config_path, MAX_CONFIG_BYTES, snapshot_uid)
        validate_config_bytes(config, managed_workspace, value["expected_snapshot_sha256"])
        executable = _absolute(value["expected_executable"])
        executable_sha256 = _digest(value["expected_executable_sha256"])
        try:
            selected = shutil.which("codex")
            if selected is None or Path(selected).resolve(strict=True) != executable:
                refuse()
            if executable.resolve(strict=True) != executable or not os.access(executable, os.X_OK):
                refuse()
            binary = read_regular(executable, MAX_EXECUTABLE_BYTES)
            if executable.stat().st_uid not in (0, os.getuid()):
                refuse()
            if hashlib.sha256(binary).hexdigest() != executable_sha256:
                refuse()
        except (Refusal, OSError, RuntimeError, ValueError, KeyError, TypeError):
            raise Refusal("sandbox_toolchain_unqualified")
        return {"config_bytes": config, "executable": str(executable)}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        refuse()
