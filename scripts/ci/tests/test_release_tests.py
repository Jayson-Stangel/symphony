"""Verify real fixture failures/skips cannot become successful preflight receipts."""
import importlib.util
from pathlib import Path
import tempfile
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


if __name__ == '__main__':
    unittest.main()
