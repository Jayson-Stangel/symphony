"""Inert public-cache archive boundaries; no runtime or service execution."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

before = sys.path[:]
try:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import validation_cache as cache
    from validation_inputs import UnknownInput
finally:
    sys.path[:] = before


class PublicSidecar(unittest.TestCase):
    def fixture(self, root):
        target = root/'elixir/_build/test'
        target.mkdir(parents=True)
        (target/'compiled.beam').write_bytes(b'raw compiler bytes\x00unchanged')
        os.chmod(target/'compiled.beam', 0o644)
        return target

    def test_preserves_actual_bytes_modes_and_audited_external_link_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.fixture(root)
            (root/'elixir/priv').mkdir()
            (root/'elixir/priv/config').write_text('public fixture')
            link = target/'lib/symphony_elixir/priv'
            link.parent.mkdir(parents=True)
            link.symlink_to('../../../../priv')
            raw, meta = cache.create(target, root)
            stage = target.with_name('.test-evidence')
            observed = cache.stage(raw, meta, stage, root)
            self.assertEqual(observed['sha256'], meta['cache']['sha256'])
            self.assertEqual((stage/'compiled.beam').read_bytes(), (target/'compiled.beam').read_bytes())
            self.assertEqual((stage/'compiled.beam').stat().st_mode, (target/'compiled.beam').stat().st_mode)
            self.assertEqual((stage/'lib/symphony_elixir/priv').readlink(), link.readlink())

    def test_hash_mismatch_refuses_before_creating_any_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = self.fixture(root)
            raw, meta = cache.create(target, root)
            stage = target.with_name('.test-evidence')
            with self.assertRaises(UnknownInput):
                cache.stage(raw+b'corrupt', meta, stage, root)
            self.assertFalse(stage.exists())

    def test_unknown_symlink_source_refuses_public_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = self.fixture(root)
            (target/'unknown').symlink_to('/etc/passwd')
            with self.assertRaises(UnknownInput):
                cache.create(target, root)

    def test_tar_traversal_duplicate_hardlink_and_special_files_refuse_before_write(self):
        for kind in ('traversal', 'duplicate', 'hardlink', 'device', 'foreign-owner', 'writable'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); target = self.fixture(root); stage = target.with_name('.test-evidence')
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w:gz') as archive:
                    base=tarfile.TarInfo('.');base.type=tarfile.DIRTYPE;base.mode=0o755;base.uid=os.getuid();base.gid=os.getgid();archive.addfile(base)
                    item=tarfile.TarInfo('../escape' if kind=='traversal' else '.')
                    item.mode=0o644;item.uid=os.getuid();item.gid=os.getgid()
                    if kind=='hardlink':item.type=tarfile.LNKTYPE;item.linkname='/etc/passwd'
                    if kind=='device':item.type=tarfile.CHRTYPE
                    if kind=='foreign-owner':item.name='foreign';item.uid=os.getuid()+1
                    if kind=='writable':item.name='writable';item.mode=0o666
                    archive.addfile(item)
                raw=data.getvalue();meta=dict(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                with self.assertRaises(UnknownInput):cache.stage(raw,meta,stage,root)
                self.assertFalse(stage.exists())


if __name__ == '__main__':
    unittest.main()
