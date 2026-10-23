"""Keep all owned CI displays readable while preserving graph identifiers."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]


class WorkflowNames(unittest.TestCase):
    def test_every_workflow_job_and_step_has_a_sentence_case_display(self):
        for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
            text = path.read_text()
            title = text.splitlines()[0].removeprefix('name: ')
            self.assertTrue(title[0].isupper() and '-' not in title, (path, title))
            jobs = text.split('\njobs:\n', 1)[1]
            for identifier, block in re.findall(r'(?ms)^  ([^\n:]+):\n(.*?)(?=^  [^ :]+:|\Z)', jobs):
                self.assertRegex(identifier, r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')
                self.assertRegex(block, r'^    name: [A-Z]', (path, identifier))
                # Matrix display suffixes describe the selected platform/provider.
                for step in re.findall(r'^      - (.+)$', block, re.M):
                    self.assertTrue(step.startswith('name: '), (path, identifier, step))
        for path in sorted((ROOT / '.github/actions').glob('*/action.yml')):
            text = path.read_text()
            self.assertRegex(text, r'^name: [A-Z]')
            for step in re.findall(r'^    - (.+)$', text, re.M):
                self.assertTrue(step.startswith('name: '), (path, step))

    def test_required_displays_are_unique_ordinary_gates(self):
        names = {'make-all.yml': 'Application checks', 'package-checks.yml': 'Package qualification',
                 'runner-integration.yml': 'Browser qualification', 'pr-description-lint.yml': 'PR description'}
        for filename, name in names.items():
            text = (ROOT / '.github/workflows' / filename).read_text()
            self.assertEqual(text.count('\n    name: ' + name + '\n'), 1)
        package = (ROOT / '.github/workflows/package-checks.yml').read_text()
        self.assertNotIn('  listener-and-cleanup:', package)
        self.assertNotIn('  macos-package:', package)
