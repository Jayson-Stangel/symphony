import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).parents[1]))
import component_publication as publication
import test_distribution


class PublicationTest(unittest.TestCase):
    def fixture(self):
        metadata=test_distribution.DistributionTest().fixture();metadata.pop('distribution')
        metadata['source_revision']='a'*40
        plan={'manager_source_revision':'a'*40,'planning_complete':True,'components':{}}
        for provider,group in metadata['runners'].items():
            plan['components'][provider]={}
            for architecture,value in group.items():
                plan['components'][provider][architecture]={'action':'reuse','record':value,'origin':{'exact':'origin'},'input_identity':value['input_identity']}
        return plan,metadata

    def prepare(self,plan,metadata,probe=lambda image:True):
        return publication.plan_distribution(plan,metadata,'a'*40,lambda record:{'exact':'origin'},lambda p,a:'d'*64,probe)

    def test_candidate_distribution_refuses_before_publication_effects(self):
        plan,metadata=self.fixture()
        with self.assertRaisesRegex(ValueError,'candidate-only distribution'):
            publication.plan_distribution(plan,metadata,'a'*40,lambda record:self.fail('origin lookup'),
                                          lambda p,a:self.fail('input lookup'),lambda image:self.fail('registry probe'),
                                          prefix='localhost:5000/symphony')
        prepared=self.prepare(plan,metadata)
        for image in ('localhost:5000/symphony-codex', 'ghcr.io/foreign/codex'):
            invalid=copy.deepcopy(prepared)
            original=invalid['manifest']['distribution']['codex']['arm64']
            invalid['manifest']['distribution']['codex']['arm64']=image+'@'+original.split('@')[1]
            with self.assertRaisesRegex(ValueError,'candidate-only or unsupported'):
                publication.publish(invalid,lambda record:self.fail('origin lookup'),
                                    probe=lambda image:self.fail('registry probe'),fetch=lambda *a:self.fail('payload fetch'))

    def test_manager_only_release_changes_no_original_record_and_runs_no_image_commands(self):
        plan,metadata=self.fixture();original=copy.deepcopy(metadata['runners'])
        prepared=self.prepare(plan,metadata)
        self.assertEqual(prepared['transfers'],[])
        self.assertEqual(prepared['manifest']['runners'],original)
        def no_image(*args,**kwargs):self.fail('manager-only publication executed image operation')
        observed=[]
        result=publication.publish(prepared,lambda record:observed.append(record),fetch=no_image,invoke=no_image,probe=lambda image:True)
        self.assertEqual(result['runners'],original)
        self.assertEqual(len(observed),4)

    def test_missing_reuse_refuses_before_any_transfer_and_missing_fresh_is_planned_once(self):
        plan,metadata=self.fixture()
        with self.assertRaisesRegex(ValueError,'reused original distribution'):
            self.prepare(plan,metadata,lambda image:False)
        built=metadata['runners']['codex']['arm64'];built['source_revision']='a'*40
        built['qualification']['source_revision']='a'*40
        plan['components']['codex']['arm64'].update(action='build')
        with self.assertRaisesRegex(ValueError,'reused original distribution'):
            self.prepare(plan,metadata,lambda image:'-codex@' not in image)
        built['image']='ghcr.io/original/codex@sha256:'+'7'*64
        built['qualification']['image']=built['image']
        prepared=self.prepare(plan,metadata,lambda image:not image.endswith('7'*64))
        self.assertEqual(len(prepared['transfers']),1)
        self.assertEqual(prepared['transfers'][0]['record'],built)
        self.assertEqual(prepared['manifest']['runners'],metadata['runners'])

    def test_registry_proves_raw_digest_and_refuses_unknown_errors(self):
        raw=b'{"schemaVersion":2,"manifests":[]}'
        digest='sha256:'+hashlib.sha256(raw).hexdigest()
        image='localhost:5000/symphony-codex@'+digest
        self.assertTrue(publication.manifest_available(image,lambda *args:(raw,{'Docker-Content-Digest':digest})))
        with self.assertRaisesRegex(ValueError,'changes original'):
            publication.manifest_available(image,lambda *args:(raw+b' ',{'Docker-Content-Digest':digest}))
        def missing(*args):
            raise HTTPError(args[0],404,'missing',{},io.BytesIO(b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}'))
        self.assertFalse(publication.manifest_available(image,missing))
        def daemon(*args):
            raise HTTPError(args[0],503,'unavailable',{},io.BytesIO(b'uncertain'))
        with self.assertRaises(HTTPError):publication.manifest_available(image,daemon)

    def test_payloads_all_extract_before_raw_transfer_and_no_docker_conversion(self):
        plan,metadata=self.fixture()
        for provider,group in metadata['runners'].items():
            for architecture,record in group.items():
                record['source_revision']='a'*40;record['qualification']['source_revision']='a'*40
                plan['components'][provider][architecture]['action']='build'
        prepared=self.prepare(plan,metadata,lambda image:False)
        events=[]
        def fetch(record,origin,destination,**kwargs):events.append(('fetch',record['image']));return Path('/verified-transport')
        def extract(archive,records,destination):events.append(('extract',records[0]['image']))
        def transfer(items):
            self.assertEqual(len([e for e in events if e[0]=='extract']),4)
            self.assertEqual([x['record'] for x in items],[x['record'] for x in prepared['transfers']])
            events.append(('raw-transfer',len(items)))
        def forbidden(*args,**kwargs):self.fail('Docker conversion invoked')
        publication.publish(prepared,lambda record:{'exact':'origin'},fetch=fetch,extract=extract,transfer=transfer,
                            probe=lambda image:False,invoke=forbidden,qualify=lambda image:True)
        self.assertEqual(events[-1],('raw-transfer',4))
        events.clear()
        def bad_last(archive,records,destination):
            events.append(('extract',records[0]['image']))
            if len([e for e in events if e[0]=='extract'])==4:raise ValueError('last graph corrupted')
        with self.assertRaisesRegex(ValueError,'last graph corrupted'):
            publication.publish(prepared,lambda record:{'exact':'origin'},fetch=fetch,extract=bad_last,transfer=forbidden,
                                probe=lambda image:False,invoke=forbidden)
        self.assertFalse(any(e[0]=='raw-transfer' for e in events))

    def test_schema1_bridge_remains_publishable_with_strict_same_source_originals(self):
        plan,metadata=self.fixture();metadata['schema_version']=1;metadata.pop('compatibility')
        for provider,group in metadata['runners'].items():
            for architecture,record in group.items():
                record['source_revision']='a'*40;record['qualification']['source_revision']='a'*40
                plan['components'][provider][architecture]['action']='build'
        prepared=self.prepare(plan,metadata,lambda image:False)
        self.assertEqual(len(prepared['transfers']),4)
        stable=prepared['manifest']
        self.assertEqual(stable['original_components'],metadata['runners'])
        for provider,group in stable['runners'].items():
            for architecture,record in group.items():
                self.assertTrue(record['image'].startswith('ghcr.io/jayson-stangel/symphony-'))
                self.assertNotIn('qualification',record)
        broken=copy.deepcopy(stable);broken['runners']['codex']['arm64']['image_config']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'differs from original'):
            publication.artifacts.validate(broken,require_bundle=False)
        plan['components']['codex']['arm64']['action']='reuse'
        with self.assertRaisesRegex(ValueError,'schema1 bridge'):
            self.prepare(plan,metadata,lambda image:True)

    def test_workflow_uses_plan_transfers_and_complete_stable_manifest(self):
        import re
        text=(Path(__file__).resolve().parents[3]/'.github/workflows/production-publish.yml').read_text()
        block=re.search(r'^  registry:\n(?:(?!^  [a-z]).*\n)*',text,re.M).group()
        self.assertNotIn('matrix:',block)
        self.assertIn('component_publication.py --prepared retained/signed/production-plan.json',block)
        self.assertIn('environment: production-release',block)
        self.assertIn("vars.SYMPHONY_PRODUCTION_RELEASES == 'enabled'",text)
        self.assertIn('production_release.py compare',block)
        self.assertNotIn('pattern: runner-stable-*',text)
        self.assertNotIn('build-manager-package.sh',text)

    def test_every_transfer_origin_destination_and_availability_precedes_any_mutation(self):
        plan,metadata=self.fixture()
        for provider,group in metadata['runners'].items():
            for arch,record in group.items():
                record['source_revision']='a'*40;record['qualification']['source_revision']='a'*40
                plan['components'][provider][arch]['action']='build'
        prepared=self.prepare(plan,metadata,lambda image:False)
        for variant in ('origin','record','destination','duplicate','late-service-error'):
            with self.subTest(variant=variant):
                changed=copy.deepcopy(prepared);calls=[];probes=[]
                if variant=='origin':changed['transfers'][1]['origin']={'stale':'origin'}
                if variant=='record':changed['transfers'][1]['record']['source_revision']='b'*40
                if variant=='destination':changed['transfers'][1]['image']='ghcr.io/foreign@sha256:'+'c'*64
                if variant=='duplicate':changed['transfers'][1]=copy.deepcopy(changed['transfers'][0])
                def no_effect(*args,**kwargs):calls.append(args);self.fail('mutation preceded complete prevalidation')
                def probe(image):
                    probes.append(image)
                    if variant=='late-service-error' and len(probes)==4:
                        raise HTTPError(image,503,'uncertain',{},io.BytesIO())
                    return False
                with self.assertRaises((ValueError,HTTPError)):
                    publication.publish(changed,lambda record:{'exact':'origin'},fetch=no_effect,invoke=no_effect,probe=probe)
                self.assertEqual(calls,[])
