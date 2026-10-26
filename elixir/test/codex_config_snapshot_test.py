"""Credential-free local source exporter fixtures, never native auth access."""
import base64
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

PRIV = Path(__file__).resolve().parents[1] / "priv"
CONTEXT = {"__name__": "snapshot_fixture"}
exec(compile((PRIV / "codex_config_boundary.py").read_text(), str(PRIV / "codex_config_boundary.py"), "exec"), CONTEXT)
exec(compile((PRIV / "codex_config_snapshot.py").read_text(), str(PRIV / "codex_config_snapshot.py"), "exec"), CONTEXT)


class ConfigSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-config-export-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.native = self.home / ".codex"
        self.managed = self.root / "workspaces"
        for directory in (self.home, self.native, self.managed):
            directory.mkdir(mode=0o700)
        self.source = self.native / "config.toml"
        self.auth = self.native / "auth.json"
        self.auth.write_bytes(b"synthetic-private-canary-do-not-read")
        self.auth.chmod(0o600)
        self.auth_before = self.auth.stat()
        self.environment = patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.native)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.system_root = self.root / "etc-codex"
        self.system = patch.dict(CONTEXT, {"SYSTEM_CONFIG_ROOT": self.system_root})
        self.system.start()
        self.addCleanup(self.system.stop)

    def export(self):
        return CONTEXT["export_config_snapshot"](self.home, self.native, self.managed)

    def write(self, data):
        self.source.write_bytes(data)
        self.source.chmod(0o600)

    def test_absent_and_empty_source_emit_exact_empty_snapshot_without_auth_access(self):
        # File access instrumentation records the real bounded reader's opens;
        # listing or touching auth would fail rather than return canned proof.
        original = os.open
        opened = []
        def record(path, *args, **kwargs):
            opened.append(Path(path))
            self.assertEqual(Path(path), self.source)
            return original(path, *args, **kwargs)
        with patch.object(os, "open", side_effect=record), patch.object(os, "listdir", side_effect=AssertionError("no listing")), \
                patch.object(os, "scandir", side_effect=AssertionError("no listing")):
            result = self.export()
            self.assertEqual(result["source"], {"present": False})
            self.write(b"")
            empty = self.export()
        self.assertEqual(opened, [self.source])
        for value in (result, empty):
            self.assertEqual(value["schema"], 1)
            self.assertEqual(value["mode"], "codex-config-snapshot")
            self.assertEqual(value["profile"], "trust-ro-v2")
            self.assertEqual(value["config_base64"], "")
            self.assertEqual(value["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(self.auth.stat().st_mtime_ns, self.auth_before.st_mtime_ns)
        self.assertEqual(self.auth.read_bytes(), b"synthetic-private-canary-do-not-read")

    def test_retained_trust_bytes_source_identity_and_native_file_preservation(self):
        task = self.managed / "task"
        task.mkdir(mode=0o700)
        data = ('[projects."' + str(task) + '"]\ntrust_level="trusted"\n').encode()
        self.write(data)
        before = self.source.stat()
        result = self.export()
        self.assertEqual(base64.b64decode(result["config_base64"], validate=True), data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["source"], {"present": True, "uid": before.st_uid, "gid": before.st_gid,
                         "mode": 0o600, "device": before.st_dev, "inode": before.st_ino,
                         "size": len(data), "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns})
        self.assertEqual(self.source.stat().st_ino, before.st_ino)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.source.read_bytes(), data)

    def test_nontrust_config_foreign_project_and_oversize_source_refuse(self):
        cases = (b'model="not-a-trust-profile"\n',
                 ('[projects."' + str(self.root) + '"]\ntrust_level="trusted"\n').encode(),
                 b"x" * (65536 + 1))
        for data in cases:
            self.write(data)
            with self.subTest(size=len(data)), self.assertRaises(CONTEXT["Refusal"]):
                self.export()

    def test_links_fifo_directory_and_writable_source_refuse(self):
        self.source.symlink_to(self.auth)
        with self.assertRaises(CONTEXT["Refusal"]): self.export()
        self.source.unlink()
        os.mkfifo(self.source, 0o600)
        with self.assertRaises(CONTEXT["Refusal"]): self.export()
        self.source.unlink()
        self.source.mkdir(mode=0o700)
        with self.assertRaises(CONTEXT["Refusal"]): self.export()
        self.source.rmdir()
        self.write(b"")
        for mode in (0o620, 0o602):
            self.source.chmod(mode)
            with self.assertRaises(CONTEXT["Refusal"]): self.export()

    def test_native_and_system_policy_layers_refuse_before_export_without_auth_reads(self):
        self.write(b"")
        paths = [self.native / name for name in ("hooks.json", "rules")]
        self.system_root.mkdir(mode=0o700)
        paths.extend(self.system_root / name for name in
                     ("config.toml", "requirements.toml", "managed_config.toml", "hooks.json", "rules"))
        for path in paths:
            path.symlink_to(self.root / "missing-policy")
            with self.subTest(path=path), self.assertRaises(CONTEXT["Refusal"]): self.export()
            path.unlink()

    def test_native_source_hardlink_refuses_without_modifying_either_name(self):
        self.write(b"")
        alias = self.native / "other-config-name"
        os.link(self.source, alias)
        with self.assertRaises(CONTEXT["Refusal"]): self.export()
        self.assertEqual(self.source.stat().st_nlink, 2)
        self.assertEqual(alias.stat().st_ino, self.source.stat().st_ino)

    def test_strict_native_owner_and_canonical_home_environment(self):
        self.write(b"")
        with patch.object(os, "getuid", return_value=os.getuid() + 1), self.assertRaises(CONTEXT["Refusal"]):
            self.export()
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}), self.assertRaises(CONTEXT["Refusal"]):
            self.export()

    def test_source_owner_must_match_native_uid_even_when_parent_dirs_are_owned(self):
        self.write(b"")
        current_uid = os.getuid()
        # Real parent directory checks pass; only the source read's owner differs.
        with patch.object(os, "getuid", side_effect=[current_uid, current_uid, current_uid, current_uid + 1]), \
                self.assertRaises(CONTEXT["Refusal"]):
            self.export()

    def test_source_replacement_between_initial_identity_and_reader_refuses(self):
        self.write(b"")
        original = CONTEXT["read_regular"]
        def replace(path, maximum, owner=None):
            swap = self.native / "replacement"
            swap.write_bytes(b"")
            swap.chmod(0o600)
            swap.replace(path)
            return original(path, maximum, owner)
        with patch.dict(CONTEXT, {"read_regular": replace}), self.assertRaises(CONTEXT["Refusal"]):
            self.export()

    def test_standalone_entrypoint_refuses_arbitrary_path_arguments_without_export(self):
        with patch.object(CONTEXT["sys"], "argv", ["snapshot", "/arbitrary"]), \
                patch.dict(CONTEXT, {"export_config_snapshot": lambda: self.fail("must not export")}), \
                patch("builtins.print") as output:
            self.assertEqual(CONTEXT["snapshot_main"](), 1)
        self.assertEqual(output.call_args.args[0], '{"schema":1,"mode":"codex-config-snapshot","error":"sandbox_unqualified_config"}')


if __name__ == "__main__":
    unittest.main()
