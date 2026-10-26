"""Synthetic real-file tests; mountinfo reader substitution is not kernel proof."""
import hashlib
import base64
import zlib
import importlib.util
import io
import json
import sys
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "priv" / "codex_config_boundary.py"
SPEC = importlib.util.spec_from_file_location("boundary", SOURCE)
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)


def digest(value):
    return hashlib.sha256(value).hexdigest()

# Exact public @openai/codex 0.163.0 npm fixture bytes, Apache-2.0 OpenAI.
# Provenance: https://registry.npmjs.org/@openai/codex/0.163.0 and the
# corresponding 0.163.0-linux-{x64,arm64} verified SHA512 tarballs.
NPM_FIXTURE_BYTES = {
    'frontend': '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',
    'package': 'eJyNksFu2zAMhu95CiHXNo4sWbISoEOK7lJgwx5g2IGW6USrLRmyvSUo+u6T7RR2iq7NJVDInx9/0nxeELK0UOFyS5Y7V6MFs9Yux+Pytk/9Qd8YZ/ssjWLJIzrGc2y0N3V7zj30FeTh2yMxDQESAMbuCezRtqTwriI/Avn+kbQHaInvbENKp6EsT8RZcnKdDyVV3bXoo5FfGo22GVzd16APuGKvrTPTt3wOz/BntLodoqPv6HezDLmXQdue6oFRubwrcaxHuzcWm4lhQ1kv+nIXy6m0MOUg+jmK3vJ/DSKPtWtM6/xpwr323Jt2aBhCnS/PkZtD29bNdr0O70OXRWHs9Xzr0awqNx71mT0OutKlmRyGvTyFFX8HG359L6ptXe1iGvEkEjfNAUTMIkgQqShYkXIpFBMiyyWojRIbTaWARGimQKQ5YAHItNpwiKVCZFnKw0PzNNvEqDiKFEGmUiSQU15QTnmcJjLhFFWmkSvkkiFNmAzdMgWZVjTesPPS3XAqUH7FMGyOVpv5F7g4vFVpbHdcHWXSjxQm2l6kd+c7nMlu/08BX13HGYXvknLwf4393NBM9xHnCksXyndZIc3Z55Ym2QeUKwzNhf39LV4W/wAJHj4r',
    'x64': 'eJxNkE1uwjAQhfecAmVLcfwTOw5SK3qAnqDqYuJMiNVgW/lBIMTd6xhU2FhP8703z/Z1tV5nDo6Y7dbZ3gd0YHPjGzxnbws64TBa7xZKCVOC0G1v3XzenlVxd/TWoBtT/jOA6XDLCb0jP8bpd1TJFlNZ1D8JmTA/2bLrn7S2x5fcCV3jhyceMPjRTn64RM/17pkuIfUf7JSK42ge+sdk001TGHd5HnU318T4Y/76TvKSauyA5rE7S3RreruU31I5uoN16XaPZhc9i/fjnamnLX7DLxzwC1w8h4UHF457RokoiNyMHUjGCRSIVLa8LYWSmktZNwp0pWVlqJJQSMM1yLIBbAG50ZUApjQir0sRhRFlXTHUAmWJoEolC2ioaKmggpWFKgRFXRsUGoXiSAuuYlutoTaasopjtrqt/gCYiYkm',
    'arm64': 'eJxNkE1ugzAQhfc5RcQ2DfgfE6lVeoCeoOpiMEOwCsbiJ0oU5e41JmqysZ7mezNvxrfNdps46DA5bJNj79GBzUxf4SV5W9AZh9H2bqEkpYqnZN9aN1/2MHRKrJ7WGnRjnPDpwTS4ZylZUT+G6ndQ0Rb6kqB/IjJ+frJ12j+rbYsvnWd0VT888YC+H+3UD9fgua2e6erjBic7xehQmof2Udk10+THQ5YF3cxlavoue701femq7IDmMTuJdG9au4TfYzi6k3Vxu0eyC57F+/FO1dMWPuIXTvgFLrzDwr3z3ZGSlItU7sYGJGUpCEQia1bnXEnNpCwrBbrQsjBESRDSMA0yrwBrQGZ0wYEqjcjKnAdheF4WFDVHmSOoXEkBFeE14YTTXCjBCerSINfIFUMimApppYbSaEILhsnmvvkDi2iKtg==',
}


def npm_fixture(name):
    return zlib.decompress(base64.b64decode(NPM_FIXTURE_BYTES[name]))


class ConfigBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-boundary-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.native = self.home / ".codex"
        self.managed = self.root / "managed"
        self.workspace = self.managed / "task"
        self.launch = self.root / "launch"
        self.bin = self.root / "bin"
        for directory in (self.home, self.native, self.managed, self.workspace, self.launch, self.bin):
            directory.mkdir(mode=0o700)
        self.exe = self.bin / "codex"
        self.exe.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.exe.chmod(0o755)
        self.config = self.native / "config.toml"
        self.config.write_bytes(b"")
        self.config.chmod(0o600)
        self.value = {"workspace": str(self.workspace), "managed_workspace": str(self.managed),
                      "launch_root": str(self.launch), "expected_home": str(self.home),
                      "expected_codex_home": str(self.native), "expected_executable": str(self.exe),
                      "expected_executable_sha256": digest(self.exe.read_bytes()),
                      "expected_snapshot_sha256": digest(b""), "snapshot_profile": "trust-ro-v1",
                      "expected_native_environment": {}}
        self.environment = patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.native),
                                                   "PATH": str(self.bin)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.system = patch.object(boundary, "SYSTEM_CONFIG_ROOT", self.root / "etc-codex")
        self.system.start()
        self.addCleanup(self.system.stop)

    def mountinfo(self, options="ro", target=None):
        target = str(target or self.config).replace("\\", "\\134").replace(" ", "\\040")
        return f"41 30 0:25 /snapshot {target} {options},relatime - tmpfs tmpfs rw\n"

    def attest(self, **kwargs):
        with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo()):
            return boundary.attest_context(self.value, **kwargs)

    def trust(self, paths=None, metadata='trust_level = "trusted"'):
        data = "\n".join(f'[projects."{path}"]\n{metadata}\n' for path in paths or [self.workspace]).encode()
        self.config.write_bytes(data)
        self.value["expected_snapshot_sha256"] = digest(data)
        return data

    def test_empty_exact_snapshot_and_pinned_executable(self):
        self.assertEqual(self.attest(), {"config_bytes": b"", "executable": str(self.exe)})

    def test_startup_workspace_can_be_the_exact_owned_managed_root(self):
        self.value["workspace"] = str(self.managed)
        self.assertEqual(self.attest()["config_bytes"], b"")
        self.value["workspace"] = str(self.root)
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_missing_tomllib_supports_only_the_exact_empty_snapshot(self):
        import builtins
        original_import = builtins.__import__

        def without_tomllib(name, *args, **kwargs):
            if name == "tomllib":
                raise ImportError("synthetic Python 3.9")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_tomllib):
            self.assertEqual(self.attest()["config_bytes"], b"")
            self.trust()
            with self.assertRaises(boundary.Refusal): self.attest()

    def test_foreign_uid_launch_directory_requires_its_own_exact_readonly_mount(self):
        # Substitute caller UID only: directory stat, modes, canonical path and
        # mount parser remain real. Native cross-UID mount proof is separate.
        foreign_caller = self.launch.stat().st_uid + 1
        with patch.object(boundary.os, "getuid", return_value=foreign_caller):
            with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)):
                boundary._launch_directory(self.launch)
            for info in (self.mountinfo(), self.mountinfo("rw", target=self.launch)):
                with self.subTest(info=info), patch.object(boundary, "read_mountinfo", return_value=info), \
                        self.assertRaises(boundary.Refusal):
                    boundary._launch_directory(self.launch)
            self.launch.chmod(0o777)
            with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)), \
                    self.assertRaises(boundary.Refusal):
                boundary._launch_directory(self.launch)
            self.launch.chmod(0o700)

    def test_foreign_readonly_launch_git_exception_is_scoped_and_ordinary_only(self):
        metadata = self.launch / ".git"
        metadata.mkdir(mode=0o700)
        task_metadata = self.workspace / ".git"
        task_metadata.mkdir(mode=0o700)
        foreign_caller = self.launch.stat().st_uid + 1
        with patch.object(boundary.os, "getuid", return_value=foreign_caller), \
                patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)):
            capability = boundary._launch_directory(self.launch)
            self.assertEqual(capability, {"root": self.launch, "uid": self.launch.stat().st_uid})
            boundary._refuse_policy_inputs([self.launch], self.native, capability)
            with self.assertRaises(boundary.Refusal):
                boundary._refuse_policy_inputs([self.launch], self.native)
            with self.assertRaises(boundary.Refusal):
                boundary._refuse_policy_inputs([self.workspace], self.native, capability)
            metadata.chmod(0o777)
            with self.assertRaises(boundary.Refusal):
                boundary._refuse_policy_inputs([self.launch], self.native, capability)
            metadata.chmod(0o700)
            metadata.rmdir()
            metadata.write_text("gitdir: /other/project")
            with self.assertRaises(boundary.Refusal):
                boundary._refuse_policy_inputs([self.launch], self.native, capability)
            metadata.unlink()
            metadata.symlink_to(task_metadata, target_is_directory=True)
            with self.assertRaises(boundary.Refusal):
                boundary._refuse_policy_inputs([self.launch], self.native, capability)
        with patch.object(boundary, "read_mountinfo", return_value=""):
            self.assertIsNone(boundary._launch_directory(self.launch))

    def test_foreign_launch_mount_tree_refuses_nested_writable_mounts(self):
        child = self.launch / "nested"
        foreign_caller = self.launch.stat().st_uid + 1
        root_info = self.mountinfo(target=self.launch)
        with patch.object(boundary.os, "getuid", return_value=foreign_caller):
            for options, accepted in (("rw", False), ("ro", True)):
                info = root_info + self.mountinfo(options, target=child)
                with patch.object(boundary, "read_mountinfo", return_value=info):
                    if accepted:
                        self.assertEqual(boundary._launch_directory(self.launch)["root"], self.launch)
                    else:
                        with self.assertRaises(boundary.Refusal):
                            boundary._launch_directory(self.launch)

    def test_readonly_group_writable_source_supports_same_and_foreign_uid(self):
        metadata = self.launch / ".git"
        metadata.mkdir(mode=0o775)
        metadata.chmod(0o775)
        self.launch.chmod(0o775)
        source_uid = self.launch.stat().st_uid
        for caller in (source_uid, source_uid + 1):
            with patch.object(boundary.os, "getuid", return_value=caller), \
                    patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)):
                capability = boundary._launch_directory(self.launch, (self.managed, self.home))
                boundary._refuse_policy_inputs([self.launch], self.native, capability)
        for mode in (0o777, 0o757):
            self.launch.chmod(mode)
            with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)), \
                    self.assertRaises(boundary.Refusal):
                boundary._launch_directory(self.launch)
        self.launch.chmod(0o775)
        for info in ("", self.mountinfo("rw", target=self.launch),
                     self.mountinfo(target=self.launch) + self.mountinfo("rw", target=self.launch / "deep" / "nested")):
            with patch.object(boundary, "read_mountinfo", return_value=info), self.assertRaises(boundary.Refusal):
                boundary._launch_directory(self.launch)

    def test_readonly_source_capability_refuses_managed_and_native_home_overlap(self):
        for excluded in (self.launch, self.root, self.launch / "child"):
            with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=self.launch)), \
                    self.assertRaises(boundary.Refusal):
                boundary._launch_directory(self.launch, (excluded,))
        for key in ("managed_workspace", "expected_home", "expected_codex_home"):
            value = dict(self.value, launch_root=self.value[key])
            target = Path(value["launch_root"])
            with patch.object(boundary, "read_mountinfo", return_value=self.mountinfo(target=target)), \
                    self.assertRaises(boundary.Refusal):
                boundary.attest_context(value)

    def test_readonly_source_metadata_must_match_source_uid_and_not_be_world_writable(self):
        metadata = self.launch / ".git"
        metadata.mkdir(mode=0o700)
        capability = {"root": self.launch, "uid": self.launch.stat().st_uid + 1}
        with self.assertRaises(boundary.Refusal):
            boundary._refuse_policy_inputs([self.launch], self.native, capability)
        capability["uid"] = self.launch.stat().st_uid
        metadata.chmod(0o777)
        with self.assertRaises(boundary.Refusal):
            boundary._refuse_policy_inputs([self.launch], self.native, capability)
        # No capability means group-writable task metadata remains unsupported.
        metadata.chmod(0o775)
        with self.assertRaises(boundary.Refusal):
            boundary._refuse_policy_inputs([self.launch], self.native)

    def test_v2_sealed_copy_requires_exact_explicit_integer_uid(self):
        self.value["snapshot_profile"] = "trust-ro-v2"
        for invalid in (None, True, False, -1, 4294967295, "0", 0.0):
            self.value["expected_snapshot_uid"] = invalid
            with self.subTest(uid=invalid), self.assertRaises(boundary.Refusal): self.attest()
        self.value["expected_snapshot_uid"] = os.getuid()
        self.assertEqual(self.attest()["config_bytes"], b"")
        self.value["expected_snapshot_uid"] = os.getuid() + 1
        with self.assertRaises(boundary.Refusal): self.attest()
        self.value["snapshot_profile"] = "trust-ro-v1"
        self.assertEqual(self.attest()["config_bytes"], b"")

    def test_sibling_and_deleted_descendant_trust_metadata_remains_supported(self):
        sibling = self.managed / "sibling"
        sibling.mkdir(mode=0o700)
        data = self.trust([self.managed, self.workspace, sibling, self.managed / "retired" / "deleted"])
        self.assertEqual(self.attest()["config_bytes"], data)

    def test_nonempty_comments_whitespace_and_unqualified_toml_refuse(self):
        for data in (b" ", b"# metadata\n", b"projects = {}", b'model="injected"',
                     b'[projects]\n[plugins]\n', b'not valid = [', b'\xff'):
            with self.subTest(data=data):
                self.config.write_bytes(data)
                self.value["expected_snapshot_sha256"] = digest(data)
                with self.assertRaises(boundary.Refusal):
                    self.attest()

    def test_snapshot_digest_and_profile_are_fixed_inputs(self):
        self.value["expected_snapshot_sha256"] = "0" * 64
        with self.assertRaises(boundary.Refusal): self.attest()
        self.value["expected_snapshot_sha256"] = digest(b"")
        self.value["snapshot_profile"] = "unknown"
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_refuses_escaped_root_extra_metadata_and_symlinked_existing_trust_paths(self):
        outside = self.root / "foreign"
        outside.mkdir(mode=0o700)
        for path, metadata in ((outside, 'trust_level="trusted"'),
                               (self.workspace, 'trust_level="trusted"\nmodel="injected"'),
                               (self.workspace, 'trust_level="untrusted"'),
                               (self.workspace, 'trust_level=true')):
            self.trust([path], metadata)
            with self.subTest(path=path, metadata=metadata), self.assertRaises(boundary.Refusal):
                self.attest()
        linked = self.managed / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        self.trust([linked / "missing"])
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_existing_trust_path_file_or_writable_directory_refuses(self):
        target = self.managed / "retired"
        target.write_text("not a directory")
        self.trust([target / "deleted"])
        with self.assertRaises(boundary.Refusal): self.attest()
        target.unlink()
        target.mkdir(mode=0o700)
        target.chmod(0o777)
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_homes_must_match_canonical_post_wrapper_environment(self):
        for name in ("HOME", "CODEX_HOME"):
            with self.subTest(name=name), patch.dict(os.environ, {name: str(self.launch)}), \
                    self.assertRaises(boundary.Refusal):
                self.attest()
        link = self.root / "linked-home"
        link.symlink_to(self.home, target_is_directory=True)
        with patch.dict(os.environ, {"HOME": str(link)}):
            self.value["expected_home"] = str(link)
            with self.assertRaises(boundary.Refusal): self.attest()

    def test_config_symlink_fifo_directory_and_over_limit_refuse_without_blocking(self):
        self.config.unlink()
        target = self.root / "other-config"
        target.write_bytes(b"")
        self.config.symlink_to(target)
        with self.assertRaises(boundary.Refusal): self.attest()
        self.config.unlink()
        os.mkfifo(self.config, 0o600)
        with self.assertRaises(boundary.Refusal): self.attest()
        self.config.unlink()
        self.config.mkdir(mode=0o700)
        with self.assertRaises(boundary.Refusal): self.attest()
        self.config.rmdir()
        data = b"x" * (boundary.MAX_CONFIG_BYTES + 1)
        self.config.write_bytes(data)
        self.value["expected_snapshot_sha256"] = digest(data)
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_mode_and_owner_checks_apply_to_config_home_and_task(self):
        for path in (self.config, self.home, self.native, self.workspace):
            old = stat.S_IMODE(path.stat().st_mode)
            path.chmod(old | 0o020)
            with self.subTest(path=path), self.assertRaises(boundary.Refusal): self.attest()
            path.chmod(old)
        if os.getuid() == 0:
            os.chown(self.config, 12345, -1)
            try:
                with self.assertRaises(boundary.Refusal): self.attest()
            finally:
                os.chown(self.config, 0, -1)

    def test_exact_file_mount_ro_required_not_parent_directory_mount(self):
        for info in (self.mountinfo("rw"), self.mountinfo("ro,rw"),
                     self.mountinfo(target=self.native), "", self.mountinfo() * 2,
                     "malformed\n"):
            with self.subTest(info=info), patch.object(boundary, "read_mountinfo", return_value=info), \
                    self.assertRaises(boundary.Refusal):
                boundary.attest_context(self.value)

    def test_real_file_replacement_during_read_refuses_descriptor_path_mismatch(self):
        self.config.write_bytes(b"original")
        original_read = os.read
        replaced = False

        def replace_after_read(fd, count):
            nonlocal replaced
            data = original_read(fd, count)
            if not replaced:
                replaced = True
                replacement = self.native / "replacement.toml"
                replacement.write_bytes(b"original")
                replacement.replace(self.config)
            return data

        with patch.object(boundary.os, "read", side_effect=replace_after_read):
            with self.assertRaises(boundary.Refusal):
                boundary.read_regular(self.config, boundary.MAX_CONFIG_BYTES, os.getuid())

    def test_probe_only_call_can_skip_snapshot_mount_but_not_digest_or_environment(self):
        # Launch-source mount inspection still runs; no snapshot mount exists.
        with patch.object(boundary, "read_mountinfo", return_value=""):
            self.assertEqual(boundary.attest_context(self.value, require_snapshot=False)["config_bytes"], b"")
            self.config.write_bytes(b"injected")
            with self.assertRaises(boundary.Refusal):
                boundary.attest_context(self.value, require_snapshot=False)

    def test_native_environment_rejects_new_changed_or_removed_values(self):
        key = "OPENAI_BASE_URL"
        value = "https://dummy.invalid"
        with patch.dict(os.environ, {key: value}):
            with self.assertRaises(boundary.Refusal): self.attest()
            self.value["expected_native_environment"] = {key: digest(value.encode())}
            self.attest()
            with patch.dict(os.environ, {key: "https://changed.invalid"}), self.assertRaises(boundary.Refusal):
                self.attest()
        with self.assertRaises(boundary.Refusal): self.attest()

    def test_rejects_each_injection_variable_without_exposing_its_value(self):
        for name in boundary.INJECTION_VARIABLES:
            with self.subTest(name=name), patch.dict(os.environ, {name: "dummy-injection"}):
                with self.assertRaisesRegex(boundary.Refusal, "^sandbox_unqualified_config$"):
                    self.attest()

    def test_executable_substitution_bytes_mode_and_resolution_report_toolchain_stage(self):
        self.exe.write_bytes(b"changed")
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"): self.attest()
        self.value["expected_executable_sha256"] = digest(b"changed")
        self.exe.chmod(0o666)
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"): self.attest()
        self.exe.chmod(0o755)
        self.value["expected_executable"] = str(self.root / "different-codex")
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"): self.attest()

    def test_missing_or_foreign_owned_frontend_reports_toolchain_stage(self):
        with patch.dict(os.environ, {"PATH": str(self.root / "missing-bin")}):
            with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"):
                self.attest()
        original_stat = Path.stat

        def foreign_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == self.exe:
                fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                fields["st_uid"] = os.getuid() + 1
                return SimpleNamespace(**fields)
            return info

        # The bounded descriptor read still executes on the real ordinary file;
        # substitute only the final frontend ownership observation.
        with patch.object(Path, "stat", foreign_stat):
            with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"):
                self.attest()

    def test_malformed_expected_executable_metadata_and_config_remain_config_refusals(self):
        for key, value in (("expected_executable", "relative/codex"),
                           ("expected_executable_sha256", "not-a-digest")):
            previous = self.value[key]
            self.value[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(boundary.Refusal, "^sandbox_unqualified_config$"):
                self.attest()
            self.value[key] = previous
        self.config.write_bytes(b"unqualified-config")
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_unqualified_config$"):
            self.attest()

    def test_native_and_each_project_policy_surface_refuses_even_broken_links(self):
        targets = [self.native / name for name in ("hooks.json", "rules")]
        targets += [boundary.SYSTEM_CONFIG_ROOT / name for name in
                    ("config.toml", "requirements.toml", "managed_config.toml", "hooks.json", "rules")]
        for root in (self.workspace, self.launch, self.managed, self.root):
            targets += [root / ".codex" / name for name in ("config.toml", "hooks.json", "rules")]
        for target in targets:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.symlink_to(self.root / "missing-policy")
            with self.subTest(target=target), self.assertRaises(boundary.Refusal): self.attest()
            target.unlink()

    def test_linked_or_file_git_metadata_refuses_while_owned_directory_and_skills_work(self):
        metadata = self.workspace / ".git"
        metadata.mkdir(mode=0o700)
        skills = self.workspace / ".codex" / "skills"
        skills.mkdir(mode=0o700, parents=True)
        (skills / "SKILL.md").write_text("supported skill fixture")
        self.attest()
        metadata.rmdir()
        metadata.write_text("gitdir: /other/project")
        with self.assertRaises(boundary.Refusal): self.attest()
        metadata.unlink()
        metadata.symlink_to(self.root / "missing-git")
        with self.assertRaises(boundary.Refusal): self.attest()


class NpmLaunchChainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-npm-chain-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.machine = patch.object(boundary.platform, "machine", return_value="x86_64")
        self.machine.start()
        self.addCleanup(self.machine.stop)
        self.platform = patch.object(boundary.sys, "platform", "linux")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.package_root = self.root / "node_modules" / "@openai" / "codex"
        self.frontend = self.package_root / "bin" / "codex.js"
        self.write(self.frontend, npm_fixture("frontend"), 0o755)
        self.write(self.package_root / "package.json", npm_fixture("package"))
        self.native = self.platform_package("x64", "x86_64-unknown-linux-musl", 62)
        self.node = self.root / "project-toolchain" / "bin" / "node"
        self.write(self.node, self.elf(62, b"synthetic-node-not-executed"), 0o755)
        os.environ["PATH"] = str(self.node.parent)

    def write(self, path, data, mode=0o644):
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def elf(self, machine, payload=b"synthetic-native-not-executed"):
        # Synthetic ELF headers prove parser/pinning only, never native execution.
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = machine.to_bytes(2, "little")
        return bytes(header) + payload

    def platform_package(self, cpu, target, machine):
        root = self.package_root / "node_modules" / "@openai" / ("codex-linux-" + cpu)
        self.write(root / "package.json", npm_fixture(cpu))
        native = root / "vendor" / target / "bin" / "codex"
        self.write(native, self.elf(machine), 0o755)
        return native

    def test_exact_published_frontend_platform_metadata_and_native_elf_are_pinned(self):
        chain = boundary.image_chain(str(self.frontend))
        self.assertEqual(chain["profile"], "codex-npm-linux-v1")
        self.assertEqual(chain["frontend"]["sha256"], boundary.NPM_FRONTEND_SHA256)
        self.assertEqual(chain["package"]["sha256"], boundary.NPM_PACKAGE_SHA256)
        self.assertEqual(chain["native"]["path"], str(self.native))
        self.assertEqual(chain["native_package"]["sha256"], boundary.NPM_LINUX_TARGETS["x86_64"][3])

    def test_arm64_layout_uses_its_exact_published_alias_metadata(self):
        with patch.object(boundary.platform, "machine", return_value="aarch64"):
            native = self.platform_package("arm64", "aarch64-unknown-linux-musl", 183)
            chain = boundary.image_chain(str(self.frontend))
            self.assertEqual(chain["native"]["path"], str(native))
            self.assertEqual(chain["target"], "aarch64-unknown-linux-musl")

    def test_actual_requires_successful_probe_chain_and_matches_selected_node(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend))
        probe = boundary.attest_runtime_chain(value, str(self.frontend), require_launch_chain=False)
        self.assertEqual(probe["node"], {"path": str(self.node), "sha256": digest(self.node.read_bytes())})
        value["expected_launch_chain"] = probe
        self.assertEqual(boundary.attest_runtime_chain(value, str(self.frontend)), probe)
        self.node.write_bytes(self.elf(62, b"changed-node"))
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend))

    def test_foreign_owned_node_reports_toolchain_stage_without_relaxing_guard(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        original_fstat = boundary.os.fstat
        original_lstat = Path.lstat
        node_inode = self.node.stat().st_ino

        def foreign(info):
            if info.st_ino == node_inode:
                # Preserve every identity field, changing ownership consistently
                # at both path and descriptor reads; the owner check must refuse.
                fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                fields["st_uid"] = os.getuid() + 1
                return SimpleNamespace(**fields)
            return info

        def foreign_lstat(path, *args, **kwargs):
            return foreign(original_lstat(path, *args, **kwargs))

        with patch.object(boundary.os, "fstat", side_effect=lambda fd: foreign(original_fstat(fd))), \
                patch.object(Path, "lstat", foreign_lstat):
            with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"):
                boundary.attest_runtime_chain(value, str(self.frontend), False)

    def test_missing_unsafe_and_changed_nodes_report_toolchain_stage(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        value["expected_launch_chain"] = boundary.attest_runtime_chain(value, str(self.frontend), False)
        self.node.chmod(0o777)
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"):
            boundary.attest_runtime_chain(value, str(self.frontend))
        self.node.unlink()
        with self.assertRaisesRegex(boundary.Refusal, "^sandbox_toolchain_unqualified$"):
            boundary.attest_runtime_chain(value, str(self.frontend))
        with patch.dict(os.environ, {"NODE_OPTIONS": "--secret-value"}):
            with self.assertRaisesRegex(boundary.Refusal, "^sandbox_unqualified_config$"):
                boundary.attest_runtime_chain(value, str(self.frontend))

    def test_relocated_project_node_still_requires_actual_path_and_bytes(self):
        spec = importlib.util.spec_from_file_location("readiness_relocation", Path(__file__).resolve().parents[1] / "priv/codex_readiness.py")
        readiness = importlib.util.module_from_spec(spec); spec.loader.exec_module(readiness)
        actual = self.root / "actual-task"; actual.mkdir()
        relative = Path("var/toolchain/node-v24.14.0-linux-x64/bin/node")
        copied = self.root / "probe-copy"; copied.mkdir()
        self.node = copied / relative
        self.write(self.node, self.elf(62, b"qualified-project-node"), 0o755)
        os.environ["PATH"] = str(self.node.parent)
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        receipt = boundary.attest_runtime_chain(value, str(self.frontend), False)
        value["expected_launch_chain"] = readiness.rebase_project_node(receipt, copied, actual)
        selected = actual / relative
        self.write(selected, self.node.read_bytes(), 0o755)
        os.environ["PATH"] = str(selected.parent)
        self.assertEqual(boundary.attest_runtime_chain(value, str(self.frontend)), value["expected_launch_chain"])
        selected.write_bytes(self.elf(62, b"different-project-node"))
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend))

    def test_matching_bytes_at_a_different_node_path_refuses(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        value["expected_launch_chain"] = boundary.attest_runtime_chain(value, str(self.frontend), False)
        substitute = self.root / "another-node" / "node"
        self.write(substitute, self.node.read_bytes(), 0o755)
        os.environ["PATH"] = str(substitute.parent)
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend))

    def test_frontend_and_either_package_metadata_mutation_refuses(self):
        package = self.package_root / "package.json"
        native_package = self.native.parents[3] / "package.json"
        for path in (self.frontend, package, native_package):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(path=path), self.assertRaises(boundary.Refusal):
                boundary.image_chain(str(self.frontend))
            path.write_bytes(original)

    def test_native_vendor_fallback_and_linked_platform_packages_refuse(self):
        native_root = self.native.parents[3]
        metadata = native_root / "package.json"
        metadata.unlink()
        fallback = self.package_root / "vendor" / "x86_64-unknown-linux-musl" / "bin" / "codex"
        self.write(fallback, self.elf(62), 0o755)
        with self.assertRaises(boundary.Refusal): boundary.image_chain(str(self.frontend))
        self.write(metadata, npm_fixture("x64"))
        original = self.native.read_bytes()
        self.native.unlink()
        self.native.symlink_to(fallback)
        with self.assertRaises(boundary.Refusal): boundary.image_chain(str(self.frontend))
        self.native.unlink()
        self.write(self.native, original, 0o755)
        metadata.unlink()
        metadata.symlink_to(self.package_root / "package.json")
        with self.assertRaises(boundary.Refusal): boundary.image_chain(str(self.frontend))

    def test_native_replacement_and_wrong_elf_architecture_refuse(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        self.native.write_bytes(self.elf(62, b"changed-native"))
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend), False)
        self.native.write_bytes(self.elf(183))
        with self.assertRaises(boundary.Refusal): boundary.image_chain(str(self.frontend))

    def test_node_path_injection_missing_node_script_node_and_nonlinux_refuse(self):
        value = {"expected_image_chain": boundary.image_chain(str(self.frontend))}
        with patch.dict(os.environ, {"NODE_PATH": str(self.root)}), self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend), False)
        self.node.unlink()
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend), False)
        self.write(self.node, b"#!/bin/sh\nexit 0\n", 0o755)
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain(value, str(self.frontend), False)
        with patch.object(boundary.sys, "platform", "darwin"), self.assertRaises(boundary.Refusal):
            boundary.image_chain(str(self.frontend))

    def test_raw_native_elf_entrypoint_and_generic_scripts_refuse(self):
        with self.assertRaises(boundary.Refusal):
            boundary.image_chain(str(self.native))
        with self.assertRaises(boundary.Refusal):
            boundary.attest_runtime_chain({}, str(self.native), False)
        self.native.write_bytes(b"#!/bin/sh\nexit 0\n")
        with self.assertRaises(boundary.Refusal):
            boundary.image_chain(str(self.native))


