import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
import component_restore as restore


def record(provider='codex', source='a'):
    return {'provider':provider,'architecture':'x86_64','image':'localhost:5000/symphony-'+provider+'@sha256:'+source*64,
            'archive_tag':'localhost:5000/symphony-'+provider+':candidate','image_config':'sha256:'+'b'*64,'source_revision':source*40,'input_identity':'c'*64,'protocol':1}


def inspected(rec, reference, typed=False):
    return {'Os':'linux','Architecture':'amd64','Id':rec['image'].split('@')[1] if typed else rec['image_config'],
            'Descriptor':{'digest':rec['image'].split('@')[1],'mediaType':'application/vnd.docker.distribution.manifest.v2+json'},
            'RepoDigests':[reference], 'Config':{'Labels':{'org.opencontainers.image.revision':rec['source_revision'],
            'com.symphony.agent':rec['provider'],'com.symphony.architecture':'amd64','com.symphony.inputs.sha256':rec['input_identity'],'com.symphony.protocol':'1'}}}


class RestoreTests(unittest.TestCase):
    def write(self, folder, provider, value=b'new qualified graph transport fixture'):
        folder.mkdir(parents=True,exist_ok=True)
        name=provider+'-x86_64'
        (folder/(name+'.docker.tar.gz')).write_bytes(value)
        (folder/(name+'.sha256')).write_text(hashlib.sha256(value).hexdigest()+'  '+name+'.docker.tar.gz\n')

    def manifests(self):
        return {'runners':{p:{'x86_64':record(p)} for p in ('codex','claude')}}

    def test_all_archive_checksums_and_graphs_precede_any_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);images=root/'images'
            for p in ('codex','claude'):self.write(images,p)
            calls=[]
            def extract(archive, records, destination):calls.append((archive,records,destination))
            current=self.manifests()
            entries=restore.prepare_graphs(images,current,current,root/'graphs',extract=extract)
            self.assertEqual(set(entries),{('candidate','codex'),('candidate','claude'),('previous','codex'),('previous','claude')})
            self.assertEqual(len(calls),2)
            self.assertTrue(all(len(c[1])==1 for c in calls),'unchanged original roots deduplicated')
            (images/'claude-x86_64.sha256').write_text('0'*64+'  claude-x86_64.docker.tar.gz\n')
            calls.clear()
            with self.assertRaisesRegex(ValueError,'checksum differs'):
                restore.prepare_graphs(images,current,current,root/'bad',extract=extract)
            self.assertEqual(calls,[])

    def test_separate_prior_selected_without_retagging_original_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);images=root/'images';current=self.manifests();prior=self.manifests()
            for p in ('codex','claude'):
                self.write(images,p);self.write(images/'previous',p,b'actual distinct prior transport')
                prior['runners'][p]['x86_64']=record(p,'d')
            original=copy.deepcopy(prior)
            calls=[]
            result=restore.prepare_graphs(images,current,prior,root/'graphs',extract=lambda *a:calls.append(a))
            self.assertEqual(len(calls),4)
            self.assertEqual(result[('previous','codex')]['record'],prior['runners']['codex']['x86_64'])
            self.assertEqual(prior,original)

    def test_legacy_transport_role_default_and_published_originals_do_not_mutate_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);images=root/'images';legacy=self.manifests();legacy['schema_version']=1
            for provider in ('codex','claude'):
                self.write(images,provider);legacy['runners'][provider]['x86_64'].pop('archive_tag')
            original=copy.deepcopy(legacy)
            entries=restore.prepare_graphs(images,legacy,legacy,root/'graphs',extract=lambda *args:None)
            self.assertEqual(legacy,original)
            self.assertEqual(entries[('candidate','codex')]['record']['archive_tag'],'localhost:5000/symphony-codex:candidate')
            published=copy.deepcopy(legacy);published['original_components']=copy.deepcopy(legacy['runners'])
            published['runners']['codex']['x86_64']['image']='ghcr.io/public/codex@sha256:'+'e'*64
            selected=restore.prepare_graphs(images,published,published,root/'public-graphs',extract=lambda *args:None)
            self.assertEqual(selected[('candidate','codex')]['record']['image'],original['runners']['codex']['x86_64']['image'])

    def test_raw_restore_then_original_digest_pull_no_load_tag_push(self):
        rec=record();target=rec['image'].replace(':5000/',':53058/');calls=[]
        def raw(directory, records, state, destinations=None):
            calls.append(('raw',records,destinations));return {rec['image']:target}
        def docker(args):
            calls.append(tuple(args))
            return subprocess.CompletedProcess(args,0,json.dumps([inspected(rec,target,typed=True)]))
        observed=restore.restore_component(docker,{'record':rec,'directory':Path('/verified-graph')},Path('/owned-state'),restore=raw)
        self.assertEqual(observed['Id'],rec['image'].split('@')[1])
        self.assertEqual(calls[1],('docker','pull',target))
        self.assertFalse(any(isinstance(x,tuple) and len(x)>1 and x[1] in ('load','tag','push','build') for x in calls))
        calls.clear()
        def missing(*args,**kwargs):raise ValueError('graph missing/corrupt')
        with self.assertRaisesRegex(ValueError,'missing/corrupt'):
            restore.restore_component(docker,{'record':rec,'directory':Path('/bad')},Path('/state'),restore=missing)
        self.assertEqual(calls,[])

    def test_exact_typed_runtime_and_classic_config_guard(self):
        rec=record()
        for typed in (False,True):restore.verify_runtime_image(inspected(rec,rec['image'],typed),rec,rec['image'])
        for field,value in (('Id','sha256:'+'f'*64),('Architecture','arm64'),('RepoDigests',[]),
                            ('Descriptor',{'digest':rec['image'].split('@')[1],'mediaType':'application/vnd.oci.image.index.v1+json'})):
            bad=inspected(rec,rec['image'],typed=True);bad[field]=value
            with self.assertRaises(ValueError):restore.verify_runtime_image(bad,rec,rec['image'])
        bad=inspected(rec,rec['image']);bad['Config']['Labels']['com.symphony.inputs.sha256']='e'*64
        with self.assertRaises(ValueError):restore.verify_runtime_image(bad,rec,rec['image'])

    def test_destination_digest_change_refuses_before_registry_or_docker(self):
        rec=record()
        def forbidden(*args,**kwargs):self.fail('mutation before immutable destination validation')
        with self.assertRaisesRegex(ValueError,'cannot change'):
            restore.restore_component(forbidden,{'record':rec,'directory':Path('/verified')},Path('/state'),
                                      destination='localhost:5000/other@sha256:'+'f'*64,restore=forbidden)


if __name__=='__main__':unittest.main()
