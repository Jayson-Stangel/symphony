"""Manual mixed-run join authority fixtures; native origin verification remains mandatory."""
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
sys.path.insert(0,str(Path(__file__).parents[1]))
import component_selection as selection
import component_discovery as discovery
from test_artifacts import manifest
from test_component_provenance import record


class JoinedSelectionTest(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':selection.proof.REPOSITORY})
        self.env.start();self.addCleanup(self.env.stop)
        self.source='c'*40;self.records=[];self.values={};self.calls=[];self.foreign=None
        value=manifest();value.update(schema_version=2,source_revision=self.source,compatibility={'runner_protocol':1})
        plan={'planning_complete':True,'manager_source_revision':self.source,'components':{}}
        for provider in ('codex','claude'):
            plan['components'][provider]={}
            for arch in ('arm64','x86_64'):
                item=record(provider,arch)
                if provider=='claude':
                    item['source_revision']=self.source
                    item['qualification'].update(run_id=789,controller_revision=self.source,source_revision=self.source)
                self.records.append(item)
                value['runners'][provider][arch]={k:v for k,v in item.items() if k not in ('provider','architecture')}
                decision={'action':'build' if provider=='claude' else 'reuse','input_identity':item['input_identity']}
                if provider=='codex':decision.update(record=item,origin=self.origin(item))
                plan['components'][provider][arch]=decision
                self.values[provider+'-'+arch+'.json']=item
        self.values.update({'release.json':value,'component-plan.json':plan})
        self.run={'id':789,'head_sha':self.source,'run_attempt':1,'event':'workflow_dispatch','conclusion':'failure',
            'repository':{'full_name':selection.proof.REPOSITORY},'head_repository':{'full_name':selection.proof.REPOSITORY},
            'path':'.github/workflows/runner-release.yml'}
        self.job={'id':55,'name':'candidate-metadata','status':'completed','conclusion':'success',
            'started_at':'2026-10-22T00:00:00Z','completed_at':'2026-10-22T23:59:59Z',
            'steps':[{'name':selection.JOIN_STEP,'status':'completed','conclusion':'success'}]}
        self.artifact={'id':99,'name':'runner-candidate-metadata','expired':False,'expires_at':'2030-01-01T00:00:00Z',
            'created_at':'2026-10-22T12:00:00Z','workflow_run':{'id':789,'head_sha':self.source}}
        self.encode()
    def origin(self,item):return {'run_id':item['qualification']['run_id'],'artifact_id':item['qualification']['artifact_id'],
        'input_identity':item['input_identity'],'provider':item['provider'],'architecture':item['architecture']}
    def encode(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            for name,value in self.values.items():archive.writestr(name,json.dumps(value))
        self.zip=output.getvalue();self.artifact.update(size_in_bytes=len(self.zip),digest='sha256:'+hashlib.sha256(self.zip).hexdigest())
    def fetch(self,path,binary=False):
        if path=='actions/runs/789':return copy.deepcopy(self.run)
        if path=='actions/runs/123':return dict(self.run,id=123,head_sha='a'*40)
        if '/attempts/1/jobs?' in path:return {'total_count':1,'jobs':[copy.deepcopy(self.job)]}
        if path.startswith('actions/runs/789/artifacts?'):return {'total_count':1,'artifacts':[copy.deepcopy(self.artifact)]}
        if path=='actions/artifacts/99/zip':return self.zip
        if path.startswith('compare/'):
            revision=path.split('/')[1].split('...')[0]
            return {'status':'diverged' if revision==self.foreign else 'ahead','merge_base_commit':{'sha':revision}}
        raise AssertionError(path)
    def verify(self,item,**kwargs):
        self.calls.append(copy.deepcopy(item));return self.origin(item)
    def test_failed_downstream_run_does_not_erase_successful_complete_native_join(self):
        with patch.object(selection.proof,'verify_origin',side_effect=self.verify):
            verifier=selection.manual_verifier(789,fetch=self.fetch)
            self.assertEqual(verifier(self.records[0]),self.origin(self.records[0]))
            self.assertEqual(len(self.calls),4)
            self.assertEqual(verifier(self.records[1]),self.origin(self.records[1]))
            self.assertEqual(len(self.calls),4)
            self.assertEqual(verifier.selection_anchor()['overall_run_conclusion'],'failure')
            self.assertEqual(set(verifier.selection_anchor()['member_sha256']),set(self.values))
    def test_mixed_join_invokes_real_original_native_payload_and_record_verifier(self):
        payloads={};metadata={};raws={};jobs={123:[],789:[self.job]}
        for index,item in enumerate(self.records):
            native=item['qualification'];native['artifact_id']=1000+index
            run_id=native['run_id'];controller=native['controller_revision']
            payloads[1000+index]={'id':1000+index,'name':native['artifact_name'],'digest':native['artifact_digest'],
                'expired':False,'expires_at':'2030-01-01T00:00:00Z','size_in_bytes':1234,
                'workflow_run':{'id':run_id,'head_sha':controller}}
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as archive:archive.writestr(item['provider']+'-'+item['architecture']+'.json',json.dumps(item))
            data=output.getvalue();identifier=2000+index
            metadata[identifier]={'id':identifier,'name':'runner-records-'+item['provider']+'-'+item['architecture'],
                'digest':'sha256:'+hashlib.sha256(data).hexdigest(),'expired':False,'expires_at':'2030-01-01T00:00:00Z',
                'size_in_bytes':len(data),'workflow_run':{'id':run_id,'head_sha':controller}}
            raws[identifier]=data
            jobs[run_id].append({'name':native['job_name'],'status':'completed','conclusion':'success',
                'labels':['ubuntu-24.04-arm' if item['architecture']=='arm64' else 'ubuntu-24.04']})
            self.values[item['provider']+'-'+item['architecture']+'.json']=item
            self.values['release.json']['runners'][item['provider']][item['architecture']]={k:v for k,v in item.items() if k not in ('provider','architecture')}
            decision=self.values['component-plan.json']['components'][item['provider']][item['architecture']]
            if decision['action']=='reuse':decision['origin']=None
        def fetch(path,binary=False):
            if '/attempts/1/jobs?' in path:
                values=jobs[int(path.split('/')[2])];return {'total_count':len(values),'jobs':values}
            if path.startswith('actions/artifacts/'):
                identifier=int(path.split('/')[2])
                if identifier in payloads:return payloads[identifier]
                if identifier in raws:return raws[identifier]
            if '/artifacts?' in path:
                run_id=int(path.split('/')[2]);items=[item for item in metadata.values() if item['workflow_run']['id']==run_id]
                if run_id==789:items.append(self.artifact)
                return {'total_count':len(items),'artifacts':items}
            return self.fetch(path,binary)
        # The plan stores the real independently verified original origin, not a
        # fabricated receipt. Metadata still contains exact untouched records.
        for item in self.records:
            choice=self.values['component-plan.json']['components'][item['provider']][item['architecture']]
            if choice['action']=='reuse':choice['origin']=selection.proof.verify_origin(item,fetch=fetch,candidate_run=123)
        self.encode()
        verifier=selection.manual_verifier(789,fetch)
        original=verifier(self.records[0])
        self.assertEqual(original['record_run_id'],123)
        self.assertEqual(original['artifact_id'],1000)
        payloads[1003]['expired']=True
        with self.assertRaisesRegex(ValueError,'absent or expired'):
            selection.manual_verifier(789,fetch)(self.records[0])

    def test_explicit_incomplete_discovery_requires_the_complete_join_before_planning(self):
        with patch.object(selection.proof,'verify_origin',side_effect=self.verify):
            verifier=selection.manual_verifier(789,self.fetch)
            verifier.validate_candidates(self.records[2:])
            self.assertEqual(len(self.calls),4)
            self.assertEqual(verifier.selection_anchor()['source_revision'],self.source)
            self.job['conclusion']='failure'
            with self.assertRaisesRegex(ValueError,'join did not succeed'):
                selection.manual_verifier(789,self.fetch).validate_candidates(self.records[2:])
    def test_nonmember_and_foreign_original_lineage_refuse(self):
        with patch.object(selection.proof,'verify_origin',side_effect=self.verify):
            other=copy.deepcopy(self.records[0]);other['image_config']='sha256:'+'7'*64
            with self.assertRaisesRegex(ValueError,'exact selected joined'):selection.manual_verifier(789,self.fetch)(other)
            self.foreign='a'*40
            with self.assertRaisesRegex(ValueError,'outside selected joined'):selection.manual_verifier(789,self.fetch)(self.records[0])
    def test_all_four_origins_are_checked_before_any_member_is_authorized(self):
        def expired(item,**kwargs):
            if item['provider']=='claude' and item['architecture']=='x86_64':raise ValueError('last original payload expired')
            return self.verify(item,**kwargs)
        with patch.object(selection.proof,'verify_origin',side_effect=expired):
            with self.assertRaisesRegex(ValueError,'last original payload expired'):
                selection.manual_verifier(789,self.fetch)(self.records[0])
    def test_changed_plan_origin_or_record_cannot_authorize_reuse(self):
        for variant in ('origin','record','input','source'):
            with self.subTest(variant=variant):
                values=copy.deepcopy(self.values)
                decision=self.values['component-plan.json']['components']['codex']['arm64']
                if variant=='origin':decision['origin']['artifact_id']=999
                if variant=='record':decision['record']=dict(decision['record'],image_config='sha256:'+'1'*64)
                if variant=='input':decision['input_identity']='f'*64
                if variant=='source':self.values['component-plan.json']['manager_source_revision']='f'*40
                self.encode()
                with self.assertRaises(ValueError):selection.joined(789,self.fetch,self.verify)
                self.values=values;self.encode()
    def test_wrong_stage_attempt_expired_or_corrupt_service_archive_refuses(self):
        for variant in ('job','step','source','attempt','expired','checksum','extra'):
            with self.subTest(variant=variant):
                job,artifact,values=copy.deepcopy(self.job),copy.deepcopy(self.artifact),copy.deepcopy(self.values)
                if variant=='job':self.job['conclusion']='failure'
                if variant=='step':self.job['steps'][0]['conclusion']='failure'
                if variant=='source':self.values['release.json']['source_revision']='f'*40;self.encode()
                if variant=='attempt':self.artifact['created_at']='2026-10-21T00:00:00Z'
                if variant=='expired':self.artifact['expired']=True
                if variant=='checksum':self.artifact['digest']='sha256:'+'0'*64
                if variant=='extra':self.values['../../foreign']={};self.encode()
                with self.assertRaises(ValueError):selection.joined(789,self.fetch,self.verify)
                self.job,self.artifact,self.values=job,artifact,values;self.encode()
    def test_ordinary_events_cannot_instantiate_manual_anchor_and_discovery_is_untrusted(self):
        for event in ('pull_request','push'):
            with patch.dict(os.environ,{'GITHUB_EVENT_NAME':event}),self.assertRaises(ValueError):selection.manual_verifier(789,self.fetch)
        self.assertEqual(discovery.discover(self.fetch,candidate_run=789),self.records)
