"""Historical schema1 selection and actual opaque archive role/hash fixtures, not native qualification."""
import copy
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
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).parents[1]))
from test_artifacts import manifest
from test_component_provenance import record as component_record
import component_provenance as proof
SPEC=importlib.util.spec_from_file_location('previous_subject',Path(__file__).parents[1]/'previous-package-subject.py')
subject=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(subject)


def zipped(files):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        for name,body in files.items():archive.writestr(name,body)
    return data.getvalue()


class PreviousSubjectTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.source=self.root/'source';self.source.mkdir()
        subprocess.run(['git','init','--quiet',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','--allow-empty','--quiet','-m','actual selected source'],check=True)
        self.revision=subprocess.check_output(['git','-C',str(self.source),'rev-parse','HEAD'],text=True).strip()
        self.env=patch.dict(os.environ,{'GITHUB_SHA':self.revision,'GITHUB_REPOSITORY':proof.REPOSITORY,'GITHUB_EVENT_NAME':'workflow_dispatch'})
        self.env.start();self.addCleanup(self.env.stop)
        value=manifest();value['source_revision']=self.revision;value.pop('bundle')
        for group in value['runners'].values():
            for record in group.values():record['source_revision']=self.revision
        package=b'opaque actual old Linux archive fixture'
        value['bundle']={'filename':'symphony-0.0.3-macos.tar.gz','sha256':'a'*64}
        value['bundles']={'macos':copy.deepcopy(value['bundle']),'linux_x86_64':{
            'filename':'symphony-0.0.3-linux_x86_64.tar.gz','sha256':hashlib.sha256(package).hexdigest()}}
        self.manifest=value
        self.run={'id':123,'run_attempt':1,'head_sha':self.revision,'event':'workflow_dispatch',
                  'repository':{'full_name':proof.REPOSITORY},'head_repository':{'full_name':proof.REPOSITORY},
                  'path':'.github/workflows/runner-release.yml'}
        names=[f'runner-{p}-{a}' for p in ('codex','claude') for a in ('arm64','x86_64')]
        names+=['candidate-metadata','packaged-candidate-metadata','ubuntu-package-lifecycle',
                'packaged-candidate (macos, macos-14)','packaged-candidate (linux_x86_64, ubuntu-24.04)']
        self.jobs=[dict(id=i+1,name=name,status='completed',conclusion='success',labels=[
            'macos-14' if name.startswith('packaged-candidate (macos') else 'ubuntu-24.04-arm' if name.endswith('-arm64') else 'ubuntu-24.04'],
            started_at='2026-10-22T00:00:00Z',completed_at='2026-10-22T23:59:59Z') for i,name in enumerate(names)]
        self.payloads={};self.artifacts=[]
        def artifact(name,data):
            identifier=10+len(self.artifacts)
            self.payloads[identifier]=data
            self.artifacts.append(dict(id=identifier,name=name,digest='sha256:'+hashlib.sha256(data).hexdigest(),
                size_in_bytes=len(data),expired=False,expires_at='2030-01-01T00:00:00Z',created_at='2026-10-22T12:00:00Z',
                workflow_run={'id':123,'head_sha':self.revision}))
        artifact('runner-candidate-metadata',zipped({'release.json':json.dumps(value)}))
        artifact('packaged-candidate',zipped({'release.json':json.dumps(value),value['bundles']['linux_x86_64']['filename']:package,
                                             'previous/unused-linux.tar.gz':b'not selected'}))
        for provider in ('codex','claude'):
            data=io.BytesIO()
            with tarfile.open(fileobj=data,mode='w:gz') as archive:
                entries=[]
                for tag,source in (('candidate',self.revision),('previous','b'*40)):
                    raw=json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{
                        'com.symphony.agent':provider,'com.symphony.architecture':'amd64','org.opencontainers.image.revision':source}}}).encode()
                    config=hashlib.sha256(raw).hexdigest()+'.json'
                    info=tarfile.TarInfo(config);info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
                    entries.append({'Config':config,'RepoTags':[f'localhost:5000/symphony-{provider}:'+tag],'Layers':[]})
                raw=json.dumps(entries).encode();info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
            image=data.getvalue();name=provider+'-x86_64.docker.tar.gz'
            artifact('runner-candidate-'+provider+'-x86_64',zipped({name:image,provider+'-x86_64.sha256':hashlib.sha256(image).hexdigest()+'  ./'+name+'\n','../../foreign':b'never extracted'}))
    def api(self,path,binary=False):
        if path=='actions/runs/123':return copy.deepcopy(self.run)
        if '/attempts/1/jobs?' in path:return {'total_count':len(self.jobs),'jobs':copy.deepcopy(self.jobs)}
        if path=='actions/runs/123/artifacts?per_page=100':return {'total_count':len(self.artifacts),'artifacts':copy.deepcopy(self.artifacts)}
        if path.startswith('compare/'):return {'status':'ahead','merge_base_commit':{'sha':self.revision}}
        if path.startswith('actions/artifacts/') and binary:return self.payloads[int(path.split('/')[2])]
        raise AssertionError(path)
    def download(self,artifact,path):path.write_bytes(self.payloads[artifact['id']])
    def test_historical_skipped_lifecycle_remains_refused_with_bootstrap_disabled(self):
        for stage in self.jobs:
            if stage['name']=='ubuntu-package-lifecycle':stage['conclusion']='skipped'
        with self.assertRaisesRegex(ValueError,'historical subject stage'):
            subject.select(123,self.source,self.api)
        with self.assertRaisesRegex(ValueError,'historical subject stage'):
            subject.select(123,self.source,self.api,bootstrap='')

    def test_exact_pending_selection_preserves_skipped_history_and_rejects_failed_or_duplicate(self):
        stage=next(s for s in self.jobs if s['name']=='ubuntu-package-lifecycle');stage['conclusion']='skipped'
        package=next(a for a in self.artifacts if a['name']=='packaged-candidate')
        desc=dict(run_id=123,run_attempt=1,source_revision=self.revision,lifecycle_job_id=stage['id'],
                  package_artifact_id=package['id'],package_artifact_digest=package['digest'],candidate_source_revision=self.revision)
        with patch.object(subject.prior_baseline,'descriptor',return_value=desc):
            pending=subject.select(123,self.source,self.api,bootstrap='explicit fixture')
            self.assertEqual(pending['baseline_status'],'UNQUALIFIED_PENDING')
            self.assertEqual(stage['conclusion'],'skipped')
            for state in ('failure','cancelled','success',None):
                stage['conclusion']=state
                with self.assertRaises(ValueError):subject.select(123,self.source,self.api,bootstrap='explicit fixture')
            stage['conclusion']='skipped';self.jobs.append(copy.deepcopy(stage))
            with self.assertRaises(ValueError):subject.select(123,self.source,self.api,bootstrap='explicit fixture')

    def test_current_display_names_select_the_same_actual_historical_jobs(self):
        for item in self.jobs:
            old=item['name']
            if old.startswith('packaged-candidate ('):
                item['name']='Build candidate package ('+old.split('(',1)[1].split(',',1)[0]+')'
            else:
                item['name']=subject.artifacts.candidate_job_names(old)[-1]
        selected=subject.select(123,self.source,self.api)
        self.assertEqual(len(selected['native_jobs']),4)
        duplicate=copy.deepcopy(self.jobs[0]);duplicate['name']=subject.artifacts.runner_job_names('codex','arm64')[0]
        self.jobs.append(duplicate)
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            subject.select(123,self.source,self.api)

    def test_actual_current_role_opaque_payloads_preserve_originals_without_reexport(self):
        selected=subject.select(123,self.source,self.api)
        subject.retrieve(selected,self.root/'packages',self.root/'images',self.root/'evidence',self.download)
        observed=json.loads((self.root/'evidence/previous-package-subject.json').read_text())
        self.assertFalse(observed['prior_image_export_executed'])
        self.assertFalse(observed['prior_image_build_executed'])
        self.assertEqual(json.loads((self.root/'packages/previous/release.json').read_text()),self.manifest)
        for provider,record in observed['observed_x86_64'].items():
            self.assertEqual(record['source_revision'],self.revision)
            self.assertEqual(record['archive_tag'],f'localhost:5000/symphony-{provider}:candidate')
            self.assertIsNone(record['protocol']);self.assertIsNone(record['input_identity'])
        self.assertFalse((self.root/'foreign').exists())
    def test_published_bridge_verifies_every_original_receipt_even_when_public_view_omits_it(self):
        value=copy.deepcopy(self.manifest);value['original_components']={}
        for provider in ('codex','claude'):
            value['original_components'][provider]={}
            for arch in ('arm64','x86_64'):
                original=component_record(provider,arch);original['source_revision']=self.revision
                original['qualification'].update(controller_revision=self.revision,source_revision=self.revision)
                value['original_components'][provider][arch]=original
                public=copy.deepcopy(original);public.pop('qualification')
                value['runners'][provider][arch]=public
        metadata=self.artifacts[0];raw=zipped({'release.json':json.dumps(value)})
        self.payloads[metadata['id']]=raw;metadata.update(digest='sha256:'+hashlib.sha256(raw).hexdigest(),size_in_bytes=len(raw))
        with patch.object(subject.proof,'verify_origin',side_effect=ValueError('expired original receipt')) as verify:
            with self.assertRaisesRegex(ValueError,'expired original receipt'):subject.select(123,self.source,self.api)
            self.assertEqual(verify.call_count,1)
        with patch.object(subject.proof,'verify_origin',return_value={'verified':'actual original'}) as verify:
            selected=subject.select(123,self.source,self.api)
            self.assertEqual(verify.call_count,4)
            self.assertEqual(selected['manifest'],value)
    def test_three_manager_route_downloads_previous_package_without_duplicate_runner_payloads(self):
        selected=subject.select(123,self.source,self.api);calls=[]
        def fetch(item,path):
            calls.append(item['id']);self.download(item,path)
        subject.retrieve(selected,self.root/'packages',self.root/'images',self.root/'evidence',fetch,package_only=True)
        self.assertEqual(calls,[selected['package_artifact']['id']])
        self.assertTrue((self.root/'packages/previous/release.json').exists())
        self.assertFalse((self.root/'images/previous').exists())
        self.assertEqual(json.loads((self.root/'evidence/previous-package-subject.json').read_text())['observed_x86_64'],{})
    def independent_fixture(self):
        value=copy.deepcopy(self.manifest);value.update(schema_version=2,compatibility={'runner_protocol':1})
        records=[];origins={};original_jobs=[];original_source='b'*40
        for provider in ('codex','claude'):
            for arch in ('arm64','x86_64'):
                item=component_record(provider,arch)
                item['source_revision']=original_source
                item['qualification'].update(run_id=456,controller_revision=original_source,source_revision=original_source)
                payload=next((x for x in self.artifacts if x['name']=='runner-candidate-'+provider+'-x86_64'))
                if arch=='x86_64':
                    config=json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{
                        'com.symphony.agent':provider,'com.symphony.architecture':'amd64','org.opencontainers.image.revision':original_source,
                        'com.symphony.protocol':'1','com.symphony.inputs.sha256':item['input_identity']}}}).encode()
                    item['image_config']='sha256:'+hashlib.sha256(config).hexdigest()
                    item['archive_tag']=f'localhost:5000/symphony-{provider}:candidate'
                    output=io.BytesIO()
                    with tarfile.open(fileobj=output,mode='w:gz') as archive:
                        filename=hashlib.sha256(config).hexdigest()+'.json'
                        info=tarfile.TarInfo(filename);info.size=len(config);archive.addfile(info,io.BytesIO(config))
                        raw=json.dumps([{'Config':filename,'RepoTags':[item['archive_tag']],'Layers':[]}]).encode()
                        info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
                    archive_bytes=output.getvalue();item['qualification']['archive_sha256']=hashlib.sha256(archive_bytes).hexdigest()
                    name=provider+'-x86_64.docker.tar.gz'
                    raw=zipped({name:archive_bytes,provider+'-x86_64.sha256':item['qualification']['archive_sha256']+'  '+name+'\n'})
                    self.payloads[payload['id']]=raw
                    payload.update(digest='sha256:'+hashlib.sha256(raw).hexdigest(),size_in_bytes=len(raw),
                        workflow_run={'id':456,'head_sha':original_source})
                records.append(item)
                value['runners'][provider][arch]={k:v for k,v in item.items() if k not in ('provider','architecture')}
                origins[provider+'-'+arch]={'run_id':456,'run_attempt':1,'job_name':item['qualification']['job_name'],
                    'controller_revision':original_source,'artifact_id':payload['id'],'artifact_digest':payload['digest']}
                original_jobs.append(dict(id=100+len(original_jobs),name=item['qualification']['job_name'],status='completed',conclusion='success',
                    labels=['ubuntu-24.04-arm' if arch=='arm64' else 'ubuntu-24.04'],
                    started_at='2026-10-22T00:00:00Z',completed_at='2026-10-22T23:59:59Z'))
        plan={'planning_complete':True,'manager_source_revision':self.revision,'components':{}}
        for item in records:
            plan['components'].setdefault(item['provider'],{})[item['architecture']]={'action':'reuse','record':item,
                'input_identity':item['input_identity'],'origin':origins[item['provider']+'-'+item['architecture']]}
        files={'release.json':json.dumps(value),'component-plan.json':json.dumps(plan)}
        files.update({item['provider']+'-'+item['architecture']+'.json':json.dumps(item) for item in records})
        raw=zipped(files);meta=self.artifacts[0];self.payloads[meta['id']]=raw
        meta.update(digest='sha256:'+hashlib.sha256(raw).hexdigest(),size_in_bytes=len(raw))
        bundle=value['bundles']['linux_x86_64'];raw=zipped({'release.json':json.dumps(value),bundle['filename']:b'opaque actual old Linux archive fixture'})
        package=self.artifacts[1];self.payloads[package['id']]=raw
        package.update(digest='sha256:'+hashlib.sha256(raw).hexdigest(),size_in_bytes=len(raw))
        self.jobs=[j for j in self.jobs if not j['name'].startswith('runner-')]
        for platform,runner in (('macos','macos-14'),('linux_x86_64','ubuntu-24.04')):
            self.jobs.append(dict(id=300+len(self.jobs),name='package-inputs / native-host-package ('+runner+')',
                status='completed',conclusion='success',labels=[runner]))
        next(j for j in self.jobs if j['name']=='candidate-metadata')['steps']=[{
            'name':'Join exact planned native components without exporting reused payloads','status':'completed','conclusion':'success'}]
        def api(path,binary=False):
            if path=='actions/runs/456/attempts/1/jobs?per_page=100':return {'total_count':len(original_jobs),'jobs':original_jobs}
            if path.startswith('actions/artifacts/') and not binary:return next(x for x in self.artifacts if x['id']==int(path.split('/')[2]))
            return self.api(path,binary)
        def verify(record,**kwargs):return origins[record['provider']+'-'+record['architecture']]
        return value,records,api,verify

    def test_schema2_prior_manager_uses_original_native_sources_without_claiming_current_native_jobs(self):
        value,records,api,verify=self.independent_fixture()
        with patch.object(subject.proof,'verify_origin',side_effect=verify) as checked:
            selected=subject.select(123,self.source,api)
            self.assertEqual(checked.call_count,4)
        self.assertTrue(all(identifier>=100 for identifier in selected['native_jobs'].values()))
        self.assertEqual(len(selected['manager_helper_jobs']),2)
        subject.retrieve(selected,self.root/'packages',self.root/'images',self.root/'evidence',self.download)
        retained=json.loads((self.root/'packages/previous/release.json').read_text())
        self.assertEqual(retained,value)
        observed=json.loads((self.root/'evidence/previous-package-subject.json').read_text())
        for actual in observed['observed_x86_64'].values():
            self.assertEqual(actual['source_revision'],'b'*40)
            self.assertNotEqual(actual['source_revision'],selected['source_revision'])
            self.assertEqual(actual['protocol'],'1');self.assertEqual(actual['input_identity'],'d'*64)

    def test_schema2_prior_requires_manager_lifecycle_and_helpers_separately_from_native_join(self):
        for kind in ('helper','lifecycle','join','origin'):
            with self.subTest(kind=kind):
                jobs,items,payloads=copy.deepcopy(self.jobs),copy.deepcopy(self.artifacts),copy.deepcopy(self.payloads)
                value,records,api,verify=self.independent_fixture()
                if kind=='helper':next(j for j in self.jobs if 'native-host-package' in j['name'])['conclusion']='failure'
                if kind=='lifecycle':next(j for j in self.jobs if j['name']=='ubuntu-package-lifecycle')['conclusion']='failure'
                if kind=='join':next(j for j in self.jobs if j['name']=='candidate-metadata')['steps'][0]['conclusion']='failure'
                def checked(item,**kwargs):
                    actual=verify(item,**kwargs)
                    return dict(actual,artifact_id=999) if kind=='origin' else actual
                with patch.object(subject.proof,'verify_origin',side_effect=checked),self.assertRaises(ValueError):
                    subject.select(123,self.source,api)
                self.jobs,self.artifacts,self.payloads=jobs,items,payloads

    def test_failed_native_or_foreign_expired_ambiguous_origin_refuses_before_download(self):
        for variant in ('native','foreign','expired','duplicate'):
            with self.subTest(variant=variant):
                jobs,run,items=copy.deepcopy(self.jobs),copy.deepcopy(self.run),copy.deepcopy(self.artifacts)
                if variant=='native':self.jobs[0]['conclusion']='failure'
                if variant=='foreign':self.run['head_repository']['full_name']='foreign/repository'
                if variant=='expired':self.artifacts[2]['expired']=True
                if variant=='duplicate':self.artifacts.append(copy.deepcopy(self.artifacts[2]))
                with self.assertRaises(ValueError):subject.select(123,self.source,self.api)
                self.jobs,self.run,self.artifacts=jobs,run,items
    def test_corrupt_service_zip_refuses_without_publishing_previous_directories(self):
        selected=subject.select(123,self.source,self.api)
        def corrupt(artifact,path):path.write_bytes(b'not the original package')
        with self.assertRaisesRegex(ValueError,'service checksum'):
            subject.retrieve(selected,self.root/'packages',self.root/'images',self.root/'evidence',corrupt)
        self.assertFalse((self.root/'packages/previous').exists())
        self.assertFalse((self.root/'images/previous').exists())


