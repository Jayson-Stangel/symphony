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
import manager_version
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
    artifacts.require(prefix == 'ghcr.io/jayson-stangel/symphony',
                      'production publication refuses candidate-only distribution; use the approved public GHCR namespace')
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


def validate_public_distribution(stable):
    """Reject candidate-only references even if a forged prepared plan reaches publish."""
    artifacts.require(stable.get('channel') == 'stable', 'publication requires stable distribution metadata')
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            image = selected_image(stable, provider, architecture)
            artifacts.require(re.fullmatch(r'ghcr\.io/jayson-stangel/symphony-' + provider +
                                          r'@sha256:[a-f0-9]{64}', image or ''),
                              'production publication refuses candidate-only or unsupported distribution references')


def selected_image(stable,provider,architecture):
    if stable['schema_version']==1:return stable['runners'][provider][architecture]['image']
    return stable['distribution'][provider][architecture]


def docker(*args, timeout=600):
    return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.PIPE,timeout=timeout).strip()


def publish(prepared, verify, fetch=retrieve, probe=registry_available, invoke=docker, qualify=None, transfer=None, extract=None, before_transfer=lambda: None):
    """Protected job only: transfer only missing newly built images; never retag reused images."""
    stable=prepared['manifest'];artifacts.validate(stable,require_bundle=False)
    validate_public_distribution(stable)
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
    # Keep all payloads alive and fully validate ALL graphs before the first
    # authorization request or raw blob write. No Docker conversion is allowed.
    if extract is None:
        from qualified_graph import extract_archive
        extract=extract_archive
    if transfer is None:
        from graph_publication import publish_selected
        transfer=publish_selected
    with tempfile.TemporaryDirectory(prefix='component-publication-') as folder:
        folder=Path(folder).resolve()
        pending=[]
        for index,(key,item) in enumerate(selected.items()):
            if available[key]:continue
            record=item['record']
            archive=fetch(record,item['origin'],Path(folder)/('payload-'+str(index)),verify=verify)
            directory=Path(folder)/('graph-'+str(index))
            extract(archive,[record],directory)
            pending.append({'record':record,'directory':directory,'image':item['image']})
        if pending:
            before_transfer()
            transfer(pending)
        for item in pending:
            checked=qualify(item['image']) if qualify is not None else registry_available(item['image'],full=True)
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
        artifacts.require(prepared['manifest']['schema_version']==manager_version.schema(args.root),'publication format differs from committed release schema policy')
        artifacts.require(prepared['manager_source_revision']==revision and os.environ.get('GITHUB_REF_NAME')=='v'+prepared['manifest']['version'],
                          'publication tag/source differs')
        # The raw graph client reads credentials only after complete payload
        # preflight. No docker login, load, retag, or push can transform identity.
        from production_release import live_source
        check_tag=lambda: live_source(revision, os.environ['GITHUB_REF_NAME'])
        check_tag()
        result=publish(prepared,verify,before_transfer=check_tag)
    else:
        artifacts.require(args.plan is not None and args.metadata is not None,'publication planning inputs missing')
        metadata=json.loads(args.metadata.read_text())
        artifacts.require(metadata['schema_version']==manager_version.schema(args.root), 'publication format differs from committed release schema policy')
        result=plan_distribution(json.loads(args.plan.read_text()),metadata,revision,verify,
                                 lambda p,a:identity(args.root,p,a)['input_identity'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':main()
