"""One ordinary native owner, with truthful fixture and composition contexts."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[3]


class OrdinaryManagerRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = (ROOT / '.github/workflows/package-checks.yml').read_text()
        cls.integration = (ROOT / '.github/workflows/runner-integration.yml').read_text()
        block = cls.packages.split('      - name: Require complete ordinary manager and process fixture evidence\n', 1)[1]
        cls.guard = textwrap.dedent(block.split('        run: |\n', 1)[1])

    def gate(self, **changes):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, RESULT='success', CODE='true', PORTABLE='success', NATIVE='success',
                       PACKAGE='success', AGGREGATE='success', GITHUB_SHA='a' * 40, QUALIFIED_REVISION='a' * 40,
                       GITHUB_STEP_SUMMARY=str(Path(folder) / 'summary'))
            env.update(changes)
            return subprocess.run(['bash', '-e', '-c', self.guard], env=env, capture_output=True).returncode

    def test_ordinary_events_have_one_native_helper_and_process_fixture_owner(self):
        events = self.integration.split('permissions:', 1)[0]
        self.assertIn('  workflow_dispatch:', events)
        self.assertNotIn('  pull_request:', events)
        self.assertNotIn('  push:', events)
        self.assertIn('  pull_request:', self.packages)
        self.assertIn('    branches: [dev, main]', self.packages)
        self.assertIn('uses: ./.github/workflows/release-native.yml', self.packages)
        self.assertIn('  native-host-package:', self.packages)
        listener = self.packages.split('  listener-and-cleanup:\n', 1)[1]
        self.assertIn('needs: [changes, qualification, native, native-host-package, macos-package]', listener)
        self.assertNotIn('build-host-helper', listener)
        self.assertNotIn('build-linux-host-helper', listener)
        self.assertNotIn('component-plan', listener)
        self.assertNotIn('runner-release', listener)
        self.assertIn('fixture proof', listener)

    def test_required_contexts_retain_names_and_exact_successful_source(self):
        self.assertIn('  macos-package:', self.packages)
        self.assertIn('  listener-and-cleanup:', self.packages)
        self.assertEqual(self.gate(), 0)
        for field in ('RESULT', 'PORTABLE', 'NATIVE', 'PACKAGE', 'AGGREGATE'):
            for outcome in ('failure', 'cancelled', 'skipped', ''):
                self.assertNotEqual(self.gate(**{field: outcome}), 0, (field, outcome))
        for source in ('', 'b' * 40):
            self.assertNotEqual(self.gate(QUALIFIED_REVISION=source), 0)

    def test_docs_require_explicit_omission_after_successful_classification(self):
        omitted = dict(CODE='false', NATIVE='skipped', PACKAGE='skipped', QUALIFIED_REVISION='')
        self.assertEqual(self.gate(**omitted), 0)
        for field in ('RESULT', 'PORTABLE', 'AGGREGATE'):
            self.assertNotEqual(self.gate(**dict(omitted, **{field: 'failure'})), 0)
        for field in ('NATIVE', 'PACKAGE'):
            self.assertNotEqual(self.gate(**dict(omitted, **{field: 'success'})), 0)
        self.assertNotEqual(self.gate(**dict(omitted, QUALIFIED_REVISION='a' * 40)), 0)

    def test_manual_composition_keeps_actual_artifact_and_lifecycle_guards(self):
        for command in ('test "$PLAN" = success', 'test "$INPUTS" = success', 'test "$PACKAGE" = success',
                        'true) test "$RUNNERS" = success', 'false) test "$RUNNERS" = skipped',
                        'package-lifecycle.py --current-manager-only', 'prepare-manager-inputs.py --plan'):
            self.assertIn(command, self.integration)
        self.assertIn("needs.helpers.result == 'success'", self.integration)
