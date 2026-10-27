#!/usr/bin/env python3
"""Bound one already-approved, offline-configured Mac runner/VM session.

Run this on the host, obtain the ``armed`` receipt, then start the guest's
``run.sh --once`` under its own deadline. This tool never registers a runner,
starts a VM, selects a workflow, or supplies admission authority. SIGKILL or host
power loss can prevent cleanup: the guest needs an independent total deadline.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import stat
import subprocess
import time
import uuid

REPOSITORY = "Jayson-Stangel/symphony"
PREFIX = "symphony-backup-mac-"


class Refusal(ValueError):
    pass


def private_path(path, directory=False):
    """Reject redirected, foreign-owned, or group/world-accessible state."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise Refusal("private state path must be absolute and contain no symlinks")
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refusal("private state must be owned by this user and inaccessible to others")
    return path


def load_lease(path):
    lease_path = private_path(path)
    lease = json.loads(lease_path.read_text())
    fields = {"schema", "session_id", "tart_home", "tart_binary", "gh_binary", "vm_name",
              "vm_mac", "runner_id", "runner_name", "runner_label", "run_id",
              "run_attempt", "job_id", "head_sha", "source_sha", "consumer_workflow", "job_key",
              "permit_sha256", "expires_at", "acquisition_seconds", "total_seconds"}
    if not isinstance(lease, dict) or set(lease) != fields or type(lease["schema"]) is not int or lease["schema"] != 1:
        raise Refusal("unexpected lease schema")
    try:
        session = str(uuid.UUID(lease["session_id"]))
    except (ValueError, AttributeError, TypeError):
        raise Refusal("session must be a canonical UUID") from None
    if session != lease["session_id"]:
        raise Refusal("session must be a canonical UUID")
    identity = PREFIX + uuid.UUID(session).hex
    if lease["vm_name"] != identity or lease["runner_name"] != identity or lease["runner_label"] != identity:
        raise Refusal("VM, runner and label must bind this session")
    home = private_path(lease["tart_home"], directory=True)
    if home.name != identity or home == Path.home() / ".tart":
        raise Refusal("TART_HOME must be a dedicated session directory")
    protected = {(Path.home() / ".tart").resolve(), (Path.home() / "src" / "symphony").resolve()}
    if os.environ.get("SYMPHONY_HOME"):
        protected.add(Path(os.environ["SYMPHONY_HOME"]).resolve())
    installed = shutil.which("symphony")
    if installed:
        script = Path(installed).resolve()
        if script.parent.name == "scripts":
            protected.add(script.parent.parent)
    if any(root == home or root in home.parents for root in protected):
        raise Refusal("TART_HOME must not use the stable installation or default Tart storage")
    marker = private_path(home / ".symphony-backup-mac-session")
    if marker.read_text().strip() != session:
        raise Refusal("TART_HOME ownership marker differs")
    if not isinstance(lease["vm_mac"], str) or not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", lease["vm_mac"]):
        raise Refusal("VM MAC must be explicit lowercase hexadecimal")
    for field in ("head_sha", "source_sha"):
        if not isinstance(lease[field], str) or not re.fullmatch(r"[0-9a-f]{40}", lease[field]):
            raise Refusal("head/source must be exact commit SHAs")
    if not isinstance(lease["permit_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", lease["permit_sha256"]):
        raise Refusal("permit must have its exact raw-value SHA256")
    expiry_seconds(lease["expires_at"])
    if not isinstance(lease["consumer_workflow"], str) or not re.fullmatch(r"\.github/workflows/[a-z][a-z0-9-]*\.yml", lease["consumer_workflow"]):
        raise Refusal("consumer workflow must be exact")
    if not isinstance(lease["job_key"], str) or not re.fullmatch(r"[a-z][a-z0-9-]*", lease["job_key"]):
        raise Refusal("job key must be exact")
    for field in ("runner_id", "run_id", "run_attempt", "job_id", "acquisition_seconds", "total_seconds"):
        if type(lease[field]) is not int or lease[field] <= 0:
            raise Refusal("IDs and deadlines must be positive integers")
    if not 1 <= lease["acquisition_seconds"] <= 300 or not lease["acquisition_seconds"] <= lease["total_seconds"] <= 3600:
        raise Refusal("deadlines must bound acquisition to 300s and session to 3600s")
    for field in ("tart_binary", "gh_binary"):
        binary = Path(lease[field])
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise Refusal("tool executables must be explicit absolute executable files")
        lease[field] = str(binary.resolve())
    vm = home / "vms" / identity
    private_path(home / "vms", directory=True)
    private_path(vm, directory=True)
    return lease


def expiry_seconds(value):
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        raise Refusal("expiry must be an exact UTC timestamp") from None


class Host:
    """Only fixed Tart operations and bounded GitHub API calls; no shell."""
    def __init__(self, lease):
        self.lease = lease
        self.vm_path = Path(lease["tart_home"]) / "vms" / lease["vm_name"]
        info = self.vm_path.lstat()
        self.vm_inode = (info.st_dev, info.st_ino)

    def command(self, argv, env=None):
        try:
            result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal("bounded tool operation unavailable") from None
        if result.returncode:
            raise Refusal("tool operation failed; inspect diagnostics privately")
        return result.stdout

    def tart(self, *args):
        env = dict(os.environ, TART_HOME=self.lease["tart_home"])
        return self.command([self.lease["tart_binary"], *args], env)

    def api(self, method, path):
        # --include gives a typed HTTP status, including an honest absent resource.
        argv = [self.lease["gh_binary"], "api", "--hostname", "github.com", "--include", "--method", method,
                "repos/" + REPOSITORY + "/" + path]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal("GitHub operation unavailable") from None
        header, separator, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
        status = re.match(r"HTTP/\S+ (\d{3})\b", header)
        if not separator or not status:
            raise Refusal("GitHub HTTP status unverified")
        code = int(status.group(1))
        if code == 404 and method == "GET":
            return None
        if result.returncode or code not in (200, 204):
            raise Refusal("GitHub operation failed; inspect diagnostics privately")
        if code == 204:
            return None
        try:
            return json.loads(body)
        except ValueError:
            raise Refusal("GitHub returned invalid metadata") from None

    def vm(self):
        private_path(self.vm_path, directory=True)
        info = self.vm_path.lstat()
        if (info.st_dev, info.st_ino) != self.vm_inode:
            raise Refusal("VM directory identity changed")
        try:
            data = json.loads(self.tart("get", self.lease["vm_name"], "--format", "json"))
        except ValueError:
            raise Refusal("VM metadata unverified") from None
        if data.get("MACAddress") != self.lease["vm_mac"] or type(data.get("Running")) is not bool:
            raise Refusal("VM identity differs")
        return data

    def owned_runner(self):
        data = self.api("GET", "actions/runners/" + str(self.lease["runner_id"]))
        if data is not None and (data.get("id") != self.lease["runner_id"] or
                                 data.get("name") != self.lease["runner_name"] or
                                 self.lease["runner_label"] not in [item.get("name") for item in data.get("labels", [])]):
            raise Refusal("runner identity differs")
        return data

    def validate_permit(self):
        data = self.api("GET", "actions/variables/SYMPHONY_BACKUP_MAC_PERMIT")
        raw = data.get("value") if isinstance(data, dict) else None
        if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != self.lease["permit_sha256"]:
            raise Refusal("routing permit changed, disappeared or differs")
        try:
            permit = json.loads(raw)
            expected = {"run_id": self.lease["run_id"], "run_attempt": self.lease["run_attempt"],
                        "source_sha": self.lease["source_sha"], "expires_at": self.lease["expires_at"]}
            target = {"consumer_workflow": self.lease["consumer_workflow"],
                      "job_key": self.lease["job_key"], "runner_label": self.lease["runner_label"]}
            if any(permit.get(key) != value for key, value in expected.items()) or permit.get("targets", []).count(target) != 1:
                raise Refusal("routing permit identities differ")
            if (permit.get("pr_head") or permit["source_sha"]) != self.lease["head_sha"]:
                raise Refusal("routing permit provider head differs")
        except (ValueError, KeyError, AttributeError, TypeError):
            raise Refusal("routing permit metadata incomplete") from None
        return permit

    def validate_run(self):
        data = self.api("GET", f"actions/runs/{self.lease['run_id']}/attempts/{self.lease['run_attempt']}")
        if data is None or data.get("id") != self.lease["run_id"] or data.get("run_attempt") != self.lease["run_attempt"] or data.get("head_sha") != self.lease["head_sha"]:
            raise Refusal("run identity differs")

    def job(self):
        data = self.api("GET", "actions/jobs/" + str(self.lease["job_id"]))
        if data is None or any(data.get(field) != self.lease[field] for field in ("run_id", "run_attempt", "head_sha")) or data.get("id") != self.lease["job_id"] or data.get("labels") != [self.lease["runner_label"]]:
            raise Refusal("job identity or selected routing label differs")
        return data

    def stop_vm(self):
        if self.vm()["Running"]:
            self.tart("stop", self.lease["vm_name"], "--timeout", "10")
        if self.vm()["Running"]:
            raise Refusal("VM stop unverified")

    def delete_vm(self):
        if self.vm()["Running"]:
            raise Refusal("refusing to delete running VM")
        self.tart("delete", self.lease["vm_name"])
        if self.vm_path.exists() or self.vm_path.is_symlink():
            raise Refusal("VM deletion unverified")

    def deregister(self):
        if self.owned_runner() is not None:
            self.api("DELETE", "actions/runners/" + str(self.lease["runner_id"]))
        if self.owned_runner() is not None:
            raise Refusal("runner deregistration unverified")


def supervise(lease, host, cancelled=lambda: False, clock=time.monotonic, wall_clock=time.time, pause=time.sleep, emit=lambda _: None):
    """Arm before listening; any observed outcome enters independent cleanup."""
    receipt = {"schema": 1, "session_id": lease["session_id"], "run_id": lease["run_id"],
               "run_attempt": lease["run_attempt"], "job_id": lease["job_id"], "head_sha": lease["head_sha"],
               "runner_id": lease["runner_id"], "vm_name": lease["vm_name"], "state": "initializing",
               "outcome": "unavailable", "job_conclusion": None,
               "vm_stopped": False, "vm_deleted": False, "runner_deregistered": False, "errors": []}
    started = clock()
    expiry = expiry_seconds(lease["expires_at"])
    expiry_deadline = started + expiry - wall_clock()
    def deadline(acquiring=False):
        if wall_clock() >= expiry or clock() >= expiry_deadline:
            return "permit_expired"
        elapsed = clock() - started
        if elapsed >= lease["total_seconds"]:
            return "total_timeout"
        if acquiring and elapsed >= lease["acquisition_seconds"]:
            return "acquisition_timeout"
        return None
    try:
        if not wall_clock() < expiry <= wall_clock() + 7200:
            raise Refusal("permit expired or exceeds the two hour global window")
        host.vm()
        host.validate_run()
        registration = host.owned_runner()
        if registration is None or registration.get("status") != "offline" or registration.get("busy") is not False:
            raise Refusal("registration must exist offline and idle before arming")
        first = host.job()
        if first.get("status") == "completed" or first.get("runner_id") or first.get("runner_name"):
            raise Refusal("job already assigned before watchdog was armed")
        host.validate_permit()
        if cancelled():
            receipt["outcome"] = "cancelled"
            raise Refusal("cancelled before arming")
        expired = deadline(acquiring=True)
        if expired:
            receipt["outcome"] = expired
            raise Refusal("deadline elapsed before arming")
        receipt["state"] = "armed"
        emit(dict(receipt))
        while True:
            if cancelled():
                receipt["outcome"] = "cancelled"
                break
            expired = deadline()
            if expired:
                receipt["outcome"] = expired
                break
            host.validate_permit()
            expired = deadline()
            if expired:
                receipt["outcome"] = expired
                break
            job = host.job()
            runner = job.get("runner_id")
            expired = deadline(acquiring=not runner)
            if expired:
                receipt["outcome"] = expired
                break
            if runner and (runner != lease["runner_id"] or job.get("runner_name") != lease["runner_name"]):
                receipt["outcome"] = "wrong_assignment"
                break
            if job.get("status") == "completed":
                receipt["outcome"] = "completed" if runner else "completed_without_runner"
                receipt["job_conclusion"] = job.get("conclusion")
                break
            registration = host.owned_runner()
            if not runner and (registration is None or registration.get("busy") is not False):
                receipt["outcome"] = "unexpected_runner_activity"
                break
            expired = deadline(acquiring=not runner)
            if expired:
                receipt["outcome"] = expired
                break
            elapsed = clock() - started
            remaining = lease["total_seconds"] - elapsed
            if not runner:
                remaining = min(remaining, lease["acquisition_seconds"] - elapsed)
            pause(max(0, min(5, remaining, expiry_deadline - clock(), expiry - wall_clock())))
    except (Refusal, OSError, ValueError, TypeError, AttributeError):
        receipt["errors"].append("session observation or identity verification failed")
    finally:
        # Each cleanup has its own proof; API failure must not prevent VM stop.
        for action, field in ((host.stop_vm, "vm_stopped"), (host.deregister, "runner_deregistered")):
            try:
                action()
                receipt[field] = True
            except (Refusal, OSError, ValueError, TypeError, AttributeError):
                receipt["errors"].append(field + " unverified")
        if receipt["vm_stopped"]:
            try:
                host.delete_vm()
                receipt["vm_deleted"] = True
            except (Refusal, OSError, ValueError, TypeError, AttributeError):
                receipt["errors"].append("vm_deleted unverified")
        receipt["state"] = "finished"
        receipt["elapsed_seconds"] = round(clock() - started, 3)
        receipt["cleanup_complete"] = all(receipt[field] for field in ("vm_stopped", "vm_deleted", "runner_deregistered"))
        emit(dict(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        lease = load_lease(args.lease)
        receipt_path = Path(args.receipt)
        private_path(receipt_path.parent, directory=True)
        # A new exclusive file prevents clobbering another session's evidence.
        descriptor = os.open(receipt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        interrupted = [False]
        def stop(_signum, _frame):
            interrupted[0] = True
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, stop)
        def emit(receipt):
            temporary = receipt_path.with_name(receipt_path.name + "." + lease["session_id"] + ".tmp")
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(receipt, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, receipt_path)
            print(json.dumps({"state": receipt["state"], "session_id": lease["session_id"]}), flush=True)
        receipt = supervise(lease, Host(lease), cancelled=lambda: interrupted[0], emit=emit)
        return 0 if receipt["outcome"] == "completed" and receipt["job_conclusion"] == "success" and receipt["cleanup_complete"] else 2
    except (Refusal, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"state": "refused", "error": "lease, ownership or receipt validation failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
