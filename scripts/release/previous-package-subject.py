#!/usr/bin/env python3
"""Retrieve a verified historical schema1 package/current-image test subject; never rebuild it."""
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
import component_provenance as proof
from reuse_payload import checksum,download
MAX_PACKAGE=512*1024*1024
MAX_PAYLOAD=4*1024**3


def job(jobs,name):
    values=[item for item in jobs if item['name'] in (name,'runners / '+name)]
    artifacts.require(len(values)==1 and values[0].get('status')=='completed' and values[0].get('conclusion')=='success',
                      'historical subject stage missing, ambiguous or unsuccessful: '+name)
    return values[0]


def within(artifact,stage):
    created=proof.instant(artifact['created_at'])
    artifacts.require(proof.instant(stage['started_at'])<=created<=proof.instant(stage['completed_at']),
                      'historical artifact does not belong to the selected successful attempt')


def select(run_id,root,fetch=proof.api):
    artifacts.require(type(run_id) is int and run_id>0,'invalid historical package run')
    controller=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    artifacts.require(os.environ.get('GITHUB_REPOSITORY')==proof.REPOSITORY and os.environ.get('GITHUB_SHA')==controller,
                      'historical consumer source/repository differs')
    run=fetch(f'actions/runs/{run_id}');source=run.get('head_sha')
    proof.run_identity(run_id,source,fetch)
    artifacts.require(type(run.get('run_attempt')) is int and run['run_attempt']>0,'historical run attempt missing')
    try:proof.trusted_lineage(source,fetch)
    except ValueError:
        artifacts.require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch','pre-merge historical subject requires exact manual selection')
    artifacts.require(subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',source,controller],
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0,
                      'historical subject is outside this manager lineage')
    inventory=fetch(f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    artifacts.require(inventory.get('total_count',0)<=100,'historical stage discovery incomplete')
    jobs=inventory['jobs'];native={}
    for provider in artifacts.PROVIDERS:
        for arch in artifacts.ARCHITECTURES:
            stage=job(jobs,f'runner-{provider}-{arch}')
            expected='ubuntu-24.04-arm' if arch=='arm64' else 'ubuntu-24.04'
            artifacts.require(expected in stage.get('labels',[]),'historical qualification was not on actual native architecture')
            native[provider+'-'+arch]=stage
    metadata=job(jobs,'candidate-metadata');packaged=job(jobs,'packaged-candidate-metadata')
    lifecycle=job(jobs,'ubuntu-package-lifecycle')
    for platform,runner in (('macos','macos-14'),('linux_x86_64','ubuntu-24.04')):
        found=[item for item in jobs if re.fullmatch(r'(?:runners / )?packaged-candidate \('+platform+r', '+runner+r'\)',item['name'])]
        artifacts.require(len(found)==1 and found[0]['status']=='completed' and found[0]['conclusion']=='success' and
                          runner in found[0].get('labels',[]),'historical actual native manager package missing')
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
    meta=artifact('runner-candidate-metadata',proof.MAX_RECORD_BYTES,metadata)
    raw=fetch(f"actions/artifacts/{meta['id']}/zip",binary=True)
    artifacts.require(len(raw)<=proof.MAX_RECORD_BYTES and 'sha256:'+hashlib.sha256(raw).hexdigest()==meta['digest'],
                      'historical native metadata archive checksum differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        artifacts.require(archive.namelist().count('release.json')==1 and
                          sum(item.file_size for item in archive.infolist())<=proof.MAX_RECORD_BYTES,'historical metadata member is ambiguous or oversized')
        manifest=json.loads(archive.read('release.json'))
    artifacts.validate(manifest,require_bundle=False)
    artifacts.require(manifest['schema_version']==1 and manifest['source_revision']==source,'historical subject must be the actual current schema1 source')
    for provider,group in manifest['runners'].items():
        for architecture,record in group.items():
            if 'qualification' in record:
                native_proof=record['qualification']
                artifacts.require(native_proof.get('run_id')==run_id and native_proof.get('controller_revision')==source,
                                  'schema1 bridge record was not qualified by this exact current run')
                proof.verify_origin(dict(record,provider=provider,architecture=architecture),fetch=fetch,
                    candidate_run=(run_id if os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and run.get('event')=='workflow_dispatch' else None))
    payloads={provider:artifact(f'runner-candidate-{provider}-x86_64',MAX_PAYLOAD,native[provider+'-x86_64']) for provider in artifacts.PROVIDERS}
    package=artifact('packaged-candidate',MAX_PACKAGE,packaged)
    return {'run_id':run_id,'run_attempt':run['run_attempt'],'source_revision':source,'role':'current',
            'native_jobs':{key:value['id'] for key,value in native.items()},'lifecycle_job':lifecycle['id'],
            'metadata_artifact':meta,'package_artifact':package,'payload_artifacts':payloads,'manifest':manifest}


def archive_config(path,provider,source):
    tag=f'localhost:5000/symphony-{provider}:candidate'
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
            artifacts.require(manifest['schema_version']==1 and manifest['source_revision']==selected['source_revision'] and
                              manifest['runners']==selected['manifest']['runners'],'historical package/native identities differ')
            bundle=manifest['bundles']['linux_x86_64'];name=bundle['filename']
            artifacts.require(archive.namelist().count(name)==1 and archive.getinfo(name).file_size<=MAX_PACKAGE,
                              'historical Linux package missing or oversized')
            with archive.open(name) as source,(previous/name).open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
            artifacts.require(checksum(previous/name)==bundle['sha256'],'historical Linux bundle checksum differs')
            (previous/'release.json').write_bytes(raw)
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
            actual=archive_config(old_images/name,provider,selected['source_revision'])
            record=manifest['runners'][provider]['x86_64']
            if 'image_config' in record:artifacts.require(record['image_config']==actual['image_config'],'historical recorded config differs')
            observed[provider]=dict(actual,image=record['image'],archive_sha256=match[1],payload_artifact_id=artifact['id'],payload_artifact_digest=artifact['digest'])
        (evidence/(selection+'-package-subject.json')).write_text(json.dumps(dict(selected,observed_x86_64=observed,
            qualification_kind='historical schema1 package integration subject; not a new component qualification',
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
    args=parser.parse_args();selected=select(args.run_id,args.root)
    if args.check_only:
        args.check_only.parent.mkdir(parents=True,exist_ok=True)
        args.check_only.write_text(json.dumps(selected,indent=2,sort_keys=True)+'\n')
    else:
        artifacts.require(args.packages is not None and args.images is not None and args.evidence is not None,'historical payload destinations missing')
        retrieve(selected,args.packages,args.images,args.evidence,selection=args.selection,package_only=args.package_only)


if __name__=='__main__':main()
