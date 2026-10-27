"""Explicit historical integration selection still proves real native origins."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import component_provenance as provenance
import component_selection as selection
import test_component_provenance as fixtures


class ManualIntegrationOriginTest(unittest.TestCase):
    def setUp(self):
        fixtures.ComponentProofTest.setUp(self)
        self.run['path'] = '.github/workflows/runner-integration.yml'
        self.payload['expires_at'] = self.metadata['expires_at'] = '2099-01-01T00:00:00Z'
        self.calls = []
        self.env = patch.dict(os.environ, {
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_REPOSITORY': provenance.REPOSITORY,
            'GITHUB_RUN_ID': '456',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def fetch(self, path, binary=False):
        self.calls.append(path)
        try:
            return fixtures.ComponentProofTest.fetch(self, path, binary)
        except KeyError:
            raise ValueError('fixture origin service resource unavailable') from None

    def verify(self, **kwargs):
        return provenance.verify_origin(self.record, fetch=self.fetch, now=self.now, **kwargs)

    def encode_original(self, original):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('codex-arm64.json', json.dumps(original))
        self.zip = buffer.getvalue()
        self.metadata.update(size_in_bytes=len(self.zip),
                             digest='sha256:' + hashlib.sha256(self.zip).hexdigest())

    def test_selected_manual_native_pass_survives_overall_integration_failure(self):
        self.assertEqual(self.run['conclusion'], 'failure')
        direct = self.verify(candidate_run=123)
        manual = selection.manual_verifier(123, fetch=self.fetch)(self.record)
        selected = provenance.selection_verifier(123, fetch=self.fetch)(self.record)
        self.assertEqual(direct, manual)
        self.assertEqual(direct, selected)
        self.assertEqual(direct['native_qualification_scope'], 'successful-native-job')
        self.assertEqual(direct['record_artifact_id'], 654)
        source_check = 'compare/' + 'b' * 40 + '...' + 'a' * 40
        self.assertIn(source_check, self.calls)
        self.assertLess(self.calls.index(source_check),
                        self.calls.index('actions/runs/123/attempts/1/jobs?per_page=100'))

    def test_default_unselected_and_wrong_current_context_refuse(self):
        for kwargs in ({}, {'candidate_run': 456}, {'candidate_run': True},
                       {'current_context': {'run_id': 456, 'controller_revision': 'a' * 40,
                                            'event': 'workflow_dispatch'}},
                       {'current_context': {'run_id': 123, 'controller_revision': 'f' * 40,
                                            'event': 'workflow_dispatch'}},
                       {'current_context': {'run_id': 123, 'controller_revision': 'a' * 40,
                                            'event': 'push'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.verify(**kwargs)

    def test_manual_selection_requires_explicit_fork_dispatch_context(self):
        for values in ({'GITHUB_EVENT_NAME': 'push'}, {'GITHUB_EVENT_NAME': 'pull_request'},
                       {'GITHUB_EVENT_NAME': ''}, {'GITHUB_REPOSITORY': ''},
                       {'GITHUB_REPOSITORY': 'foreign/fork'}):
            for factory in (selection.manual_verifier, provenance.selection_verifier):
                with self.subTest(values=values, factory=factory.__name__), \
                        patch.dict(os.environ, values), self.assertRaises(ValueError):
                    factory(123, fetch=self.fetch)
        for selected in (True, 0, -1, '123'):
            for factory in (selection.manual_verifier, provenance.selection_verifier):
                with self.subTest(selected=selected, factory=factory.__name__), \
                        self.assertRaises(ValueError):
                    factory(selected, fetch=self.fetch)

    def test_selected_integration_refuses_nonmanual_unknown_and_foreign_origins(self):
        for field, value in (('event', 'push'), ('event', 'pull_request'),
                             ('path', '.github/workflows/unknown.yml'),
                             ('repository', {'full_name': 'foreign/fork'}),
                             ('head_repository', {'full_name': 'foreign/fork'}),
                             ('head_sha', 'f' * 40)):
            previous = self.run[field]
            self.run[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.verify(candidate_run=123)
            self.run[field] = previous

    def test_nonpr_current_context_refuses_foreign_head_repository(self):
        self.run['head_repository'] = {'full_name': 'foreign/fork'}
        context = {'run_id': 123, 'controller_revision': 'a' * 40,
                   'event': 'workflow_dispatch'}
        with self.assertRaises(ValueError):
            self.verify(current_context=context)

    def test_same_fork_current_manual_context_still_passes(self):
        context = {'run_id': 123, 'controller_revision': 'a' * 40,
                   'event': 'workflow_dispatch'}
        self.assertEqual(self.verify(current_context=context)['artifact_id'], 987)

    def test_source_outside_selected_controller_lineage_refuses_before_native_jobs(self):
        def fetch(path, binary=False):
            if path.startswith('compare/'):
                self.calls.append(path)
                return {'status': 'diverged', 'merge_base_commit': {'sha': 'f' * 40}}
            return self.fetch(path, binary)
        with self.assertRaisesRegex(ValueError, 'lineage'):
            provenance.verify_origin(self.record, fetch=fetch, now=self.now, candidate_run=123)
        self.assertFalse(any('/jobs?' in path for path in self.calls))

    def test_failed_wrong_architecture_ambiguous_or_changed_attempt_refuse(self):
        original = copy.deepcopy(self.jobs)
        for conclusion in ('failure', 'cancelled', 'skipped'):
            self.jobs['jobs'][0]['conclusion'] = conclusion
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                self.verify(candidate_run=123)
        self.jobs = copy.deepcopy(original)
        self.jobs['jobs'][0]['labels'] = ['ubuntu-24.04']
        with self.assertRaisesRegex(ValueError, 'native platform'):
            self.verify(candidate_run=123)
        self.jobs = copy.deepcopy(original)
        self.jobs['jobs'].append(copy.deepcopy(self.jobs['jobs'][0]))
        with self.assertRaises(ValueError):
            self.verify(candidate_run=123)
        self.jobs = original
        self.record['qualification']['run_attempt'] = 2
        self.encode_original(self.record)
        with self.assertRaisesRegex(ValueError, 'resource unavailable'):
            self.verify(candidate_run=123)
        self.assertIn('actions/runs/123/attempts/2/jobs?per_page=100', self.calls)

    def test_modified_expired_and_absent_payloads_refuse(self):
        for field, value in (('digest', 'sha256:' + '0' * 64), ('expired', True),
                             ('expires_at', '2026-01-01T00:00:00Z'), ('size_in_bytes', 0),
                             ('workflow_run', {'id': 456, 'head_sha': 'a' * 40})):
            previous = self.payload[field]
            self.payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(candidate_run=123)
            self.payload[field] = previous

    def test_original_record_modification_and_checksum_corruption_refuse(self):
        original = copy.deepcopy(self.record)
        original['tool_versions']['codex'] = 'modified-original'
        self.encode_original(original)
        with self.assertRaisesRegex(ValueError, 'differs from the original'):
            self.verify(candidate_run=123)
        self.encode_original(self.record)
        self.zip += b'corruption'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.verify(candidate_run=123)

    def test_expired_modified_and_foreign_small_record_metadata_refuse(self):
        for field, value in (('expired', True), ('expires_at', '2026-01-01T00:00:00Z'),
                             ('size_in_bytes', 0),
                             ('workflow_run', {'id': 456, 'head_sha': 'a' * 40}),
                             ('digest', 'sha256:' + '0' * 64)):
            previous = self.metadata[field]
            self.metadata[field] = value
            try:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.verify(candidate_run=123)
            finally:
                self.metadata[field] = previous

    def test_unavailable_payload_and_missing_original_record_refuse(self):
        def unavailable(path, binary=False):
            if path == 'actions/artifacts/987':
                raise ValueError('fixture payload unavailable')
            return self.fetch(path, binary)
        with self.assertRaisesRegex(ValueError, 'payload unavailable'):
            provenance.verify_origin(self.record, fetch=unavailable, now=self.now,
                                     candidate_run=123)

        def missing_record(path, binary=False):
            if path == 'actions/runs/123/artifacts?per_page=100':
                return {'total_count': 0, 'artifacts': []}
            return self.fetch(path, binary)
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            provenance.verify_origin(self.record, fetch=missing_record, now=self.now,
                                     candidate_run=123)


if __name__ == '__main__':
    unittest.main()
