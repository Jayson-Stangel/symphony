"""Verify real fixture failures/skips cannot become successful preflight receipts."""
import importlib.util
from pathlib import Path
import tempfile
import os
import subprocess
import unittest

SPEC = importlib.util.spec_from_file_location('release_tests', Path(__file__).parents[1] / 'release_tests.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleasePreflightTest(unittest.TestCase):
    def execute(self, kind, filename, body):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / filename).write_text('import unittest\nclass Fixture(unittest.TestCase):\n' + body)
            return RELEASE.run(kind, directory)

    def test_real_failure_and_skip_refuse_qualification(self):
        self.assertFalse(self.execute('native', 'test_process.py',
                                     '    def test_failed(self): self.fail("actual fixture failure")\n'))
        self.assertFalse(self.execute('native', 'test_process.py',
                                     '    @unittest.skip("missing native utility")\n    def test_skipped(self): pass\n'))

    def test_unknown_suite_remains_native(self):
        self.assertTrue(self.execute('native', 'test_new_process.py', '    def test_process(self): self.assertTrue(True)\n'))
        with self.assertRaisesRegex(ValueError, 'no release qualification'):
            self.execute('portable', 'test_new_process.py', '    def test_process(self): self.assertTrue(True)\n')

    def test_portable_metadata_suite_runs(self):
        self.assertTrue(self.execute('portable', 'test_artifacts.py', '    def test_policy(self): self.assertTrue(True)\n'))


class WorkflowGuardTest(unittest.TestCase):
    def guard(self, filename, name, values):
        root = Path(__file__).resolve().parents[3]
        text = (root / '.github/workflows' / filename).read_text()
        section = text.split('      - name: ' + name + '\n', 1)[1]
        script = section.split('        run: |\n', 1)[1].split('\n      - ', 1)[0]
        # Execute the actual workflow shell, including failure/cancellation checks.
        lines = []
        for line in script.splitlines():
            if line.strip() and not line.startswith('          '):
                break
            lines.append(line[10:])
        script = '\n'.join(lines)
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, GITHUB_SHA='a' * 40, GITHUB_STEP_SUMMARY=str(Path(directory) / 'summary'), **values)
            return subprocess.run(['bash', '-e', '-c', script], cwd=directory, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

    def test_source_output_never_accepts_failed_missing_or_wrong_head(self):
        name = 'Require exact successful local or caller-owned preflight'
        for result in ('failure', 'cancelled', 'skipped', ''):
            self.assertNotEqual(self.guard('runner-release.yml', name,
                dict(LOCAL_RESULT=result, LOCAL_REVISION='a'*40, CALLER_REVISION='')), 0)
        self.assertEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='success', LOCAL_REVISION='a'*40, CALLER_REVISION='')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='success', LOCAL_REVISION='b'*40, CALLER_REVISION='')), 0)
        self.assertEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='skipped', LOCAL_REVISION='', CALLER_REVISION='a'*40)), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='skipped', LOCAL_REVISION='', CALLER_REVISION='b'*40)), 0)

    def test_terminal_requires_every_requested_native_package_and_image_result(self):
        name = 'Require all requested candidate work without accepting omitted failures'
        good = dict(PREFLIGHT='success', PACKAGES='success', RUNNERS='success', METADATA='success')
        self.assertEqual(self.guard('runner-release.yml', name, good), 0)
        for key in good:
            for result in ('failure', 'cancelled', 'skipped', ''):
                self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, **{key: result})), 0)


class RetainedDiagnosticContractTest(unittest.TestCase):
    def test_retention_crosses_reusable_workflow_boundary_explicitly(self):
        root = Path(__file__).resolve().parents[3]
        runner = (root / '.github/workflows/runner-release.yml').read_text()
        packages = (root / '.github/workflows/package-checks.yml').read_text()
        burrito = (root / '.github/workflows/burrito-release.yml').read_text()
        for text in (runner, packages):
            call = text.split('  workflow_call:', 1)[1].split('  pull_request:', 1)[0].split('  workflow_dispatch:', 1)[0]
            self.assertIn('      retain_diagnostic:', call)
            self.assertIn("inputs.retain_diagnostic && github.run_id || 'latest'", text)
        nested = runner.split('  package-inputs:', 1)[1].split('  qualification:', 1)[0]
        self.assertIn('retain_diagnostic: ${{ inputs.retain_diagnostic }}', nested)
        self.assertIn('retain_diagnostic: ${{ inputs.controller_only }}', burrito)
        self.assertNotIn('inputs.controller_only', packages)


if __name__ == '__main__':
    unittest.main()
