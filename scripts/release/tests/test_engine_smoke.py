"""Adversarial process fixtures test the controller, not native engine acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("engine_smoke", Path(__file__).parents[1] / "engine-smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)

FIXTURE = '''#!PYTHON
import http.server, json, os, signal, sys, time
MODE = "MODE_VALUE"
if MODE == "banner":
    print("This Symphony implementation is a low key engineering preview.")
    sys.exit(1)
if MODE == "timeout":
    time.sleep(30)
port = int(sys.argv[sys.argv.index("--port") + 1])
state = {"generated_at": "2026-10-22T00:00:00Z", "counts": {"running": 0, "retrying": 0, "blocked": 0},
         "running": [], "retrying": [], "blocked": [],
         "agent": {"provider": "claude", "readiness": {"ready": False, "code": "runtime_unavailable", "local_policy_passed": False}}}
if MODE == "bad_state":
    state["error"] = {"code": "snapshot_unavailable"}
if MODE == "bool_count":
    state["counts"]["running"] = False
if MODE == "false_ready":
    state["agent"]["readiness"]["ready"] = True
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500 if MODE == "http_failure" else 200)
        self.send_header("Content-Type", "application/json" if self.path == "/api/v1/state" else "text/html")
        self.end_headers()
        self.wfile.write(json.dumps(state).encode() if self.path == "/api/v1/state" else
                         b"Symphony Observability Operations Dashboard runner-identity")
    def log_message(self, *args):
        pass
if MODE in ("child_survives", "detached_child") and os.fork() == 0:
    if MODE == "detached_child":
        os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)
signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == "shutdown_timeout" else lambda *args: sys.exit(0))
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@unittest.skipUnless(shutil.which("lsof"), "controller requires lsof for real socket ownership inspection")
class EngineSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def execute(self, mode, expected=None):
        executable = self.root / (mode + ".py")
        executable.write_text(FIXTURE.replace("PYTHON", sys.executable).replace("MODE_VALUE", mode))
        executable.chmod(0o700)
        evidence = self.root / mode
        kwargs = dict(startup_timeout=2, shutdown_timeout=0.4, hold_seconds=0.1)
        if expected:
            with self.assertRaisesRegex(SMOKE.SmokeFailure, expected):
                SMOKE.qualify(executable, evidence, **kwargs)
        else:
            SMOKE.qualify(executable, evidence, **kwargs)
        result = json.loads((evidence / "result.json").read_text())
        self.assertTrue(SMOKE.port_closed(result["port"]))
        self.assertTrue(result["cleanup_process_group_gone"], result)
        self.assertTrue(result["cleanup_listener_closed"], result)
        self.assertTrue(result["cleanup_descendants_gone"], result)
        return result

    def test_actual_fixture_server_and_graceful_shutdown(self):
        result = self.execute("success")
        self.assertTrue(result["passed"])
        self.assertFalse(result["forced_cleanup"])
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["listener_pids"])

    def test_banner_exit_cannot_pass(self):
        self.assertFalse(self.execute("banner", "exited before qualification")["passed"])

    def test_failed_state_cannot_pass(self):
        self.assertFalse(self.execute("bad_state", "state contains an error")["passed"])

    def test_boolean_count_cannot_pass(self):
        self.assertFalse(self.execute("bool_count", "invalid count types")["passed"])

    def test_false_agent_readiness_cannot_pass(self):
        self.assertFalse(self.execute("false_ready", "positive readiness")["passed"])

    def test_failed_http_cannot_pass(self):
        self.assertFalse(self.execute("http_failure", "startup timed out")["passed"])

    def test_startup_timeout_cannot_pass(self):
        self.assertFalse(self.execute("timeout", "startup timed out")["passed"])

    def test_forced_shutdown_cannot_pass(self):
        result = self.execute("shutdown_timeout", "graceful shutdown timed out")
        self.assertFalse(result["passed"])
        self.assertTrue(result["forced_cleanup"])

    def test_surviving_child_cannot_pass(self):
        result = self.execute("child_survives", "child survived")
        self.assertFalse(result["passed"])
        self.assertTrue(result["forced_cleanup"])

    def test_detached_child_cannot_pass_and_is_cleaned(self):
        result = self.execute("detached_child", "child survived")
        self.assertFalse(result["passed"])
        self.assertTrue(result["forced_cleanup"])
        self.assertGreater(len(result["owned_pids"]), 1)

    def test_preexisting_listener_is_not_owned(self):
        server = subprocess.Popen([sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: server.wait(timeout=5))
        self.addCleanup(server.terminate)
        # Exercise ownership directly with a known unrelated listening socket.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaisesRegex(SMOKE.SmokeFailure, "another process group"):
                SMOKE.owned_listener(listener.getsockname()[1], server, shutil.which("lsof"))


if __name__ == "__main__":
    unittest.main()
