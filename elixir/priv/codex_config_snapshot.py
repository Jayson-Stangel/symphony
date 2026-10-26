# Prefixed with the trusted codex_config_boundary.py source by ConfigSnapshot.
# The CLI supplies no path selectors. Synthetic local fixtures call the function
# with owned paths directly; deployed execution uses only these fixed defaults.
import base64


def export_config_snapshot(home=Path("/root"), codex_home=Path("/root/.codex"),
                           managed_workspace=Path("/workspaces")):
    home, codex_home, managed_workspace = (_absolute(str(path)) for path in
                                           (home, codex_home, managed_workspace))
    if os.environ.get("HOME") != str(home) or os.environ.get("CODEX_HOME") != str(codex_home):
        refuse()
    _directory(home, os.getuid())
    _directory(codex_home, os.getuid())
    _directory(managed_workspace, os.getuid())
    _refuse_policy_inputs([managed_workspace], codex_home)
    source = codex_home / "config.toml"
    if os.path.lexists(source):
        before = source.lstat()
        if before.st_nlink != 1:
            refuse()
        data = read_regular(source, MAX_CONFIG_BYTES, os.getuid())
        after = source.lstat()
        if after.st_nlink != 1 or _identity(before) != _identity(after):
            refuse()
        identity = {"present": True, "uid": before.st_uid, "gid": before.st_gid,
                    "mode": stat.S_IMODE(before.st_mode), "device": before.st_dev,
                    "inode": before.st_ino, "size": before.st_size,
                    "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
    else:
        data = b""
        identity = {"present": False}
    digest = hashlib.sha256(data).hexdigest()
    validate_config_bytes(data, managed_workspace, digest)
    if not identity["present"] and os.path.lexists(source):
        refuse()
    return {"schema": 1, "mode": "codex-config-snapshot", "profile": "trust-ro-v2",
            "config_base64": base64.b64encode(data).decode("ascii"),
            "sha256": digest, "source": identity}


def snapshot_main():
    try:
        if len(sys.argv) != 1:
            refuse()
        result = export_config_snapshot()
        encoded = json.dumps(result, separators=(",", ":"))
        if len(encoded) > 90000:
            refuse()
        print(encoded)
        return 0
    except (Refusal, OSError, RuntimeError, ValueError, TypeError):
        print(json.dumps({"schema": 1, "mode": "codex-config-snapshot",
                          "error": "sandbox_unqualified_config"}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(snapshot_main())
