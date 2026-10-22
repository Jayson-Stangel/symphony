#!/usr/bin/env python3
"""Plan immutable distribution before protected publication; preserve original records."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import artifacts
from component_provenance import REPOSITORY, selection_verifier
from reuse_payload import retrieve
from runner_inputs import identity

from registry_closure import registry_available, manifest_available


def plan_distribution(plan, manifest, revision, verify, inputs, probe=registry_available,
                      prefix='ghcr.io/jayson-stangel/symphony'):
    artifacts.validate(manifest,require_bundle=False)
    artifacts.require(manifest['schema_version'] in (1,2) and manifest['source_revision']==revision and
                      plan.get('manager_source_revision')==revision and plan.get('planning_complete') is True,
                      'publication requires complete current-source component metadata')
    artifacts.require(prefix in ('ghcr.io/jayson-stangel/symphony','localhost:5000/symphony'),'unexpected distribution namespace')
    stable=copy.deepcopy(manifest);stable['channel']='stable'
    legacy=manifest['schema_version']==1
    if legacy:stable['original_components']=copy.deepcopy(manifest['runners'])
    else:stable['distribution']={}
    transfers=[];checks=[]
    # Every origin and retained public digest is checked before the first push.
    for provider in artifacts.PROVIDERS:
        if not legacy:stable['distribution'][provider]={}
        for architecture in artifacts.ARCHITECTURES:
            record=dict(manifest['runners'][provider][architecture],provider=provider,architecture=architecture)
            decision=plan['components'][provider][architecture]
            artifacts.require(decision.get('action') in ('build','reuse'),'unknown publication component decision')
            artifacts.require(record['input_identity']==decision['input_identity']==inputs(provider,architecture),
                              'publication component differs from current input closure')
            if decision['action']=='reuse':
                artifacts.require(record==decision['record'],'publication changed an original reused record')
            else:
                artifacts.require(record['source_revision']==revision,'new publication component has another source')
            if legacy:
                artifacts.require(decision['action']=='build' and record['source_revision']==revision,
                                  'schema1 bridge requires all four newly qualified same-source components')
            origin=verify(record)
            if decision['action']=='reuse':
                artifacts.require(origin==decision['origin'],'original publication origin changed after planning')
            image=f'{prefix}-{provider}@'+record['image'].split('@',1)[1]
            present=probe(image)
            artifacts.require(present or decision['action']=='build','reused original distribution digest is unavailable; no image push permitted')
            if legacy:
                stable['runners'][provider][architecture]['image']=image
                stable['runners'][provider][architecture].pop('qualification',None)
            else:stable['distribution'][provider][architecture]=image
            checks.append({'provider':provider,'architecture':architecture,'image':image,'available':present,'action':decision['action']})
            if not present:transfers.append({'record':record,'origin':origin,'image':image})
    artifacts.validate(stable,require_bundle=False)
    return {'manager_source_revision':revision,'schema_version':1,'distribution_checks':checks,'transfers':transfers,
            'manifest':stable,'original_records_preserved':stable.get('original_components',stable['runners'])==manifest['runners']}


def selected_image(stable,provider,architecture):
    if stable['schema_version']==1:return stable['runners'][provider][architecture]['image']
    return stable['distribution'][provider][architecture]


def docker(*args, timeout=600):
    return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.PIPE,timeout=timeout).strip()


def publish(prepared, verify, fetch=retrieve, probe=registry_available, invoke=docker, qualify=None):
    """Protected job only: transfer only missing newly built images; never retag reused images."""
    stable=prepared['manifest'];artifacts.validate(stable,require_bundle=False)
    artifacts.require(prepared['manager_source_revision']==stable['source_revision'],'publication manager identity differs')
    transfers=prepared['transfers']
    components=stable.get('original_components',stable['runners'])
    artifacts.require(prepared.get('original_records_preserved') is True,'publication did not preserve original components')
    # Retry/re-entry rechecks all reused refs before any transfer, independent of
    # the prior preflight receipt. No fixed latest tag is ever selected.
    artifacts.require(isinstance(transfers,list) and len(transfers)<=4,'invalid publication transfer selection')
    selected={};origins={};available={}
    # Validate the entire mutation plan before the first fetch/load/tag/push.
    for item in transfers:
        artifacts.require(isinstance(item,dict) and isinstance(item.get('record'),dict),'invalid transfer entry')
        record=item['record'];key=(record.get('provider'),record.get('architecture'))
        artifacts.require(key[0] in artifacts.PROVIDERS and key[1] in artifacts.ARCHITECTURES and key not in selected,
                          'unknown or duplicate publication transfer')
        artifacts.require(record==dict(components[key[0]][key[1]],provider=key[0],architecture=key[1]) and
                          record['source_revision']==stable['source_revision'],'publication transfer differs from current selected record')
        artifacts.require(item.get('image')==selected_image(stable,*key),'publication transfer destination differs')
        selected[key]=item
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            key=(provider,architecture)
            record=dict(components[provider][architecture],provider=provider,architecture=architecture)
            origins[key]=verify(record)
            if key in selected:
                artifacts.require(origins[key]==selected[key].get('origin'),'original payload origin changed before publication')
            available[key]=probe(selected_image(stable,provider,architecture))
            artifacts.require(available[key] or key in selected,'existing distribution digest unavailable before publication')
    for key,item in selected.items():
        if available[key]:continue
        record=item['record'];provider,architecture=key
        image=item['image']
        with tempfile.TemporaryDirectory(prefix='component-publication-') as folder:
            archive=fetch(record,item['origin'],Path(folder)/'payload',verify=verify)
            invoke('load','--input',str(archive),timeout=1200)
            original=record['archive_tag']
            observed=json.loads(invoke('image','inspect',original))[0]
            labels=observed.get('Config',{}).get('Labels',{})
            artifacts.require(observed.get('Id')==record['image_config'] and
                              observed.get('Os')+'/'+observed.get('Architecture')==record['platform'] and
                              labels.get('org.opencontainers.image.revision')==record['source_revision'] and
                              labels.get('com.symphony.inputs.sha256')==record['input_identity'] and
                              labels.get('com.symphony.agent')==provider and labels.get('com.symphony.protocol')=='1',
                              'loaded original publication config/source/input/platform differs')
            repository=image.split('@',1)[0]
            tag=f'{repository}:source-{record["source_revision"]}-{architecture}'
            invoke('tag',original,tag)
            invoke('push',tag)
            artifacts.require(image in json.loads(invoke('image','inspect',tag))[0].get('RepoDigests',[]),
                              'publication transfer changed original OCI digest')
            checked=qualify(image) if qualify is not None else registry_available(image,full=True)
            artifacts.require(checked,'new original digest blob closure is not anonymously available')
    return stable


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--metadata',type=Path)
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    revision=subprocess.check_output(['git','-C',str(args.root),'rev-parse','HEAD'],text=True).strip()
    artifacts.require(os.environ.get('GITHUB_REPOSITORY')==REPOSITORY and os.environ.get('GITHUB_SHA')==revision,
                      'publication source/repository differs')
    verify=selection_verifier(root=args.root)
    if args.prepared:
        artifacts.require(os.environ.get('SYMPHONY_PRODUCTION_RELEASES')=='enabled' and os.environ.get('GITHUB_REF_TYPE')=='tag',
                          'publication requires protected production tag context')
        prepared=json.loads(args.prepared.read_text())
        artifacts.require(prepared['manager_source_revision']==revision and os.environ.get('GITHUB_REF_NAME')=='v'+prepared['manifest']['version'],
                          'publication tag/source differs')
        # Credentials reach only the registry client, never Docker build args or images.
        login=subprocess.run(['docker','login','ghcr.io','--username',os.environ['GITHUB_ACTOR'],'--password-stdin'],
                             input=os.environ['GH_TOKEN'],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        artifacts.require(login.returncode==0,'protected registry login failed')
        try:result=publish(prepared,verify)
        finally:docker('logout','ghcr.io',timeout=120)
    else:
        artifacts.require(args.plan is not None and args.metadata is not None,'publication planning inputs missing')
        result=plan_distribution(json.loads(args.plan.read_text()),json.loads(args.metadata.read_text()),revision,verify,
                                 lambda p,a:identity(args.root,p,a)['input_identity'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':main()
