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
    try:
        if any(os.environ.get(name) for name in INJECTION_VARIABLES):
            refuse()
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
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        refuse()


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
        selected = shutil.which("codex")
        if selected is None or Path(selected).resolve(strict=True) != executable:
            refuse()
        if executable.resolve(strict=True) != executable or not os.access(executable, os.X_OK):
            refuse()
        binary = read_regular(executable, MAX_EXECUTABLE_BYTES)
        if executable.stat().st_uid not in (0, os.getuid()):
            refuse()
        if hashlib.sha256(binary).hexdigest() != _digest(value["expected_executable_sha256"]):
            refuse()
        return {"config_bytes": config, "executable": str(executable)}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        refuse()
