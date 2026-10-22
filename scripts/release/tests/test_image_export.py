"""Transport regressions; these fixtures do not qualify a production image."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('image_export', ROOT / 'scripts/release/export-image.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
BENCH_SPEC = importlib.util.spec_from_file_location('benchmark_transfer', ROOT / 'scripts/release/benchmark-transfer.py')
BENCH = importlib.util.module_from_spec(BENCH_SPEC)
BENCH_SPEC.loader.exec_module(BENCH)


class ExportTransport(unittest.TestCase):
    def fixture(self):
        config = b'{"architecture":"amd64","config":{"Labels":{"org.opencontainers.image.revision":"fixture"}}}'
        digest = hashlib.sha256(config).hexdigest()
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode='w') as archive:
            for name, data in [(digest + '.json', config), ('layer.tar', b'layer contents' * 1000),
                               ('manifest.json', json.dumps([{'Config': digest + '.json', 'RepoTags': ['fixture:test'], 'Layers': ['layer.tar']}]).encode())]:
                entry = tarfile.TarInfo(name); entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return content.getvalue(), 'sha256:' + digest

    def test_real_compressor_preserves_docker_tar_bytes_and_bound_config(self):
        raw, identity = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            source, result = Path(directory) / 'source.tar', Path(directory) / 'result.tar.gz'
            source.write_bytes(raw)
            with result.open('wb') as stream:
                metrics = EXPORT.pipeline(['cat', str(source)], EXPORT.compressor(), stream)
            self.assertEqual(gzip.decompress(result.read_bytes()), raw)
            self.assertGreater(EXPORT.verify_tar(result, identity), 0)
            self.assertEqual(metrics['export']['exit_code'], 0)
            self.assertEqual(metrics['compression']['exit_code'], 0)
            self.assertIn('overlapped', metrics['wall_time_definition'])
            self.assertGreater(metrics['pipeline_wall_seconds'], 0)

    def test_shared_layer_archive_preserves_both_source_configs(self):
        configs = [b'{"source":"first"}', b'{"source":"second"}']
        digests = [hashlib.sha256(value).hexdigest() for value in configs]
        raw = io.BytesIO()
        manifest = [{'Config': digest + '.json', 'RepoTags': ['fixture:' + str(index)], 'Layers': ['shared-layer.tar']}
                    for index, digest in enumerate(digests)]
        with tarfile.open(fileobj=raw, mode='w') as bundle:
            files = list(zip([digest + '.json' for digest in digests], configs))
            files += [('shared-layer.tar', b'one shared layer'), ('manifest.json', json.dumps(manifest).encode())]
            for name, content in files:
                entry = tarfile.TarInfo(name); entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'two-source.tar.gz'
            archive.write_bytes(gzip.compress(raw.getvalue()))
            EXPORT.verify_tar(archive, ['sha256:' + digest for digest in digests])
            with self.assertRaisesRegex(ValueError, 'different image config'):
                EXPORT.verify_tar(archive, ['sha256:' + digests[0]])
            with tarfile.open(archive, 'r:gz') as bundle:
                self.assertEqual(bundle.getnames().count('shared-layer.tar'), 1)

    def test_two_source_diagnostic_binds_records_and_refuses_foreign_source(self):
        records, configs, manifest = [], [], []
        for index, letter in enumerate(('a', 'b')):
            revision = letter * 40
            config = json.dumps({'architecture': 'amd64', 'config': {'Labels': {
                'com.symphony.agent': 'codex', 'com.symphony.architecture': 'amd64',
                'org.opencontainers.image.revision': revision}}}).encode()
            digest = hashlib.sha256(config).hexdigest()
            configs.append((digest + '.json', config))
            manifest.append({'Config': digest + '.json', 'RepoTags': ['fixture:' + str(index)], 'Layers': ['shared-layer.tar']})
            records.append({'provider': 'codex', 'architecture': 'x86_64', 'platform': 'linux/amd64',
                            'source_revision': revision, 'image': 'localhost:5000/codex@sha256:' + letter * 64})
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as bundle:
            for name, content in configs + [('shared-layer.tar', b'shared'), ('manifest.json', json.dumps(manifest).encode())]:
                entry = tarfile.TarInfo(name); entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'candidate.tar.gz'
            archive.write_bytes(gzip.compress(raw.getvalue()))
            observed = BENCH.identities(archive, records, 'codex', 'x86_64')
            self.assertEqual(len(observed), 2)
            self.assertEqual({value['record']['source_revision'] for value in observed}, {'a'*40, 'b'*40})
            with self.assertRaisesRegex(ValueError, 'no unique original qualification'):
                BENCH.identities(archive, records[:1], 'codex', 'x86_64')
            with self.assertRaisesRegex(ValueError, 'differs from original'):
                BENCH.identities(archive, records, 'claude', 'x86_64')

    def test_source_failure_cannot_be_hidden_by_successful_gzip(self):
        with tempfile.TemporaryFile() as stream:
            with self.assertRaises(subprocess.CalledProcessError):
                EXPORT.pipeline([sys.executable, '-c', 'import sys;sys.stdout.write("partial");sys.exit(23)'], EXPORT.compressor(), stream)

    def test_compressor_failure_cannot_leave_export_running(self):
        with tempfile.TemporaryFile() as stream:
            with self.assertRaises(subprocess.CalledProcessError):
                EXPORT.pipeline([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"x"*10000000)'],
                                [sys.executable, '-c', 'raise SystemExit(24)'], stream)

    def test_actual_byte_corruption_refuses_and_restores_without_docker(self):
        raw, _ = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.tar.gz'
            result.write_bytes(gzip.compress(raw))
            before = result.read_bytes()
            digest = EXPORT.checksum(result)
            BENCH.corruption_probe(result, digest)
            self.assertEqual(result.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'before Docker load'):
                BENCH.verified_checksum(result, '0' * 64)

    def test_corrupt_archive_or_wrong_config_refuses(self):
        raw, identity = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.tar.gz'
            result.write_bytes(gzip.compress(raw))
            with self.assertRaises(ValueError):
                EXPORT.verify_tar(result, 'sha256:' + '0' * 64)
            result.write_bytes(result.read_bytes()[:30])
            with self.assertRaises((EOFError, tarfile.TarError)):
                EXPORT.verify_tar(result, identity)


if __name__ == '__main__':
    unittest.main()
