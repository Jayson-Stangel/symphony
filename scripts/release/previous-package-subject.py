#!/usr/bin/env python3
"""Retrieve a verified prior manager and its original native image subjects; never rebuild them."""
import argparse
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

import artifacts
import prior_baseline
import component_provenance as proof
from reuse_payload import checksum,download
MAX_PACKAGE=512*1024*1024
MAX_JOIN_BYTES=4*1024*1024
MAX_PAYLOAD=4*1024**3


def job(jobs,name):
    values=[item for item in jobs if item['name'] in artifacts.candidate_job_names(name)]
    artifacts.require(len(values)==1 and values[0].get('status')=='completed' and values[0].get('conclusion')=='success',
                      'historical subject stage missing, ambiguous or unsuccessful: '+name)
    return values[0]


def within(artifact,stage):
    created=proof.instant(artifact['created_at'])
    artifacts.require(proof.instant(stage['started_at'])<=created<=proof.instant(stage['completed_at']),
                      'historical artifact does not belong to the selected successful attempt')


def select(run_id,root,fetch=proof.api,bootstrap=None):
    bootstrap = prior_baseline.descriptor(bootstrap)
    artifacts.require(type(run_id) is int and run_id>0,'invalid historical package run')
    controller=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    artifacts.require(os.environ.get('GITHUB_REPOSITORY')==proof.REPOSITORY and os.environ.get('GITHUB_SHA')==controller,
                      'historical consumer source/repository differs')
    run=fetch(f'actions/runs/{run_id}');source=run.get('head_sha')
    proof.run_identity(run_id,source,fetch)
    artifacts.require(type(run.get('run_attempt')) is int and run['run_attempt']>0,'historical run attempt missing')
    trusted=True
    try:proof.trusted_lineage(source,fetch)
    except ValueError:
        trusted=False
        artifacts.require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch','pre-merge historical subject requires exact manual selection')
    artifacts.require(not bootstrap or trusted, 'bootstrap requires verified trusted original lineage')
    artifacts.require(subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',source,controller],
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0,
                      'historical subject is outside this manager lineage')
    inventory=fetch(f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    artifacts.require(inventory.get('total_count',0)<=100,'historical stage discovery incomplete')
    jobs=inventory['jobs'];native={};helper_receipts={};owned_mac_package=False
    metadata=job(jobs,'candidate-metadata');packaged=job(jobs,'packaged-candidate-metadata')
    if bootstrap:
        values=[item for item in jobs if item['name'] in artifacts.candidate_job_names('ubuntu-package-lifecycle')]
        artifacts.require(run_id==bootstrap['run_id'] and run['run_attempt']==bootstrap['run_attempt']
                          and source==bootstrap['source_revision'] and len(values)==1
                          and values[0].get('id')==bootstrap['lifecycle_job_id']
                          and values[0].get('status')=='completed' and values[0].get('conclusion')=='skipped',
                          'bootstrap historical skipped lifecycle identity differs')
        lifecycle=values[0]
    else:
        lifecycle=job(jobs,'ubuntu-package-lifecycle')
    for platform,runner in (('macos','macos-14'),('linux_x86_64','ubuntu-24.04')):
        names=(f'packaged-candidate ({platform}, {runner})', f'runners / packaged-candidate ({platform}, {runner})',
               f'Build candidate package ({platform})', f'Qualify runner components / Build candidate package ({platform})')
        found=[item for item in jobs if item['name'] in names]
        artifacts.require(len(found)==1 and found[0]['status']=='completed' and found[0]['conclusion']=='success',
                          'historical actual native manager package missing')
        if runner not in found[0].get('labels',[]):
            artifacts.require(platform=='macos' and trusted, 'historical native manager platform differs')
            prior_baseline.owned_mac_job(found[0],source,root)
            owned_mac_package=True
    listing=fetch(f'actions/runs/{run_id}/artifacts?per_page=100')
    artifacts.require(listing.get('total_count',0)<=100,'historical artifact discovery incomplete')
    def artifact(name,maximum,stage):
        found=[item for item in listing['artifacts'] if item['name']==name]
        artifacts.require(len(found)==1,'historical artifact missing or ambiguous: '+name)
        item=found[0]
        artifacts.require(re.fullmatch(r'sha256:[a-f0-9]{64}',item.get('digest','')) and item['size_in_bytes']<=maximum,
                          'historical artifact has no bounded immutable identity')
        proof.available(item,name,item['digest'],run_id,source,datetime.now(timezone.utc));within(item,stage)
        return item
    meta=artifact('runner-candidate-metadata',MAX_JOIN_BYTES,metadata)
    raw=fetch(f"actions/artifacts/{meta['id']}/zip",binary=True)
    artifacts.require(len(raw)<=MAX_JOIN_BYTES and 'sha256:'+hashlib.sha256(raw).hexdigest()==meta['digest'],
                      'historical native metadata archive checksum differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        artifacts.require(archive.namelist().count('release.json')==1 and
                          sum(item.file_size for item in archive.infolist())<=MAX_JOIN_BYTES,'historical metadata member is ambiguous or oversized')
        manifest=json.loads(archive.read('release.json'))
        raw_manifest=manifest
        core_bytes=None
        if 'manager_envelope' in manifest:
            artifacts.require(archive.namelist().count('manager-core.json')==1,'historical manager core missing')
            from manager_envelope import normalize_document
            core_bytes=archive.read('manager-core.json')
            manifest=normalize_document(manifest,core_bytes)
    artifacts.validate(manifest,require_bundle=False)
    artifacts.require(manifest['source_revision']==source,'historical manager must be the actual selected current source')
    artifacts.require(not owned_mac_package or manifest['schema_version']==2, 'owned Mac prior requires original helper-bound schema2 provenance')
    originals=manifest.get('original_components',manifest['runners']);origins={};helper_jobs={}
    if manifest['schema_version']==2:
        from component_selection import records_from_join,manual_verifier,JOIN_STEP
        bundle=records_from_join(raw)
        steps=[item for item in metadata.get('steps',[]) if item.get('name')==JOIN_STEP]
        artifacts.require(len(steps)==1 and steps[0].get('status')=='completed' and steps[0].get('conclusion')=='success',
                          'schema2 prior manager has no successful complete component join')
        verifier=proof.verify_origin if trusted else manual_verifier(run_id,fetch=fetch)
        for record in bundle['records']:
            origin=verifier(record,fetch=fetch) if trusted else verifier(record)
            decision=bundle['plan']['components'][record['provider']][record['architecture']]
            if decision['action']=='reuse':
                artifacts.require(origin==decision['origin'],'prior manager selected original origin differs')
            key=record['provider']+'-'+record['architecture'];origins[key]=origin
            native[key]=proof.selected_job(origin['run_id'],origin['run_attempt'],origin['job_name'],record['architecture'],fetch)
        for platform,runner in (('macos','macos-14'),('linux_x86_64','ubuntu-24.04')):
            names=('package-inputs / native-host-package ('+runner+')','runners / package-inputs / native-host-package ('+runner+')',
                   f'Qualify package inputs / Native helpers and fixtures ({platform})',
                   f'Qualify runner components / Qualify package inputs / Native helpers and fixtures ({platform})')
            found=[item for item in jobs if item['name'] in names]
            artifacts.require(len(found)==1 and found[0].get('status')=='completed' and found[0].get('conclusion')=='success',
                              'schema2 prior manager native helper qualification missing')
            if runner not in found[0].get('labels',[]):
                artifacts.require(platform=='macos' and trusted, 'historical helper platform differs')
                prior_baseline.owned_mac_job(found[0],source,root,helper=True)
                item=artifact('native-host-helper-macos-'+source,2*1024*1024,found[0])
                helper_receipts[platform]=prior_baseline.mac_helper_receipt(fetch(f"actions/artifacts/{item['id']}/zip",binary=True),item,source)
            helper_jobs[platform]=found[0]['id']
        payloads={}
        for provider in artifacts.PROVIDERS:
            origin=origins[provider+'-x86_64'];item=fetch(f"actions/artifacts/{origin['artifact_id']}")
            proof.available(item,'runner-candidate-'+provider+'-x86_64',origin['artifact_digest'],origin['run_id'],
                            origin['controller_revision'],datetime.now(timezone.utc))
            artifacts.require(item['size_in_bytes']<=MAX_PAYLOAD,'original prior payload exceeds bound')
            within(item,native[provider+'-x86_64']);payloads[provider]=item
    else:
        for provider in artifacts.PROVIDERS:
            for arch in artifacts.ARCHITECTURES:
                stage=job(jobs,f'runner-{provider}-{arch}')
                expected='ubuntu-24.04-arm' if arch=='arm64' else 'ubuntu-24.04'
                artifacts.require(expected in stage.get('labels',[]),'historical qualification was not on actual native architecture')
                native[provider+'-'+arch]=stage
        for provider,group in originals.items():
            for architecture,record in group.items():
                if 'qualification' in record:
                    native_proof=record['qualification']
                    artifacts.require(native_proof.get('run_id')==run_id and native_proof.get('controller_revision')==source,
                                      'schema1 bridge record was not qualified by this exact current run')
                    origins[provider+'-'+architecture]=proof.verify_origin(dict(record,provider=provider,architecture=architecture),fetch=fetch,
                        candidate_run=(run_id if not trusted and run.get('event')=='workflow_dispatch' else None))
        payloads={provider:artifact(f'runner-candidate-{provider}-x86_64',MAX_PAYLOAD,native[provider+'-x86_64']) for provider in artifacts.PROVIDERS}
    package=artifact('packaged-candidate',MAX_PACKAGE,packaged)
    if bootstrap:
        artifacts.require(package['id']==bootstrap['package_artifact_id'] and package['digest']==bootstrap['package_artifact_digest'],
                          'bootstrap original package identity differs')
    return {'run_id':run_id,'run_attempt':run['run_attempt'],'source_revision':source,'role':'current',
            'baseline_status':prior_baseline.PENDING if bootstrap else 'HISTORICALLY_QUALIFIED',
            'bootstrap_descriptor':bootstrap,
            'native_jobs':{key:value['id'] for key,value in native.items()},'lifecycle_job':lifecycle['id'],
            'metadata_artifact':meta,'package_artifact':package,'payload_artifacts':payloads,'manifest':manifest,
            'original_native_origins':origins,'manager_helper_jobs':helper_jobs,'manager_helper_receipts':helper_receipts,'raw_manifest':raw_manifest,
            'manager_core_ascii':core_bytes.decode('ascii') if core_bytes is not None else None}


def archive_config(path,provider,source,record=None):
    tag=(record or {}).get('archive_tag',f'localhost:5000/symphony-{provider}:candidate')
    artifacts.require(tag in (f'localhost:5000/symphony-{provider}:candidate',f'localhost:5000/symphony-{provider}:previous'),
                      'historical archive role outside supported original repository')
    with tarfile.open(path,'r:gz') as archive:
        item=archive.getmember('manifest.json');artifacts.require(item.size<=1024*1024,'historical Docker manifest oversized')
        values=json.load(archive.extractfile(item))
        selected=[entry for entry in values if tag in (entry.get('RepoTags') or [])]
        artifacts.require(len(selected)==1,'historical current role missing or ambiguous')
        item=archive.getmember(selected[0]['Config']);artifacts.require(item.size<=1024*1024,'historical config oversized')
        raw=archive.extractfile(item).read();config=json.loads(raw);labels=config.get('config',{}).get('Labels',{})
        artifacts.require(config.get('os')=='linux' and config.get('architecture')=='amd64' and
                          labels.get('com.symphony.agent')==provider and labels.get('com.symphony.architecture')=='amd64' and
                          labels.get('org.opencontainers.image.revision')==source,'historical current image config/source/provider/platform differs')
        return {'image_config':'sha256:'+hashlib.sha256(raw).hexdigest(),'archive_tag':tag,'source_revision':source,
                'protocol':labels.get('com.symphony.protocol'),'input_identity':labels.get('com.symphony.inputs.sha256')}


def bounded_payload(artifact,path):
    download(artifact['id'],path,max_bytes=artifact['size_in_bytes'])
    artifacts.require(path.stat().st_size==artifact['size_in_bytes'],'historical artifact byte count differs from service receipt')


def retrieve(selected,packages,images,evidence,fetch_payload=bounded_payload,selection='previous',package_only=False):
    artifacts.require(selection in ('previous','legacy'),'unknown historical package selection slot')
    artifacts.require(not selected.get('bootstrap_descriptor') or (selection=='previous' and not package_only), 'bootstrap requires the complete prior package and images')
    artifacts.require(not (packages/selection).exists() and not (images/selection).exists(),'historical destinations already exist')
    packages.mkdir(parents=True,exist_ok=True);images.mkdir(parents=True,exist_ok=True);evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.previous-subject-',dir=packages.parent) as folder:
        staging=Path(folder);package_zip=staging/'package.zip'
        fetch_payload(selected['package_artifact'],package_zip)
        artifacts.require('sha256:'+checksum(package_zip)==selected['package_artifact']['digest'],'historical package service checksum differs')
        previous=staging/'previous-package';previous.mkdir()
        with zipfile.ZipFile(package_zip) as archive:
            artifacts.require(archive.namelist().count('release.json')==1 and archive.getinfo('release.json').file_size<=proof.MAX_RECORD_BYTES,
                              'historical package manifest missing or oversized')
            raw=archive.read('release.json');manifest=json.loads(raw);artifacts.validate(manifest)
            if 'manager_envelope' in manifest:
                artifacts.require(archive.namelist().count('manager-core.json')==1 and
                                  archive.getinfo('manager-core.json').file_size<=1024*1024,'historical packaged manager core missing or oversized')
                core=archive.read('manager-core.json')
                artifacts.require(core.decode('ascii')==selected.get('manager_core_ascii'),'historical package core differs from selected original metadata')
                from manager_envelope import normalize_document
                manifest=normalize_document(manifest,core)
                (previous/'manager-core.json').write_bytes(core)
            artifacts.require(manifest['schema_version']==selected['manifest']['schema_version'] and manifest['source_revision']==selected['source_revision'] and
                              manifest['runners']==selected['manifest']['runners'] and
                              manifest.get('original_components')==selected['manifest'].get('original_components'),
                              'historical package/native identities differ')
            bundle=manifest['bundles']['linux_x86_64'];name=bundle['filename']
            artifacts.require(archive.namelist().count(name)==1 and archive.getinfo(name).file_size<=MAX_PACKAGE,
                              'historical Linux package missing or oversized')
            with archive.open(name) as source,(previous/name).open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
            artifacts.require(checksum(previous/name)==bundle['sha256'],'historical Linux bundle checksum differs')
            (previous/'release.json').write_bytes(raw)
        if selected.get('bootstrap_descriptor'):
            prior_baseline.package_inventory(previous/name,selected['source_revision'],expected_manifest=manifest)
        if selected.get('manager_helper_receipts',{}).get('macos'):
            # Bind the actual packaged helper, not merely a sibling named artifact.
            mac=manifest['bundles']['macos']
            with zipfile.ZipFile(package_zip) as archive:
                artifacts.require(archive.namelist().count(mac['filename'])==1
                                  and archive.getinfo(mac['filename']).file_size<=MAX_PACKAGE,'original Mac bundle missing or oversized')
                mac_bytes=archive.read(mac['filename'])
            artifacts.require(hashlib.sha256(mac_bytes).hexdigest()==mac['sha256'],'original Mac bundle checksum differs')
            mac_archive=staging/'original-macos.tar.gz';mac_archive.write_bytes(mac_bytes)
            contents=prior_baseline.package_inventory(mac_archive,selected['source_revision'],'macos',manifest)
            artifacts.require(hashlib.sha256(contents['libexec/symphony-host'][0]).hexdigest()==
                              selected['manager_helper_receipts']['macos']['helper_sha256'], 'original packaged Mac helper differs from native producer')
        package_zip.unlink()
        old_images=staging/'previous-images';old_images.mkdir();observed={}
        for provider,artifact in (() if package_only else selected['payload_artifacts'].items()):
            payload=staging/(provider+'.zip');fetch_payload(artifact,payload)
            artifacts.require('sha256:'+checksum(payload)==artifact['digest'],'historical image service checksum differs')
            name=provider+'-x86_64.docker.tar.gz';receipt=provider+'-x86_64.sha256'
            with zipfile.ZipFile(payload) as archive:
                artifacts.require(archive.namelist().count(name)==1 and archive.namelist().count(receipt)==1 and
                                  archive.getinfo(receipt).file_size<=4096 and archive.getinfo(name).file_size<=MAX_PAYLOAD,
                                  'historical image archive/checksum missing or oversized')
                raw_receipt=archive.read(receipt)
                match=re.fullmatch(r'([a-f0-9]{64})  (?:\./)?'+re.escape(name)+r'\s*',raw_receipt.decode())
                artifacts.require(match is not None,'historical archive receipt invalid')
                with archive.open(name) as source,(old_images/name).open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
                artifacts.require(checksum(old_images/name)==match[1],'historical image archive checksum differs')
                (old_images/receipt).write_bytes(raw_receipt)
            payload.unlink()
            record=manifest.get('original_components',manifest['runners'])[provider]['x86_64']
            actual=archive_config(old_images/name,provider,record['source_revision'],record)
            if manifest['schema_version']==2:
                artifacts.require(actual['protocol']=='1' and actual['input_identity']==record['input_identity'] and
                                  record['qualification']['archive_sha256']==match[1],
                                  'prior original protocol/input/archive checksum differs')
            if 'image_config' in record:artifacts.require(record['image_config']==actual['image_config'],'historical recorded config differs')
            observed[provider]=dict(actual,image=record['image'],archive_sha256=match[1],payload_artifact_id=artifact['id'],payload_artifact_digest=artifact['digest'])
        (evidence/(selection+'-package-subject.json')).write_text(json.dumps(dict(selected,observed_x86_64=observed,
            qualification_kind=('UNQUALIFIED_PENDING fresh baseline lifecycle' if selected.get('bootstrap_descriptor') else 'historical verified manager package integration subject; original native qualification retained'),
            prior_image_build_executed=False,prior_image_export_executed=False,prior_large_upload_executed=False,
            package_only=package_only),indent=2,sort_keys=True)+'\n')
        previous.rename(packages/selection)
        if not package_only:old_images.rename(images/selection)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-id',type=int,required=True)
    parser.add_argument('--root',type=Path,default=Path('.'));parser.add_argument('--packages',type=Path)
    parser.add_argument('--images',type=Path);parser.add_argument('--evidence',type=Path);parser.add_argument('--selection',choices=('previous','legacy'),default='previous')
    parser.add_argument('--package-only',action='store_true',help='retrieve only the actual manager package; current selected component payloads are already present')
    parser.add_argument('--check-only',type=Path,help='retain verified small metadata only; never download package/image payloads')
    parser.add_argument('--bootstrap-descriptor',default=os.environ.get('BOOTSTRAP_PREVIOUS',''),help='explicit pinned manual dev bootstrap; historical lifecycle remains unqualified')
    args=parser.parse_args();selected=select(args.run_id,args.root,bootstrap=args.bootstrap_descriptor)
    if args.check_only:
        args.check_only.parent.mkdir(parents=True,exist_ok=True)
        args.check_only.write_text(json.dumps(selected,indent=2,sort_keys=True)+'\n')
    else:
        artifacts.require(args.packages is not None and args.images is not None and args.evidence is not None,'historical payload destinations missing')
        retrieve(selected,args.packages,args.images,args.evidence,selection=args.selection,package_only=args.package_only)


if __name__=='__main__':main()
