#!/usr/bin/env python3
"""Trusted cache preparation must preserve native data and reject link escapes."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

prepare = runpy.run_path(str(Path(__file__).with_name('node-headers.py')))['prepare_home']


class HeaderHomeTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name)
        self.home = self.root / 'native-home'
        self.home.mkdir()
        self.store = self.root / 'image-headers'
        (self.store / '24.22.1').mkdir(parents=True)
        (self.store / '24.22.1/installVersion').write_text('11\n')
        (self.store / 'manifest.json').write_text(json.dumps({
            'version': '24.22.1', 'installVersion': 11}))

    def test_missing_view_is_idempotent_and_preserves_auth(self):
        auth = self.home / 'auth-canary'
        auth.write_text('unchanged native data')
        self.assertEqual(prepare(self.home, self.store), 'prepared')
        view = self.home / '.cache/node-gyp/24.22.1'
        before = view.lstat().st_ino
        self.assertEqual(view.resolve(), self.store / '24.22.1')
        self.assertEqual(prepare(self.home, self.store), 'existing')
        self.assertEqual(view.lstat().st_ino, before)
        self.assertEqual(auth.read_text(), 'unchanged native data')

    def test_existing_cache_directory_and_custom_link_are_not_replaced(self):
        version = self.home / '.cache/node-gyp/24.22.1'
        version.mkdir(parents=True)
        data = version / 'operator-owned'
        data.write_text('retain')
        self.assertEqual(prepare(self.home, self.store), 'existing')
        self.assertFalse(version.is_symlink())
        self.assertEqual(data.read_text(), 'retain')
        data.unlink()
        version.rmdir()
        version.symlink_to(self.root / 'absent-custom-destination')
        self.assertEqual(prepare(self.home, self.store), 'existing')
        self.assertEqual(version.readlink(), self.root / 'absent-custom-destination')

    def test_cache_parent_links_cannot_create_entries_in_auth(self):
        auth = self.root / 'native-auth'
        auth.mkdir()
        (auth / 'canary').write_text('retain')
        for part in ('.cache', '.cache/node-gyp'):
            with self.subTest(parent=part):
                home = self.root / part.replace('/', '-')
                home.mkdir()
                parent = home / part
                parent.parent.mkdir(parents=True, exist_ok=True)
                parent.symlink_to(auth)
                self.assertEqual(prepare(home, self.store), 'existing-custom-parent')
                self.assertEqual([p.name for p in auth.iterdir()], ['canary'])
                self.assertEqual((auth / 'canary').read_text(), 'retain')

    def test_inconsistent_image_metadata_fails_before_home_write(self):
        (self.store / '24.22.1/installVersion').write_text('10\n')
        with self.assertRaisesRegex(RuntimeError, 'metadata differs'):
            prepare(self.home, self.store)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_next_image_version_adds_only_its_missing_link(self):
        prepare(self.home, self.store)
        first = self.home / '.cache/node-gyp/24.22.1'
        before = first.lstat().st_ino
        (self.store / '24.22.2').mkdir()
        (self.store / '24.22.2/installVersion').write_text('11\n')
        (self.store / 'manifest.json').write_text(json.dumps({
            'version': '24.22.2', 'installVersion': 11}))
        self.assertEqual(prepare(self.home, self.store), 'prepared')
        self.assertEqual(first.lstat().st_ino, before)
        self.assertEqual((self.home / '.cache/node-gyp/24.22.2').resolve(),
                         self.store / '24.22.2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
