"""Credential-free production selection and no-publication-before-readiness regressions."""
import copy
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
import production_release as production
import test_macos_signing_inputs as signing_fixtures
digest = signing_fixtures.digest
import test_runner_reuse as reuse_fixtures

ROOT = Path(__file__).resolve().parents[3]


class ProductionInputsTest(unittest.TestCase):
    def fixture(self):
        old_fetch, old_run, old_inventory, old_payloads, internal, helper, linux = signing_fixtures.SigningInputsTest().fixture()
        internal['channel'] = 'stable'
        for provider, group in internal['runners'].items():
            for record in group.values():
                record['image'] = 'ghcr.io/jayson-stangel/symphony-' + provider + '@sha256:' + 'c'*64
        package_files = {}
        with zipfile.ZipFile(io.BytesIO(old_payloads['actions/artifacts/12/zip'])) as z:
            package_files = {n: z.read(n) for n in z.namelist()}
        external = json.loads(package_files['release.json']); external.update(channel='stable', runners=copy.deepcopy(internal['runners']))
        package_files['release.json'] = json.dumps(external).encode()
        package_files['production-plan.json'] = json.dumps({'manager_source_revision':'a'*40, 'manifest':internal}).encode()
        helper_files = {'symphony-host':helper, 'symphony-host.sha256':(digest(helper)+'  symphony-host\n').encode()}
        inventory=[]; payloads={}
        for identifier, (name, files) in enumerate((('packaged-release', package_files),
                                                   ('native-host-helper-macos-'+'a'*40,helper_files)),10):
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:
                for filename, raw in files.items():archive.writestr(filename,raw)
            raw=buffer.getvalue();payloads[f'actions/artifacts/{identifier}/zip']=raw
            inventory.append({'id':identifier,'name':name,'digest':'sha256:'+digest(raw), 'expired':False,
                              'size_in_bytes':len(raw),'expires_at':'2099-01-01T00:00:00Z',
                              'created_at':'2026-01-01T00:01:00Z',
                              'workflow_run':{'id':123,'head_sha':'a'*40}})
        run=dict(old_run,head_branch='main',path='.github/workflows/burrito-release.yml',
                 run_started_at='2026-01-01T00:00:00Z',updated_at='2026-01-01T01:00:00Z')
        names=['Candidate qualification','Build release package (macos)','Build release package (linux_x86_64)',
               'Assemble release packages','Verify engine (linux_x86_64)','Verify engine (macos_arm64)',
               'Verify production preparation source']
        jobs=[{'name':n,'conclusion':'success'} for n in names]
        def fetch(path,binary=False):
            if path in ('actions/runs/123','actions/runs/123/attempts/2'):return run
            if path=='actions/runs/123/attempts/2/jobs?per_page=100':return {'jobs':jobs}
            if path=='actions/runs/123/artifacts?per_page=100':return {'artifacts':inventory}
            return payloads[path]
        return fetch,run,jobs,inventory,payloads,helper,linux

    def test_main_qualification_keeps_originals_and_materializes_internal_manifest(self):
        fetch,_,_,_,_,helper,linux=self.fixture()
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)/'q'
            receipt=production.qualification('123','2','a'*40,folder,fetch)
            self.assertEqual(receipt['original_qualified_helper_sha256'],digest(helper))
            self.assertEqual((folder/'packages/symphony-0.0.3-linux_x86_64.tar.gz').read_bytes(),linux)
            internal=json.loads((folder/'internal/release.json').read_text())
            self.assertNotIn('sha256',internal['bundles']['macos'])
            self.assertEqual(receipt['run_attempt'],2)

    def test_failed_foreign_revision_attempt_mode_and_skipped_producer_refuse(self):
        for change in ({'head_sha':'b'*40},{'head_branch':'dev'},{'event':'pull_request'},
                       {'conclusion':'failure'},{'run_attempt':1},{'path':'.github/workflows/runner-integration.yml'},
                       {'head_repository':{'full_name':'foreign/repo'}}):
            fetch,run,*_=self.fixture();run.update(change)
            with tempfile.TemporaryDirectory() as temporary,self.assertRaises(ValueError):
                production.qualification('123','2','a'*40,Path(temporary)/'q',fetch)
        for status in ('skipped','cancelled','failure',None):
            fetch,_,jobs,*_=self.fixture();jobs[0]['conclusion']=status
            with tempfile.TemporaryDirectory() as temporary,self.assertRaises(ValueError):
                production.qualification('123','2','a'*40,Path(temporary)/'q',fetch)

    def test_expired_foreign_attempt_and_corrupt_artifacts_refuse(self):
        changes=[{'expired':True},{'expires_at':'2000-01-01T00:00:00Z'},
                 {'created_at':'2025-12-31T00:00:00Z'}, {'workflow_run':{'id':124,'head_sha':'a'*40}},
                 {'digest':'sha256:'+'0'*64}]
        for change in changes:
            fetch,_,_,inventory,*_=self.fixture();inventory[0].update(change)
            with tempfile.TemporaryDirectory() as temporary,self.assertRaises(ValueError):
                production.qualification('123','2','a'*40,Path(temporary)/'q',fetch)

    def test_zip_duplicate_traversal_symlink_and_oversized_expansion_refuse(self):
        for filename,mode in (('../escape',0),('/escape',0),('link',0o120777)):
            fetch,_,_,inventory,payloads,*_=self.fixture();buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:
                info=zipfile.ZipInfo(filename);info.external_attr=mode<<16;archive.writestr(info,b'bad')
            raw=buffer.getvalue();payloads['actions/artifacts/10/zip']=raw
            inventory[0].update(digest='sha256:'+digest(raw),size_in_bytes=len(raw))
            with tempfile.TemporaryDirectory() as temporary,self.assertRaises(ValueError):
                production.qualification('123','2','a'*40,Path(temporary)/'q',fetch)

    def test_final_manifests_change_only_computed_archive_hashes(self):
        fetch,*_=self.fixture()
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)/'q';production.qualification('123','2','a'*40,folder,fetch)
            manifest=json.loads((folder/'packages/release.json').read_text())
            expected=json.loads((folder/'internal/release.json').read_text())
            production.same_manifest(expected,manifest)
            for mutation in (lambda m:m.update(source_revision='b'*40),lambda m:m.update(channel='candidate'),
                             lambda m:m['runners']['codex']['arm64'].update(image='ghcr.io/foreign@sha256:'+'c'*64)):
                broken=copy.deepcopy(manifest);mutation(broken)
                with self.assertRaises(ValueError):production.same_manifest(expected,broken)


