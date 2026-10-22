#!/usr/bin/env python3
"""macOS package and host-helper regression checks; no account/model requests."""
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

ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(os.environ.get("SYMPHONY_TEST_HOST_HELPER", "/tmp/symphony-package-test-host"))


def call(args, **kwargs):
    return subprocess.run([str(v) for v in args], text=True, capture_output=True, **kwargs)


def manifest(version="1.2.3", revision=None):
    revision = revision or call(["git", "-C", ROOT, "rev-parse", "HEAD"]).stdout.strip()
    runners = {}
    for provider in ("codex", "claude"):
        runners[provider] = {}
        for arch, platform in (("arm64", "linux/arm64"), ("x86_64", "linux/amd64")):
            runners[provider][arch] = dict(platform=platform, image=f"ghcr.io/test/{provider}@sha256:" + "b" * 64,
                source_revision=revision, tool_versions={tool: "1.0" for tool in (provider, "git", "node", "playwright")})
    return dict(schema_version=1, version=version, source_revision=revision, channel="candidate",
        host_architectures=["arm64", "x86_64"], runners=runners, bundle=dict(filename=f"symphony-{version}-macos.tar.gz"))


@unittest.skipUnless(sys.platform == "darwin", "native installed-macOS helper requires macOS")
class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HELPER.is_file():
            result = call([ROOT / "scripts/release/build-host-helper.sh", HELPER, "--native"])
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symphony-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / "release.json"
        self.file.write_text(json.dumps(manifest()))

    def helper(self, *args, **kwargs):
        return call([HELPER, *args], **kwargs)

    def assertOK(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def bundle(self):
        output = self.root / "output"
        self.assertOK(call([ROOT / "scripts/release/package.sh", self.file, output, HELPER]))
        archive = output / manifest()["bundle"]["filename"]
        extracted = self.root / "download"
        extracted.mkdir()
        self.assertOK(call(["tar", "-xzf", archive, "-C", extracted]))
        return extracted

    def environment(self):
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        # Poison optional host runtimes; installed code must never execute these.
        for tool in ("git", "python3", "node", "mise", "elixir", "erl", "swiftc", "codex", "claude"):
            (tools / tool).write_text(f"#!/bin/sh\necho 'unexpected host dependency: {tool}' >&2\nexit 98\n")
            (tools / tool).chmod(0o755)
        docker = tools / "docker"
        docker.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$TEST_DOCKER_CALLS\"\ncase \"$1\" in info) exit 0 ;; image) exit 1 ;; pull) exit 29 ;; volume) exit 1 ;; *) exit 97 ;; esac\n")
        docker.chmod(0o755)
        return dict(os.environ, HOME=str(self.root / "home"), PATH=f"{tools}:/usr/bin:/bin",
            SYMPHONY_HOME="", SYMPHONY_APP_HOME=str(self.root / "app"),
            SYMPHONY_CONFIG_HOME=str(self.root / "config"), SYMPHONY_BIN_DIR=str(self.root / "bin"),
            SYMPHONY_SKILLS_DIR=str(self.root / "skills"), SYMPHONY_RUNNER_NAMESPACE="package-fixture",
            SYMPHONY_CONTAINER_RUNTIME=str(docker), TEST_DOCKER_CALLS=str(self.root / "docker.calls"))

    def test_manifest_validation_and_typed_json(self):
        self.assertOK(self.helper("validate-manifest", self.file))
        for change in [lambda m: m.update(version="1.2.3\n"), lambda m: m.update(schema_version=True), lambda m: m.update(schema_version=1.0),
                lambda m: m["runners"]["codex"]["arm64"].update(image="mutable:latest"),
                lambda m: m["runners"]["claude"]["x86_64"].pop("tool_versions"),
                lambda m: m["bundle"].update(filename="other.tar.gz"),
                lambda m: m["runners"].update(other={})]:
            value = copy.deepcopy(manifest()); change(value); self.file.write_text(json.dumps(value))
            self.assertNotEqual(self.helper("validate-manifest", self.file).returncode, 0)
        self.assertNotEqual(self.helper("get").returncode, 0)
        self.assertEqual(self.helper("json", "idle", input='{"agent":{"draining":true},"counts":{"running":false}}').returncode, 1)
        self.assertEqual(self.helper("json", "idle", input='{"agent":{"draining":true},"counts":{"running":0}}').returncode, 0)
        self.assertEqual(self.helper("json", "admission", input='{"draining":false,"admission":{"ready":false,"reason":"quota_exhausted","retry_at":3}}').stdout.strip(), "quota_paused")

    def test_git_identity_and_linked_worktree_match_git(self):
        repo = self.root / "project with space"; repo.mkdir()
        self.assertOK(call(["git", "init", "-q", repo]))
        remote = 'https://github.com/owner/example.git'
        self.assertOK(call(["git", "-C", repo, "remote", "add", "origin", remote]))
        self.assertEqual(self.assertOK(self.helper("repository-root", repo)), str(repo))
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo)), remote)
        self.assertEqual(self.assertOK(self.helper("hash-blob", remote)), self.assertOK(call(["git", "hash-object", "--stdin"], input=remote)))
        self.assertOK(call(["git", "-C", repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "Fixture"]))
        worktree = self.root / "linked"
        self.assertOK(call(["git", "-C", repo, "worktree", "add", "-qb", "fixture", worktree]))
        self.assertEqual(self.assertOK(self.helper("repository-remote", worktree)), remote)
        with (repo / ".git/config").open("a") as config:
            config.write('\n[remote "origin"]\nurl = "https://github.com/owner/encoded%20path.git" # comment\n')
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo)), self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"])))
        with (repo / ".git/config").open("a") as config:
            config.write('\n[include]\npath = ../other\n')
        self.assertNotEqual(self.helper("repository-remote", repo).returncode, 0)

    def test_flock_matches_python_owner_and_exclusion(self):
        lock = self.root / "lock"
        script = '''exec 8>>"$1"; "$2" lock "$1" 8 acquire; "$2" lock "$1" 8 inherited; python3 - "$1" <<'PY'
import fcntl,sys
try:
 fcntl.flock(open(sys.argv[1],'a'), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
 sys.exit(0)
sys.exit(1)
PY
'''
        self.assertOK(call(["bash", "-e", "-c", script, "fixture", lock, HELPER]))
        self.assertNotEqual(call(["bash", "-c", 'exec 8>>"$1"; "$2" lock "$1" 8 inherited', "fixture", lock, HELPER]).returncode, 0)

    def test_install_twice_source_free_init_doctor_and_failed_pull(self):
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        self.assertOK(call([bundle / "install.sh"], env=env))
        shutil.rmtree(bundle)
        self.assertIn('"installation":"packaged"', self.assertOK(call([command, "--version"], env=env)))
        self.assertIn('"ready":true', self.assertOK(call([command, "doctor", "--json"], env=env)))
        repo = self.root / "project"; repo.mkdir(); (repo / ".git").mkdir()
        (repo / ".git/config").write_text('[remote "origin"]\nurl = https://github.com/fixture/project.git\n')
        self.assertIn("Project: initialized", self.assertOK(call([command, "init", "--linear-project", "disposable-fixture"], cwd=repo, env=env)))
        original = (repo / "WORKFLOW.md").read_text()
        self.assertOK(call([command, "init", "--linear-project", "preserve"], cwd=repo, env=env))
        self.assertEqual((repo / "WORKFLOW.md").read_text(), original)
        failed = call([command, "auth", "codex", "login"], cwd=repo, env=env)
        self.assertNotEqual(failed.returncode, 0); self.assertIn("cannot pull released codex image", failed.stderr)
        self.assertNotIn("build", (self.root / "docker.calls").read_text())
        self.assertOK(call([command, "--version"], env=env))

    def test_conflict_and_corrupt_install_do_not_activate(self):
        bundle = self.bundle(); env = self.environment()
        command = self.root / "bin/symphony"; command.parent.mkdir(); command.write_text("owned")
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0); self.assertEqual(command.read_text(), "owned")
        self.assertFalse((self.root / "app").exists())
        command.unlink()
        canary = self.root / "payload-ran"
        (bundle / "scripts/symphony-package.sh").write_text(f"touch {canary}\n")
        # Installer verifies shell payload before sourcing the manager library.
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "app/current").exists())
        self.assertFalse(canary.exists())
        self.assertIn("bundle checksum mismatch", result.stderr)

    def test_custom_app_dir_does_not_require_environment_override(self):
        bundle = self.bundle(); env = self.environment()
        env.pop("SYMPHONY_APP_HOME")
        app = self.root / "custom-app"
        self.assertOK(call([bundle / "install.sh", "--app-dir", app], env=env))
        self.assertOK(call([bundle / "install.sh", "--app-dir", app], env=env))
        shutil.rmtree(bundle)
        command = self.root / "bin/symphony"
        self.assertIn('"ready":true', self.assertOK(call([command, "doctor", "--json"], env=env)))
        self.assertOK(call([command, "--version"], env=env))

    def test_stage_only_does_not_activate_and_arch_failure_is_clear(self):
        bundle = self.bundle(); env = self.environment()
        path = self.assertOK(call([bundle / "install.sh", "--stage-only"], env=env))
        self.assertTrue(Path(path).is_dir()); self.assertFalse((self.root / "app/current").exists())
        uname = self.root / "tools/uname"; uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin ;; *) echo unsupported ;; esac\n'); uname.chmod(0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0); self.assertIn("unsupported host architecture", result.stderr)


if __name__ == "__main__":
    unittest.main()
