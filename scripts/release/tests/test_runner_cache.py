"""Exercise cache authority at the actual shell consumer boundary."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class RunnerCachePolicyTest(unittest.TestCase):
    def policy(self, event, ref, arch="x86_64", number="17", target="codex", variant="", mode="max"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            uname = root / "uname"
            uname.write_text(f"#!/bin/sh\nprintf '%s\\n' '{arch}'\n")
            uname.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", EVENT=event,
                       REF=ref, PR_NUMBER=number, TARGET=target, CACHE_VARIANT=variant, CACHE_MODE=mode,
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

    def test_export_comparison_modes_keep_pr_branch_and_architecture_scopes_separate(self):
        _, base = self.policy('workflow_dispatch', 'refs/heads/feature')
        for mode in ('min', 'max'):
            _, probe = self.policy('workflow_dispatch', 'refs/heads/feature', variant='export-'+mode, mode=mode)
            _, changed = self.policy('workflow_dispatch', 'refs/heads/feature', variant='export-'+mode+'-changed', mode=mode)
            self.assertEqual(probe['namespace'], base['namespace']+'-export-'+mode)
            self.assertEqual(changed['base_namespace'], probe['namespace'])
            self.assertEqual(changed['namespace'], probe['namespace']+'-changed')
        result, output = self.policy('workflow_dispatch', 'refs/heads/feature', mode='arbitrary')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, {})

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


class CacheBuildTimingTest(unittest.TestCase):
    def test_reused_local_builder_cannot_be_reported_as_remote_cache_proof(self):
        import importlib.util, json, sys
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('cache_probe', ROOT/'scripts/release/runner-cache-probe.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)/'runner-cache-measurements'; folder.mkdir()
            for phase in ('cold', 'warm'):
                (folder/(phase+'-build.json')).write_text(json.dumps({
                    'builder_ref': 'same-local-builder/node/'+phase, 'cache_mode': 'max'}))
            with patch.dict(os.environ, RUNNER_TEMP=directory, PROVIDER='claude', ARCHITECTURE='arm64'), \
                    patch.object(sys, 'argv', ['probe', 'warm']), \
                    patch.object(module.subprocess, 'check_output', return_value='a'*40), \
                    patch.object(module.subprocess, 'run', side_effect=AssertionError('must refuse before Docker')), \
                    self.assertRaisesRegex(ValueError, 'reused a local builder'):
                module.main()

    def test_buildkit_intervals_preserve_overlap_and_distinguish_export_import_load(self):
        import importlib.util, json
        spec = importlib.util.spec_from_file_location('cache_probe', ROOT/'scripts/release/runner-cache-probe.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        records = [
            {'digest': '1', 'name': 'importing cache manifest from gha:scope'},
            {'digest': '2', 'name': 'exporting to GitHub Actions Cache'},
            {'digest': '5', 'name': 'exporting cache to registry'},
            {'digest': '3', 'name': 'exporting to docker image format'},
            {'digest': '4', 'name': '[common 1/1] COPY runtime /app', 'cached': True}]
        for item in records:
            item.update(started='2026-10-28T00:00:00.000000123Z', completed='2026-10-28T00:00:10.000000456Z')
        result = module.build_timings(json.dumps({'vertexes': records}))
        by_vertex = {i['vertex_id']: i['category'] for i in result['intervals']}
        self.assertEqual(by_vertex['2'], 'cache_export')
        self.assertEqual(by_vertex['5'], 'cache_export')
        self.assertEqual(by_vertex['3'], 'image_export_load')
        self.assertEqual({i['category'] for i in result['intervals']},
                         {'cache_import', 'cache_export', 'image_export_load', 'build'})
        self.assertTrue(all(i['seconds']==10 for i in result['intervals']))
        self.assertEqual(result['cached_vertices'], 1)
        self.assertIn('do not sum', result['note'])
        with self.assertRaises(ValueError):
            module.build_timings('{}')


class CacheComparisonWorkflowTest(unittest.TestCase):
    def test_retired_composite_probe_leaves_bounded_controller_interface(self):
        import re
        workflow = (ROOT/'.github/workflows/runner-release.yml').read_text()
        dispatch = workflow.split('  workflow_dispatch:\n', 1)[1].split('\npermissions:', 1)[0]
        names = re.findall(r'^      ([a-z_]+):$', dispatch, re.M)
        self.assertEqual(len(names), 22)
        self.assertLessEqual(len(names), 25)
        self.assertIn('bootstrap_previous', names)
        self.assertIn('require_reuse', names)
        self.assertIn('cache_controller_revision', names)
        self.assertIn('cache_export_overlap', names)
        self.assertNotIn('cache_probe', workflow)
        self.assertNotIn('  cache-probe:', workflow)
        comparison = workflow.split('  cache-controller-comparison:\n', 1)[1].split('  packaged-candidate:', 1)[0]
        self.assertIn('runs-on: ubuntu-24.04-arm', comparison)
        self.assertIn('strategy: [serial, overlap]', comparison)
        self.assertIn("'claude','arm64'", comparison)
        self.assertNotIn('        include:', comparison)
        self.assertIn('runner-cache-probe.py', comparison)

    def test_retained_browser_action_keeps_production_cache_default(self):
        action = (ROOT/'.github/actions/build-runner/action.yml').read_text()
        browser = (ROOT/'.github/workflows/runner-integration.yml').read_text()
        self.assertIn('uses: ./.github/actions/build-runner', browser)
        self.assertIn('  cache-mode:\n', action)
        self.assertIn('    default: max\n', action)
        self.assertIn("inputs.trusted-cache-fallback == 'true'", action)
        self.assertIn("if: inputs.builder == ''", action)
        self.assertIn("'builder_ref': ref", action)
        self.assertIn("ref.rsplit('/', 1)[1]", action)