class LaunchReceiptTest(unittest.TestCase):
    def setUp(self):
        self.launch = dict(boundary.__dict__)
        source = SOURCE.with_name("codex_launch.py").read_text()
        exec(compile(source, str(SOURCE.with_name("codex_launch.py")), "exec"), self.launch)

    def run_failure(self, error, operation="probe"):
        read_fd, write_fd = os.pipe()
        value = {"operation": operation, "receipt_fd": write_fd}
        self.launch["attest_context"] = lambda *args, **kwargs: (_ for _ in ()).throw(error)
        output = io.StringIO()
        with patch.object(sys, "argv", ["-c", json.dumps(value), "codex", "app-server"]), patch.object(sys, "stderr", output):
            self.assertEqual(self.launch["launch_main"](), 78)
        if operation != "probe":
            os.close(write_fd)
        try:
            receipt = os.read(read_fd, 16385)
        finally:
            os.close(read_fd)
        return receipt, output.getvalue()

    def test_guard_error_receipt_preserves_config_and_toolchain_stage(self):
        for code in ("sandbox_unqualified_config", "sandbox_toolchain_unqualified"):
            with self.subTest(code=code):
                raw, stderr = self.run_failure(boundary.Refusal(code))
                self.assertEqual(json.loads(raw), {"error": code})
                self.assertEqual(stderr, "symphony: " + code + " before native launch\n")

    def test_unexpected_exception_never_leaks_child_or_secret_details(self):
        raw, stderr = self.run_failure(RuntimeError("secret-child-output"))
        self.assertEqual(json.loads(raw), {"error": "sandbox_launch_failed"})
        self.assertNotIn("secret", stderr)

    def test_actual_exec_refusal_writes_no_probe_receipt(self):
        raw, stderr = self.run_failure(boundary.Refusal("sandbox_toolchain_unqualified"), operation="exec")
        self.assertEqual(raw, b"")
        self.assertIn("sandbox_toolchain_unqualified", stderr)

    def test_frontend_argument_substitution_reports_toolchain_error_receipt(self):
        read_fd, write_fd = os.pipe()
        value = {"operation": "probe", "receipt_fd": write_fd}
        self.launch["attest_context"] = lambda *args, **kwargs: {"executable": "/qualified/codex"}
        self.launch["attest_runtime_chain"] = lambda *args, **kwargs: self.fail("substituted frontend reached runtime chain")
        output = io.StringIO()
        with patch.object(sys, "argv", ["-c", json.dumps(value), "/substituted/codex", "app-server"]), patch.object(sys, "stderr", output):
            self.assertEqual(self.launch["launch_main"](), 78)
        try:
            self.assertEqual(json.loads(os.read(read_fd, 16385)), {"error": "sandbox_toolchain_unqualified"})
        finally:
            os.close(read_fd)
        self.assertEqual(output.getvalue(), "symphony: sandbox_toolchain_unqualified before native launch\n")

    def test_actual_launch_chain_substitution_reports_toolchain_without_exec(self):
        executable = str(Path(sys.executable).resolve())
        value = {"operation": "exec", "expected_launch_chain": {"node": "expected"}}
        self.launch["attest_context"] = lambda *args, **kwargs: {"executable": executable}
        self.launch["attest_runtime_chain"] = lambda *args, **kwargs: {"node": "substituted"}
        output = io.StringIO()
        with patch.object(sys, "argv", ["-c", json.dumps(value), executable, "app-server"]), \
                patch.object(sys, "stderr", output), patch.object(os, "execve", side_effect=AssertionError("must refuse before exec")):
            self.assertEqual(self.launch["launch_main"](), 78)
        self.assertEqual(output.getvalue(), "symphony: sandbox_toolchain_unqualified before native launch\n")

    def test_success_receipt_retains_existing_raw_chain_schema(self):
        read_fd, write_fd = os.pipe()
        chain = {"profile": "codex-npm-linux-v1", "node": {"path": "/node", "sha256": "a" * 64}}
        self.launch["write_launch_receipt"]({"receipt_fd": write_fd}, chain)
        try:
            self.assertEqual(json.loads(os.read(read_fd, 16385)), chain)
        finally:
            os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
