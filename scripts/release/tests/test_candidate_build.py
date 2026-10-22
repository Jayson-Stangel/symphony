"""Candidate controllers fail closed on ownership and native qualification failures."""
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class CandidateBuildTest(unittest.TestCase):
    def test_failed_registry_acquisition_never_removes_preexisting_runtime(self):
        with tempfile.TemporaryDirectory(prefix="symphony-registry-owner-") as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('''#!/bin/sh
if [ "$1" = run ]; then echo 'existing name/occupied port' >&2; exit 42; fi
if [ "$1" = rm ]; then touch "$PROOF_CANARY"; exit 0; fi
exit 43
''')
            docker.chmod(0o755)
            canary = root / "unrelated-runtime-was-removed"
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], PROOF_CANARY=str(canary))
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-candidate-runners.sh"),
                "codex", "x86_64", str(root / "output")], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            self.assertFalse(canary.exists(), "failed acquisition attempted to delete a runtime")

    def test_post_acquisition_failure_removes_only_observed_container_id(self):
        with tempfile.TemporaryDirectory(prefix="symphony-registry-owned-") as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('''#!/bin/sh
case "$1" in
run) printf '%064d\\n' 1 ;;
build) exit 44 ;;
rm) printf '%s\\n' "$*" > "$PROOF_REMOVAL" ;;
inspect) exit 1 ;;
*) exit 45 ;;
esac
''')
            docker.chmod(0o755)
            removal = root / "removed"
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], PROOF_REMOVAL=str(removal))
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-candidate-runners.sh"),
                "codex", "x86_64", str(root / "output")], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 44, result.stdout + result.stderr)
            self.assertEqual(removal.read_text().strip(), "rm -f " + "1".zfill(64))