class ProductionReuseTest(unittest.TestCase):
    def setUp(self):
        fixture = reuse_fixtures.ReusePlannerTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.baseline, self.verify, self.inputs = fixture.root, fixture.baseline, fixture.verify, fixture.inputs

    def test_unpublished_private_originals_schedule_real_current_source_builds(self):
        import runner_reuse
        before=copy.deepcopy(self.baseline)
        value=runner_reuse.plan(self.root,self.baseline,self.verify,self.inputs,reusable=lambda record:False)
        self.assertEqual(self.baseline,before)
        self.assertEqual(len(value['build_matrix']['include']),4)
        self.assertEqual(value['reuse_matrix']['include'],[])
        for item in value['build_matrix']['include']:
            self.assertEqual(item['source_revision'],value['manager_source_revision'])
        public=runner_reuse.plan(self.root,self.baseline,self.verify,self.inputs,reusable=lambda record:True)
        self.assertFalse(public['has_builds'])
        self.assertNotEqual(public['reuse_matrix']['include'][0]['source_revision'],public['manager_source_revision'])


class ProductionWorkflowTest(unittest.TestCase):
    def test_only_explicit_signed_publisher_can_mutate_production(self):
        runner=(ROOT/'.github/workflows/runner-release.yml').read_text()
        engine=(ROOT/'.github/workflows/burrito-release.yml').read_text()
        publish=(ROOT/'.github/workflows/production-publish.yml').read_text()
        sign=(ROOT/'.github/workflows/macos-production-signing.yml').read_text()
        self.assertNotIn('component_publication.py --prepared',runner)
        self.assertNotIn('gh release ',engine)
        self.assertIn('publish: false',engine)
        self.assertIn('test "${LEGACY_PUBLISH:-false}" != true',runner)
        self.assertIn("github.event_name == 'workflow_dispatch'",engine)
        self.assertIn('needs: production-source',engine)
        self.assertIn('environment: macos-production-signing',sign)
        self.assertIn("github.ref == 'refs/heads/main'",sign)
        self.assertNotIn('packages: write',sign)
        self.assertNotIn('contents: write',sign)
        self.assertIn('needs: [inputs, registry]',publish)
        self.assertNotIn('retained-production-inputs', publish)
        self.assertNotIn('actions/upload-artifact', publish)
        # One original retrieval per job, with mutable source rechecks at both
        # independent protected boundaries. Local verified bytes serve all
        # subsequent operations within each job.
        self.assertEqual(publish.count('production_release.py signed'), 3)
        self.assertEqual(publish.count('production_release.py source'), 3)
        self.assertEqual(publish.count('environment: production-release'),2)
        self.assertLess(publish.index('production_release.py signed'),publish.index('component_publication.py --prepared'))
        self.assertLess(publish.index('production_release.py public'),publish.index('production_release.py publish'))
        self.assertIn('production_release.py compare',publish)
        self.assertNotIn('macos_distribution.py build',publish)
        dev=(ROOT/'.github/workflows/macos-signing.yml').read_text()
        self.assertIn("github.ref == 'refs/heads/dev'",dev)
        self.assertIn('environment: macos-signing',dev)

