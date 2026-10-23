#!/usr/bin/env python3
"""Local proof harness failure ownership tests; no Docker daemon operations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("lifecycle", Path(__file__).with_name("test-zero-rebuild-lifecycle.py"))
lifecycle = importlib.util.module_from_spec(spec); spec.loader.exec_module(lifecycle)


class OwnershipTests(unittest.TestCase):
    def test_uncertain_volume_acquisition_retains_intent_and_owned_cleanup(self):
        pending, objects, calls = set(), {}, []
        def docker(*values, **kwargs):
            calls.append(values)
            if values[:2] == ('volume','create'):
                objects['owned-name'] = {'Name':'owned-name','Labels':{lifecycle.LABEL:'token'}}
                raise subprocess.TimeoutExpired(['docker'],1)
            if values[:2] == ('volume','rm'):
                objects.pop(values[-1]);return subprocess.CompletedProcess([],0,'','')
            if values[-1] in objects:return subprocess.CompletedProcess([],0,json.dumps([objects[values[-1]]]),'')
            return subprocess.CompletedProcess([],1,'','Error: No such volume')
        with self.assertRaises(subprocess.TimeoutExpired):
            lifecycle.acquire_owned_volume(docker,'owned-name','token',pending)
        self.assertEqual(pending, {'owned-name'})
        lifecycle.cleanup_owned_volume(docker,'owned-name','token')
        self.assertFalse(objects)
        self.assertEqual(calls[-1], ('volume','inspect','owned-name'))

    def test_foreign_volume_replacement_and_uncertain_daemon_are_preserved(self):
        for receipt in (subprocess.CompletedProcess([],0,json.dumps([{'Name':'owned-name','Labels':{lifecycle.LABEL:'foreign'}}]),''),
                        subprocess.CompletedProcess([],1,'','daemon unavailable')):
            calls = []
            def docker(*values, **kwargs):calls.append(values);return receipt
            with self.assertRaises(AssertionError):lifecycle.cleanup_owned_volume(docker,'owned-name','token')
            self.assertEqual(calls, [('volume','inspect','owned-name')])

    def test_only_explicitly_approved_local_socket_is_accepted(self):
        lifecycle.require_local_daemon('unix:///Users/jayson/.docker/run/docker.sock')
        for value in ('tcp://127.0.0.1:2375','ssh://example','unix:///tmp/other.sock','default'):
            with self.assertRaises(ValueError):lifecycle.require_local_daemon(value)


class ProjectTransportTests(unittest.TestCase):
    def test_placeholder_passes_actual_product_loader_without_host_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp); (config / "projects").mkdir()
            path = config / "projects/owned.env"
            path.write_text("# initialized fixture\nLINEAR_API_KEY=\nGH_TOKEN=\n")
            source = (Path(__file__).parents[1] / "symphony").read_text()
            loader = next(line.strip() for line in source.splitlines() if line.strip().startswith("git_token=$(unset GH_TOKEN GITHUB_TOKEN;"))
            guard = next(line.strip() for line in source.splitlines() if "[ -n \"$git_token\" ] || die 'missing GITHUB_TOKEN" in line)
            command = 'die() { echo "$*" >&2; exit 1; }; project_env=$1; ' + loader + '; ' + guard + '; printf "%s" "$git_token"'
            def invoke():
                return subprocess.run(["/bin/bash", "-euc", command, "fixture", str(path)],
                    env={"GH_TOKEN":"host-must-not-be-used", "GITHUB_TOKEN":"host-must-not-be-used"}, text=True, capture_output=True)
            self.assertNotEqual(invoke().returncode, 0)
            lifecycle.seed_project_transport(config, "owned")
            result = invoke(); self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "credential-free-local-memory-placeholder")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): lifecycle.seed_project_transport(config, "owned")

    def test_foreign_nonempty_symlink_and_malformed_project_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp); (config / "projects").mkdir()
            path = config / "projects/owned.env"
            for content in ("LINEAR_API_KEY=foreign\nGH_TOKEN=\n", "LINEAR_API_KEY=\nGH_TOKEN=foreign\n", "OTHER=\n"):
                path.write_text(content)
                with self.assertRaises(ValueError): lifecycle.seed_project_transport(config, "owned")
                self.assertEqual(path.read_text(), content)
            path.unlink(); foreign = config / "foreign"; foreign.write_text("LINEAR_API_KEY=\nGH_TOKEN=\n"); path.symlink_to(foreign)
            with self.assertRaises(ValueError): lifecycle.seed_project_transport(config, "owned")
            self.assertEqual(foreign.read_text(), "LINEAR_API_KEY=\nGH_TOKEN=\n")
            with self.assertRaises(ValueError): lifecycle.seed_project_transport(config, "../foreign")


class AuthFixtureTests(unittest.TestCase):
    def setUp(self):
        self.cwd = "/private/tmp/owned-project"
        self.storage = "symphony-owned-project"
        self.profile = "a" * 64
        self.policy = {"projects": {self.cwd: {"storage": self.storage, "profile": self.profile}},
            "images": {"codex": ["codex@sha256:" + "b" * 64], "claude": ["claude@sha256:" + "c" * 64]},
            "codex_volume": "symphony-owned-codex-auth"}

    def claude(self, action="check"):
        return ["run", "--rm", *(["--interactive", "--tty"] if action == "login" else []),
            "--name", self.storage + "-claude-auth", "--label", "com.symphony.storage=" + self.storage,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
            "--volume", self.storage + "-claude-auth:/home/claude", "--entrypoint", "/usr/local/bin/node",
            self.policy["images"]["claude"][0], "/opt/symphony/claude/auth.mjs", action]

    def test_exact_login_check_and_actual_product_profile_parser(self):
        output = lifecycle.auth_fixture(self.claude(), self.policy, self.cwd)
        self.assertEqual(json.loads(output), {"ready": True, "profile": self.profile})
        self.assertIn("credential-free", lifecycle.auth_fixture(self.claude("login"), self.policy, self.cwd))
        script = Path(__file__).parents[1] / "symphony"
        result = subprocess.run(["/bin/bash", "-euc", 'source "$1" --help >/dev/null; host_json profile', "fixture", str(script)],
            input=output, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.profile)

    def test_foreign_scope_image_storage_and_malformed_auth_refuse(self):
        for changed in (self.claude() + ["extra"], self.claude("logout"),
                        [x.replace(self.storage, "foreign") for x in self.claude()],
                        [x.replace("claude@", "foreign@") for x in self.claude()]):
            with self.assertRaises(ValueError): lifecycle.auth_fixture(changed, self.policy, self.cwd)
        with self.assertRaises(ValueError): lifecycle.auth_fixture(self.claude(), self.policy, "/private/tmp/foreign")
        self.policy["projects"][self.cwd]["profile"] = True
        with self.assertRaises((ValueError, TypeError)): lifecycle.auth_fixture(self.claude(), self.policy, self.cwd)

    def test_codex_exact_owned_volume_and_no_real_auth_fallback(self):
        args = ["run", "--rm", "--volume", self.policy["codex_volume"] + ":/root/.codex", "--entrypoint", "codex", self.policy["images"]["codex"][0], "login", "status"]
        self.assertIn("credential-free", lifecycle.auth_fixture(args, self.policy, self.cwd))
        for bad in (args + ["--network"], [x.replace(self.policy["codex_volume"], "foreign") for x in args], ["run", "--entrypoint", "codex", "foreign", "login"]):
            with self.assertRaises(ValueError): lifecycle.auth_fixture(bad, self.policy, self.cwd)
        self.assertIsNone(lifecycle.auth_fixture(["image", "inspect", "sha256:abc"], self.policy, self.cwd))

    def test_preexisting_codex_volume_is_preserved_even_if_looks_owned(self):
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess([], 0, json.dumps([{"Name":"owned", "Labels":{lifecycle.LABEL:"token"}}]), "")
        pending = set()
        with self.assertRaises(AssertionError): lifecycle.acquire_owned_volume(docker, "owned", "token", pending)
        self.assertEqual(calls, [("volume", "inspect", "owned")]); self.assertEqual(pending, set())

    def test_storage_mismatch_never_removes_volume(self):
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess([], 0, json.dumps([{"Name":"owned", "Labels":{lifecycle.LABEL:"token", "com.symphony.storage":"foreign"}}]), "")
        with self.assertRaises(AssertionError): lifecycle.cleanup_owned_volume(docker, "owned", "token", "expected")
        self.assertEqual(calls, [("volume", "inspect", "owned")])

    def test_dual_owned_volume_created_and_verified(self):
        calls = []; value = None
        def docker(*args, **kwargs):
            nonlocal value
            calls.append(args)
            if args[:2] == ("volume", "create"):
                value = {"Name":"owned", "Labels":{lifecycle.LABEL:"token", "com.symphony.storage":"storage"}}
                return subprocess.CompletedProcess([], 0, "owned", "")
            return subprocess.CompletedProcess([], 1, "", "No such volume") if value is None else subprocess.CompletedProcess([], 0, json.dumps([value]), "")
        pending = set(); lifecycle.acquire_owned_volume(docker, "owned", "token", pending, "storage")
        self.assertIn("com.symphony.storage=storage", calls[1]); self.assertEqual(pending, {"owned"})


if __name__ == '__main__':unittest.main()
