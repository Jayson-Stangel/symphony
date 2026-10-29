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
            stamp=1740000000125000000
            os.utime(target/'compiled.beam',ns=(stamp,stamp));os.utime(target,ns=(stamp,stamp))
            raw, meta = cache.create(target, root)
            stage = target.with_name('.test-evidence')
            observed = cache.stage(raw, meta, stage, root)
            self.assertEqual(observed['sha256'], meta['cache']['sha256'])
            self.assertEqual((stage/'compiled.beam').read_bytes(), (target/'compiled.beam').read_bytes())
            self.assertEqual((stage/'compiled.beam').stat().st_mode, (target/'compiled.beam').stat().st_mode)
            self.assertEqual((stage/'lib/symphony_elixir/priv').readlink(), link.readlink())
            self.assertEqual((stage/'compiled.beam').stat().st_mtime_ns,stamp)
            self.assertEqual(stage.stat().st_mtime_ns,stamp)

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


    def test_private_hex_target_is_exact_public_lock_bound_and_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory);root=parent/'source';root.mkdir();(root/'elixir').mkdir()
            (root/'elixir/mix.lock').write_text('  "phoenix": {:hex, :phoenix, "1.0.0"}\n')
            state=parent/'state';state.mkdir(mode=0o700);target=state/'hex';target.mkdir()
            (target/'packages/hexpm').mkdir(parents=True)
            (target/'cache.bin').write_bytes(b'opaque raw timestamp and registry bytes')
            (target/'packages/hexpm/phoenix-1.0.0.tar').write_bytes(b'public lock-bound archive')
            raw,metadata=cache.create(target,root,private_root=state)
            stage=state/'.hex-evidence-v2';cache.stage(raw,metadata,stage,root,private_root=state)
            self.assertEqual((stage/'cache.bin').read_bytes(),(target/'cache.bin').read_bytes())
            self.assertEqual(cache.identity(stage,root,private_root=state)['sha256'],metadata['cache']['sha256'])
            for name in ('hex.config','auth','cache.bin.123.tmp','packages/private/phoenix-1.0.0.tar','packages/hexpm/unknown-1.0.0.tar'):
                with self.subTest(name=name):
                    path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'not public supported input')
                    with self.assertRaises(UnknownInput):cache.create(target,root,private_root=state)
                    path.unlink()
                    if path.parent.name=='private':path.parent.rmdir()
            (target/'link').symlink_to(target/'cache.bin')
            with self.assertRaises(UnknownInput):cache.create(target,root,private_root=state)
            with self.assertRaises(UnknownInput):cache.stage(raw,metadata,state/'other',root,private_root=state)
            with self.assertRaises(UnknownInput):cache.stage(raw,metadata,parent/'.hex-evidence-v2',root,private_root=state)


    def test_invalid_original_timestamps_refuse_before_materialization(self):
        from unittest.mock import patch
        for timestamp in ('nan','inf','-1','4102444801'):
            with self.subTest(timestamp=timestamp),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);target=self.fixture(root);raw,metadata=cache.create(target,root)
                import gzip
                source=tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw)),mode='r:')
                output=io.BytesIO()
                with source,tarfile.open(fileobj=output,mode='w:gz') as archive:
                    for row in source:
                        row.pax_headers['mtime']=timestamp
                        archive.addfile(row,source.extractfile(row) if row.isfile() else None)
                raw=output.getvalue();metadata.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                stage=target.with_name('.invalid')
                with self.assertRaises(UnknownInput):cache.stage(raw,metadata,stage,root)
                self.assertFalse(stage.exists())

    def test_early_combined_inventory_refuses_before_fingerprinting_or_coverage(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'a';b=root/'b';a.mkdir();b.mkdir();(a/'payload').write_bytes(b'1234')
            self.assertEqual(cache.precheck([a,b]),dict(entries=3,bytes=4))
            with patch.object(cache,'MAX_ENTRIES',2),self.assertRaises(UnknownInput):cache.precheck([a,b])
            with patch.object(cache,'MAX_EXPANDED',3),self.assertRaises(UnknownInput):cache.precheck([a,b])


if __name__ == '__main__':
    unittest.main()
