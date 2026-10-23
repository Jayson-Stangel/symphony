"""Resolve real selected commits/capabilities before any native build."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('prior_source', ROOT/'scripts/release/verify-prior-source.py')
PRIOR = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(PRIOR)


class PriorSource(unittest.TestCase):
    def test_selected_source_must_exist_and_retain_native_linux_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
            for name in PRIOR.REQUIRED:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# fixture source --native\n')
            def commit():
                subprocess.run(['git', '-C', str(root), 'add', '-A'], check=True)
                subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'source'], check=True)
                return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            revision = commit()
            self.assertEqual(PRIOR.verify(root, revision), revision)
            with self.assertRaises(ValueError): PRIOR.verify(root, 'bad-source')
            with self.assertRaises(subprocess.CalledProcessError): PRIOR.verify(root, 'f'*40)
            (root/'scripts/package/linux/Package.swift').unlink()
            unavailable = commit()
            with self.assertRaises(subprocess.CalledProcessError): PRIOR.verify(root, unavailable)
            self.assertEqual(PRIOR.verify(root, revision), revision, 'selected historical source was replaced by current files')
