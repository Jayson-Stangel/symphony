#!/usr/bin/env python3
"""Failure-injection tests, distinct from real native sandbox qualification."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROBE = Path(__file__).resolve().parents[1] / "elixir/priv/codex_readiness.py"
spec = importlib.util.spec_from_file_location("probe", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jay704-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspaces"
        self.workspace.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.binary = self.root / "codex"
        self.mode = self.root / "mode"
        self.binary.write_text(f'''#!{sys.executable}
import errno,os,pathlib,signal,sys,time
mode=pathlib.Path({str(self.mode)!r}).read_text()
if mode in ('namespace','proc'):
    print("bwrap: No permissions to create a new namespace" if mode=='namespace' else "bwrap: Can't mount proc on /proc: Operation not permitted", file=sys.stderr)
    sys.exit(1)
if mode=='signal':os.kill(os.getpid(),signal.SIGKILL)
if mode=='zero':sys.exit(0)
if mode=='timeout':
    import subprocess
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    pathlib.Path({str(self.root / 'descendant.pid')!r}).write_text(str(child.pid))
    time.sleep(60)
args=sys.argv[sys.argv.index('--')+1:]
if mode=='permissive':os.execv(args[0],args)
if mode=='denial-fixture':
    source=args[2]
    sys.argv=['-c',*args[3:]]
    original=pathlib.Path.write_text
    denied=[pathlib.Path(p) for p in sys.argv[-2:]]
    def write(p,*a,**kw):
        if any(root in p.parents for root in denied):raise PermissionError(errno.EACCES,'fixture denial')
        return original(p,*a,**kw)
    pathlib.Path.write_text=write
    exec(source)
''')
        self.binary.chmod(0o755)
        self.value = dict(command=f"{self.binary} app-server", workspace=str(self.workspace), remote=False,
                          policy=dict(type="workspaceWrite", networkAccess=False,
                                      excludeTmpdirEnvVar=True, excludeSlashTmp=True))
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_mode(self, mode):
        self.mode.write_text(mode)
        try:
            return probe.check(self.value)
        finally:
            self.assertEqual(list(self.workspace.iterdir()), [], "owned workspace canaries leaked")

    def test_success_fixture_requires_both_effects_and_denials(self):
        self.assertTrue(self.run_mode("denial-fixture")["ready"])

    def test_zero_exit_without_effects_is_not_ready(self):
        with self.assertRaisesRegex(probe.Refusal, "sandbox_required_effect_missing"):
            self.run_mode("zero")

    def test_plain_shell_cannot_pass(self):
        with self.assertRaisesRegex(probe.Refusal, "sandbox_policy_enforcement_failed"):
            self.run_mode("permissive")

    def test_stage_diagnostics(self):
        for mode, code in (("namespace", "sandbox_namespace_denied"), ("proc", "sandbox_proc_setup_failed"),
                           ("signal", "sandbox_helper_terminated")):
            with self.subTest(mode=mode), self.assertRaisesRegex(probe.Refusal, code):
                self.run_mode(mode)

    def test_timeout_kills_helper_and_cleans_canaries(self):
        original = subprocess.Popen.wait

        def short_wait(child, timeout=None):
            if timeout:
                deadline = time.monotonic() + 2
                while not (self.root / "descendant.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
            return original(child, timeout=0.1 if timeout else timeout)

        with patch.object(subprocess.Popen, "wait", short_wait), self.assertRaisesRegex(probe.Refusal, "sandbox_timeout"):
            self.run_mode("timeout")
        pid = int((self.root / "descendant.pid").read_text())
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), "sandbox descendant remained running")

    def test_missing_helper(self):
        self.value["command"] = "/definitely-missing-codex app-server"
        with self.assertRaisesRegex(probe.Refusal, "sandbox_helper_missing"):
            probe.check(self.value)

    def test_unsupported_inputs_fail_closed(self):
        for field, value in (("command", "codex app-server; true"), ("remote", True),
                             ("policy", {"type": "dangerFullAccess"}),
                             ("policy", {"type": "workspaceWrite", "unknown": True})):
            with self.subTest(field=field), self.assertRaises(probe.Refusal):
                probe.check({**self.value, field: value})

    def test_unknown_native_config_is_not_silently_ignored(self):
        (self.home / "config.toml").write_text("[features]\nuse_legacy_landlock=true\n")
        with self.assertRaisesRegex(probe.Refusal, "sandbox_unqualified_config"):
            probe.check(self.value)

    def test_generated_command_is_supported(self):
        self.value["command"] = f"{self.binary} --config shell_environment_policy.inherit=all app-server"
        self.assertTrue(self.run_mode("denial-fixture")["ready"])


class BoundaryHarnessTests(unittest.TestCase):
    def test_failed_or_empty_helper_cannot_qualify_process_and_network_effects(self):
        boundary_spec = importlib.util.spec_from_file_location("boundaries", PROBE.parents[2] / "runner/codex-boundaries.py")
        boundaries = importlib.util.module_from_spec(boundary_spec)
        boundary_spec.loader.exec_module(boundaries)
        for status, stdout in [(1, ""), (0, ""), (0, "unrelated success")]:
            with self.subTest(status=status, stdout=stdout):
                result = subprocess.CompletedProcess([], status, stdout, "")
                with patch.object(boundaries.subprocess, "run", return_value=result), self.assertRaises(RuntimeError):
                    boundaries.protected_checks(12345, 34567)


if __name__ == "__main__":
    unittest.main()
