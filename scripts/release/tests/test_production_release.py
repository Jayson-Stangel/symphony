"""Credential-free production selection and no-publication-before-readiness regressions."""
import copy
import hashlib
import io
import json
import shutil
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

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

class DraftPublicationTest(unittest.TestCase):
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
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            calls=[]
            def invoke(argv,**kwargs):
                calls.append(argv)
                if argv[1]=='api':
                    return SimpleNamespace(returncode=0,stdout=json.dumps({'tag_name':'v0.0.6','draft':True,'assets':[]}))
                return SimpleNamespace(returncode=0)
            with patch.object(production,'release_files',return_value=[file]),self.assertRaises(ValueError):
                production.publish_release(Path(temporary),'v0.0.6',invoke,verify_tag=lambda:None)
            self.assertTrue(any('upload' in call for call in calls))
            self.assertFalse(any('--draft=false' in call for call in calls))

    def test_success_compares_remote_hashes_before_completion_without_clobber(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            asset={'name':file.name,'digest':'sha256:'+production.checksum(file),'size':file.stat().st_size}
            calls=[];states=iter([[],[asset]])
            def invoke(argv,**kwargs):
                calls.append(argv)
                if argv[1]=='api':return SimpleNamespace(returncode=0,stdout=json.dumps({'tag_name':'v0.0.6','draft':True,'assets':next(states)}))
                return SimpleNamespace(returncode=0)
            with patch.object(production,'release_files',return_value=[file]):
                production.publish_release(Path(temporary),'v0.0.6',invoke,verify_tag=lambda:None)
            self.assertIn('--draft=false',calls[-1])
            self.assertFalse(any('--clobber' in call for call in calls))

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
            remote=[];calls=[]
            def invoke(argv,**kwargs):
                calls.append(argv)
                if argv[1]=='api':return SimpleNamespace(returncode=0,stdout=json.dumps({'tag_name':'v0.0.6','draft':True,'assets':remote}))
                if 'upload' in argv:
                    for file in argv[4:]:
                        p=Path(file);remote.append({'name':p.name,'digest':'sha256:'+production.checksum(p),'size':p.stat().st_size})
                return SimpleNamespace(returncode=0)
            production.publish_release(root,'v0.0.6',invoke,verify_tag=lambda:None)
            self.assertEqual({a['name'] for a in remote},set(by_name))
            self.assertIn('--draft=false',calls[-1])

    def test_live_tag_peels_annotations_and_rejects_moved_commit(self):
        revision='a'*40;annotation='b'*40
        def fetch(path):return {'object':{'sha':annotation,'type':'tag'}} if path.startswith('git/ref/') else {'object':{'sha':revision,'type':'commit'}}
        production.live_tag(revision,'v0.0.6',fetch)
        with self.assertRaises(ValueError):production.live_tag('c'*40,'v0.0.6',fetch)

    def test_tag_move_after_upload_refuses_final_completion(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'signed.dmg';file.write_bytes(b'assessed')
            asset={'name':file.name,'digest':'sha256:'+production.checksum(file),'size':file.stat().st_size}
            states=iter([[],[asset]]);calls=[];checks=[]
            def invoke(argv,**kwargs):
                calls.append(argv)
                if argv[1]=='api':return SimpleNamespace(returncode=0,stdout=json.dumps({'tag_name':'v0.0.6','draft':True,'assets':next(states)}))
                return SimpleNamespace(returncode=0)
            def moved():
                checks.append(True)
                if len(checks)==2:raise ValueError('live tag moved')
            with patch.object(production,'release_files',return_value=[file]),self.assertRaises(ValueError):
                production.publish_release(Path(temporary),'v0.0.6',invoke,verify_tag=moved)
            self.assertFalse(any('--draft=false' in call for call in calls))

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