class BaselineBootstrapTest(unittest.TestCase):
    """Credential-free admission/receipt fixtures, never actual baseline qualification."""
    def setUp(self):
        self.b = subject.prior_baseline
        self.candidate = 'd'*40
        self.descriptor = dict(self.b.ORIGINAL,candidate_source_revision=self.candidate)
        self.env = dict(GITHUB_SHA=self.candidate,GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/dev',
                        GITHUB_REF_TYPE='branch',GITHUB_REPOSITORY=proof.REPOSITORY,GITHUB_ACTOR='Jayson-Stangel',
                        GITHUB_TRIGGERING_ACTOR='Jayson-Stangel',PREVIOUS_ARTIFACT_RUN=str(self.b.ORIGINAL['run_id']),
                        GITHUB_RUN_ID='777',GITHUB_RUN_ATTEMPT='1',
                        GITHUB_WORKFLOW_REF='Jayson-Stangel/symphony/.github/workflows/burrito-release.yml@refs/heads/dev')

    def test_default_off_and_exact_manual_dev_descriptor(self):
        self.assertIsNone(self.b.descriptor('',{}))
        self.assertEqual(self.b.descriptor(json.dumps(self.descriptor),self.env),self.descriptor)
        for key,value in [('GITHUB_EVENT_NAME','push'),('GITHUB_REF','refs/heads/main'),('GITHUB_REF_TYPE','tag'),
                          ('GITHUB_ACTOR','foreign'),('GITHUB_TRIGGERING_ACTOR','foreign'),('PREPARE_RELEASE','true'),
                          ('MANAGER_ONLY_UPDATE','true'),('COMPOSE_PACKAGES','false'),('LEGACY_ARTIFACT_RUN','123'),
                          ('GITHUB_SHA','e'*40),('PREVIOUS_ARTIFACT_RUN','123')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.b.descriptor(json.dumps(self.descriptor),dict(self.env,**{key:value}))
        for key in self.descriptor:
            wrong=dict(self.descriptor);wrong[key]='wrong'
            with self.subTest(field=key),self.assertRaises((ValueError,TypeError)):
                self.b.descriptor(json.dumps(wrong),self.env)
        for value in ('{}','[]','null',json.dumps(dict(self.descriptor,extra=True)),
                      json.dumps(self.descriptor)[:-1]+',\"run_id\":40588897389}') :
            with self.assertRaises(ValueError):self.b.descriptor(value,self.env)

    def test_owned_mac_needs_actual_successful_source_guard_and_helper_steps(self):
        stage=dict(status='completed',conclusion='success',steps=[dict(name=name,number=n,status='completed',conclusion='success')
            for n,name in enumerate(['Require actual native Mac prerequisites','Verify qualified helper source, platform and checksum',
                                     'Build source-bound native candidate archives'],1)])
        text="  packaged-candidate:\n      - name: Require actual native Mac prerequisites\n        if: matrix.platform == 'macos'\n        run: |\n"+'\n'.join('          '+line for line in self.b.NATIVE_GUARD.splitlines())+'\n      - name: Next\n'
        with patch.object(self.b.subprocess,'check_output',return_value=text):
            self.b.owned_mac_job(stage,'a'*40,Path('.'))
            for broken in (dict(stage,conclusion='failure'),dict(stage,steps=stage['steps']*2),
                           dict(stage,steps=stage['steps'][1:])):
                with self.assertRaises(ValueError):self.b.owned_mac_job(broken,'a'*40,Path('.'))
        for bad in (text.replace('Darwin/arm64','Darwin/x86_64'),text.replace('test "$(uname','true; test "$(uname'),text*2):
            with patch.object(self.b.subprocess,'check_output',return_value=bad),self.assertRaises(ValueError):
                self.b.owned_mac_job(stage,'a'*40,Path('.'))

    def test_helper_requires_service_and_inner_digest_cpu_and_compiled_source(self):
        import struct
        source=self.b.ORIGINAL['source_revision']
        binary=bytes.fromhex('cffaedfe')+struct.pack('<I',0x100000c)+b'0'*24+source.encode()
        def data(raw,receipt=None):
            return zipped({'symphony-host':raw,'symphony-host.sha256':receipt or hashlib.sha256(raw).hexdigest()+'  symphony-host\n'})
        raw=data(binary);item=dict(id=44,digest='sha256:'+hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.b.mac_helper_receipt(raw,item,source)['helper_sha256'],hashlib.sha256(binary).hexdigest())
        with self.assertRaises(ValueError):self.b.mac_helper_receipt(raw,dict(item,digest='sha256:'+'0'*64),source)
        for wrong in (data(binary,'0'*64+'  symphony-host\n'),data(binary[:4]+b'0'*4+binary[8:]),data(binary.replace(source.encode(),b'e'*40))):
            with self.assertRaises(ValueError):self.b.mac_helper_receipt(wrong,dict(item,digest='sha256:'+hashlib.sha256(wrong).hexdigest()),source)

    def pending_subject(self):
        value=self.b.ORIGINAL
        return dict(run_id=value['run_id'],run_attempt=value['run_attempt'],source_revision=value['source_revision'],
                    lifecycle_job=value['lifecycle_job_id'],package_artifact=dict(id=value['package_artifact_id'],digest=value['package_artifact_digest']),
                    baseline_status=self.b.PENDING,bootstrap_descriptor=self.descriptor)

    def test_initial_source_doctor_listener_failure_cannot_produce_baseline_proof(self):
        projects=[dict(listener=True,image_bound=True,canaries=True)]*2
        value=self.b.initial_proof(self.pending_subject(),self.candidate,self.b.ORIGINAL['source_revision'],{'ready':True},projects)
        self.assertEqual(value['baseline_status'],self.b.PENDING)
        for source,doctor,actual in [('e'*40,{'ready':True},projects),(self.b.ORIGINAL['source_revision'],{'ready':False},projects),
                                      (self.b.ORIGINAL['source_revision'],{'ready':True},[dict(listener=False,image_bound=True,canaries=True)]*2)]:
            with self.assertRaises(ValueError):self.b.initial_proof(self.pending_subject(),self.candidate,source,doctor,actual)

    def test_final_proof_requires_full_transition_same_run_and_verified_cleanup(self):
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'777','GITHUB_RUN_ATTEMPT':'1'}):
            initial=self.b.initial_proof(self.pending_subject(),self.candidate,self.b.ORIGINAL['source_revision'],{'ready':True},
                                         [dict(listener=True,image_bound=True,canaries=True)]*2)
            result=dict(source_revision=self.candidate,passed=True,mode='two-revision-lifecycle',bootstrap_initial=initial,
                        bootstrap_transition_complete=True,cleanup=dict(errors=[],verified_absent=['owned-resource']))
            self.assertEqual(self.b.completed_proof(result,self.pending_subject(),self.candidate)['historical_lifecycle_conclusion'],'skipped')
            for changes in ({'passed':False},{'bootstrap_initial':None},{'bootstrap_transition_complete':False},
                            {'source_revision':'e'*40},{'mode':'current-manager-only'},
                            {'cleanup':dict(errors=['uncertain'],verified_absent=['owned'])},{'cleanup':dict(errors=[],verified_absent=[])}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    self.b.completed_proof(dict(result,**changes),self.pending_subject(),self.candidate)
            bad=copy.deepcopy(result);bad['bootstrap_initial']['current_run_id']='foreign'
            with self.assertRaises(ValueError):self.b.completed_proof(bad,self.pending_subject(),self.candidate)

    def test_real_executor_orders_initial_receipt_before_candidate_update_and_terminal_join(self):
        source=(Path(__file__).parents[1]/'package-lifecycle.py').read_text()
        self.assertLess(source.index("result['bootstrap_initial'] ="),source.index('client_run([str(command), "update", *update_args]'))
        self.assertIn("prior_baseline.completed_proof(result,bootstrap_subject",source)
        workflow=(Path(__file__).parents[3]/'.github/workflows/runner-release.yml').read_text()
        self.assertIn('python3 scripts/release/package-lifecycle.py "${args[@]}" packages runners evidence',workflow)
        self.assertIn('python3 scripts/release/prior_baseline.py verify-result evidence',workflow)
        self.assertIn('needs.ubuntu-package-lifecycle.outputs.bootstrap_qualified',workflow)
        caller=(Path(__file__).parents[3]/'.github/workflows/burrito-release.yml').read_text()
        before=caller.split('  production-source:\n',1)[1].split('  qualification:',1)[0]
        self.assertIn('python3 scripts/release/prior_baseline.py validate',before)
        self.assertIn('needs: production-source',caller.split('  qualification:',1)[1].split('  host-policy:',1)[0])

    def test_inner_package_source_architecture_inventory_and_manifest_before_execution(self):
        import struct
        source=self.b.ORIGINAL['source_revision']
        helper=b'\x7fELF\x02\x01'+b'0'*12+struct.pack('<H',62)+source.encode()
        info=dict(source_revision=source,version='0.0.6',channel='candidate',runners={})
        def package(root, binary=helper, manifest=info, corrupt=False):
            files={'host-platform':b'linux_x86_64\n','release.json':json.dumps(manifest).encode(),'libexec/symphony-host':binary}
            receipt=''.join(hashlib.sha256(raw).hexdigest()+'  ./'+name+'\n' for name,raw in files.items())
            files['files.sha256']=('0'*64+'  ./host-platform\n').encode() if corrupt else receipt.encode()
            target=root/'bundle.tar.gz'
            with tarfile.open(target,'w:gz') as archive:
                for name,raw in files.items():
                    entry=tarfile.TarInfo(name);entry.size=len(raw);entry.mode=0o644;archive.addfile(entry,io.BytesIO(raw))
            return target
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.b.package_inventory(package(root),source,expected_manifest=info)
            for kwargs in ({'corrupt':True},{'binary':helper[:18]+b'00'+helper[20:]},
                           {'manifest':dict(info,source_revision='e'*40)}, {'manifest':dict(info,runners={'foreign':True})}):
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                    self.b.package_inventory(package(root,**kwargs),source,expected_manifest=info)
