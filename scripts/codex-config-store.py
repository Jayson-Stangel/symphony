#!/usr/bin/env python3
"""Development fallback for the packaged native codex-config-store operation.

Caller owns the project lock. No concurrent privileged writer is defended here.
This is not a packaged runtime dependency and never inspects native auth storage.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

MAX_ARTIFACTS = 64


def refuse():
    raise ValueError("unsafe Codex config snapshot store")


def digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        refuse()
    return value


def identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def checked(path, directory=False, private=True):
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (info.st_uid != os.geteuid() or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            (mode != (0o700 if directory else 0o600) if private else bool(mode & 0o022)) or
            (not directory and info.st_nlink != 1) or path.resolve(strict=True) != path):
        refuse()
    return info


def read_file(path, maximum):
    before = checked(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if identity(os.fstat(fd)) != identity(before) or before.st_size > maximum:
            refuse()
        data = bytearray()
        while len(data) <= maximum:
            piece = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not piece:
                break
            data.extend(piece)
        if (len(data) != before.st_size or len(data) > maximum or
                identity(os.fstat(fd)) != identity(before) or identity(path.lstat()) != identity(before)):
            refuse()
        return bytes(data)
    finally:
        os.close(fd)


def validate_receipt(value, full):
    keys = {"schema", "mode", "profile", "sha256", "source"}
    if full:
        keys.add("config_base64")
    if (not isinstance(value, dict) or set(value) != keys or type(value["schema"]) is not int or
            value["schema"] != 1 or value["mode"] != "codex-config-snapshot" or value["profile"] != "trust-ro-v2"):
        refuse()
    digest(value["sha256"])
    source = value["source"]
    if not isinstance(source, dict) or type(source.get("present")) is not bool:
        refuse()
    if source["present"]:
        fields = {"uid", "gid", "mode", "device", "inode", "size", "mtime_ns", "ctime_ns"}
        if set(source) != fields | {"present"} or any(type(source[name]) is not int or not 0 <= source[name] <= 2**63 - 1 for name in fields):
            refuse()
        if source["mode"] > 0o7777 or source["mode"] & 0o022 or source["size"] > 65536:
            refuse()
    elif source != {"present": False}:
        refuse()
    return value


def lexical_path(value):
    return (value == "/" or isinstance(value, str) and value.startswith("/") and "\0" not in value and
            all(part not in ("", ".", "..") for part in value[1:].split("/")))


def root_path(value, create):
    root = Path(value)
    if not lexical_path(value) or str(root) != value:
        refuse()
    checked(root.parent, directory=True, private=False)
    if create and not os.path.lexists(root):
        root.mkdir(mode=0o700)
        sync_directory(root.parent, private=False)
    checked(root, directory=True)
    return root


def verify_artifact(root, sha):
    digest(sha)
    artifact = root / sha
    checked(artifact, directory=True)
    if set(os.listdir(artifact)) != {"config.toml", "receipt.json"}:
        refuse()
    data = read_file(artifact / "config.toml", 65536)
    receipt = validate_receipt(json.loads(read_file(artifact / "receipt.json", 4096)), False)
    if receipt["sha256"] != sha or hashlib.sha256(data).hexdigest() != sha:
        refuse()
    if (receipt["source"]["present"] and receipt["source"]["size"] != len(data)) or (not receipt["source"]["present"] and data):
        refuse()
    return receipt


def sync_directory(path, private=True):
    checked(path, directory=True, private=private)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def transient(root, name):
    stage = re.fullmatch(r"\.stage-[0-9a-f]{32}", name)
    retire = re.fullmatch(r"\.retire-([0-9a-f]{64})-[0-9a-f]{32}", name)
    if not stage and not retire:
        refuse()
    directory = root / name
    checked(directory, directory=True)
    names = set(os.listdir(directory))
    if not names.issubset({"config.toml", "receipt.json"}):
        refuse()
    for child in names:
        data = read_file(directory / child, 65536 if child == "config.toml" else 4096)
        if retire:
            if child == "config.toml" and hashlib.sha256(data).hexdigest() != retire.group(1):
                refuse()
            if child == "receipt.json" and validate_receipt(json.loads(data), False)["sha256"] != retire.group(1):
                refuse()
    return names


def remove_transient(root, name):
    directory = root / name
    for child in transient(root, name):
        (directory / child).unlink()
    sync_directory(directory)
    directory.rmdir()
    sync_directory(root)


def inventory(root):
    names = os.listdir(root)
    if len(names) > MAX_ARTIFACTS * 2:
        refuse()
    artifacts, leftovers = [], []
    # Validate the entire namespace before recovering any recognized leftovers.
    for name in names:
        if re.fullmatch(r"[0-9a-f]{64}", name):
            verify_artifact(root, name)
            artifacts.append(name)
        else:
            transient(root, name)
            leftovers.append(name)
    if len(artifacts) > MAX_ARTIFACTS or len(leftovers) > MAX_ARTIFACTS:
        refuse()
    for name in leftovers:
        remove_transient(root, name)
    return artifacts


def write_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def inspect_container(root, sha, uid, auth_volume, host_os, raw):
    if (re.fullmatch(r"0|[1-9][0-9]{0,9}", uid) is None or int(uid) > 4294967294 or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", auth_volume) is None or
            host_os not in ("Darwin", "Linux") or len(raw) > 1048576):
        refuse()
    state = json.loads(raw)
    if not isinstance(state, list) or len(state) != 1 or not isinstance(state[0], dict):
        refuse()
    state = state[0]
    config = state.get("Config")
    mounts = state.get("Mounts")
    if not isinstance(config, dict) or not isinstance(mounts, list):
        refuse()
    expected = {"HOME": "/root", "CODEX_HOME": "/root/.codex", "SYMPHONY_CODEX_CONFIG_PROFILE": "trust-ro-v2",
                "SYMPHONY_CODEX_CONFIG_SHA256": sha, "SYMPHONY_CODEX_CONFIG_OWNER": uid,
                "SYMPHONY_CONTAINERIZED": "1"}
    env = config.get("Env")
    if not isinstance(env, list) or any(not isinstance(item, str) for item in env):
        refuse()
    for name, value in expected.items():
        if [item for item in env if item.partition("=")[0] == name] != [name + "=" + value]:
            refuse()
    labels = config.get("Labels")
    if not isinstance(labels, dict) or any(labels.get("com.symphony.codex.config." + key) != value for key, value in
            (("profile", "trust-ro-v2"), ("sha256", sha), ("owner", uid))):
        refuse()
    source = str(root / sha / "config.toml")
    sources = {source} | ({"/host_mnt" + source} if host_os == "Darwin" else set())
    auth_count = snapshot_count = 0
    for mount in mounts:
        if not isinstance(mount, dict) or not isinstance(mount.get("Destination"), str):
            refuse()
        destination = mount["Destination"]
        if not lexical_path(destination):
            refuse()
        if destination == "/root/.codex":
            auth_count += 1
            if mount.get("Type") != "volume" or mount.get("Name") != auth_volume or mount.get("RW") is not True:
                refuse()
        elif destination == "/root/.codex/config.toml":
            snapshot_count += 1
            if mount.get("Type") != "bind" or mount.get("Source") not in sources or mount.get("RW") is not False:
                refuse()
        elif destination in ("/", "/root") or destination.startswith("/root/.codex/"):
            refuse()
    if auth_count != 1 or snapshot_count != 1:
        refuse()


def selection_record(image, sha, owner):
    if (not isinstance(image, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None or
            re.fullmatch(r"0|[1-9][0-9]{0,9}", owner) is None or int(owner) > 4294967294):
        refuse()
    return ("codex-config-v2\t" + image + "\t" + digest(sha) + "\t" + owner + "\n").encode("ascii")


def selection_read(path):
    data = read_file(path, 256)
    fields = data.decode("ascii").split("\t")
    if len(fields) != 4 or fields[0] != "codex-config-v2" or not fields[3].endswith("\n"):
        refuse()
    if selection_record(fields[1], fields[2], fields[3][:-1]) != data:
        refuse()
    return data


def selection(args):
    operation = args[0]
    if operation not in ("selection-stage", "selection-read", "selection-publish", "selection-abort"):
        refuse()
    if len(args) != (2 if operation == "selection-read" else 5):
        refuse()
    path = Path(args[1])
    if (not lexical_path(args[1]) or str(path) != args[1] or
            path.name not in (("image", "image.next") if operation == "selection-read" else ("image",))):
        refuse()
    checked(path.parent, directory=True)
    if operation == "selection-read":
        sys.stdout.buffer.write(selection_read(path))
        return
    expected = selection_record(*args[2:])
    next_path = path.parent / "image.next"
    current = selection_read(path) if os.path.lexists(path) else None
    proposed = selection_read(next_path) if os.path.lexists(next_path) else None
    leftovers = []
    for name in os.listdir(path.parent):
        if name.startswith(".image.stage-"):
            if re.fullmatch(r"\.image\.stage-[0-9a-f]{32}", name) is None:
                refuse()
            read_file(path.parent / name, 256)
            leftovers.append(path.parent / name)
    if len(leftovers) > 64 or proposed is not None and proposed != expected:
        refuse()
    if operation == "selection-publish" and proposed is None and current != expected:
        refuse()
    for leftover in leftovers:
        leftover.unlink()
    if leftovers:
        sync_directory(path.parent)
    if operation == "selection-stage" and proposed is None:
        stage = path.parent / (".image.stage-" + uuid.uuid4().hex)
        try:
            write_file(stage, expected)
            if os.path.lexists(next_path):
                refuse()
            stage.rename(next_path)
            sync_directory(path.parent)
        finally:
            if os.path.lexists(stage):
                read_file(stage, 256)
                stage.unlink()
                sync_directory(path.parent)
    elif operation == "selection-publish" and proposed is not None:
        # Both source and replacement were validated under the manager's lock.
        os.replace(next_path, path)
        sync_directory(path.parent)
    elif operation == "selection-abort" and proposed is not None:
        next_path.unlink()
        sync_directory(path.parent)
    # Retry may observe a completed rename/unlink whose prior parent fsync failed.
    sync_directory(path.parent)
    sys.stdout.buffer.write(expected)


def run(args):
    if args and args[0].startswith("selection-"):
        selection(args)
        return
    if len(args) < 2:
        refuse()
    operation, value = args[:2]
    if operation not in ("write", "verify", "cleanup", "container"):
        refuse()
    if operation == "write":
        if len(args) != 2:
            refuse()
        raw = sys.stdin.buffer.read(98305)
        if len(raw) > 98304:
            refuse()
        exported = validate_receipt(json.loads(raw), True)
        encoded = exported["config_base64"]
        if not isinstance(encoded, str) or len(encoded) > 87384:
            refuse()
        data = base64.b64decode(encoded, validate=True)
        if (len(data) > 65536 or base64.b64encode(data).decode() != encoded or
                hashlib.sha256(data).hexdigest() != exported["sha256"] or
                (exported["source"]["present"] and exported["source"]["size"] != len(data)) or
                (not exported["source"]["present"] and data)):
            refuse()
        root = root_path(value, True)
        names = inventory(root)
        sha = exported["sha256"]
        if sha in names:
            print(sha)
            return
        if len(names) >= MAX_ARTIFACTS:
            refuse()
        stage = root / (".stage-" + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        try:
            for name, content in (("config.toml", data), ("receipt.json", json.dumps({k: v for k, v in exported.items() if k != "config_base64"}, sort_keys=True, separators=(",", ":")).encode())):
                write_file(stage / name, content)
            if os.path.lexists(root / sha):
                refuse()
            sync_directory(stage)
            stage.rename(root / sha)
            sync_directory(root)
        finally:
            # Only the exact files created by this invocation may be removed.
            if os.path.lexists(stage):
                remove_transient(root, stage.name)
        print(sha)
    elif operation == "container":
        if len(args) != 6:
            refuse()
        root = root_path(value, False)
        inventory(root)
        sha = digest(args[2])
        verify_artifact(root, sha)
        inspect_container(root, sha, args[3], args[4], args[5], sys.stdin.buffer.read(1048577))
        print(sha)
    elif operation == "verify":
        if len(args) != 3:
            refuse()
        root = root_path(value, False)
        inventory(root)
        sha = digest(args[2])
        verify_artifact(root, sha)
        print(sha)
    else:
        if len(args) > MAX_ARTIFACTS + 2:
            refuse()
        keep = set(digest(name) for name in args[2:])
        root = root_path(value, False)
        names = inventory(root)
        if not keep.issubset(names):
            refuse()
        for name in names:
            if name not in keep:
                retired = ".retire-" + name + "-" + uuid.uuid4().hex
                if os.path.lexists(root / retired):
                    refuse()
                (root / name).rename(root / retired)
                sync_directory(root)
                remove_transient(root, retired)


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError):
        print("symphony: unsafe Codex config snapshot store", file=sys.stderr)
        sys.exit(1)
