"""Execute event guards with failed, missing and cancelled planner decisions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class ComponentEvents(unittest.TestCase):
    def test_browser_wrapper_requires_complete_plan_except_explicit_docs(self):
        workflow = (ROOT / '.github/workflows/browser-smoke.yml').read_text()
        block = workflow.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        guard = '\n'.join(line[10:] for line in block.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            for code, plan, browser, success in (
                ('false', 'skipped', '', True), ('true', 'success', 'reuse', True),
                ('true', 'success', 'build', True), ('false', 'success', 'reuse', False),
                ('true', 'failure', 'reuse', False), ('true', 'cancelled', 'reuse', False),
                ('true', 'skipped', 'reuse', False), ('true', 'success', '', False),
                ('true', 'success', 'unknown', False), ('', 'skipped', '', False),
            ):
                result = subprocess.run(['bash', '-e', '-c', guard], cwd=directory,
                                        env=dict(os.environ, RESULT='success', CODE=code, PLAN=plan, BROWSER=browser))
                self.assertEqual(result.returncode == 0, success, (code, plan, browser))

    def test_current_manager_wrapper_rejects_missing_failed_or_cancelled_evidence(self):
        workflow = (ROOT / '.github/workflows/runner-integration.yml').read_text()
        section = workflow.split('      - name: Require complete current-manager and component decisions\n', 1)[1]
        body = section.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        script = '\n'.join(line[10:] for line in body.splitlines())
        good = dict(RESULT='success', CODE='true', PLAN='success', HAS_BUILDS='false',
                    RUNNERS='skipped', INPUTS='success', PACKAGE='success')
        with tempfile.TemporaryDirectory() as directory:
            def accepted(values):
                return subprocess.run(['bash', '-e', '-c', script], cwd=directory,
                                      env=dict(os.environ, **values)).returncode == 0
            self.assertTrue(accepted(good))
            self.assertTrue(accepted(dict(good, HAS_BUILDS='true', RUNNERS='success')))
            for key in ('RESULT', 'PLAN', 'INPUTS', 'PACKAGE'):
                for state in ('failure', 'cancelled', 'skipped', ''):
                    self.assertFalse(accepted(dict(good, **{key: state})), (key, state))
            for state in ('success', 'failure', 'cancelled', ''):
                self.assertFalse(accepted(dict(good, RUNNERS=state)))
            self.assertFalse(accepted(dict(good, HAS_BUILDS='')))
            docs = dict(RESULT='success', CODE='false', PLAN='skipped', RUNNERS='skipped',
                        INPUTS='skipped', PACKAGE='skipped', HAS_BUILDS='')
            self.assertTrue(accepted(docs))
            self.assertFalse(accepted(dict(docs, PACKAGE='failure')))


if __name__ == '__main__':
    unittest.main()
