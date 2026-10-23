import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import component_discovery as discovery
import component_provenance as provenance
from test_component_provenance import record


class DiscoveryTest(unittest.TestCase):
    def artifact(self, value, name='codex-arm64.json'):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr(name, json.dumps(value))
        data = output.getvalue()
        return {'id': 1, 'name': 'runner-records-codex-arm64', 'expired': False,
                'size_in_bytes': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}, data

    def test_discovers_exact_previous_record_in_shared_archive(self):
        value = record()
        value['qualification']['record_filename'] = 'previous/codex-arm64.json'
        metadata, data = self.artifact(value, 'previous/codex-arm64.json')
        def fetch(path, binary=False):
            return data if binary else {'artifacts': [metadata]}
        self.assertEqual(discovery.discover(fetch, candidate_run=123), [value])
        self.assertEqual(discovery.discover(fetch, candidate_run=456), [])

    def test_corrupt_missing_expired_and_oversized_metadata_never_selects(self):
        metadata, data = self.artifact(record())
        for change in ({'expired': True}, {'size_in_bytes': 2**30}, {'digest': 'sha256:' + '0'*64}):
            def fetch(path, binary=False):
                return data if binary else {'artifacts': [dict(metadata, **change)]}
            self.assertEqual(discovery.discover(fetch), [])

    def test_discovery_is_bounded(self):
        metadata, data = self.artifact(record())
        calls = []
        def fetch(path, binary=False):
            calls.append(path)
            return data if binary else {'artifacts': [dict(metadata, id=n) for n in range(100)]}
        self.assertEqual(len(discovery.discover(fetch)), discovery.MAX_RECORD_ARTIFACTS)
        self.assertEqual(len([path for path in calls if path.endswith('/zip')]), discovery.MAX_RECORD_ARTIFACTS)

    def test_both_controller_and_source_require_protected_lineage(self):
        seen = []
        def fetch(path, binary=False):
            seen.append(path)
            return {'status': 'diverged', 'merge_base_commit': {'sha': 'f'*40}}
        with self.assertRaisesRegex(ValueError, 'outside verified'):
            provenance.trusted_lineage('a'*40, fetch)
        self.assertEqual(seen, ['compare/'+'a'*40+'...dev', 'compare/'+'a'*40+'...main'])
        def wrong_base(path, binary=False):
            return {'status': 'ahead', 'merge_base_commit': {'sha': 'b'*40}}
        with self.assertRaises(ValueError):
            provenance.trusted_lineage('a'*40, wrong_base)
