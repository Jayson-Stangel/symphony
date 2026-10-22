#!/usr/bin/env python3
"""Qualify a standalone engine artifact; Python and lsof are test controllers only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


class SmokeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def native_target():
    cpu = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(platform.machine())
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    require(cpu and system, "unsupported native platform")
    return f"{system}_{cpu}"


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def process_table():
    # Start identity avoids targeting a subsequently reused PID. Do not collect
    # command lines or environment from unrelated host processes.
    snapshot = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,lstart="],
                              capture_output=True, text=True, timeout=5, check=True)
    rows = {}
    for line in snapshot.stdout.splitlines():
        pid, parent, started = line.strip().split(None, 2)
        rows[int(pid)] = (int(parent), started)
    return rows


class Descendants:
    """Retain child identities even after setsid or reparenting on shutdown."""
    def __init__(self, root, lease, lsof):
        self.root = root
        self.lease = lease
        self.lsof = lsof
        self.identities = {}
        self.observe()

    def lease_holders(self):
        holders = subprocess.run([self.lsof, "-t", "--", str(self.lease)],
                                 capture_output=True, text=True, timeout=5)
        require(holders.returncode in (0, 1), "ownership descriptor inspection failed")
        return {int(pid) for pid in holders.stdout.split()} - {os.getpid()}

    def observe(self):
        rows = process_table()
        # The private inherited descriptor survives fork, setsid and reparenting,
        # so ordinary daemon double-forks cannot erase test ownership between
        # ancestry snapshots. Inspect only this test's inaccessible private file.
        for pid in self.lease_holders():
            if pid in rows:
                self.identities[pid] = rows[pid][1]
        if self.root not in self.identities and self.root in rows:
            self.identities[self.root] = rows[self.root][1]
        changed = True
        while changed:
            changed = False
            for pid, (parent, started) in rows.items():
                if pid not in self.identities and parent in self.identities and parent in rows:
                    if rows[parent][1] == self.identities[parent]:
                        self.identities[pid] = started
                        changed = True
        return {pid for pid, started in self.identities.items() if pid in rows and rows[pid][1] == started}

    def kill(self):
        # Observe again before cleanup to include children created during stop.
        for pid in self.observe():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def reap(self):
        for pid in self.identities:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass


def listener_owners(port, lsof):
    result = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
                            capture_output=True, text=True, timeout=5)
    require(result.returncode in (0, 1), "lsof listener inspection failed")
    return {int(line[1:]) for line in result.stdout.splitlines() if line.startswith("p")}


def owned_listener(port, process, lsof):
    owners = listener_owners(port, lsof)
    require(owners, "HTTP response has no attributable listening process")
    for owner in owners:
        try:
            require(os.getpgid(owner) == process.pid, "listener belongs to another process group")
        except ProcessLookupError as exc:
            raise SmokeFailure("listener exited during attribution") from exc
    return sorted(owners)


def validate_state(state):
    require(isinstance(state, dict) and "error" not in state, "state contains an error or is not an object")
    require(isinstance(state.get("generated_at"), str) and bool(state["generated_at"]), "state lacks generation time")
    counts = state.get("counts")
    require(isinstance(counts, dict) and set(counts) == {"running", "retrying", "blocked"}, "invalid counts schema")
    require(all(type(value) is int and value == 0 for value in counts.values()), "fixture has active work or invalid count types")
    require(all(state.get(key) == [] for key in counts), "fixture work collections are not empty")
    agent = state.get("agent", {})
    require(isinstance(agent, dict), "agent is not an object")
    readiness = agent.get("readiness", {})
    require(isinstance(readiness, dict), "readiness is not an object")
    require(agent.get("provider") == "claude", "unexpected provider")
    if readiness.get("code") == "checking":
        return False
    require(readiness.get("ready") is False and readiness.get("local_policy_passed") is False
            and readiness.get("code") == "runtime_unavailable", "missing native runtime must not report positive readiness")
    return True


def fetch(port, path):
    # Ignore proxy settings even in the controller environment.
    with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        require(response.status == 200, f"unexpected HTTP status for {path}")
        body = response.read(2_000_001)
        require(len(body) <= 2_000_000, "HTTP response exceeds qualification bound")
        return body, response.headers.get("Content-Type", "")


def port_closed(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def qualify(artifact, evidence_dir, startup_timeout=120, shutdown_timeout=30, hold_seconds=3, port=None):
    """Run a verified executable. Callers verify artifact identity before entering."""
    evidence_dir = Path(evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    require(not Path("/opt/symphony/claude/helper.mjs").exists(),
            "qualification requires absent host Claude helper; refusing to invoke host tooling")
    lsof = shutil.which("lsof")
    require(lsof, "lsof is required for process attribution (controller dependency)")
    result = {"passed": False, "forced_cleanup": False}
    process = None
    descendants = None
    with tempfile.TemporaryDirectory(prefix="symphony-engine-smoke-") as directory:
        root = Path(directory)
        for name in ("home", "cache", "config", "tmp", "workspaces", "logs", "extract"):
            (root / name).mkdir()
        executable = root / "engine"
        shutil.copyfile(artifact, executable)
        executable.chmod(0o700)
        require(hashlib.sha256(executable.read_bytes()).digest() == hashlib.sha256(Path(artifact).read_bytes()).digest(), "staged executable differs")
        workflow = root / "WORKFLOW.md"
        workflow.write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: " + str(root / "workspaces") + "\npolling:\n  interval_ms: 100\nserver:\n  host: 127.0.0.1\nobservability:\n  dashboard_enabled: false\n---\nNo issues in this isolated qualification fixture.\n")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", port or 0))
            port = reservation.getsockname()[1]
        require(port_closed(port) and not listener_owners(port, lsof), "selected port already has a listener")
        # Deliberate allowlist: no tracker, model, GitHub, agent, proxy or host runtime credentials.
        environment = {
            "HOME": str(root / "home"), "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(root / "config"), "TMPDIR": str(root / "tmp"),
            "SYMPHONY_INSTALL_DIR": str(root / "extract"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C.UTF-8", "TERM": "dumb", "ERL_CRASH_DUMP": str(root / "logs" / "erl_crash.dump"),
        }
        with (evidence_dir / "startup.log").open("wb") as log, (root / "ownership").open("wb") as lease:
            try:
                process = subprocess.Popen([str(executable), "--i-understand-that-this-will-be-running-without-the-usual-guardrails",
                                            "--agent", "claude", "--logs-root", str(root / "logs"), "--port", str(port), str(workflow)],
                                           cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, pass_fds=(lease.fileno(),))
                descendants = Descendants(process.pid, root / "ownership", lsof)
                result.update(pid=process.pid, port=port)
                deadline = time.monotonic() + startup_timeout
                healthy_since = None
                while time.monotonic() < deadline:
                    descendants.observe()
                    require(process.poll() is None, f"engine exited before qualification: {process.returncode}")
                    try:
                        body, content_type = fetch(port, "/api/v1/state")
                        require("application/json" in content_type, "state endpoint is not JSON")
                        state = json.loads(body)
                        settled = validate_state(state)
                        dashboard, content_type = fetch(port, "/")
                        require("text/html" in content_type and b"Symphony Observability" in dashboard
                                and b"Operations Dashboard" in dashboard and b"runner-identity" in dashboard,
                                "dashboard content is not Symphony observability")
                        result["listener_pids"] = owned_listener(port, process, lsof)
                        require(process.pid in descendants.lease_holders(), "engine discarded inherited test ownership descriptor")
                        if settled:
                            healthy_since = healthy_since or time.monotonic()
                            (evidence_dir / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
                            (evidence_dir / "dashboard.html").write_bytes(dashboard)
                            if time.monotonic() - healthy_since >= hold_seconds:
                                break
                        else:
                            healthy_since = None
                    except (URLError, ConnectionError, TimeoutError) as exc:
                        result["last_connection_error"] = f"{type(exc).__name__}: {exc}"
                        if isinstance(exc, HTTPError):
                            exc.close()
                        healthy_since = None
                    time.sleep(0.2)
                else:
                    raise SmokeFailure("engine startup timed out")
                require(process.poll() is None, "engine exited before termination request")
                # SIGTERM is the Erlang runtime's supported graceful termination signal.
                # Signal the executable's session, including its Burrito/BEAM child if present.
                descendants.observe()
                signal_group(process.pid, signal.SIGTERM)
                try:
                    status = process.wait(timeout=shutdown_timeout)
                except subprocess.TimeoutExpired as exc:
                    raise SmokeFailure("graceful shutdown timed out") from exc
                result["exit_code"] = status
                require(status == 0, f"engine did not exit cleanly after SIGTERM: {status}")
                deadline = time.monotonic() + shutdown_timeout
                while (descendants.observe() or group_alive(process.pid) or not port_closed(port)) and time.monotonic() < deadline:
                    time.sleep(0.1)
                require(not descendants.observe() and not group_alive(process.pid), "test-owned child survived graceful shutdown")
                require(port_closed(port) and not listener_owners(port, lsof), "listener survived graceful shutdown")
                result.update(passed=True, graceful_shutdown=True, listener_closed=True, process_group_gone=True)
            except (SmokeFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
                result["error"] = str(exc)
                raise SmokeFailure(str(exc)) from exc
            finally:
                if process is not None and (group_alive(process.pid) or (descendants and descendants.observe())):
                    result["forced_cleanup"] = True
                    signal_group(process.pid, signal.SIGKILL)
                    if descendants:
                        descendants.kill()
                if process is not None:
                    process.wait(timeout=5)
                    cleanup_deadline = time.monotonic() + 5
                    while (group_alive(process.pid) or (descendants and descendants.observe())) and time.monotonic() < cleanup_deadline:
                        descendants.reap()
                        time.sleep(0.1)
                    result["owned_pids"] = sorted(descendants.identities) if descendants else [process.pid]
                    result["cleanup_descendants_gone"] = not descendants or not descendants.observe()
                    result["cleanup_process_group_gone"] = not group_alive(process.pid)
                    result["cleanup_listener_closed"] = port_closed(port)
                    if not result["cleanup_process_group_gone"] or not result["cleanup_listener_closed"] or not result["cleanup_descendants_gone"]:
                        result["passed"] = False
                        result["cleanup_error"] = "owned process group or listener survived cleanup"
                shutil.copytree(root / "logs", evidence_dir / "engine-logs", dirs_exist_ok=True)
                (evidence_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    require(result["passed"], result.get("cleanup_error", "qualification failed"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    artifact = args.artifact.resolve(strict=True)
    require(re.fullmatch(r"[0-9a-f]{40}", args.source_revision), "expected full source revision")
    require(args.target == native_target(), "artifact target does not match native OS/architecture")
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    checksum_line = artifact.with_name(artifact.name + ".sha256").read_text().strip()
    require(checksum_line == f"{checksum}  {artifact.name}", "artifact checksum sidecar mismatch")
    metadata = json.loads(artifact.with_name(artifact.name + ".source.json").read_text())
    require(metadata == {"artifact": artifact.name, "sha256": checksum, "source_revision": args.source_revision, "target": args.target},
            "artifact source/target identity mismatch")
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    (args.evidence_dir / "identity.json").write_text(json.dumps({**metadata, "os": platform.platform(), "machine": platform.machine(),
        "controller": "Python 3 standard library and lsof; excluded from engine runtime dependencies"}, indent=2) + "\n")
    qualify(artifact, args.evidence_dir)
    print(f"Qualified {artifact.name}: actual native server startup, truthful unavailable agent, attributed HTTP, graceful shutdown and cleanup")


if __name__ == "__main__":
    try:
        main()
    except SmokeFailure as error:
        raise SystemExit(f"engine smoke failed: {error}")