class SignedSelectionTest(unittest.TestCase):
    def fixture(self, root):
        fetch,*_=ProductionInputsTest().fixture()
        inputs=root/'qualified';production.qualification('123','2','a'*40,inputs,fetch)
        signed=root/'signed';signed.mkdir()
        manifest=json.loads((inputs/'packages/release.json').read_text())
        mac=manifest['bundles']['macos']['filename'];dmg=mac.removesuffix('.tar.gz')+'.dmg'
        helper=b'OBVIOUS FAKE DEVELOPER-ID HELPER FOR COMMAND-PEER TEST'
        with tarfile.open(signed/mac,'w:gz') as archive:
            entry=tarfile.TarInfo('./libexec/symphony-host');entry.size=len(helper);archive.addfile(entry,io.BytesIO(helper))
        (signed/dmg).write_bytes(b'OBVIOUS FAKE POST-STAPLE DMG')
        payload='11111111-1111-4111-8111-111111111111';outer='22222222-2222-4222-8222-222222222222'
        receipt={'status':'notarized-stapled-assessed','source_revision':'a'*40,
                 'original_qualified_helper_sha256':json.loads((inputs/'qualification.json').read_text())['original_qualified_helper_sha256'],
                 'payloads':{'libexec/symphony-host':{'sha256':digest(helper),'certificate_sha1':'A'*40,
                    'team_id':'FAKETEAM01','secure_timestamp':True,'hardened_runtime':True}},
                 'dmg_signature':{'sha256':'b'*64,'certificate_sha1':'A'*40,'team_id':'FAKETEAM01','secure_timestamp':True},
                 'payload_notary_submission_id':payload,'notary_submission_id':outer,
                 'artifacts':{name:production.checksum(signed/name) for name in (mac,dmg)}}
        production.write_json(signed/'macos-distribution.json',receipt)
        evidence=signed/'evidence';evidence.mkdir()
        for label,identifier in (('payload',payload),('notary',outer)):
            production.write_json(evidence/(label+'-submission.json'),{'id':identifier,'status':'Accepted','submitted_sha256':'b'*64})
            production.write_json(evidence/(label+'-log.json'),{'id':identifier,'status':'Accepted','sha256':'b'*64,'statusCode':0,'issues_count':0})
        import package_diagnostics
        production.write_json(evidence/'package-diagnostics.json',{'schema':1,'operations':[{'stage':n,'status':'success'} for n in package_diagnostics.STAGES]})
        stages=('remove-p12','verify-p12-absent','delete-keychain','verify-keychain-absent','inspect-search-list','restore-search-list','verify-search-list-restored')
        production.write_json(evidence/'keychain-diagnostics.json',{'schema':1,'operations':[{'stage':'cleanup.'+n,'status':'success'} for n in stages]})
        shutil.copyfile(inputs/'internal/release.json',signed/'internal-release.json')
        production.seal_signed(inputs,signed,'a'*40)
        return inputs,signed

    def test_seal_preserves_linux_and_hashes_final_not_pre_staple_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,signed=self.fixture(Path(temporary))
            result=production.verify_signed(signed,inputs,'a'*40)
            self.assertEqual(result['bundles']['macos']['sha256'],production.checksum(signed/result['bundles']['macos']['filename']))
            self.assertEqual((signed/'symphony-0.0.3-linux_x86_64.tar.gz').read_bytes(),(inputs/'packages/symphony-0.0.3-linux_x86_64.tar.gz').read_bytes())
            sums=(signed/'SHA256SUMS').read_text()
            self.assertIn(production.checksum(signed/'symphony-0.0.3-macos.dmg'),sums)
            self.assertIn('notarization-evidence.json',sums)

    def test_rejection_timeout_missing_receipt_cleanup_failure_and_changed_bytes_refuse(self):
        mutations=[lambda f:(f/'symphony-0.0.3-macos.dmg').write_bytes(b'changed after staple'),
                   lambda f:(f/'evidence/notary-log.json').unlink(),
                   lambda f:production.write_json(f/'evidence/notary-submission.json',{'status':'In Progress'}),
                   lambda f:production.write_json(f/'evidence/payload-log.json',{'status':'Rejected'}),
                   lambda f:production.write_json(f/'evidence/keychain-diagnostics.json',{'operations':[]})]
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as temporary:
                inputs,signed=self.fixture(Path(temporary));mutation(signed)
                with self.assertRaises((ValueError,KeyError,OSError)):production.verify_signed(signed,inputs,'a'*40)

    def test_recovery_cleanup_stages_remain_strict_and_old_receipts_stay_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,signed=self.fixture(Path(temporary))
            path=signed/'evidence/keychain-diagnostics.json'
            original=json.loads(path.read_text())
            for stage in ('check-search-list','remove-recovery-record','sync-private-directory'):
                value=copy.deepcopy(original)
                value['operations'].append({'stage':'cleanup.'+stage,'status':'success'})
                production.write_json(path,value)
                production.verify_signed(signed,inputs,'a'*40)
                value['operations'][-1]['status']='failed';production.write_json(path,value)
                with self.assertRaisesRegex(ValueError,'credential cleanup incomplete'):
                    production.verify_signed(signed,inputs,'a'*40)
            value=copy.deepcopy(original)
            value['operations'].append({'stage':'cleanup.unknown','status':'success'})
            production.write_json(path,value)
            with self.assertRaisesRegex(ValueError,'credential cleanup incomplete'):
                production.verify_signed(signed,inputs,'a'*40)

