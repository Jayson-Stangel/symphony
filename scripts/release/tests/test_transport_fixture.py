"""Pure service peers only; never credentials, dispatch, mutation or native work."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).parents[1]))
import transport_fixture as fixture
import staging_ingress as ingress
import test_staging_ingress as peers


class Response:
    def __init__(self, status, headers=None, raw=b''):
        self.status, self.headers, self.raw = status, headers or {}, io.BytesIO(raw)
    def read(self, size):return self.raw.read(size)


class RegistryPeer:
    def __init__(self, unknown=False, wrong_digest=False):
        self.blobs, self.manifests, self.calls = {}, {}, []
        self.unknown, self.wrong_digest = unknown, wrong_digest

    def connection(self, host, **kwargs):
        outer = self
        class Connection:
            def request(self, method, path, body=None, headers=None):
                raw = b''
                if hasattr(body, 'read'):
                    while block := body.read(7):raw += block
                elif body is not None:raw = body
                outer.calls.append((host, method, path, raw))
                self.method, self.path, self.raw = method, path, raw
            def getresponse(self):
                method, path, raw = self.method, self.path, self.raw
                if path.startswith('/token?'):
                    return Response(200, raw=json.dumps({'token': 'SYNTHETIC-SECRET-TOKEN', 'expires_in': 300}).encode())
                if method == 'HEAD':
                    identity = path.rsplit('/', 1)[-1]
                    return Response(200, {'Docker-Content-Digest': identity, 'Content-Length': str(len(outer.blobs[identity]))}) if identity in outer.blobs else Response(404)
                if method == 'POST':
                    return Response(202, {'Location': 'https://ghcr.io/v2/' + fixture.REPOSITORY + '/blobs/upload/SECRET_SESSION.a==?_state=SECRET%2BSTATE'})
                if method == 'PUT':
                    identity = parse_qs(path.split('?', 1)[1])['digest'][0] if '/blobs/' in path else path.rsplit('/', 1)[-1]
                    if fixture.graph.digest(raw) != identity:raise AssertionError('peer receives changed bytes')
                    (outer.blobs if '/blobs/' in path else outer.manifests)[identity] = raw
                    if outer.unknown:raise ConnectionError('SECRET_SESSION.a== unknown effect')
                    return Response(201, {'Docker-Content-Digest': 'sha256:' + 'f' * 64 if outer.wrong_digest else identity})
                raise AssertionError((method, path))
            def close(self):pass
        return Connection()

    def probe(self, image):
        return fixture.MANIFEST in self.manifests

    def closure(self, image, full=False):
        assert image == fixture.IMAGE and full
        _, _, files = fixture.fixture()
        return (self.manifests == {fixture.MANIFEST: files['blobs/sha256/' + fixture.MANIFEST[7:]]}
                and self.blobs == {identity: files['blobs/sha256/' + identity[7:]] for identity in (fixture.CONFIG, fixture.LAYER)})


class TransportFixtureTests(unittest.TestCase):
    def setUp(self):
        self.peer = peers.StagingIngressTest(); self.peer.setUp(); self.addCleanup(self.peer.doCleanups)
        self.root = self.peer.root.resolve()
        self.payload, _, _ = fixture.fixture()
        self.peer.payload = self.payload
        self.peer.staging_digest = fixture.PAYLOAD_DIGEST
        self.peer.origin.update(digest=fixture.PAYLOAD_DIGEST, size=165, name='jay772-opaque-fixture.zip')
        self.peer.run.update(head_branch='dev', run_attempt=1)
        self.peer.attempt.update(head_branch='dev', run_attempt=1)
        self.peer.job['name'] = fixture.CONTRACT.producer_job
        self.reader = dict(self.peer.job, id=92, name=fixture.READ_JOB)
        self.peer.jobs = {'total_count': 2, 'jobs': [self.peer.job, self.reader]}
        self.calls = []

    def env(self, mode):
        value = self.peer.context(fixture.JOBS[mode])
        value.update(GITHUB_REF='refs/heads/dev', GITHUB_RUN_ATTEMPT='1',
            GITHUB_WORKFLOW_REF=ingress.REPOSITORY + '/' + ingress.WORKFLOW + '@refs/heads/dev',
            GITHUB_ACTOR='Jayson-Stangel', TRANSPORT_FIXTURE_ONLY='true',
            APPROVED_REVISION=self.peer.revision, GH_TOKEN='SYNTHETIC-CREDENTIAL',
            STAGING_RELEASE='11', STAGING_ASSET='12', STAGING_DIGEST=fixture.PAYLOAD_DIGEST)
        if hasattr(self.peer, 'artifact'):
            value.update(INGRESS_ARTIFACT_ID='81', INGRESS_ARTIFACT_NAME=self.peer.artifact['name'],
                         INGRESS_ARTIFACT_DIGEST=self.peer.artifact['digest'])
        return value

    def fetch(self, path, binary=False):
        self.calls.append((path, binary))
        if path == 'actions/runs/71':return self.peer.run
        if path == 'actions/runs/71/attempts/1':return self.peer.attempt
        if path == 'actions/runs/71/attempts/1/jobs?per_page=100':return self.peer.jobs
        if path == 'actions/artifacts/81':return self.peer.artifact
        if path == 'actions/artifacts/81/zip':return self.peer.archive
        self.fail('unexpected endpoint')

    def download(self, *selection, max_bytes):
        self.assertEqual(max_bytes, 165)
        return self.payload, copy.deepcopy(self.peer.origin)

    def execute(self, mode, **kwargs):
        env = kwargs.pop('env', self.env(mode))
        return fixture.execute(mode, self.root / mode, env=env, fetch=self.fetch,
            download=self.download, invoke=self.peer.git, **kwargs)

    def prepare(self):
        result = self.execute('stage')
        self.peer.manifest = json.loads((self.root / 'stage/ingress.json').read_text())
        self.peer.artifact = {'id': 81, 'name': result['artifact_name'], 'digest': '', 'size_in_bytes': 1,
            'expired': False, 'expires_at': '2099-01-01T00:00:00Z', 'created_at': '2026-01-01T01:01:00Z',
            'workflow_run': {'id': 71, 'head_sha': self.peer.revision}}
        self.peer.package()
        return result

    def test_exact_data_and_actual_opaque_stage_receive_keep_original_ids(self):
        self.assertEqual(len(self.payload), 165)
        self.prepare(); result = self.execute('receive')
        self.assertEqual((self.root / 'receive/payload.zip').read_bytes(), self.payload)
        self.assertEqual(result['origin'], self.peer.origin)
        self.assertEqual(result['scope'], 'private-staging-fixture-proof-only')
        self.assertEqual(result['artifact']['id'], 81)
        self.assertEqual(result['artifact']['run_attempt'], 1)
        with self.assertRaises(ValueError):
            ingress.stage('11', '12', fixture.PAYLOAD_DIGEST, self.peer.revision, self.root / 'production',
                          context=self.env('stage'), invoke=self.peer.git, fetch=self.fetch)
        self.assertFalse((self.root / 'production').exists())

    def test_wrong_owner_optin_source_ref_event_attempt_and_digest_stop_before_api(self):
        changes = [('GITHUB_REPOSITORY', 'foreign/repo'), ('GITHUB_ACTOR', 'Other'), ('TRANSPORT_FIXTURE_ONLY', 'false'),
            ('APPROVED_REVISION', 'f' * 40), ('GITHUB_REF', 'refs/heads/main'),
            ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_RUN_ATTEMPT', '2'),
            ('GITHUB_WORKFLOW_SHA', 'f' * 40), ('GITHUB_JOB', 'verify'), ('STAGING_DIGEST', 'sha256:' + 'f' * 64)]
        for key, value in changes:
            self.calls.clear(); env = self.env('stage');env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):self.execute('stage', env=env)
            self.assertEqual(self.calls, [])
            self.assertFalse((self.root / 'stage').exists())

    def test_private_fixture_changed_name_or_bytes_cannot_be_staged(self):
        self.peer.origin['name'] = 'other.zip'
        with self.assertRaises(ValueError):self.execute('stage')
        self.assertFalse((self.root / 'stage').exists())

    def test_immutable_artifact_bound_is_checked_before_download(self):
        self.prepare();self.peer.artifact['size_in_bytes'] = 65537;self.calls.clear()
        with self.assertRaises(ValueError):self.execute('receive')
        self.assertNotIn(('actions/artifacts/81/zip', True), self.calls)
        self.assertFalse((self.root / 'receive').exists())

    def test_canonical_upload_confirmed_interruption_one_resume_and_no_secret_evidence(self):
        self.prepare();self.execute('receive');peer = RegistryPeer()
        result = self.execute('registry', connection=peer.connection, probe=peer.probe, closure=peer.closure)
        self.assertEqual(result['continuations'], 1)
        self.assertTrue(peer.closure(fixture.IMAGE, full=True))
        puts = [row for row in peer.calls if row[1] == 'PUT' and '/blobs/' in row[2]]
        self.assertEqual(len(puts), 2)
        self.assertEqual(len({row[2].split('digest=')[1] for row in puts}), 2)
        raw = (self.root / 'registry/registry-proof.json').read_text(); rows = json.loads(raw)
        self.assertEqual(sum(row['event'] == 'confirmed-interruption' for row in rows), 1)
        self.assertEqual(sum(row['event'] == 'explicit-continuation' for row in rows), 1)
        self.assertEqual(rows[-1]['event'], 'success')
        for secret in ('SECRET_SESSION', 'SECRET%2BSTATE', 'SECRET-TOKEN', 'SYNTHETIC-CREDENTIAL'):
            self.assertNotIn(secret, raw)
        self.assertTrue(all(row[0] == 'ghcr.io' for row in peer.calls))

    def test_unknown_write_stops_without_any_continuation_or_secret_error(self):
        self.prepare();peer = RegistryPeer(unknown=True)
        with self.assertRaises(ConnectionError):
            self.execute('registry', connection=peer.connection, probe=peer.probe, closure=peer.closure)
        raw = (self.root / 'registry/registry-proof.json').read_text()
        self.assertNotIn('SECRET_SESSION', raw)
        self.assertNotIn('explicit-continuation', raw)
        self.assertEqual(len([row for row in peer.calls if row[1] == 'PUT']), 1)
        self.assertEqual(peer.manifests, {})
        self.assertEqual(json.loads(raw)[-1]['event'], 'failure')

    def test_wrong_commit_digest_or_existing_manifest_never_resume(self):
        self.prepare();peer = RegistryPeer(wrong_digest=True)
        with self.assertRaises(ValueError):
            self.execute('registry', connection=peer.connection, probe=peer.probe, closure=peer.closure)
        self.assertEqual(peer.manifests, {})
        self.assertNotIn('explicit-continuation', (self.root / 'registry/registry-proof.json').read_text())

    def test_missing_reader_stops_before_any_registry_access(self):
        self.prepare();self.reader['conclusion'] = 'skipped';peer = RegistryPeer()
        with self.assertRaises(ValueError):
            self.execute('registry', connection=peer.connection, probe=peer.probe, closure=peer.closure)
        self.assertEqual(peer.calls, [])

    def test_existing_fixture_cannot_silently_remove_upload_proof(self):
        self.prepare();peer = RegistryPeer();_, _, files = fixture.fixture()
        peer.blobs = {identity: files['blobs/sha256/' + identity[7:]] for identity in (fixture.CONFIG, fixture.LAYER)}
        with self.assertRaises(ValueError):
            self.execute('registry', connection=peer.connection, probe=peer.probe, closure=peer.closure)
        self.assertFalse(any(row[1] in ('POST', 'PUT') for row in peer.calls))


if __name__ == '__main__':unittest.main()
