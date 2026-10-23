"""Exact graph transport fixtures; no production image qualification or Docker calls."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/release'))
import qualified_graph as graph
SPEC = importlib.util.spec_from_file_location('graph_export', ROOT / 'scripts/release/export-image.py')
EXPORT = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(EXPORT)


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode()


class Fixture:
    def __init__(self, provider='codex', arch='arm64', sources=('a', 'b')):
        self.data = {}; self.records = []; self.roots = {}
        raw_layer = b'unchanged shared tar layer' * 40
        self.layer = gzip.compress(raw_layer, mtime=0)
        self.layer_id = graph.digest(self.layer)
        for source in sources:
            labels = {'com.symphony.agent': provider, 'com.symphony.architecture': arch,
                      'org.opencontainers.image.revision': source * 40,
                      'com.symphony.inputs.sha256': source * 64, 'com.symphony.protocol': '1'}
            config = encode({'os': 'linux', 'architecture': arch, 'config': {'Labels': labels},
                             'rootfs': {'type': 'layers', 'diff_ids': [graph.digest(raw_layer)]}})
            config_id = graph.digest(config)
            manifest = encode({'schemaVersion': 2, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
                'config': {'mediaType': 'application/vnd.docker.container.image.v1+json', 'size': len(config), 'digest': config_id},
                'layers': [{'mediaType': 'application/vnd.docker.image.rootfs.diff.tar.gzip', 'size': len(self.layer), 'digest': self.layer_id}]})
            identity = graph.digest(manifest)
            record = {'image': 'localhost:5000/symphony-' + provider + '@' + identity,
                      'image_config': config_id, 'archive_tag': 'localhost:5000/symphony-' + provider + (':candidate' if not self.records else ':previous'),
                      'provider': provider, 'architecture': 'x86_64' if arch == 'amd64' else arch,
                      'source_revision': source * 40, 'input_identity': source * 64, 'protocol': 1}
            self.records.append(record); self.roots[identity] = manifest
            self.data.update({config_id: config, self.layer_id: self.layer})

    def write(self, root):
        root.mkdir(); (root / 'blobs' / 'sha256').mkdir(parents=True)
        for identity, raw in dict(self.data, **self.roots).items():
            graph.blob_path(root, identity).write_bytes(raw)
        (root / 'index.json').write_bytes(encode({'schema_version': 1, 'images': [
            {key: record[key] for key in ('image', 'archive_tag', 'image_config')} for record in self.records]}))
        return root


class FakeRegistry:
    def __init__(self, fixture):
        self.fixture = fixture; self.calls = []; self.stored = {}; self.port = 53058

    def client(self, port, repo):
        outer = self
        class Client:
            def request(self, method, suffix, body=None, size=None, expected=(200,), maximum=graph.MAX_METADATA, output=None, content_type=None):
                outer.calls.append((method, repo, suffix))
                identifier = suffix.split('/')[-1]
                if method == 'GET' and suffix.startswith('/manifests/'):
                    raw = outer.stored.get(identifier, outer.fixture.roots.get(identifier))
                elif method == 'GET' and suffix.startswith('/blobs/'):
                    raw = outer.stored.get(identifier, outer.fixture.data.get(identifier))
                elif method == 'HEAD':
                    return 404, {}, b'', 0, graph.digest(b'')
                elif method == 'POST':
                    return 202, {'Location': '/v2/' + repo + '/blobs/uploads/one'}, b'', 0, graph.digest(b'')
                elif method == 'PUT':
                    identifier = suffix.split('digest=')[-1] if '/uploads/' in suffix else identifier
                    identifier = identifier.replace('%3A', ':')
                    if hasattr(body, 'read'):
                        parts = []
                        while block := body.read(31): parts.append(block)
                        raw = b''.join(parts)
                    else: raw = body
                    outer.stored[identifier] = raw
                    return 201, {'Docker-Content-Digest': graph.digest(raw)}, b'', 0, graph.digest(b'')
                else: raise AssertionError((method, suffix))
                if raw is None: raise AssertionError('missing fixture ' + identifier)
                if len(raw) > maximum: raise ValueError('response bound')
                if output is not None: output.write(raw)
                return 200, {'Docker-Content-Digest': graph.digest(raw)}, b'' if output is not None else raw, len(raw), graph.digest(raw)
            def upload_location(self, location, identity):
                return '/blobs/uploads/one?digest=' + identity
        return Client()


class GraphTransport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def archive(self, root, extra=None):
        output = self.root / 'graph.tar.gz'
        with tarfile.open(output, 'w:gz') as bundle:
            for path in sorted(root.rglob('*')):
                if path.is_file(): bundle.add(path, arcname=graph.PREFIX + '/' + path.relative_to(root).as_posix())
            if extra:
                info = tarfile.TarInfo(extra[0]); info.size = len(extra[1]); bundle.addfile(info, io.BytesIO(extra[1]))
        return output

    def test_all_four_platform_provider_targets_two_roots_shared_blob_exact_roundtrip(self):
        for provider in ('codex', 'claude'):
            for arch in ('arm64', 'amd64'):
                with self.subTest(provider=provider, arch=arch):
                    fixture = Fixture(provider, arch)
                    root = fixture.write(self.root / (provider + arch))
                    graph.validate_graphs(root, fixture.records)
                    self.assertEqual(len(list((root / 'blobs' / 'sha256').iterdir())), 5)
                    archive = self.archive(root)
                    extracted = graph.extract_archive(archive, fixture.records[:1], self.root / ('out' + provider + arch))
                    verified = graph.validate_graphs(extracted, fixture.records)
                    self.assertEqual(set(verified), {record['image'] for record in fixture.records})
                    for record in fixture.records:
                        key = record['image']; self.assertEqual(verified[key]['manifest'], fixture.roots[key.split('@')[1]])
                    self.assertEqual(graph.blob_path(extracted, fixture.layer_id).read_bytes(), fixture.layer)

    def test_actual_normal_export_uses_registry_exact_graph_not_docker_save(self):
        fixture = Fixture(); registry = FakeRegistry(fixture)
        records = []
        for number, record in enumerate(fixture.records):
            path = self.root / ('record' + str(number)); path.write_bytes(encode(record)); records.append(path)
        output = self.root / 'payload.docker.tar.gz'; checksum = self.root / 'payload.sha256'
        refs = [record['image'].replace(':5000/', ':53058/') for record in fixture.records]
        with mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry', side_effect=registry.client):
            metrics = EXPORT.export(refs, output, checksum, self.root / 'state', records)
        self.assertEqual(metrics['format'], 'qualified-graph-v1+tar+gzip')
        self.assertEqual(metrics['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertGreater(metrics['graph_capture_wall_seconds'], 0)
        with tarfile.open(output, 'r:gz') as bundle:
            self.assertTrue(all(member.name.startswith(graph.PREFIX + '/') for member in bundle))
        extracted = graph.extract_archive(output, fixture.records, self.root / 'actual-export')
        registry.calls.clear()
        with mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry', side_effect=registry.client):
            restored = graph.restore_graphs(extracted, fixture.records, self.root / 'state')
        self.assertEqual(set(restored), {record['image'] for record in fixture.records})
        self.assertEqual(registry.stored[fixture.layer_id], fixture.layer)
        for record in fixture.records:
            identity = record['image'].split('@')[1]
            self.assertEqual(registry.stored[identity], fixture.roots[identity])

    def test_corrupt_fourth_blob_refuses_before_any_registry_or_docker(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph')
        graph.blob_path(root, fixture.records[1]['image_config']).write_bytes(b'changed')
        with mock.patch.object(graph, 'owned_registry') as owned, mock.patch.object(graph, 'Registry') as client:
            with self.assertRaises(ValueError): graph.restore_graphs(root, fixture.records, self.root / 'state')
            owned.assert_not_called(); client.assert_not_called()

    def test_selected_subset_still_refuses_corrupt_unselected_root_and_unknown_blob(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph')
        original = graph.blob_path(root, fixture.records[1]['image_config']).read_bytes()
        graph.blob_path(root, fixture.records[1]['image_config']).write_bytes(b'bad')
        with self.assertRaises(ValueError): graph.validate_graphs(root, fixture.records[:1], exact=False)
        graph.blob_path(root, fixture.records[1]['image_config']).write_bytes(original)
        (root / 'blobs/sha256' / ('f' * 64)).write_bytes(b'orphan')
        with self.assertRaisesRegex(ValueError, 'unreferenced'): graph.validate_graphs(root, fixture.records[:1], exact=False)

    def test_boolean_schema_or_protocol_is_not_an_integer_version(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph')
        index = json.loads((root / 'index.json').read_text()); index['schema_version'] = True
        (root / 'index.json').write_bytes(encode(index))
        with self.assertRaisesRegex(ValueError, 'invalid index'): graph.validate_graphs(root, fixture.records)
        index['schema_version'] = 1; (root / 'index.json').write_bytes(encode(index))
        records = [dict(record, protocol=True) for record in fixture.records]
        with self.assertRaisesRegex(ValueError, 'protocol'): graph.validate_graphs(root, records)

    def test_mixed_docker_save_foreign_member_and_graph_symlink_parent_refuse(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph')
        with self.assertRaisesRegex(ValueError, 'outside graph-only'):
            graph.extract_archive(self.archive(root, ('manifest.json', b'[]')), fixture.records, self.root / 'mixed')
        link = self.root / 'link'; link.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'): graph.validate_graphs(link, fixture.records)

    def test_changed_blob_after_validation_refuses_before_http(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph'); original = graph.validate_graphs
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            graph.blob_path(root, fixture.layer_id).write_bytes(b'changed')
            return result
        with mock.patch.object(graph, 'validate_graphs', side_effect=mutate), mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry') as client:
            with self.assertRaisesRegex(ValueError, 'prevalidated graph changed'):
                graph.restore_graphs(root, fixture.records, self.root / 'state')
            client.assert_called()  # construction is read-only; no request allowed
            client.return_value.request.assert_not_called()

    def test_missing_registry_no_implicit_docker_save_and_failed_capture_no_outputs(self):
        output = self.root / 'payload'; sidecar = self.root / 'checksum'
        with mock.patch.object(EXPORT.subprocess, 'check_output') as docker:
            with self.assertRaisesRegex(ValueError, 'normal export requires'):
                EXPORT.export('fixture:current', output, sidecar)
            docker.assert_not_called()
        fixture = Fixture(); registry = FakeRegistry(fixture)
        record_paths = []
        for number, record in enumerate(fixture.records):
            path = self.root / ('r' + str(number)); path.write_bytes(encode(record)); record_paths.append(path)
        registry.fixture.data[fixture.records[1]['image_config']] = b'broken fourth blob'
        with mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry', side_effect=registry.client):
            with self.assertRaises(ValueError):
                EXPORT.export([record['image'].replace(':5000/', ':53058/') for record in fixture.records], output, sidecar, self.root / 'state', record_paths)
        self.assertFalse(output.exists()); self.assertFalse(sidecar.exists())
        self.assertFalse(output.with_name(output.name + '.metrics.txt').exists())

    def test_hidden_nonzero_tar_header_inside_stream_buffer_and_crc_refuse(self):
        fixture = Fixture(); root = fixture.write(self.root / 'graph')
        archive = self.archive(root)
        raw = gzip.decompress(archive.read_bytes())
        # Append an otherwise valid foreign header AFTER the tar EOF zeros but
        # INSIDE tarfile's 10240-byte read buffer, not merely after its buffer.
        end = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as source:
            for member in source:
                end = max(end, member.offset_data + ((member.size + 511) // 512) * 512)
        hidden = tarfile.TarInfo('hidden'); hidden.size = 1
        changed = raw[:end] + b'\0' * 1024 + hidden.tobuf() + b'x' + b'\0' * 511
        archive.write_bytes(gzip.compress(changed))
        with self.assertRaisesRegex(ValueError, 'trailer'):
            graph.extract_archive(archive, fixture.records, self.root / 'hidden')
        archive = self.archive(root)
        bad = bytearray(archive.read_bytes()); bad[-8] ^= 1; archive.write_bytes(bad)
        with self.assertRaises((OSError, EOFError)):
            graph.extract_archive(archive, fixture.records, self.root / 'badcrc')

    def test_owned_registry_checks_exact_name_id_label_and_loopback_both_views(self):
        state = {'owner': 'a' * 32, 'container_id': 'b' * 64, 'name': 'symphony-native-' + 'a' * 32, 'cleanup': 'pending', 'host_port': 53058}
        path = self.root / 'state'; path.write_bytes(encode(state))
        binding = {'5000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '53058'}]}
        value = {'Id': state['container_id'], 'Name': '/' + state['name'], 'Config': {'Labels': {'com.symphony.native-test-owner': state['owner']}},
                 'State': {'Running': True}, 'HostConfig': {'PortBindings': binding}, 'NetworkSettings': {'Ports': binding}}
        with mock.patch.object(graph.subprocess, 'check_output', side_effect=lambda *a, **k: json.dumps([value])):
            self.assertEqual(graph.owned_registry(path), state)
            value['Config']['Labels']['com.symphony.native-test-owner'] = 'foreign'
            with self.assertRaisesRegex(ValueError, 'ownership'): graph.owned_registry(path)

    def test_true_config_from_manifest_store_and_classic_refuses_transport_identity(self):
        fixture = Fixture(sources=('a',)); registry = FakeRegistry(fixture); record = fixture.records[0]
        reference = record['image'].replace(':5000/', ':53058/'); identity = reference.split('@')[1]
        for actual_id, descriptor, accepted in ((record['image_config'], {}, True),
            (identity, {'digest': identity, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json'}, True),
            ('sha256:' + 'f' * 64, {'digest': 'sha256:' + 'f' * 64, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json'}, False)):
            with self.subTest(actual_id=actual_id), mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry', side_effect=registry.client):
                inspect = {'Id': actual_id, 'Descriptor': descriptor, 'RepoDigests': [reference]}
                if accepted:
                    self.assertEqual(graph.qualified_config(reference, self.root / 'state', inspect)[1], record['image_config'])
                else:
                    with self.assertRaisesRegex(ValueError, 'neither qualified'): graph.qualified_config(reference, self.root / 'state', inspect)

    def test_destination_digest_change_and_foreign_registry_refuse_before_http(self):
        fixture = Fixture(sources=('a',)); root = fixture.write(self.root / 'graph'); record = fixture.records[0]
        for destination in ('localhost:53058/symphony-codex@sha256:' + 'f' * 64, record['image'].replace('localhost:5000', 'example.com')):
            with mock.patch.object(graph, 'owned_registry', return_value={'host_port': 53058}), mock.patch.object(graph, 'Registry') as registry:
                with self.assertRaises(ValueError): graph.restore_graphs(root, fixture.records, self.root / 'state', {record['image']: destination})
                registry.return_value.request.assert_not_called()


if __name__ == '__main__': unittest.main()
