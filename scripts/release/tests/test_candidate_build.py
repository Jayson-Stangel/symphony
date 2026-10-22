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
if [ "$1" = create ]; then echo 'existing name/occupied port' >&2; exit 42; fi
if [ "$1" = rm ]; then touch "$PROOF_CANARY"; exit 0; fi
if [ "$1" = container ]; then exit 0; fi
exit 43
''')
            docker.chmod(0o755)
            canary = root / "unrelated-runtime-was-removed"
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], PROOF_CANARY=str(canary))
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-candidate-runners.sh"),
                "codex", "x86_64", str(root / "output")], text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(canary.exists(), "failed acquisition attempted to delete a runtime")

    def test_post_acquisition_failure_removes_only_observed_container_id(self):
        with tempfile.TemporaryDirectory(prefix="symphony-registry-owned-") as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text('''#!/bin/sh
case "$1" in
create) printf '%s\\n%s\\n' "$3" "${5#*=}" > "$REGISTRY_RECEIPT"; printf '%064d\\n' 1 ;;
start) exit 0 ;;
container) exit 0 ;;
build) exit 44 ;;
rm) printf '%s\\n' "$*" > "$PROOF_REMOVAL" ;;
inspect) REGISTRY_NAME=$(sed -n '1p' "$REGISTRY_RECEIPT"); REGISTRY_OWNER=$(sed -n '2p' "$REGISTRY_RECEIPT"); printf '{"Name":"/%s","Id":"%064d","Config":{"Labels":{"com.symphony.native-test-owner":"%s"}}}\\n' "$REGISTRY_NAME" 1 "$REGISTRY_OWNER" | sed 's/^/[ /; s/$/ ]/' ;;
*) exit 45 ;;
esac
''')
            docker.chmod(0o755)
            removal = root / "removed"
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], PROOF_REMOVAL=str(removal), REGISTRY_RECEIPT=str(root / "receipt"))
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-candidate-runners.sh"),
                "codex", "x86_64", str(root / "output")], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 44, result.stdout + result.stderr)
            self.assertEqual(removal.read_text().strip(), "rm --force " + "1".zfill(64))
            output = root / "output"
            self.assertTrue((output / "codex-x86_64.registry-state.txt").exists())
            # Execute the producer's actual top-level small-record glob: diagnostics
            # cannot masquerade as native records at metadata assembly.
            (output / "codex-x86_64.json").write_text('{"provider":"codex"}')
            self.assertEqual([path.name for path in output.glob("*.json")], ["codex-x86_64.json"])


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


class LinuxSdkControllerTest(unittest.TestCase):
    def controller_fixture(self, root, curl_status=0):
        tools = root / "tools"; tools.mkdir()
        scripts = {
            "uname": '#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo x86_64 ;; *) echo Linux-x86_64 ;; esac\n',
            "curl": f'''#!/bin/sh
touch "$PROOF_CURL_CALLED"
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then shift; output=$1; fi
  shift
done
printf corrupt-or-partial-sdk > "$output"
exit {curl_status}
''',
            "docker": '#!/bin/sh\ntouch "$PROOF_DOCKER_CALLED"\nexit 71\n',
        }
        for name, script in scripts.items():
            path = tools / name; path.write_text(script); path.chmod(0o755)
        output = root / "output"; output.mkdir()
        (output / "symphony-host").write_text("keep prior output")
        return dict(os.environ, PATH=str(tools) + ":" + os.environ["PATH"], TMPDIR=str(root),
                    PROOF_CURL_CALLED=str(root / "curl-called"), PROOF_DOCKER_CALLED=str(root / "docker-called"))

    def test_corrupt_sdk_is_rejected_before_docker_or_output_replacement(self):
        with tempfile.TemporaryDirectory(prefix="symphony-sdk-checksum-") as directory:
            root = Path(directory); env = self.controller_fixture(root)
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-linux-host-helper.sh"), str(root / "output")],
                text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Swift SDK archive checksum mismatch", result.stderr)
            self.assertTrue((root / "curl-called").exists())
            self.assertFalse((root / "docker-called").exists(), "corrupt SDK reached Swift installer/build")
            self.assertEqual((root / "output/symphony-host").read_text(), "keep prior output")
            self.assertFalse(list(root.glob("symphony-linux-sdk.*")), "SDK staging directory leaked")

    def test_failed_download_is_not_installed_or_reported_as_built(self):
        with tempfile.TemporaryDirectory(prefix="symphony-sdk-download-") as directory:
            root = Path(directory); env = self.controller_fixture(root, curl_status=22)
            result = subprocess.run(["bash", str(ROOT / "scripts/release/build-linux-host-helper.sh"), str(root / "output")],
                text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 22, result.stdout + result.stderr)
            self.assertTrue((root / "curl-called").exists())
            self.assertFalse((root / "docker-called").exists())
            self.assertEqual((root / "output/symphony-host").read_text(), "keep prior output")
            self.assertFalse((root / "output/build.txt").exists())
            self.assertFalse(list(root.glob("symphony-linux-sdk.*")))

    def test_wrong_or_empty_explicit_source_revision_refuses_before_download(self):
        with tempfile.TemporaryDirectory(prefix="symphony-sdk-source-") as directory:
            root = Path(directory); env = self.controller_fixture(root)
            for revision in ("0" * 40, ""):
                with self.subTest(revision=revision):
                    result = subprocess.run(["bash", str(ROOT / "scripts/release/build-linux-host-helper.sh"), str(root / "output"), str(ROOT), revision],
                        text=True, capture_output=True, env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("selected helper source revision differs", result.stderr)
                    self.assertFalse((root / "curl-called").exists())
                    self.assertFalse((root / "docker-called").exists())
                    self.assertEqual((root / "output/symphony-host").read_text(), "keep prior output")

    def test_generated_candidate_outputs_are_allowed_but_dirty_source_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="symphony-sdk-clean-inputs-") as directory:
            root = Path(directory); env = self.controller_fixture(root)
            env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
            source = root / "source"; source.mkdir()

            def git(*args):
                result = subprocess.run(["git", "-C", str(source), *args], text=True, capture_output=True, env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout.strip()

            git("init", "--quiet", "--initial-branch", "codex/source-fixture")
            swift = source / "scripts/package/host.swift"; swift.parent.mkdir(parents=True)
            swift.write_text("// exact tracked fixture source\n")
            build = source / "scripts/release/build-host-helper.sh"; build.parent.mkdir(parents=True)
            build.write_text("#!/bin/sh\nexit 99\n")
            git("add", "scripts")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "source")
            revision = git("rev-parse", "HEAD")
            generated = []
            for folder in ("metadata", "dist", "records"):
                path = source / folder / "canary"; path.parent.mkdir(); path.write_text("preserved output")
                generated.append(path)
            arguments = ["bash", str(ROOT / "scripts/release/build-linux-host-helper.sh"), str(root / "output"), str(source), revision]
            result = subprocess.run(arguments, text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Swift SDK archive checksum mismatch", result.stderr, "ordinary generated outputs prevented reaching the verified download")
            self.assertTrue((root / "curl-called").exists())
            self.assertTrue(all(path.read_text() == "preserved output" for path in generated))
            (root / "curl-called").unlink()
            swift.write_text("// uncommitted tracked source mutation\n")
            refused = subprocess.run(arguments, text=True, capture_output=True, env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("selected helper source inputs are dirty", refused.stderr)
            self.assertFalse((root / "curl-called").exists())
            self.assertFalse((root / "docker-called").exists())
            self.assertTrue(all(path.read_text() == "preserved output" for path in generated))
