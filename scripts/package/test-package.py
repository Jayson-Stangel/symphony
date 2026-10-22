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
        original = (repo / ".git/config").read_text()
        for extra in ('\n[extensions]\nworktreeConfig = true\n', '\n[url "ssh://git@example.invalid/"]\ninsteadOf = https://github.com/\n'):
            (repo / ".git/config").write_text(original + extra)
            self.assertNotEqual(self.helper("repository-remote", repo).returncode, 0)
        (repo / ".git/config").write_text(original)
        # A symlinked Git directory still resolves the same explicit origin.
        moved = repo / "git-metadata"; (repo / ".git").rename(moved); (repo / ".git").symlink_to(moved)
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
        self.assertOK(call([bundle / "install.sh"], env=env))
        canary = self.root / "payload-ran"
        (bundle / "scripts/symphony-package.sh").write_text(f"touch {canary}\n")
        # Installer verifies shell payload before sourcing the manager library.
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "app/current").exists())
        self.assertOK(call([command, "--version"], env=env))
        self.assertFalse(canary.exists())
        self.assertIn("bundle checksum mismatch", result.stderr)

    def test_release_storage_conflicts_do_not_write_through_links(self):
        bundle = self.bundle(); env = self.environment()
        app = self.root / "app"; app.mkdir(mode=0o700)
        foreign = self.root / "foreign"; foreign.mkdir()
        canary = foreign / "keep"; canary.write_text("untouched")
        releases = app / "releases"
        for target in (foreign, self.root / "missing-target"):
            with self.subTest(target=target.name):
                releases.symlink_to(target)
                result = call([bundle / "install.sh"], env=env)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("unsafe or foreign metadata", result.stderr)
                self.assertEqual(list(foreign.iterdir()), [canary])
                self.assertEqual(canary.read_text(), "untouched")
                self.assertFalse((self.root / "missing-target").exists())
                self.assertFalse((app / "current").exists())
                releases.unlink()
        releases.write_text("owned file")
        result = call([bundle / "install.sh", "--stage-only"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(releases.read_text(), "owned file")
        releases.unlink(); releases.mkdir(mode=0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(releases.iterdir()))
        self.assertFalse((app / "current").exists())
        # Canonical parent aliases may be intentional; only managed storage
        # components themselves must be real owned directories.
        releases.chmod(0o700)
        alias = self.root / "parent-alias"; alias.symlink_to(self.root, target_is_directory=True)
        self.assertOK(call([bundle / "install.sh", "--app-dir", alias / "app"], env=env))
        self.assertIn('"ready":true', self.assertOK(call([self.root / "bin/symphony", "doctor", "--json"], env=env)))

    def test_failed_stage_rename_never_activates_and_preserves_existing_install(self):
        bundle = self.bundle(); env = self.environment()
        mv = self.root / "tools/mv"; mv.write_text("#!/bin/sh\nexit 73\n"); mv.chmod(0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Installed Symphony", result.stdout)
        self.assertFalse((self.root / "app/current").is_symlink())
        self.assertFalse((self.root / "bin/symphony").is_symlink())
        self.assertFalse(list((self.root / "app/releases").iterdir()))
        mv.unlink()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        before = self.assertOK(call([command, "--version"], env=env))
        # A second fresh staging destination isolates the rename failure while
        # retaining the existing active manager and command.
        script = 'source "$1" --help >/dev/null; package_app_home="$2"; package_stage_bundle "$3"'
        other = self.root / "other-app"; other.mkdir(mode=0o700)
        mv.write_text("#!/bin/sh\nexit 73\n"); mv.chmod(0o755)
        result = call(["bash", "-e", "-c", script, "fixture", command, other, bundle], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list((other / "releases").iterdir()))
        self.assertEqual(self.assertOK(call([command, "--version"], env=env)), before)

    def test_host_layouts_missing_engine_and_source_bound_archive(self):
        mismatch = manifest(revision="c" * 40)
        self.file.write_text(json.dumps(mismatch))
        result = call([ROOT / "scripts/release/package.sh", self.file, self.root / "mismatch-output", HELPER])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source differs", result.stderr)
        self.file.write_text(json.dumps(manifest()))
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        uname = self.root / "tools/uname"
        for architecture, platform in (("arm64", "linux/arm64"), ("x86_64", "linux/amd64")):
            uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo Darwin ;; *) echo {architecture} ;; esac\n'); uname.chmod(0o755)
            output = self.assertOK(call([command, "doctor", "--json"], env=env))
            self.assertEqual(json.loads(output)["architecture"], architecture)
            result = call([command, "auth", "codex", "login"], env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"pull --platform {platform}", (self.root / "docker.calls").read_text())
        result = call([command, "doctor", "--json"], env=dict(env, SYMPHONY_CONTAINER_RUNTIME="/unavailable/engine"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ready"])

    def test_mutable_or_foreign_image_overrides_are_rejected(self):
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        for image in ("mutable:latest", "sha256:" + "c" * 64, "ghcr.io/foreign/runner@sha256:" + "d" * 64):
            result = call([command, "auth", "codex", "login"], env=dict(env, SYMPHONY_RUNNER_IMAGE=image))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("remove SYMPHONY_RUNNER_IMAGE", result.stderr)
        self.assertFalse((self.root / "docker.calls").exists())

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