class CleanUbuntuDriverTest(unittest.TestCase):
    @staticmethod
    def actual_workflow_script():
        # Execute the workflow's real literal script using the observed default
        # Actions shell (bash -e), without adding pipefail in the test harness.
        lines = (ROOT / ".github/workflows/package-checks.yml").read_text().splitlines()
        start = lines.index("      - name: Execute artifact in clean Ubuntu 26.04 without build tools")
        run = next(i for i in range(start + 1, len(lines)) if lines[i] == "        run: |")
        script = []
        for line in lines[run + 1:]:
            if line and not line.startswith("          "):
                break
            script.append(line[10:] if line else "")
        return "\n".join(script) + "\n"

    def test_docker_contract_failure_cannot_be_hidden_by_successful_tee(self):
        with tempfile.TemporaryDirectory(prefix="symphony-clean-driver-") as directory:
            root = Path(directory); (root / "linux-helper").mkdir()
            docker = root / "docker"
            for status in (0, 23):
                with self.subTest(docker_exit=status):
                    docker.write_text(f"#!/bin/sh\necho 'container result {status}'\nexit {status}\n")
                    docker.chmod(0o755)
                    env = dict(os.environ, PATH=str(root) + ":/usr/bin:/bin", RUNNER_TEMP=str(root),
                               HELPER="/unused/symphony-host", GITHUB_SHA="a" * 40)
                    result = subprocess.run(["bash", "-e", "-c", self.actual_workflow_script()],
                        text=True, capture_output=True, env=env)
                    self.assertEqual(result.returncode == 0, status == 0, result.stdout + result.stderr)
                    self.assertIn(f"container result {status}", (root / "linux-helper/ubuntu26.log").read_text())

    def test_present_forbidden_runtime_fails_at_absence_guard(self):
        with tempfile.TemporaryDirectory(prefix="symphony-clean-tools-") as directory:
            root = Path(directory); (root / "linux-helper").mkdir()
            scripts = {
                "docker": '''#!/bin/sh
while [ "$#" -gt 0 ] && [ "$1" != ubuntu:26.04 ]; do shift; done
[ "$#" -gt 0 ] || exit 90
shift
exec "$@"
''',
                "uname": "#!/bin/sh\necho Linux-x86_64\n",
                "cat": "#!/bin/sh\necho Ubuntu-26.04-fixture\n",
                "swift": "#!/bin/sh\nexit 91\n",
            }
            for name, body in scripts.items():
                path = root / name; path.write_text(body); path.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ":/usr/bin:/bin", RUNNER_TEMP=str(root),
                       HELPER="/unused/symphony-host", GITHUB_SHA="a" * 40)
            result = subprocess.run(["bash", "-e", "-c", self.actual_workflow_script()],
                text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected helper runtime dependency present: swift", result.stderr)
            self.assertNotIn("/symphony-host: No such file", result.stderr)


class CandidateSourceTest(unittest.TestCase):
    def test_shallow_caller_prepares_exact_fetch_head_only_revision(self):
        with tempfile.TemporaryDirectory(prefix="symphony-shallow-source-") as directory:
            root = Path(directory)
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_ALLOW_PROTOCOL="file")

            def git(*args, expected=0):
                result = subprocess.run(["git", *map(str, args)], text=True, capture_output=True, env=env)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                return result.stdout.strip()

            origin = root / "origin.git"; publisher = root / "publisher"
            git("init", "--quiet", "--bare", origin)
            git("init", "--quiet", "--initial-branch", "dev", publisher)
            git("-C", publisher, "remote", "add", "origin", origin.as_uri())
            payload = publisher / "payload"; payload.write_text("actual prior tree\n")
            git("-C", publisher, "add", "payload")
            git("-C", publisher, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "prior")
            previous = git("-C", publisher, "rev-parse", "HEAD")
            payload.write_text("actual current tree\n")
            git("-C", publisher, "add", "payload")
            git("-C", publisher, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "current")
            current = git("-C", publisher, "rev-parse", "HEAD")
            git("-C", publisher, "push", "--quiet", "origin", "dev")
            git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/dev")
            caller = root / "shallow-checkout"
            git("clone", "--quiet", "--depth=1", origin.as_uri(), caller)
            self.assertEqual(git("-C", caller, "rev-parse", "--is-shallow-repository"), "true")
            self.assertNotEqual(subprocess.run(["git", "-C", str(caller), "cat-file", "-e", previous + "^{tree}"],
                capture_output=True, env=env).returncode, 0, "fixture unexpectedly has prior source history")
            # Reproduce Actions: fetch succeeds but only updates FETCH_HEAD.
            git("-C", caller, "fetch", "--no-tags", "--depth=1", "origin", previous)
            self.assertEqual(git("-C", caller, "rev-parse", "FETCH_HEAD"), previous)
            self.assertNotIn(previous, git("-C", caller, "show-ref"))
            helper = caller / "scripts/release/prepare-candidate-source.sh"
            helper.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "scripts/release/prepare-candidate-source.sh", helper)
            (caller / "payload").write_text("preserve intentional dirty source\n")
            (caller / "untracked").write_text("preserve untracked evidence\n")
            original_status = git("-C", caller, "status", "--porcelain", "--untracked-files=all")
            prepared = root / "prepared-prior"
            result = subprocess.run(["bash", str(helper), previous, str(prepared)], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((prepared / ".git").is_dir(), "prior build needs independent mountable Git metadata")
            self.assertEqual(git("-C", prepared, "rev-parse", "HEAD"), previous)
            self.assertEqual((prepared / "payload").read_text(), "actual prior tree\n")
            self.assertEqual(git("-C", caller, "rev-parse", "HEAD"), current)
            self.assertEqual(git("-C", caller, "rev-parse", "FETCH_HEAD"), previous)
            self.assertEqual(git("-C", caller, "status", "--porcelain", "--untracked-files=all"), original_status)
            self.assertEqual((caller / "payload").read_text(), "preserve intentional dirty source\n")
            self.assertEqual((caller / "untracked").read_text(), "preserve untracked evidence\n")
            # Refuse reuse rather than overwrite any existing candidate/user data.
            sentinel = prepared / "keep"; sentinel.write_text("preserved")
            refused = subprocess.run(["bash", str(helper), previous, str(prepared)], text=True, capture_output=True, env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(sentinel.read_text(), "preserved")