class ProductionCheckpointTest(unittest.TestCase):
    def setUp(self):
        from test_signing_checkpoint import ContinuationTests
        self.peer = ContinuationTests(); self.peer.setUp(); self.addCleanup(self.peer.doCleanups)
        self.root = self.peer.root
        self.inputs = self.root / 'qualified'
        for name in ('internal', 'helper', 'packages'): (self.inputs / name).mkdir(parents=True)
        manifest = self.inputs / 'internal/release.json'
        manifest.write_text(json.dumps({'source_revision': 'a' * 40,
                                       'bundles': {'macos': {'filename': self.peer.qualified.name}}}))
        shutil.copyfile(self.peer.helper, self.inputs / 'helper/symphony-host')
        shutil.copyfile(self.peer.qualified, self.inputs / 'packages' / self.peer.qualified.name)
        qualified = {'repository': production.REPOSITORY, 'source_revision': 'a' * 40,
                     'run_id': 123, 'run_attempt': 2, 'original_artifacts': [{'id': 10, 'digest': 'sha256:' + 'b' * 64}],
                     'original_qualified_helper_sha256': production.checksum(self.peer.helper)}
        production.write_json(self.inputs / 'qualification.json', qualified)
        self.env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': production.REPOSITORY,
                    'GITHUB_RUN_ID': '999', 'GITHUB_RUN_ATTEMPT': '1',
                    'GITHUB_WORKFLOW_REF': production.REPOSITORY + '/.github/workflows/macos-production-signing.yml@refs/heads/main',
                    'MACOS_SIGNING_IDENTITY_SHA1': 'A' * 40, 'MACOS_TEAM_ID': 'FAKETEAM00'}
        from macos_distribution import signing_context
        self.origin = dict(self.env, GITHUB_RUN_ID='998')
        self.peer.checkpoint.value.update(
            context=signing_context(self.peer.qualified, self.origin, self.inputs / 'qualification.json'),
            input_manifest_sha256=production.checksum(manifest))
        self.peer.checkpoint.save()
        self.original = {'id': 998, 'head_sha': 'a' * 40, 'head_branch': 'main', 'run_attempt': 1,
                         'path': '.github/workflows/macos-production-signing.yml', 'event': 'workflow_dispatch',
                         'status': 'completed', 'conclusion': 'failure',
                         'head_repository': {'full_name': production.REPOSITORY},
                         'run_started_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T01:00:00Z'}
        self.current = dict(self.original, id=999, status='in_progress', conclusion=None)
        self.history = {'total_count': 2, 'workflow_runs': [self.current, self.original]}
        self.jobs = {'jobs': [{'name': 'Sign exact production preparation', 'conclusion': 'failure'}]}
        self.pack()

    def pack(self):
        target = io.BytesIO()
        with zipfile.ZipFile(target, 'w') as archive:
            for path in self.peer.checkpoint.path.iterdir(): archive.writestr(path.name, path.read_bytes())
        self.raw = target.getvalue()
        self.artifact = {'id': 55, 'name': 'production-signing-checkpoint-' + 'a' * 40 + '-998-1',
                         'digest': 'sha256:' + digest(self.raw), 'size_in_bytes': len(self.raw), 'expired': False,
                         'expires_at': '2099-01-01T00:00:00Z', 'created_at': '2026-01-01T00:01:00Z',
                         'workflow_run': {'id': 998, 'head_sha': 'a' * 40}}

    def fetch(self, path, binary=False):
        if path.startswith('actions/workflows/'): return self.history
        if path.endswith('/jobs?per_page=100'): return self.jobs
        if path.endswith('/artifacts?per_page=100'): return {'artifacts': [self.artifact]}
        if path == 'actions/artifacts/55/zip': return self.raw
        if path in ('actions/runs/998', 'actions/runs/998/attempts/1'): return self.original
        raise AssertionError(path)

    def admit(self, **changes):
        def native(checkpoint, package, helper, env):
            from macos_distribution import validate_retained_payload
            validate_retained_payload(checkpoint, package, env)
            self.assertFalse(any(name in env for name in ('MACOS_SIGNING_P12_BASE64', 'MACOS_NOTARY_APP_PASSWORD')))
            if changes.get('invalid_native'): raise ValueError('retained native signature differs')
        with patch('macos_distribution.validate_retained_native', side_effect=native):
            return production.checkpoint_inputs(self.inputs, self.root / 'imported', 'a' * 40,
                changes.get('current_run', '999'), changes.get('current_attempt', '1'),
                changes.get('resume_run', '998'), changes.get('resume_attempt', '1'), env=self.env, fetch=self.fetch)

    def test_exact_failed_attempt_artifact_origin_qualification_and_bytes_are_admitted(self):
        before = {p.name: production.checksum(p) for p in self.peer.checkpoint.path.iterdir()}
        admission = self.admit()
        self.assertEqual(admission['origin']['run_id'], 998)
        self.assertEqual(admission['artifact']['id'], 55)
        self.assertEqual(admission['artifact']['digest'], 'sha256:' + digest(self.raw))
        self.assertEqual((self.root / 'imported').stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in (self.root / 'imported').iterdir()))
        self.assertEqual(before, {p.name: production.checksum(p) for p in self.peer.checkpoint.path.iterdir()})
        from macos_distribution import SignedCheckpoint, signing_context
        package = self.inputs / 'packages' / self.peer.qualified.name
        continued = SignedCheckpoint.reopen(self.root / 'imported', self.root / 'continued', 'a' * 40,
            self.inputs / 'internal/release.json', production.checksum(self.peer.helper), admission['checkpoint_sha256'],
            signing_context(package, self.env, self.inputs / 'qualification.json'), admission)
        self.assertEqual(continued.value['context']['origin']['run_id'], 999)
        self.assertEqual(continued.value['continuations'][-1]['origin']['run_id'], 998)
        self.assertEqual(continued.value['continuations'][-1]['admission']['artifact'], admission['artifact'])

    def test_start_and_ordinary_rerun_cannot_repeat_original_submission(self):
        with self.assertRaisesRegex(ValueError, 'original signing attempt exists'):
            self.admit(resume_run='', resume_attempt='')
        with self.assertRaisesRegex(ValueError, 'ordinary signing rerun refused'):
            self.admit(current_attempt='2')
        self.history['workflow_runs'] = [self.current]; self.history['total_count'] = 1
        self.assertIsNone(self.admit(resume_run='', resume_attempt=''))
        self.history['workflow_runs'].append(dict(self.original, head_branch='dev', conclusion='success'))
        self.history['total_count'] = 2
        self.assertIsNone(self.admit(resume_run='', resume_attempt=''))  # Guarded-out non-main dispatch is ineligible.

    def test_conflicting_active_successful_foreign_attempt_and_missing_history_refuse(self):
        for changes in ({'status': 'in_progress'}, {'conclusion': 'success'}, {'head_branch': 'dev'},
                        {'run_attempt': 2}, {'path': '.github/workflows/foreign.yml'},
                        {'head_repository': {'full_name': 'foreign/repo'}}):
            with self.subTest(changes=changes):
                old = dict(self.original); self.original.update(changes)
                with self.assertRaises(ValueError): self.admit()
                self.original.clear(); self.original.update(old)
        self.history['workflow_runs'].append(dict(self.original, id=1000))
        self.history['total_count'] = 3
        with self.assertRaisesRegex(ValueError, 'latest original attempt'): self.admit()
        self.history['workflow_runs'] = [self.original]
        self.history['total_count'] = 1
        with self.assertRaisesRegex(ValueError, 'current protected signing run missing'): self.admit()

    def test_embedded_origin_corruption_and_unknown_payload_are_refused(self):
        state = self.peer.checkpoint.value
        state['context']['origin']['run_id'] = 997; self.peer.checkpoint.save(); self.pack()
        with self.assertRaisesRegex(ValueError, 'embedded producer origin'): self.admit()

    def test_wrong_artifact_attempt_and_digest_refuse_before_checkpoint_use(self):
        for change in ({'created_at': '2025-01-01T00:00:00Z'}, {'workflow_run': {'id': 997, 'head_sha': 'a' * 40}},
                       {'digest': 'sha256:' + '0' * 64}, {'expired': True}):
            with self.subTest(change=change):
                old = dict(self.artifact); self.artifact.update(change)
                with self.assertRaises(ValueError): self.admit()
                self.artifact.clear(); self.artifact.update(old)

    def test_native_refusal_prevents_public_admission_before_secret_step(self):
        with self.assertRaisesRegex(ValueError, 'retained native signature differs'):
            self.admit(invalid_native=True)
        self.assertFalse((self.root / 'imported-admission.json').exists())

    def test_history_filters_unrelated_releases_and_refuses_incomplete_relevant_inventory(self):
        def thousands_of_unrelated_runs(path, binary=False):
            if path.startswith('actions/workflows/'):
                return self.history if '&head_sha=' + 'a' * 40 in path else {'total_count': 10002, 'workflow_runs': []}
            return self.fetch(path, binary)
        origin = production.signing_attempt('a' * 40, '999', '1', '998', '1', thousands_of_unrelated_runs)
        self.assertEqual(origin['run_id'], 998)
        self.history['total_count'] = 3
        with self.assertRaisesRegex(ValueError, 'exact-source signing history incomplete'):
            production.signing_attempt('a' * 40, '999', '1', '998', '1', self.fetch)


