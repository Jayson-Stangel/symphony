"""Exercise actual lifecycle ownership and image-removal controller boundaries."""
import ast
import contextlib
import importlib.util
import io
import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("package_lifecycle", ROOT / "scripts/release/package-lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class LifecycleControllerTest(unittest.TestCase):
    def test_detached_id_uses_stdout_while_pull_progress_is_stderr(self):
        identifier = "a" * 64
        def run(args, **kwargs):
            self.assertEqual(args, ["docker", "run", "-d", "registry:3"])
            self.assertTrue(kwargs["separate_stderr"])
            # Docker emits this on a real first pull before the successful ID.
            return subprocess.CompletedProcess(args, 0, identifier + "\n", "Unable to find image locally\nPull complete\n")
        self.assertEqual(lifecycle.acquire_container(run, "-d", "registry:3"), identifier)

    def test_actual_command_driver_keeps_pull_stderr_out_of_owned_id(self):
        # Execute the actual nested command driver against a real subprocess,
        # including the stderr merge that caused the native cleanup failure.
        tree = ast.parse((ROOT / "scripts/release/package-lifecycle.py").read_text())
        driver = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run")
        namespace = {"subprocess": subprocess, "log": io.StringIO()}
        exec(compile(ast.Module(body=[driver], type_ignores=[]), "actual-lifecycle-driver", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text("#!/bin/sh\necho 'Unable to find image locally; Pull complete' >&2\nprintf '%s\\n' " + "a" * 64 + "\n")
            docker.chmod(0o755)
            with patch.dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"]), contextlib.redirect_stdout(io.StringIO()):
                identifier = lifecycle.acquire_container(namespace["run"], "-d", "registry:3")
            self.assertEqual(identifier, "a" * 64)
            self.assertIn("Pull complete", namespace["log"].getvalue(), "stderr progress was lost from diagnostic evidence")

    def test_invalid_or_duplicate_stdout_never_grants_ownership(self):
        identifier = "a" * 64
        for output in ("", "registry-name\n", "progress\n" + identifier + "\n", identifier + "\n" + identifier + "\n", "b" * 63 + "\n"):
            with self.subTest(output=output), self.assertRaisesRegex(AssertionError, "invalid detached container ID"):
                lifecycle.acquire_container(lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, output, "pull progress"), "-d")

    def test_tag_removal_can_already_remove_immutable_digest(self):
        tag, digest = "localhost:5000/symphony-codex:candidate", "localhost:5000/symphony-codex@sha256:" + "a" * 64
        present = {tag, digest}; removals = []; inspections = []
        def run(args, expected=0):
            operation, reference = args[2], args[3]
            if operation == "inspect":
                inspections.append(reference)
                return subprocess.CompletedProcess(args, 0 if reference in present else 1, "[]" if reference in present else "Error response from daemon: No such image: " + reference)
            self.assertEqual(operation, "rm")
            self.assertIn(reference, present, "attempted redundant removal")
            removals.append(reference); present.clear()
            return subprocess.CompletedProcess(args, 0, "removed")
        lifecycle.remove_local_image(run, tag)
        lifecycle.remove_local_image(run, digest)
        self.assertEqual(removals, [tag])
        self.assertEqual(inspections, [tag, tag, digest, digest])
        self.assertFalse(present)

    def test_image_absence_requires_positive_daemon_response_and_postcheck(self):
        for reply in ((1, "Cannot connect to Docker daemon"), (0, "still present")):
            with self.subTest(reply=reply):
                def run(args, expected=0):
                    return subprocess.CompletedProcess(args, *reply)
                with self.assertRaises(AssertionError):
                    lifecycle.remove_local_image(run, "candidate")

    def test_cleanup_removes_only_exact_acquired_id_and_refuses_replacement(self):
        identifier = "a" * 64
        for observed_id in (identifier, "b" * 64):
            with self.subTest(observed_id=observed_id), tempfile.TemporaryDirectory() as directory:
                calls = []
                def run(args, **kwargs):
                    calls.append(args)
                    if args == ["docker", "inspect", "client"]:
                        return subprocess.CompletedProcess(args, 0, json.dumps([{"Id": observed_id, "Config": {"Labels": {}}}]))
                    if args == ["docker", "rm", "-f", identifier]:
                        return subprocess.CompletedProcess(args, 0, identifier)
                    if args == ["docker", "inspect", identifier]:
                        return subprocess.CompletedProcess(args, 1, "")
                    self.fail("unexpected cleanup mutation: " + repr(args))
                with patch.object(lifecycle.subprocess, "run", side_effect=run):
                    if observed_id == identifier:
                        self.assertEqual(lifecycle.cleanup_container("client", {"client": identifier}, set(), Path(directory)), "client")
                        self.assertIn(["docker", "rm", "-f", identifier], calls)
                    else:
                        with self.assertRaisesRegex(AssertionError, "refused foreign resource"):
                            lifecycle.cleanup_container("client", {"client": identifier}, set(), Path(directory))
                        self.assertEqual(calls, [["docker", "inspect", "client"]])

    def test_cleanup_preserves_unowned_runner_with_foreign_or_missing_label(self):
        for labels in ({"com.symphony.storage": "foreign"}, None):
            with self.subTest(labels=labels), tempfile.TemporaryDirectory() as directory:
                with patch.object(lifecycle.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                    json.dumps([{"Id": "a" * 64, "Config": {"Labels": labels}}]))) as run:
                    with self.assertRaisesRegex(AssertionError, "refused foreign resource"):
                        lifecycle.cleanup_container("runner", {}, {"owned-storage"}, Path(directory))
                    run.assert_called_once()
