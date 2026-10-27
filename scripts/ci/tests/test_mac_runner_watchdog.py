import importlib.util
import datetime
import json
import itertools
import hashlib
import sys
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

MODULE = Path(__file__).resolve().parents[1] / "mac_runner_watchdog.py"
spec = importlib.util.spec_from_file_location("mac_runner_watchdog", MODULE)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def lease():
    session = str(uuid.uuid4())
    identity = watchdog.PREFIX + uuid.UUID(session).hex
    return {"schema": 1, "session_id": session, "tart_home": "/private/tmp/" + identity,
            "tart_binary": "/usr/bin/true", "gh_binary": "/usr/bin/true",
            "vm_name": identity, "vm_mac": "aa:bb:cc:dd:ee:ff", "runner_id": 11,
            "runner_name": identity, "runner_label": identity, "run_id": 22,
            "run_attempt": 1, "job_id": 33, "head_sha": "a" * 40, "source_sha": "b" * 40,
            "permit_sha256": "c" * 64, "consumer_workflow": ".github/workflows/make-all.yml",
            "job_key": "platform-contracts",
            "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "acquisition_seconds": 3, "total_seconds": 6}


def job(value, status="queued", assigned=False, **extra):
    return dict(id=value["job_id"], run_id=value["run_id"], run_attempt=value["run_attempt"],
                head_sha=value["head_sha"], labels=[value["runner_label"]], status=status,
                runner_id=value["runner_id"] if assigned else None,
                runner_name=value["runner_name"] if assigned else None,
                conclusion="success" if status == "completed" else None, **extra)


class FakeHost:
    def __init__(self, value, jobs):
        self.value = value
        self.jobs = iter(jobs)
        self.actions = []
        self.fail = set()
    def vm(self):
        if "vm" in self.fail:
            raise watchdog.Refusal("not owned")
        return {"Running": True}
    def validate_permit(self):
        if "permit" in self.fail:
            raise watchdog.Refusal("permit changed")
    def validate_run(self):
        if "run" in self.fail:
            raise watchdog.Refusal("wrong source")
    def owned_runner(self):
        return {"status": "offline", "busy": False}
    def job(self):
        if "job" in self.fail:
            raise watchdog.Refusal("provider unavailable")
        return next(self.jobs)
    def stop_vm(self):
        self.actions.append("stop")
        if "stop" in self.fail:
            raise watchdog.Refusal("not verified")
    def deregister(self):
        self.actions.append("deregister")
        if "deregister" in self.fail:
            raise watchdog.Refusal("provider unavailable")
    def delete_vm(self):
        self.actions.append("delete")
        if "delete" in self.fail:
            raise watchdog.Refusal("not verified")


class WatchdogTests(unittest.TestCase):
    def run_session(self, host, cancelled=lambda: False):
        ticks = [0]
        def pause(seconds):
            ticks[0] += seconds
        emitted = []
        result = watchdog.supervise(host.value, host, cancelled=cancelled,
                                    clock=lambda: ticks[0], pause=pause, emit=emitted.append)
        return result, emitted

    def test_arms_before_listener_and_proves_all_cleanup(self):
        value = lease()
        host = FakeHost(value, [job(value), job(value, "in_progress", True), job(value, "completed", True)])
        result, emitted = self.run_session(host)
        self.assertEqual(emitted[0]["state"], "armed")
        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(host.actions, ["stop", "deregister", "delete"])

    def test_already_assigned_job_never_arms(self):
        value = lease()
        host = FakeHost(value, [job(value, "in_progress", True)])
        result, emitted = self.run_session(host)
        self.assertNotIn("armed", [item["state"] for item in emitted])
        self.assertEqual(result["outcome"], "unavailable")
        self.assertTrue(result["cleanup_complete"])

    def test_wrong_assignment_cleans_only_own_resources(self):
        value = lease()
        foreign = job(value, "in_progress", True)
        foreign["runner_id"] = 999
        host = FakeHost(value, [job(value), foreign])
        result, _ = self.run_session(host)
        self.assertEqual(result["outcome"], "wrong_assignment")
        self.assertTrue(result["cleanup_complete"])

    def test_busy_runner_without_selected_job_assignment_is_refused(self):
        value = lease()
        host = FakeHost(value, [job(value), job(value)])
        calls = [0]
        def registration():
            calls[0] += 1
            return {"status": "offline" if calls[0] == 1 else "online", "busy": calls[0] != 1}
        host.owned_runner = registration
        result, _ = self.run_session(host)
        self.assertEqual(result["outcome"], "unexpected_runner_activity")
        self.assertTrue(result["cleanup_complete"])

    def test_acquisition_timeout_is_finite(self):
        value = lease()
        host = FakeHost(value, [job(value) for _ in range(10)])
        result, _ = self.run_session(host)
        self.assertEqual(result["outcome"], "acquisition_timeout")
        self.assertEqual(result["elapsed_seconds"], 3)
        self.assertTrue(result["cleanup_complete"])

    def test_total_timeout_cleans_assigned_job(self):
        value = lease()
        host = FakeHost(value, [job(value)] + [job(value, "in_progress", True) for _ in range(10)])
        result, _ = self.run_session(host)
        self.assertEqual(result["outcome"], "total_timeout")
        self.assertEqual(result["elapsed_seconds"], 6)
        self.assertTrue(result["cleanup_complete"])

    def test_cancellation_cleans_guest_and_registration(self):
        value = lease()
        result, emitted = self.run_session(FakeHost(value, [job(value)]), cancelled=lambda: True)
        self.assertEqual(result["outcome"], "cancelled")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual([item["state"] for item in emitted], ["finished"])

    def test_api_failure_does_not_prevent_vm_cleanup(self):
        value = lease()
        host = FakeHost(value, [job(value)])
        host.fail = {"job", "deregister"}
        result, _ = self.run_session(host)
        self.assertTrue(result["vm_stopped"])
        self.assertTrue(result["vm_deleted"])
        self.assertFalse(result["runner_deregistered"])
        self.assertFalse(result["cleanup_complete"])

    def test_unverified_stop_prevents_deletion(self):
        value = lease()
        host = FakeHost(value, [job(value)])
        host.fail = {"stop"}
        result, _ = self.run_session(host, cancelled=lambda: True)
        self.assertNotIn("delete", host.actions)
        self.assertFalse(result["vm_deleted"])
        self.assertTrue(result["runner_deregistered"])

    def test_runner_identity_mismatch_never_deletes_foreign_registration(self):
        value = lease()
        host = object.__new__(watchdog.Host)
        host.lease = value
        reads = []
        def api(method, path):
            reads.append(method)
            return {"id": value["runner_id"], "name": "foreign", "labels": []}
        host.api = api
        with self.assertRaises(watchdog.Refusal):
            host.deregister()
        self.assertEqual(reads, ["GET"])

    def test_vm_identity_mismatch_never_stops_or_deletes_foreign_vm(self):
        value = lease()
        host = object.__new__(watchdog.Host)
        host.lease = value
        calls = []
        def vm():
            raise watchdog.Refusal("MAC mismatch")
        host.vm = vm
        host.tart = lambda *args: calls.append(args)
        for operation in (host.stop_vm, host.delete_vm):
            with self.assertRaises(watchdog.Refusal):
                operation()
        self.assertEqual(calls, [])

    def test_job_identity_mismatch_refuses(self):
        value = lease()
        host = object.__new__(watchdog.Host)
        host.lease = value
        wrong = job(value)
        wrong["run_attempt"] = 2
        host.api = lambda *_: wrong
        with self.assertRaises(watchdog.Refusal):
            host.job()

    def test_other_target_job_from_same_run_cannot_arm_lease(self):
        value = lease()
        host = object.__new__(watchdog.Host)
        host.lease = value
        wrong = job(value)
        wrong["labels"] = [watchdog.PREFIX + "0" * 32]
        host.api = lambda *_: wrong
        with self.assertRaises(watchdog.Refusal):
            host.job()

    def test_typed_404_counts_absent_but_unavailable_api_does_not(self):
        host = object.__new__(watchdog.Host)
        host.lease = lease()
        for response, absent in ((subprocess.CompletedProcess([], 1, "HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n{}", ""), True),
                                 (subprocess.CompletedProcess([], 1, "", "network failure"), False)):
            with patch.object(watchdog.subprocess, "run", return_value=response):
                if absent:
                    self.assertIsNone(host.api("GET", "actions/runners/11"))
                else:
                    with self.assertRaises(watchdog.Refusal):
                        host.api("GET", "actions/runners/11")

    def test_absolute_expiry_destroys_still_queued_session(self):
        value = lease()
        value["expires_at"] = "2026-10-27T16:00:02Z"
        host = FakeHost(value, [job(value) for _ in range(10)])
        ticks = [0]
        def pause(seconds):
            ticks[0] += seconds
        start = watchdog.expiry_seconds("2026-10-27T16:00:00Z")
        result = watchdog.supervise(value, host, clock=lambda: ticks[0],
                                    wall_clock=lambda: start + ticks[0], pause=pause)
        self.assertEqual(result["outcome"], "permit_expired")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(result["elapsed_seconds"], 2)

    def test_slow_initial_provider_reads_cannot_arm_after_deadline(self):
        value = lease()
        host = FakeHost(value, [job(value)])
        ticks = [0]
        emitted = []
        def slow_permit():
            ticks[0] = 10
        host.validate_permit = slow_permit
        result = watchdog.supervise(value, host, clock=lambda: ticks[0], emit=emitted.append)
        self.assertEqual(result["outcome"], "total_timeout")
        self.assertNotIn("armed", [item["state"] for item in emitted])
        self.assertTrue(result["cleanup_complete"])

    def test_slow_job_read_refreshes_deadline_before_accepting_completion(self):
        value = lease()
        host = FakeHost(value, [])
        ticks = [0]
        calls = [0]
        def slow_job():
            calls[0] += 1
            if calls[0] == 1:
                return job(value)
            ticks[0] = 10
            return job(value, "completed", True)
        host.job = slow_job
        result = watchdog.supervise(value, host, clock=lambda: ticks[0])
        self.assertEqual(result["outcome"], "total_timeout")
        self.assertIsNone(result["job_conclusion"])
        self.assertTrue(result["cleanup_complete"])

    def test_cancellation_after_listener_assignment_cleans_session(self):
        value = lease()
        host = FakeHost(value, [job(value), job(value, "in_progress", True)])
        ticks = [0]
        emitted = []
        def pause(seconds):
            ticks[0] += seconds
        result = watchdog.supervise(value, host, clock=lambda: ticks[0], pause=pause,
                                    cancelled=lambda: ticks[0] > 0, emit=emitted.append)
        self.assertEqual(result["outcome"], "cancelled")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual([item["state"] for item in emitted], ["armed", "finished"])

    def test_wall_clock_rollback_cannot_extend_permit_deadline(self):
        value = lease()
        value["expires_at"] = "2026-10-27T16:00:02Z"
        host = FakeHost(value, [job(value) for _ in range(10)])
        ticks = [0]
        start = watchdog.expiry_seconds("2026-10-27T16:00:00Z")
        def pause(seconds):
            ticks[0] += seconds
        result = watchdog.supervise(value, host, clock=lambda: ticks[0],
                                    wall_clock=lambda: start - ticks[0], pause=pause)
        self.assertEqual(result["outcome"], "permit_expired")
        self.assertEqual(result["elapsed_seconds"], 2)
        self.assertTrue(result["cleanup_complete"])

    def test_changed_permit_triggers_cleanup_after_armed(self):
        value = lease()
        host = FakeHost(value, [job(value)])
        def observe(receipt):
            if receipt["state"] == "armed":
                host.fail.add("permit")
        result = watchdog.supervise(value, host, emit=observe)
        self.assertEqual(result["outcome"], "unavailable")
        self.assertTrue(result["cleanup_complete"])

    def test_permit_readback_binds_exact_raw_value_and_target(self):
        value = lease()
        host = object.__new__(watchdog.Host)
        host.lease = value
        permit = {"run_id": value["run_id"], "run_attempt": value["run_attempt"],
                  "source_sha": value["source_sha"], "pr_head": value["head_sha"],
                  "expires_at": value["expires_at"], "targets": [{
                      "consumer_workflow": value["consumer_workflow"], "job_key": value["job_key"],
                      "runner_label": value["runner_label"]}]}
        raw = json.dumps(permit)
        value["permit_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        host.api = lambda *_: {"value": raw}
        self.assertEqual(host.validate_permit(), permit)
        host.api = lambda *_: {"value": raw + " "}
        with self.assertRaises(watchdog.Refusal):
            host.validate_permit()
        host.api = lambda *_: None
        with self.assertRaises(watchdog.Refusal):
            host.validate_permit()

    def test_expired_permit_never_arms(self):
        value = lease()
        value["expires_at"] = "2020-01-01T00:00:00Z"
        host = FakeHost(value, [job(value)])
        result, emitted = self.run_session(host)
        self.assertNotIn("armed", [item["state"] for item in emitted])
        self.assertEqual(result["outcome"], "unavailable")
        self.assertTrue(result["cleanup_complete"])

    def test_ninety_minute_global_permit_keeps_guest_bounded_to_one_hour(self):
        sys.path.insert(0, str(MODULE.parent))
        try:
            import select_mac_runner
            now = datetime.datetime(2026, 10, 27, 16, 0, tzinfo=datetime.timezone.utc)
            value = lease()
            value["expires_at"] = (now + datetime.timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
            value["head_sha"] = value["source_sha"]
            value["consumer_workflow"] = ".github/workflows/native-manager-helpers.yml"
            value["job_key"] = "helper"
            value["total_seconds"] = 3600
            controller = ".github/workflows/runner-release.yml"
            ref = "refs/heads/codex/jay-763-mac-actions-fallback"
            workflow_ref = select_mac_runner.REPOSITORY + "/" + controller + "@" + ref
            permit = {"schema": 1, "repository": select_mac_runner.REPOSITORY,
                      "event_name": "workflow_dispatch", "controller_workflow": controller,
                      "workflow_ref": workflow_ref, "workflow_sha": value["source_sha"],
                      "run_id": value["run_id"], "run_attempt": value["run_attempt"],
                      "pr_number": None, "pr_head": None, "base_sha": None,
                      "source_sha": value["source_sha"], "actor": select_mac_runner.OWNER,
                      "triggering_actor": select_mac_runner.OWNER, "pr_author": None,
                      "expires_at": value["expires_at"], "targets": [{
                          "consumer_workflow": value["consumer_workflow"], "job_key": value["job_key"],
                          "runner_label": value["runner_label"]}]}
            context = {"GITHUB_EVENT_NAME": permit["event_name"], "GITHUB_REPOSITORY": permit["repository"],
                       "GITHUB_RUN_ID": str(value["run_id"]), "GITHUB_RUN_ATTEMPT": str(value["run_attempt"]),
                       "GITHUB_WORKFLOW_REF": workflow_ref, "GITHUB_WORKFLOW_SHA": value["source_sha"],
                       "GITHUB_SHA": value["source_sha"], "GITHUB_ACTOR": select_mac_runner.OWNER,
                       "GITHUB_TRIGGERING_ACTOR": select_mac_runner.OWNER, "GITHUB_REF": ref}
            raw = json.dumps(permit)
            value["permit_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
            selected = select_mac_runner.select_runner(raw, context, {}, value["consumer_workflow"],
                                                       value["job_key"], now=now, verify=lambda _: None)
            self.assertEqual(selected, value["runner_label"])
            jobs = itertools.chain([job(value)], itertools.repeat(job(value, "in_progress", True)))
            host = FakeHost(value, jobs)
            ticks = [0]
            emitted = []
            def pause(seconds):
                ticks[0] += seconds
            result = watchdog.supervise(value, host, clock=lambda: ticks[0],
                                        wall_clock=lambda: now.timestamp() + ticks[0],
                                        pause=pause, emit=emitted.append)
            self.assertEqual(emitted[0]["state"], "armed")
            self.assertEqual(result["outcome"], "total_timeout")
            self.assertEqual(result["elapsed_seconds"], 3600)
            self.assertTrue(result["cleanup_complete"])
        finally:
            sys.path.pop(0)

    def test_selector_and_watchdog_share_exact_label_format(self):
        # This checks the cross-component contract, not a copied regular expression.
        sys.path.insert(0, str(MODULE.parent))
        try:
            import select_mac_runner
            value = lease()
            self.assertIsNotNone(select_mac_runner.LABEL.fullmatch(value["runner_label"]))
            self.assertEqual(value["vm_name"], value["runner_label"])
            self.assertEqual(value["runner_name"], value["runner_label"])
        finally:
            sys.path.pop(0)

    def test_private_lease_requires_dedicated_owned_home_marker(self):
        value = lease()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            home = root / value["vm_name"]
            home.mkdir(mode=0o700)
            (home / "vms").mkdir(mode=0o700)
            (home / "vms" / value["vm_name"]).mkdir(mode=0o700)
            marker = home / ".symphony-backup-mac-session"
            marker.write_text(value["session_id"])
            marker.chmod(0o600)
            value["tart_home"] = str(home)
            lease_path = root / "lease.json"
            lease_path.write_text(json.dumps(value))
            lease_path.chmod(0o600)
            self.assertEqual(watchdog.load_lease(lease_path)["session_id"], value["session_id"])
            marker.write_text(str(uuid.uuid4()))
            with self.assertRaises(watchdog.Refusal):
                watchdog.load_lease(lease_path)
            marker.write_text(value["session_id"])
            home.chmod(0o755)
            with self.assertRaises(watchdog.Refusal):
                watchdog.load_lease(lease_path)


if __name__ == "__main__":
    unittest.main()