class PublicationPeer:
    """Private drafts deliberately have no by-tag API representation."""
    revision = 'a' * 40

    def __init__(self, existing=True):
        self.release = self.value() if existing else None
        self.calls = []
        self.mutations = []
        self.tag_checks = 0
        self.gets = 0
        self.pages = None
        self.read_change = None
        self.create_response = None
        self.create_error = False
        self.partial_upload = False

    def value(self):
        return {'id': 17, 'tag_name': 'v0.0.6', 'target_commitish': self.revision,
                'draft': True, 'prerelease': False, 'assets': []}

    def verify(self):
        self.tag_checks += 1

    def invoke(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ['gh', 'release', 'upload']:
            self.mutations.append(('upload', self.tag_checks))
            if not self.partial_upload:
                for filename in argv[4:]:
                    path = Path(filename)
                    self.release['assets'].append({'name': path.name, 'digest': 'sha256:' + production.checksum(path),
                                                   'size': path.stat().st_size})
            return SimpleNamespace(returncode=0)
        self.assert_api(argv)
        path = argv[2].removeprefix('repos/' + production.REPOSITORY + '/')
        method = argv[argv.index('--method') + 1] if '--method' in argv else 'GET'
        if method == 'GET' and path.startswith('releases?per_page=100&page='):
            page = int(path.rsplit('=', 1)[1])
            value = self.pages(page) if self.pages else ([self.release] if self.release else [])
        elif method == 'POST' and path == 'releases':
            self.mutations.append(('create', self.tag_checks))
            payload = json.loads(kwargs['input'])
            assert payload == {'tag_name': 'v0.0.6', 'target_commitish': self.revision,
                               'draft': True, 'generate_release_notes': True}
            self.release = self.value()
            if self.create_error:
                raise subprocess.CalledProcessError(1, argv, stderr='uncertain POST outcome')
            value = self.create_response if self.create_response is not None else self.release
        elif method == 'GET' and path == 'releases/17':
            self.gets += 1
            value = copy.deepcopy(self.release)
            if self.read_change:
                self.read_change(value, self.gets)
        elif method == 'PATCH' and path == 'releases/17':
            self.mutations.append(('complete', self.tag_checks))
            assert json.loads(kwargs['input']) == {'draft': False, 'prerelease': False, 'make_latest': 'true'}
            self.release.update(draft=False, prerelease=False)
            value = self.release
        else:
            raise AssertionError('unexpected production API method/path')
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    @staticmethod
    def assert_api(argv):
        assert argv[:2] == ['gh', 'api']
        assert '/releases/tags/' not in argv[2], 'by-tag endpoint cannot discover private drafts'


class DraftPublicationTest(unittest.TestCase):
    def publish(self, root, file, peer, verify=None):
        manifest = root / 'signed/release.json'
        manifest.parent.mkdir(exist_ok=True)
        if not manifest.exists():production.write_json(manifest, {'source_revision': peer.revision})
        with patch.object(production, 'release_files', return_value=[file]), \
                patch.dict(production.os.environ, GITHUB_SHA=peer.revision):
            production.publish_release(root, 'v0.0.6', peer.invoke, verify_tag=verify or peer.verify)

    def test_unexpected_duplicate_wrong_digest_and_incomplete_assets_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed final bytes')
            asset={'name':file.name,'digest':'sha256:'+production.checksum(file),'size':file.stat().st_size}
            valid={'draft':True,'assets':[asset]}
            self.assertEqual(production.release_inventory(valid,[file],True),{file.name})
            for value in ({'draft':False,'assets':[asset]}, {'draft':True,'assets':[asset,asset]},
                          {'draft':True,'assets':[dict(asset,name='old-unsigned.tar.gz')]},
                          {'draft':True,'assets':[dict(asset,digest='sha256:'+'0'*64)]},
                          {'draft':True,'assets':[]}):
                with self.assertRaises(ValueError):production.release_inventory(value,[file],True)

    def test_no_completion_after_partial_upload_or_stale_draft_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer();peer.partial_upload = True
            with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
            self.assertIn('upload', [name for name, _ in peer.mutations])
            self.assertNotIn('complete', [name for name, _ in peer.mutations])

    def test_success_compares_remote_hashes_before_completion_without_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer()
            self.publish(Path(temporary), file, peer)
            self.assertFalse(peer.release['draft'])
            self.assertEqual(peer.mutations, [('upload', 2), ('complete', 3)])
            self.assertFalse(any('--clobber' in call for call in peer.calls))

    def test_new_draft_uses_original_post_id_and_never_by_tag_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer(existing=False)
            self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [('create', 1), ('upload', 2), ('complete', 3)])
            self.assertEqual(peer.gets, 2)
            self.assertEqual(peer.release['assets'][0]['digest'], 'sha256:' + production.checksum(file))

    def test_retry_uses_one_original_draft_and_never_recreates_completed_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer()
            peer.release['assets'] = [{'name': file.name, 'digest': 'sha256:' + production.checksum(file), 'size': file.stat().st_size}]
            self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [('complete', 2)])
            peer.mutations.clear()
            with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [])

    def test_ambiguous_incomplete_and_invalid_target_inventory_refuse_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            for listing in ([PublicationPeer().value(), PublicationPeer().value()],
                            [dict(PublicationPeer().value(), target_commitish='')]):
                peer = PublicationPeer();peer.pages = lambda page: listing
                with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
                self.assertEqual(peer.mutations, [])
            peer = PublicationPeer();peer.pages = lambda page: [{'tag_name': 'other'}] * 100
            with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
            self.assertEqual(len(peer.calls), 10)
            self.assertEqual(peer.mutations, [])

    def test_existing_tag_source_uses_live_verifier_not_ignored_target_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer();peer.release['target_commitish'] = 'main'
            self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [('upload', 2), ('complete', 3)])
            self.assertEqual(peer.release['target_commitish'], 'main')
            peer = PublicationPeer();peer.release['target_commitish'] = 'main'
            with self.assertRaises(ValueError):
                self.publish(Path(temporary), file, peer, lambda: (_ for _ in ()).throw(ValueError('wrong live source')))
            self.assertEqual(peer.mutations, [])

    def test_missing_or_wrong_workflow_source_refuses_before_service_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / 'signed.dmg';file.write_bytes(b'assessed')
            (root / 'signed').mkdir()
            production.write_json(root / 'signed/release.json', {'source_revision': 'a' * 40})
            for revision in ('', 'b' * 40):
                peer = PublicationPeer()
                with patch.object(production, 'release_files', return_value=[file]), \
                        patch.dict(production.os.environ, GITHUB_SHA=revision), self.assertRaises(ValueError):
                    production.publish_release(root, 'v0.0.6', peer.invoke, verify_tag=peer.verify)
                self.assertEqual(peer.calls, [])

    def test_existing_draft_on_second_page_is_reconciled_without_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer()
            peer.pages = lambda page: [{'tag_name': 'other'}] * 100 if page == 1 else [peer.release]
            self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [('upload', 2), ('complete', 3)])
            self.assertEqual(sum('releases?per_page=100&page=' in call[2] for call in peer.calls if call[1] == 'api'), 2)

    def test_original_id_privacy_tag_source_and_asset_tampering_refuse(self):
        changes = ({'id': 18}, {'draft': False}, {'tag_name': 'other'}, {'target_commitish': 'b' * 40},
                   {'assets': [{'name': 'foreign', 'digest': 'sha256:' + '0' * 64, 'size': 1}]})
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            for at in (1, 2):
                for change in changes:
                    peer = PublicationPeer()
                    peer.read_change = lambda value, count: value.update(change) if count == at else None
                    with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
                    self.assertNotIn('complete', [name for name, _ in peer.mutations])

    def test_uncertain_or_malformed_creation_never_retries_or_uploads(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            for response in ({}, [], {'id': True}, {'id': 0}, {'id': 17, 'tag_name': 'wrong'}):
                peer = PublicationPeer(existing=False);peer.create_response = response
                with self.assertRaises(ValueError):self.publish(Path(temporary), file, peer)
                self.assertEqual(peer.mutations, [('create', 1)])
            peer = PublicationPeer(existing=False);peer.create_error = True
            with self.assertRaises(subprocess.SubprocessError):self.publish(Path(temporary), file, peer)
            self.assertEqual(peer.mutations, [('create', 1)])

    def test_explicit_retry_reconciles_original_after_uncertain_post_without_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer(existing=False);peer.create_error = True
            with self.assertRaises(subprocess.SubprocessError):self.publish(Path(temporary), file, peer)
            peer.create_error = False
            self.publish(Path(temporary), file, peer)
            self.assertEqual([name for name, _ in peer.mutations], ['create', 'upload', 'complete'])
            self.assertFalse(peer.release['draft'])

class EndToEndPublicationTest(unittest.TestCase):
    def test_real_seal_to_publication_inventory_matches_every_public_checksum(self):
        from types import SimpleNamespace
        import manager_version
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,signed=SignedSelectionTest().fixture(root)
            for target in ('linux_x86_64','macos_arm64'):
                folder=root/'engines'/target;folder.mkdir(parents=True)
                name=f'symphony-v{manager_version.read(ROOT)}-{target}'
                (folder/name).write_bytes(b'FAKE QUALIFIED ENGINE '+target.encode())
                (folder/(name+'.sha256')).write_text(production.checksum(folder/name)+'  '+name+'\n')
                production.write_json(folder/(name+'.source.json'),{'source_revision':'a'*40})
            actual=production.release_files(root)
            sums={line.split('  ')[1]:line.split('  ')[0] for line in (signed/'SHA256SUMS').read_text().splitlines()}
            by_name={p.name:p for p in actual}
            for name,digest in sums.items():
                self.assertIn(name,by_name)
                self.assertEqual(production.checksum(by_name[name]),digest)
            self.assertEqual({p.name for p in signed.iterdir() if p.is_file()},set(sums)|{'SHA256SUMS'})
            peer = PublicationPeer()
            with patch.dict(production.os.environ, GITHUB_SHA=peer.revision):
                production.publish_release(root, 'v0.0.6', peer.invoke, verify_tag=peer.verify)
            self.assertEqual({a['name'] for a in peer.release['assets']},set(by_name))
            self.assertFalse(peer.release['draft'])

    def test_live_tag_peels_annotations_and_rejects_moved_commit(self):
        revision='a'*40;annotation='b'*40
        def fetch(path):return {'object':{'sha':annotation,'type':'tag'}} if path.startswith('git/ref/') else {'object':{'sha':revision,'type':'commit'}}
        production.live_tag(revision,'v0.0.6',fetch)
        with self.assertRaises(ValueError):production.live_tag('c'*40,'v0.0.6',fetch)

    def test_tag_move_after_upload_refuses_final_completion(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            peer = PublicationPeer();checks=[]
            (Path(temporary) / 'signed').mkdir()
            production.write_json(Path(temporary) / 'signed/release.json', {'source_revision': peer.revision})
            def moved():
                checks.append(True)
                if len(checks)==3:raise ValueError('live tag moved')
            with patch.object(production,'release_files',return_value=[file]), \
                    patch.dict(production.os.environ, GITHUB_SHA=peer.revision), self.assertRaises(ValueError):
                production.publish_release(Path(temporary),'v0.0.6',peer.invoke,verify_tag=moved)
            self.assertNotIn('complete', [name for name, _ in peer.mutations])
            self.assertIn('upload', [name for name, _ in peer.mutations])

class LiveSourceTest(unittest.TestCase):
    def test_signing_refuses_moved_main_after_approval(self):
        fetch=lambda path:{'object':{'type':'commit','sha':'b'*40}}
        with self.assertRaisesRegex(ValueError,'main moved'):production.live_source('a'*40,fetch=fetch)

    def test_publication_allows_approved_ancestor_but_refuses_removed_source(self):
        def fetch(path):
            if path=='git/ref/heads/main':return {'object':{'type':'commit','sha':'b'*40}}
            if path.startswith('git/ref/tags/'):return {'object':{'type':'commit','sha':'a'*40}}
            return {'merge_base_commit':{'sha':'a'*40}}
        production.live_source('a'*40,'v0.0.6',fetch)
        def removed(path):
            if path.startswith('compare/'):return {'merge_base_commit':{'sha':'c'*40}}
            return fetch(path)
        with self.assertRaisesRegex(ValueError,'no longer on live main'):production.live_source('a'*40,'v0.0.6',removed)
