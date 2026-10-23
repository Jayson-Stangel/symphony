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
