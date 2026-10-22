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


if __name__ == '__main__':
    unittest.main()
