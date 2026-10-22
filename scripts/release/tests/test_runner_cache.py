"""Exercise cache authority at the actual shell consumer boundary."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class RunnerCachePolicyTest(unittest.TestCase):
    def policy(self, event, ref, arch="x86_64", number="17", target="codex", variant=""):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            uname = root / "uname"
            uname.write_text(f"#!/bin/sh\nprintf '%s\\n' '{arch}'\n")
            uname.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", EVENT=event,
                       REF=ref, PR_NUMBER=number, TARGET=target, CACHE_VARIANT=variant,
                       GITHUB_OUTPUT=str(root / "output"), GITHUB_STEP_SUMMARY=str(root / "summary"))
            result = subprocess.run(["bash", str(ROOT / "scripts/release/runner-cache-policy.sh")],
                                    env=env, capture_output=True, text=True)
            output = (root / "output").read_text() if (root / "output").exists() else ""
            return result, dict(line.split("=", 1) for line in output.splitlines())

    def test_only_explicit_integration_events_write_trusted_namespace(self):
        for event in ("push", "workflow_dispatch"):
            for ref in ("refs/heads/dev", "refs/heads/main"):
                result, output = self.policy(event, ref)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output["namespace"], "trusted")
        for event, ref in (("pull_request", "refs/heads/main"),
                           ("pull_request_target", "refs/heads/main"),
                           ("workflow_dispatch", "refs/heads/feature"),
                           ("push", "refs/tags/v1.0.0")):
            result, output = self.policy(event, ref)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(output["namespace"], "trusted")

    def test_pr_and_branch_reuse_are_stable_but_isolated(self):
        _, first = self.policy("pull_request", "refs/pull/17/merge")
        _, second = self.policy("pull_request", "refs/pull/18/merge", number="18")
        self.assertEqual(first["namespace"], "pr-17")
        self.assertNotEqual(first["namespace"], second["namespace"])
        _, branch = self.policy("workflow_dispatch", "refs/heads/feature")
        _, repeated = self.policy("workflow_dispatch", "refs/heads/feature")
        _, other = self.policy("workflow_dispatch", "refs/heads/other")
        self.assertEqual(branch, repeated)
        self.assertNotEqual(branch, other)

    def test_changed_recipe_snapshot_preserves_baseline_scope(self):
        _, baseline = self.policy("workflow_dispatch", "refs/heads/feature")
        _, changed = self.policy("workflow_dispatch", "refs/heads/feature", variant="changed")
        self.assertEqual(changed['base_namespace'], baseline['namespace'])
        self.assertEqual(changed['namespace'], baseline['namespace'] + '-changed')

    def test_architecture_and_invalid_identity_refuse_before_outputs(self):
        for arch, expected in (("x86_64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64")):
            result, output = self.policy("push", "refs/heads/dev", arch=arch)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["arch"], expected)
        for kwargs in ({"arch": "riscv64"}, {"target": "arbitrary"}, {"number": ""}, {"number": "../trusted"}, {"variant": "arbitrary"}):
            result, output = self.policy("pull_request", "refs/pull/17/merge", **kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output, {})


if __name__ == "__main__":
    unittest.main()


class CacheProbeControllerTest(unittest.TestCase):
    """Controller failure handling only; fake tools supply no native proof."""
    def execute(self, failure):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / 'tools'
            tools.mkdir()
            scripts = {
                'git': "#!/bin/sh\nprintf '%040d\\n' 1\n",
                'docker': '''#!/bin/sh
case "$1" in
  image) printf 'localhost:5000/symphony-codex@sha256:%064d\\n' 1 ;;
  save) printf 'archive bytes'; test "$FAILURE" != save ;;
  *) exit 0 ;;
esac
''',
                'bash': '#!/bin/sh\ntest "$FAILURE" != qualification\n',
                'python3': '#!/bin/sh\nexit 0\n',
            }
            for name, contents in scripts.items():
                path = tools / name
                path.write_text(contents)
                path.chmod(0o755)
            env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}", FAILURE=failure,
                       RUNNER_TEMP=str(root), PROVIDER='codex', ARCHITECTURE='x86_64')
            import sys
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/release/runner-cache-probe.py'), 'warm'],
                                    env=env, capture_output=True, text=True)
            import json
            evidence = json.loads((root / 'runner-cache-measurements/warm-qualification.json').read_text())
            self.assertEqual(list((root / 'runner-cache-measurements').glob('*.tar.gz')), [])
            return result, evidence

    def test_failed_save_pipeline_cannot_record_success(self):
        result, evidence = self.execute('save')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(evidence['passed'])
        self.assertNotIn('archive_sha256', evidence)
        self.assertIn('save=1', result.stderr)

    def test_failed_runtime_qualification_stops_before_export(self):
        result, evidence = self.execute('qualification')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(evidence['passed'])
        self.assertNotIn('export_compress_seconds', evidence)

    def test_success_retains_measurements_and_deletes_large_archive(self):
        result, evidence = self.execute('')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(evidence['passed'])
        self.assertGreater(evidence['archive_bytes'], 0)
        self.assertEqual(len(evidence['archive_sha256']), 64)
