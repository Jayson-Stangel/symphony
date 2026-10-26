"""Native Swift / development Python parity with owned synthetic store data only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def exporter(data=b""):
    import base64
    source = {"present": False} if not data else {"present": True, "uid": 0, "gid": 0, "mode": 0o600,
              "device": 1, "inode": 2, "size": len(data), "mtime_ns": 3, "ctime_ns": 4}
    return {"schema": 1, "mode": "codex-config-snapshot", "profile": "trust-ro-v2",
            "config_base64": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(), "source": source}


class ConfigStoreParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="codex-store-native-build-")
        cls.addClassCleanup(cls.build.cleanup)
        # CI already built and source-qualified this exact native helper.
        # Preserve the former standalone override only as a local compatibility alias.
        helper = os.environ.get("SYMPHONY_TEST_HOST_HELPER") or os.environ.get("SYMPHONY_TEST_CODEX_STORE_HOST")
        if not helper:
            helper = str(Path(cls.build.name).resolve() / "symphony-host")
            subprocess.run(["bash", str(ROOT / "scripts/release/build-host-helper.sh"), helper], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        cls.backends = (("native", [helper, "codex-config-store"]),
                        ("development", [sys.executable, "-B", str(ROOT / "scripts/codex-config-store.py")]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-store-parity-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()

    def store(self, backend):
        parent = self.home / backend
        parent.mkdir(mode=0o755, exist_ok=True)
        return parent / "codex-config"

    def call(self, command, args, value=None):
        raw = value if isinstance(value, bytes) else (json.dumps(value).encode() if value is not None else b"")
        return subprocess.run(command + list(map(str, args)), input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def ok(self, result, output=None):
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        if output is not None: self.assertEqual(result.stdout, output)

    def refused(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"unsafe Codex config snapshot store", result.stderr)

    def write(self, command, root, value):
        self.ok(self.call(command, ["write", root], value), (value["sha256"] + "\n").encode())
        return root / value["sha256"]

    def test_write_verify_reuse_preserves_inode_receipt_and_exact_private_modes(self):
        for backend, command in self.backends:
            with self.subTest(backend=backend):
                root, value = self.store(backend), exporter(b"ordinary typed bytes; host does not parse trust policy")
                artifact = self.write(command, root, value)
                before = {p.name: p.stat().st_ino for p in artifact.iterdir()}
                receipt = (artifact / "receipt.json").read_bytes()
                changed_identity = copy.deepcopy(value)
                changed_identity["source"]["inode"] += 1
                self.write(command, root, changed_identity)
                self.assertEqual(before, {p.name: p.stat().st_ino for p in artifact.iterdir()})
                self.assertEqual(receipt, (artifact / "receipt.json").read_bytes())
                self.ok(self.call(command, ["verify", root, value["sha256"]]), (value["sha256"] + "\n").encode())
                self.assertEqual(root.stat().st_mode & 0o7777, 0o700)
                self.assertEqual(artifact.stat().st_mode & 0o7777, 0o700)
                for path in artifact.iterdir(): self.assertEqual(path.stat().st_mode & 0o7777, 0o600)
                self.assertNotIn("config_base64", json.loads(receipt))

    def test_invalid_export_schema_base64_digest_source_and_oversize_refuse(self):
        cases = []
        for key, replacement in (("schema", True), ("schema", 2), ("profile", "trust-ro-v1"),
                                 ("config_base64", "%%%"), ("sha256", "0" * 64), ("source", {"present": 0})):
            value = exporter(); value[key] = replacement; cases.append(value)
        value = exporter(); value["extra"] = "unknown"; cases.append(value)
        value = exporter(b"x"); value["source"]["size"] = 2; cases.append(value)
        value = exporter(b"x"); value["source"]["mode"] = 0o620; cases.append(value)
        cases.extend((b"x" * 98305, exporter(b"x" * 65537)))
        for backend, command in self.backends:
            root = self.store(backend)
            for index, value in enumerate(cases):
                with self.subTest(backend=backend, case=index): self.refused(self.call(command, ["write", root], value))
            self.assertFalse(root.exists())

    def test_unknown_entry_preserves_verified_artifact_and_canary_before_cleanup(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter(); artifact = self.write(command, root, value)
            canary = root / "not-owned-by-store"; canary.write_text("preserve")
            self.refused(self.call(command, ["cleanup", root]))
            self.assertEqual(canary.read_text(), "preserve")
            self.assertTrue(artifact.is_dir())

    def test_corrupt_bytes_receipt_modes_extra_files_links_and_hardlinks_refuse(self):
        for backend, command in self.backends:
            for variant in ("bytes", "receipt", "mode", "extra", "symlink", "hardlink"):
                with self.subTest(backend=backend, variant=variant):
                    parent = self.home / (backend + variant); parent.mkdir(mode=0o700)
                    root = parent / "store"; value = exporter(); artifact = self.write(command, root, value)
                    config = artifact / "config.toml"
                    if variant == "bytes": config.write_bytes(b"changed")
                    elif variant == "receipt": (artifact / "receipt.json").write_text("{}")
                    elif variant == "mode": config.chmod(0o640)
                    elif variant == "extra": (artifact / "unknown").write_text("preserve")
                    elif variant == "symlink": config.unlink(); config.symlink_to(parent / "missing")
                    else: os.link(config, parent / "other-config")
                    self.refused(self.call(command, ["verify", root, value["sha256"]]))
                    self.refused(self.call(command, ["cleanup", root]))
                    self.assertTrue(artifact.exists())

    def test_unsafe_root_parent_symlink_and_root_modes_refuse(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter()
            root.parent.chmod(0o777)
            self.refused(self.call(command, ["write", root], value)); root.parent.chmod(0o755)
            actual = root.parent / "actual"; actual.mkdir(mode=0o700); root.symlink_to(actual)
            self.refused(self.call(command, ["write", root], value)); root.unlink()
            root.mkdir(mode=0o755)
            self.refused(self.call(command, ["write", root], value))

    def test_cleanup_keeps_active_and_rollback_refs_and_refuses_unknown_keep(self):
        for backend, command in self.backends:
            root = self.store(backend); values = [exporter(bytes([x])) for x in range(3)]
            for value in values: self.write(command, root, value)
            self.refused(self.call(command, ["cleanup", root, "0" * 64]))
            self.assertEqual(len(list(root.iterdir())), 3)
            self.ok(self.call(command, ["cleanup", root, values[0]["sha256"], values[1]["sha256"]]), b"")
            self.assertEqual(set(p.name for p in root.iterdir()), {v["sha256"] for v in values[:2]})

    def test_interrupted_stage_and_retire_are_safely_recovered_before_retry(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter(); self.write(command, root, value)
            for files in ({}, {"config.toml": b"partial"}, {"config.toml": b"partial", "receipt.json": b'{"par'}):
                stage = root / (".stage-" + "a" * 32); stage.mkdir(mode=0o700)
                for name, data in files.items(): (stage / name).write_bytes(data); (stage / name).chmod(0o600)
                self.write(command, root, value); self.assertFalse(stage.exists())
            other = exporter(b"retire")
            artifact = self.write(command, root, other)
            retired = root / (".retire-" + other["sha256"] + "-" + "b" * 32)
            artifact.rename(retired); (retired / "config.toml").unlink()
            self.write(command, root, value); self.assertFalse(retired.exists())

    def test_retired_partial_states_recover_and_wrong_sha_canary_refuses(self):
        for backend, command in self.backends:
            root = self.store(backend); active = exporter(); self.write(command, root, active)
            retired_value = exporter(b"retired bytes")
            for removed in (("config.toml",), ("receipt.json",), ("config.toml", "receipt.json")):
                artifact = self.write(command, root, retired_value)
                retired = root / (".retire-" + retired_value["sha256"] + "-" + "c" * 32)
                artifact.rename(retired)
                for name in removed: (retired / name).unlink()
                self.write(command, root, active); self.assertFalse(retired.exists())
            wrong = root / (".retire-" + "0" * 64 + "-" + "d" * 32)
            wrong.mkdir(mode=0o700)
            (wrong / "config.toml").write_bytes(b"preserve wrong identity")
            (wrong / "config.toml").chmod(0o600)
            self.refused(self.call(command, ["cleanup", root]))
            self.assertEqual((wrong / "config.toml").read_bytes(), b"preserve wrong identity")
            self.assertTrue((root / active["sha256"]).exists())

    def test_recognized_stage_links_and_writable_files_are_preserved_on_refusal(self):
        for backend, command in self.backends:
            for variant in ("symlink", "hardlink", "mode"):
                parent = self.home / (backend + "stage" + variant); parent.mkdir(mode=0o700)
                root = parent / "store"; value = exporter(); self.write(command, root, value)
                stage = root / (".stage-" + "e" * 32); stage.mkdir(mode=0o700)
                canary = parent / "canary"; canary.write_bytes(b"preserve canary"); canary.chmod(0o600)
                target = stage / "config.toml"
                if variant == "symlink": target.symlink_to(canary)
                elif variant == "hardlink": os.link(canary, target)
                else: target.write_bytes(b"preserve"); target.chmod(0o620)
                self.refused(self.call(command, ["cleanup", root]))
                self.assertEqual(canary.read_bytes(), b"preserve canary")
                self.assertTrue(os.path.lexists(target))

    def test_unknown_or_foreign_stage_contents_prevent_all_recovery(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter(); artifact = self.write(command, root, value)
            stage = root / (".stage-" + "a" * 32); stage.mkdir(mode=0o700)
            (stage / "receipt.json").write_bytes(b"partial"); (stage / "receipt.json").chmod(0o600)
            foreign = root / (".stage-" + "b" * 32); foreign.mkdir(mode=0o700)
            (foreign / "unknown").write_text("preserve")
            self.refused(self.call(command, ["cleanup", root]))
            self.assertTrue((stage / "receipt.json").exists()); self.assertTrue(artifact.exists())

    def test_store_rejects_more_than_64_verified_artifacts(self):
        for backend, command in self.backends:
            root = self.store(backend)
            root.mkdir(mode=0o700)
            for index in range(65):
                data = str(index).encode(); value = exporter(data)
                artifact = root / value["sha256"]; artifact.mkdir(mode=0o700)
                (artifact / "config.toml").write_bytes(data); (artifact / "config.toml").chmod(0o600)
                del value["config_base64"]
                (artifact / "receipt.json").write_text(json.dumps(value)); (artifact / "receipt.json").chmod(0o600)
            self.refused(self.call(command, ["write", root], exporter()))
            self.assertEqual(len(list(root.iterdir())), 65)

    def container(self, root, value):
        sha = value["sha256"]
        env = {"HOME": "/root", "CODEX_HOME": "/root/.codex", "SYMPHONY_CODEX_CONFIG_PROFILE": "trust-ro-v2",
               "SYMPHONY_CODEX_CONFIG_SHA256": sha, "SYMPHONY_CODEX_CONFIG_OWNER": "0", "SYMPHONY_CONTAINERIZED": "1"}
        return [{"Config": {"Env": [k + "=" + v for k, v in env.items()], "Labels": {
            "com.symphony.codex.config.profile": "trust-ro-v2", "com.symphony.codex.config.sha256": sha,
            "com.symphony.codex.config.owner": "0"}}, "Mounts": [
                {"Destination": "/root/.codex", "Type": "volume", "Name": "synthetic-native-auth", "RW": True},
                {"Destination": "/root/.codex/config.toml", "Type": "bind", "Source": str(root / sha / "config.toml"), "RW": False}]}]

    def test_container_live_shape_linux_and_darwin_projection_are_qualified(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter(); self.write(command, root, value)
            state = self.container(root, value)
            args = ["container", root, value["sha256"], "0", "synthetic-native-auth", "Linux"]
            self.ok(self.call(command, args, state), (value["sha256"] + "\n").encode())
            state[0]["Mounts"][1]["Source"] = "/host_mnt" + state[0]["Mounts"][1]["Source"]
            self.refused(self.call(command, args, state))
            args[-1] = "Darwin"; self.ok(self.call(command, args, state), (value["sha256"] + "\n").encode())

    def test_container_duplicate_env_wrong_labels_mounts_and_unknown_native_layers_refuse(self):
        for backend, command in self.backends:
            root = self.store(backend); value = exporter(); self.write(command, root, value)
            baseline = self.container(root, value)
            cases = []
            state = copy.deepcopy(baseline); state[0]["Config"]["Env"].append("HOME=/root"); cases.append(state)
            state = copy.deepcopy(baseline); del state[0]["Config"]["Labels"]["com.symphony.codex.config.owner"]; cases.append(state)
            for field, replacement in (("RW", True), ("Type", "volume"), ("Source", "/other/config.toml")):
                state = copy.deepcopy(baseline); state[0]["Mounts"][1][field] = replacement; cases.append(state)
            state = copy.deepcopy(baseline); state[0]["Mounts"][0]["Name"] = "other-auth"; cases.append(state)
            for destination in ("/", "/root", "/root/.codex/hooks.json", "/root/.codex/config.toml"):
                state = copy.deepcopy(baseline); state[0]["Mounts"].append({"Destination": destination, "Type": "bind", "Source": "/other", "RW": False}); cases.append(state)
            cases.extend(([], baseline + baseline, b"x" * 1048577))
            args = ["container", root, value["sha256"], "0", "synthetic-native-auth", "Linux"]
            for index, state in enumerate(cases):
                with self.subTest(backend=backend, index=index): self.refused(self.call(command, args, state))
            for uid in ("-1", "00", "4294967295", "x"):
                args[3] = uid; self.refused(self.call(command, args, baseline))

    def journal(self, backend, suffix=""):
        parent = self.home / (backend + "-journal" + suffix)
        parent.mkdir(mode=0o700)
        return parent / "image"

    def selected(self, index=0):
        return ["sha256:" + hashlib.sha256(("image" + str(index)).encode()).hexdigest(),
                hashlib.sha256(("config" + str(index)).encode()).hexdigest(), "1000"]

    def record(self, index=0):
        return ("codex-config-v2\t" + "\t".join(self.selected(index)) + "\n").encode()

    def test_selection_stage_preserves_current_until_atomic_publish_and_retries(self):
        for backend, command in self.backends:
            current = self.journal(backend)
            args = [current] + self.selected()
            self.ok(self.call(command, ["selection-stage"] + args), self.record())
            self.assertFalse(current.exists())
            proposed = current.parent / "image.next"
            before = proposed.stat().st_ino
            self.ok(self.call(command, ["selection-stage"] + args), self.record())
            self.assertEqual(proposed.stat().st_ino, before)
            self.ok(self.call(command, ["selection-read", proposed]), self.record())
            self.refused(self.call(command, ["selection-read", current]))
            self.ok(self.call(command, ["selection-publish"] + args), self.record())
            self.assertFalse(proposed.exists())
            self.assertEqual(current.read_bytes(), self.record())
            self.assertEqual(current.stat().st_mode & 0o7777, 0o600)
            self.ok(self.call(command, ["selection-publish"] + args), self.record())
            self.ok(self.call(command, ["selection-read", current]), self.record())

    def test_selection_frozen_tuple_mismatch_refuses_and_abort_is_explicit_idempotent(self):
        for backend, command in self.backends:
            current = self.journal(backend)
            for operation in ("selection-stage", "selection-publish"):
                self.ok(self.call(command, [operation, current] + self.selected()), self.record())
            self.ok(self.call(command, ["selection-stage", current] + self.selected(1)), self.record(1))
            proposed = current.parent / "image.next"
            temporary = current.parent / (".image.stage-" + "a" * 32)
            temporary.write_bytes(b"partial"); temporary.chmod(0o600)
            for operation in ("selection-stage", "selection-publish", "selection-abort"):
                self.refused(self.call(command, [operation, current] + self.selected(2)))
                self.assertEqual(current.read_bytes(), self.record())
                self.assertEqual(proposed.read_bytes(), self.record(1))
                self.assertTrue(temporary.exists())
            self.ok(self.call(command, ["selection-abort", current] + self.selected(1)), self.record(1))
            self.assertFalse(proposed.exists()); self.assertFalse(temporary.exists())
            self.assertEqual(current.read_bytes(), self.record())
            self.ok(self.call(command, ["selection-abort", current] + self.selected(1)), self.record(1))
            self.refused(self.call(command, ["selection-publish", current] + self.selected(1)))
            for operation in ("selection-stage", "selection-publish"):
                self.ok(self.call(command, [operation, current] + self.selected(2)), self.record(2))

    def test_selection_read_is_readonly_and_interrupted_owned_stages_recover(self):
        for backend, command in self.backends:
            current = self.journal(backend)
            current.write_bytes(self.record()); current.chmod(0o600)
            unrelated = current.parent / "user-canary"; unrelated.write_bytes(b"preserve unrelated")
            for partial in (b"", b"codex-config-v2\tsha", self.record(1)):
                temporary = current.parent / (".image.stage-" + "b" * 32)
                temporary.write_bytes(partial); temporary.chmod(0o600)
                self.ok(self.call(command, ["selection-read", current]), self.record())
                self.assertEqual(temporary.read_bytes(), partial)
                self.ok(self.call(command, ["selection-stage", current] + self.selected(1)), self.record(1))
                self.assertFalse(temporary.exists())
                self.ok(self.call(command, ["selection-abort", current] + self.selected(1)), self.record(1))
            self.assertEqual(unrelated.read_bytes(), b"preserve unrelated")
            self.assertEqual(current.read_bytes(), self.record())

    def test_selection_unknown_reserved_or_linked_stages_preserve_everything(self):
        for backend, command in self.backends:
            for variant in ("unknown", "symlink", "hardlink", "mode", "oversize"):
                current = self.journal(backend, variant)
                current.write_bytes(self.record()); current.chmod(0o600)
                safe = current.parent / (".image.stage-" + "c" * 32)
                safe.write_bytes(b"partial"); safe.chmod(0o600)
                unsafe = current.parent / (".image.stage-" + ("not-owned-uuid" if variant == "unknown" else "d" * 32))
                if variant == "symlink": unsafe.symlink_to(current)
                elif variant == "hardlink": os.link(current, unsafe)
                else: unsafe.write_bytes(b"x" * (257 if variant == "oversize" else 1)); unsafe.chmod(0o620 if variant == "mode" else 0o600)
                self.refused(self.call(command, ["selection-stage", current] + self.selected(1)))
                self.assertTrue(safe.exists()); self.assertTrue(os.path.lexists(unsafe))
                self.assertEqual(current.read_bytes(), self.record())

    def test_selection_plain_legacy_and_noncanonical_records_refuse_unchanged(self):
        cases = [b"sha256:" + b"a" * 64 + b"\n", self.record() + b"\n", self.record()[:-1],
                 self.record().replace(b"1000", b"01000"), self.record().replace(b"1000", b"4294967295"),
                 self.record().replace(b"codex-config-v2", b"codex-config-v1"), b"x" * 257]
        for backend, command in self.backends:
            current = self.journal(backend)
            for data in cases:
                current.write_bytes(data); current.chmod(0o600)
                for operation in ("selection-read", "selection-stage", "selection-publish", "selection-abort"):
                    args = [operation, current] + ([] if operation == "selection-read" else self.selected())
                    self.refused(self.call(command, args))
                    self.assertEqual(current.read_bytes(), data)

    def test_selection_unsafe_parent_current_next_and_path_selectors_refuse(self):
        for backend, command in self.backends:
            current = self.journal(backend)
            current.parent.chmod(0o755)
            self.refused(self.call(command, ["selection-stage", current] + self.selected()))
            current.parent.chmod(0o700)
            current.write_bytes(self.record()); current.chmod(0o644)
            self.refused(self.call(command, ["selection-stage", current] + self.selected()))
            current.chmod(0o600)
            proposed = current.parent / "image.next"
            proposed.symlink_to(current.parent / "missing")
            self.refused(self.call(command, ["selection-stage", current] + self.selected()))
            self.assertTrue(proposed.is_symlink()); proposed.unlink()
            for name in ("auth.json", "config.toml", "image.other"):
                self.refused(self.call(command, ["selection-read", current.parent / name]))
                self.refused(self.call(command, ["selection-stage", current.parent / name] + self.selected()))
            self.refused(self.call(command, ["selection-stage", proposed] + self.selected()))

    def test_selection_invalid_tuple_and_reserved_temp_count_are_bounded(self):
        for backend, command in self.backends:
            current = self.journal(backend)
            for index, bad in ((0, "image-id"), (0, "sha256:" + "A" * 64), (1, "A" * 64),
                               (2, "00"), (2, "4294967295"), (2, "-1")):
                values = self.selected(); values[index] = bad
                self.refused(self.call(command, ["selection-stage", current] + values))
                self.assertFalse(current.exists())
            for index in range(65):
                temporary = current.parent / (".image.stage-" + format(index, "032x"))
                temporary.write_bytes(b""); temporary.chmod(0o600)
            self.refused(self.call(command, ["selection-stage", current] + self.selected()))
            self.assertEqual(len(list(current.parent.iterdir())), 65)


if __name__ == "__main__":
    unittest.main()
