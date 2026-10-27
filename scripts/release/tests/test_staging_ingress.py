"""Opaque transport peers; no release, artifact, credential or native effects."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import staging_ingress as ingress


class StagingIngressTest(unittest.TestCase):
    revision = 'a' * 40

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Deliberately not a valid ZIP: neither transport endpoint may unpack it.
        self.payload = b'PK OPAQUE signed archive bytes\x1b[31m MUST remain exact'
        self.staging_digest = 'sha256:' + hashlib.sha256(self.payload).hexdigest()
        self.origin = {'schema': 1, 'origin': 'local', 'repository': ingress.REPOSITORY,
                       'source_revision': self.revision, 'release_id': 11, 'asset_id': 12,
                       'digest': self.staging_digest, 'size': len(self.payload), 'name': 'local-signed.zip',
                       'staging_tag': 'local-signing-' + self.revision + '-' + 'f' * 32}
        self.run = {'id': 71, 'run_attempt': 2, 'head_sha': self.revision, 'head_branch': 'main',
                    'event': 'workflow_dispatch', 'path': ingress.WORKFLOW,
                    'head_repository': {'full_name': ingress.REPOSITORY}, 'status': 'in_progress',
                    'conclusion': None, 'updated_at': '2020-01-01T00:00:00Z'}
        self.attempt = copy.deepcopy(self.run)
        self.job = {'id': 91, 'name': ingress.PRODUCER_JOB, 'status': 'completed', 'conclusion': 'success',
                    'started_at': '2026-01-01T01:00:00Z', 'completed_at': '2026-01-01T01:02:00Z'}
        self.jobs = {'total_count': 1, 'jobs': [self.job]}
        self.calls = []
        self.git_calls = []
        self.download_calls = []

    def context(self, job):
        return {'GITHUB_REPOSITORY': ingress.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': self.revision,
                'GITHUB_WORKFLOW_SHA': self.revision,
                'GITHUB_WORKFLOW_REF': ingress.REPOSITORY + '/' + ingress.WORKFLOW + '@refs/heads/main',
                'GITHUB_JOB': job, 'GITHUB_RUN_ID': '71', 'GITHUB_RUN_ATTEMPT': '2'}

    def git(self, argv, **kwargs):
        self.git_calls.append(argv)
        self.assertEqual(argv[0], 'git')
        return SimpleNamespace(returncode=0, stdout=self.revision + '\n' if 'rev-parse' in argv else '')

    def download(self, *args):
        self.download_calls.append(args)
        return self.payload, copy.deepcopy(self.origin)

    def fetch(self, path, binary=False):
        self.calls.append((path, binary))
        if path == 'actions/runs/71':return self.run
        if path == 'actions/runs/71/attempts/2':return self.attempt
        if path == 'actions/runs/71/attempts/2/jobs?per_page=100':return self.jobs
        if path == 'actions/artifacts/81':return self.artifact
        if path == 'actions/artifacts/81/zip':
            self.assertTrue(binary)
            return self.archive
        self.fail('unexpected API endpoint: ' + path)

    def stage(self, destination=None, context=None):
        return ingress.stage('11', '12', self.staging_digest, self.revision, destination or self.root / 'stage',
                             download=self.download, fetch=self.fetch,
                             context=context or self.context('staging-ingress'), invoke=self.git)

    def package(self, manifest=None, payload=None, entries=None):
        if manifest is None:manifest = self.manifest
        if payload is None:payload = self.payload
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            if entries is None:
                archive.writestr('payload.zip', payload)
                archive.writestr('ingress.json', json.dumps(manifest))
            else:
                for info, value in entries:archive.writestr(info, value)
        self.archive = buffer.getvalue()
        self.artifact['digest'] = 'sha256:' + hashlib.sha256(self.archive).hexdigest()
        self.artifact['size_in_bytes'] = len(self.archive)

    def prepare(self):
        result = self.stage()
        self.manifest = json.loads(result['manifest'].read_text())
        self.artifact = {'id': 81, 'name': result['artifact_name'], 'digest': '', 'size_in_bytes': 1,
                         'expired': False, 'expires_at': '2099-01-01T00:00:00Z',
                         'created_at': '2026-01-01T01:01:00Z',
                         'workflow_run': {'id': 71, 'head_sha': self.revision}}
        self.package()
        self.calls.clear()
        return result

    def receive(self, **changes):
        options = {'artifact_id': '81', 'artifact_name': self.artifact['name'],
                   'artifact_digest': self.artifact['digest'], 'release_id': '11', 'asset_id': '12',
                   'staging_digest': self.staging_digest, 'revision': self.revision,
                   'destination': self.root / 'received', 'fetch': self.fetch,
                   'context': self.context('verify'), 'invoke': self.git}
        options.update(changes)
        return ingress.receive(**options)

    def test_stage_keeps_only_exact_opaque_bytes_and_honest_private_origin(self):
        result = self.stage()
        self.assertEqual({p.name for p in result['payload'].parent.iterdir()}, {'payload.zip', 'ingress.json'})
        self.assertEqual(result['payload'].read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(result['payload'].parent.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in result['payload'].parent.iterdir()))
        manifest = json.loads(result['manifest'].read_text())
        self.assertEqual(manifest['original_origin'], self.origin)
        self.assertEqual(manifest['scope'], 'private-staging-ingress-only')
        self.assertEqual(manifest['run_attempt'], 2)
        self.assertEqual(self.download_calls, [('11', '12', self.staging_digest, self.revision)])
        self.assertEqual(len(self.git_calls), 2)

    def test_receive_accepts_same_running_workflow_without_whole_run_completion(self):
        self.prepare()
        result = self.receive()
        self.assertEqual(result['payload'].read_bytes(), self.payload)
        self.assertEqual(result['origin'], self.origin)
        self.assertEqual(result['artifact']['id'], 81)
        self.assertEqual(result['artifact']['producer_job_id'], 91)
        self.assertEqual(result['artifact']['run_attempt'], 2)
        self.assertEqual(self.run['status'], 'in_progress')
        self.assertEqual(self.calls[-1], ('actions/artifacts/81/zip', True))
        self.assertFalse(any('/artifacts?' in path or 'releases/' in path for path, _ in self.calls))

    def test_fork_ref_workflow_event_sha_job_and_dirty_context_refuse_before_api(self):
        mutations = {'GITHUB_REPOSITORY': 'foreign/repo', 'GITHUB_EVENT_NAME': 'push',
                     'GITHUB_REF': 'refs/heads/dev', 'GITHUB_SHA': 'b' * 40,
                     'GITHUB_WORKFLOW_SHA': 'b' * 40, 'GITHUB_WORKFLOW_REF': 'other',
                     'GITHUB_JOB': 'verify', 'GITHUB_RUN_ID': '0', 'GITHUB_RUN_ATTEMPT': '0'}
        for key, value in mutations.items():
            with self.subTest(key=key):
                context = self.context('staging-ingress');context[key] = value
                with self.assertRaises(ValueError):self.stage(context=context)
                self.assertEqual(self.calls, [])
        def dirty(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout=self.revision if 'rev-parse' in argv else ' M input')
        with self.assertRaises(ValueError):
            ingress.stage('11', '12', self.staging_digest, self.revision, self.root / 'dirty',
                          download=self.download, fetch=self.fetch, context=self.context('staging-ingress'), invoke=dirty)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.download_calls, [])

    def test_api_run_and_attempt_must_match_actual_context_before_private_download(self):
        for field, value in (('id', 72), ('run_attempt', 1), ('head_sha', 'b' * 40),
                             ('head_branch', 'dev'), ('event', 'pull_request'), ('path', 'other.yml'),
                             ('head_repository', {'full_name': 'foreign/repo'}), ('status', 'queued')):
            for record in (self.run, self.attempt):
                with self.subTest(field=field, record=record is self.run):
                    old = record[field];record[field] = value
                    with self.assertRaises(ValueError):self.stage()
                    record[field] = old
                    self.assertEqual(self.download_calls, [])

    def test_stage_refuses_original_identity_or_bytes_changes_without_output(self):
        changes = ({'release_id': 13}, {'asset_id': 14}, {'source_revision': 'b' * 40},
                   {'size': 1}, {'digest': 'sha256:' + '0' * 64}, {'staging_tag': 'v0.0.6'})
        for change in changes:
            original = copy.deepcopy(self.origin);self.origin.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):self.stage()
            self.origin = original
            self.assertFalse((self.root / 'stage').exists())

    def test_unique_completed_successful_producer_and_complete_jobs_required(self):
        self.prepare()
        variants = [{'total_count': 0, 'jobs': []}, {'total_count': 2, 'jobs': [self.job, self.job]},
                    {'total_count': 2, 'jobs': [self.job]}, {'total_count': 101, 'jobs': []}]
        variants += [{'total_count': 1, 'jobs': [dict(self.job, **change)]} for change in
                     ({'status': 'in_progress'}, {'conclusion': 'failure'}, {'conclusion': 'skipped'}, {'name': 'native'})]
        for jobs in variants:
            with self.subTest(jobs=jobs):
                self.jobs = jobs
                with self.assertRaises(ValueError):self.receive()
                self.assertFalse(any(path.endswith('/zip') for path, _ in self.calls))
                self.calls.clear()

    def test_selected_artifact_service_identity_and_expiry_refuse_before_download(self):
        self.prepare()
        original = copy.deepcopy(self.artifact)
        variants = ({'id': 82}, {'name': 'latest'}, {'digest': 'sha256:' + '0' * 64},
                    {'expired': True}, {'expires_at': '2000-01-01T00:00:00Z'},
                    {'size_in_bytes': ingress.MAX_ARTIFACT + 1},
                    {'workflow_run': {'id': 70, 'head_sha': self.revision}},
                    {'workflow_run': {'id': 71, 'head_sha': 'b' * 40}})
        for change in variants:
            self.artifact = dict(original, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.receive(artifact_name=original['name'], artifact_digest=original['digest'])
            self.assertFalse(any(path.endswith('/zip') for path, _ in self.calls));self.calls.clear()

    def test_artifact_creation_is_bound_to_successful_producer_not_run_updated_at(self):
        self.prepare()
        for timestamp in ('2026-01-01T00:59:59Z', '2026-01-01T01:02:01Z'):
            self.artifact['created_at'] = timestamp
            with self.assertRaises(ValueError):self.receive()
        for number, timestamp in enumerate((self.job['started_at'], self.job['completed_at'])):
            self.artifact['created_at'] = timestamp
            self.receive(destination=self.root / ('edge-' + str(number)))

    def test_cross_attempt_manifest_or_original_dispatch_identity_cannot_rebind_bytes(self):
        self.prepare()
        original = copy.deepcopy(self.manifest)
        for change in ({'run_attempt': 1}, {'run_id': 72}, {'source_revision': 'b' * 40},
                       {'scope': 'production-signed'}, {'unknown': True}):
            self.package(manifest=dict(original, **change))
            with self.subTest(change=change), self.assertRaises(ValueError):self.receive()
        self.package(manifest=original)
        for change in ({'release_id': '13'}, {'asset_id': '14'}, {'staging_digest': 'sha256:' + '0' * 64},
                       {'artifact_name': 'latest'}, {'artifact_digest': 'sha256:' + '0' * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):self.receive(**change)
        self.assertFalse((self.root / 'received').exists())

    def test_immutable_outer_digest_and_inner_payload_binding_refuse_tamper(self):
        self.prepare()
        expected = self.artifact['digest'];self.archive += b'changed'
        with self.assertRaises(ValueError):self.receive(artifact_digest=expected)
        self.package(payload=b'altered opaque bytes')
        with self.assertRaises(ValueError):self.receive()
        self.assertFalse((self.root / 'received').exists())

    def test_transport_rejects_duplicates_paths_links_and_oversized_members(self):
        self.prepare()
        symlink = zipfile.ZipInfo('payload.zip');symlink.external_attr = 0o120777 << 16
        variants = [[('../escape', b'x'), ('ingress.json', b'{}')],
                    [('payload.zip', b'x'), ('payload.zip', b'y')],
                    [(symlink, b'x'), ('ingress.json', b'{}')],
                    [('payload.zip', self.payload), ('ingress.json', b'x' * (ingress.MAX_MANIFEST + 1))]]
        for entries in variants:
            self.package(entries=entries)
            with self.subTest(entries=[str(n) for n, _ in entries]), self.assertRaises(ValueError):self.receive()
        self.assertFalse((self.root / 'received').exists())

    def test_transport_does_not_overwrite_existing_destination(self):
        self.prepare()
        result = self.receive()
        with self.assertRaises(ValueError):self.receive()
        self.assertEqual(result['payload'].read_bytes(), self.payload)

    def test_same_transport_core_uses_actual_distinct_feature_context_without_main_spoof(self):
        contract = ingress.TransportContract('refs/heads/codex/local-staging-release-id',
                    'private-staging-fixture-proof-only', 'local-signing-fixture-proof',
                    'Read fixed fixture into opaque handoff', 'fixture-ingress', 'fixture-read-consumer')
        context = self.context(contract.producer_key)
        context['GITHUB_REF'] = contract.ref
        context['GITHUB_WORKFLOW_REF'] = ingress.REPOSITORY + '/' + ingress.WORKFLOW + '@' + contract.ref
        self.run['head_branch'] = self.attempt['head_branch'] = 'codex/local-staging-release-id'
        with self.assertRaises(ValueError):self.stage(context=context)
        self.assertEqual(self.calls, [])
        staged = ingress.stage_core('11', '12', self.staging_digest, self.revision, self.root / 'feature',
                    contract=contract, download=self.download, fetch=self.fetch, context=context, invoke=self.git)
        self.manifest = json.loads(staged['manifest'].read_text())
        self.assertEqual(self.manifest['scope'], contract.scope)
        self.artifact = {'id': 81, 'name': staged['artifact_name'], 'digest': '', 'size_in_bytes': 1,
                        'expired': False, 'expires_at': '2099-01-01T00:00:00Z',
                        'created_at': '2026-01-01T01:01:00Z', 'workflow_run': {'id': 71, 'head_sha': self.revision}}
        self.package()
        self.job['name'] = contract.producer_job
        context['GITHUB_JOB'] = contract.consumer_key
        received = ingress.receive_core('81', self.artifact['name'], self.artifact['digest'], '11', '12',
                    self.staging_digest, self.revision, self.root / 'feature-read', contract=contract,
                    fetch=self.fetch, context=context, invoke=self.git)
        self.assertEqual(received['payload'].read_bytes(), self.payload)
        self.assertEqual(received['artifact']['scope'], contract.scope)
        with self.assertRaises(ValueError):self.receive(context=context)
        # Even a renamed artifact and a successful main producer cannot relabel
        # the fixture-only transport manifest as production admission.
        self.run['head_branch'] = self.attempt['head_branch'] = 'main'
        self.job['name'] = ingress.PRODUCER_JOB
        self.artifact['name'] = ingress.expected_artifact_name(self.revision, 71, 2)
        with self.assertRaises(ValueError):self.receive()


if __name__ == '__main__':
    unittest.main()
