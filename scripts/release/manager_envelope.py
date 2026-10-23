#!/usr/bin/env python3
"""A bound legacy descriptor and independently authoritative manager component core."""
import copy
import hashlib
from pathlib import Path
import re

import artifacts
import manager_version
from finite_transition import validate_evidence,evaluate
from manager_contract import canonical,decode,require

CORE='manager-core.json'
HOSTS={'macos':['arm64'],'linux_x86_64':['x86_64']}
VERSION=re.compile(r'([0-9]+\.[0-9]+\.[0-9]+)-manager\.([a-f0-9]{40})\.manifest\.([a-f0-9]{64})')
CORE_KEYS={'schema_version','manager_source_revision','base_version','channel','legacy_source_revision','original_components',
           'verified_origins','distribution','supported_hosts','compatibility'}


def keys(value,expected,message):require(isinstance(value,dict) and set(value)==set(expected),message)


def validate_core(core):
    canonical(core);keys(core,CORE_KEYS,'unknown or incomplete manager core')
    require(type(core['schema_version']) is int and core['schema_version']==1,'unsupported manager core schema')
    manager=core['manager_source_revision'];legacy=core['legacy_source_revision']
    require(re.fullmatch('[a-f0-9]{40}',manager) and re.fullmatch('[a-f0-9]{40}',legacy),'invalid manager/legacy identity')
    require(re.fullmatch('[0-9]+\.[0-9]+\.[0-9]+',core['base_version']) and len(core['base_version'])<=32,'invalid bounded base manager version')
    require(core['channel'] in ('candidate','stable') and core['supported_hosts']==HOSTS,'unsupported manager channel or host policy')
    compat=core['compatibility'];keys(compat,{'runner_protocol','selection_mode','input_transition'},'invalid manager compatibility contract')
    require(type(compat['runner_protocol']) is int and compat['runner_protocol']==1 and
            compat['selection_mode']=='finite-input-transition-v1','unsupported manager/component compatibility')
    validate_evidence(compat['input_transition'],manager)
    require(legacy==compat['input_transition']['source_revision'],'legacy source differs from exact transition source authority')
    for field in ('original_components','distribution','verified_origins'):
        keys(core[field],artifacts.PROVIDERS,'both core providers required')
        for provider in artifacts.PROVIDERS:keys(core[field][provider],artifacts.ARCHITECTURES,'both core architectures required')
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            record=core['original_components'][provider][architecture]
            require('provider' not in record and 'architecture' not in record,'core must retain manifest-style original records')
            artifacts.validate_component(record,provider,architecture)
            require(record['source_revision']==legacy,'legacy envelope requires genuinely common original source')
            require(record['input_identity']==compat['input_transition']['source_input_identities'][provider][architecture],
                    'original component input differs from transition')
            image=core['distribution'][provider][architecture]
            require(isinstance(image,str) and artifacts.DIGEST_IMAGE.fullmatch(image) and
                    image.split('@',1)[1]==record['image'].split('@',1)[1],'distribution changes original OCI identity')
            origin=core['verified_origins'][provider][architecture];proof=record['qualification']
            keys(origin,{'run_id','run_attempt','artifact_id','artifact_digest','controller_revision','job_name',
                         'archive_filename','archive_sha256','record_run_id','record_run_attempt','record_controller_revision',
                         'record_job_name','native_qualification_scope','original_job_conclusion','record_artifact_id',
                         'record_artifact_digest','record_filename'},'invalid verified original origin keys')
            record_origin=proof.get('record_origin',{})
            for destination,source in (('record_run_id','run_id'),('record_run_attempt','run_attempt'),
                                       ('record_controller_revision','controller_revision'),('record_job_name','job_name')):
                require(origin[destination]==record_origin.get(source,proof[source]),'verified record origin differs: '+destination)
            require(origin['record_filename']==proof.get('record_filename',proof['archive_filename'].removesuffix('.docker.tar.gz')+'.json'),
                    'verified record member differs')
            recovered='native_step_recovery' in proof
            require(origin['native_qualification_scope']==('successful-native-step-before-seal-failure' if recovered else 'successful-native-job') and
                    origin['original_job_conclusion']==('failure' if recovered else 'success'),'verified original native outcome differs')
            for name in ('run_id','run_attempt','artifact_id','artifact_digest','controller_revision','job_name','archive_filename','archive_sha256'):
                require(origin.get(name)==proof.get(name),'verified original origin differs: '+name)
            for name in ('record_artifact_id','record_run_id','record_run_attempt'):
                require(type(origin.get(name)) is int and origin[name]>0,'invalid original record origin')
            require(re.fullmatch('sha256:[a-f0-9]{64}',origin.get('record_artifact_digest','')),'missing original record artifact checksum')
    return core


def artifact_version(core):
    validate_core(core)
    return core['base_version']+'-manager.'+core['manager_source_revision']+'.manifest.'+hashlib.sha256(canonical(core)).hexdigest()


def make_core(root,records,origins,transition,distribution=None,channel='candidate'):
    import subprocess
    manager=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    require(evaluate(root,records)==transition,'core transition was not measured from actual manager checkout')
    components={p:{a:{k:v for k,v in records[p][a].items() if k not in ('provider','architecture')}
                   for a in artifacts.ARCHITECTURES} for p in artifacts.PROVIDERS}
    core={'schema_version':1,'manager_source_revision':manager,'base_version':manager_version.read(root),
          'channel':channel,'legacy_source_revision':transition['source_revision'],'original_components':copy.deepcopy(components),
          'verified_origins':copy.deepcopy(origins),'distribution':copy.deepcopy(distribution or {p:{a:r['image'] for a,r in group.items()} for p,group in components.items()}),
          'supported_hosts':copy.deepcopy(HOSTS),'compatibility':{'runner_protocol':1,'selection_mode':'finite-input-transition-v1','input_transition':transition}}
    return validate_core(core)


def make_envelope(core):
    validate_core(core);version=artifact_version(core);digest=hashlib.sha256(canonical(core)).hexdigest()
    runners={p:{a:{'image':core['distribution'][p][a],'platform':r['platform'],'source_revision':r['source_revision'],
                       'tool_versions':copy.deepcopy(r['tool_versions'])} for a,r in group.items()} for p,group in core['original_components'].items()}
    mac={'filename':'symphony-'+version+'-macos.tar.gz','architectures':['arm64']}
    value={'schema_version':1,'version':version,'source_revision':core['legacy_source_revision'],'channel':core['channel'],
           'host_architectures':['arm64','x86_64'],'supported_hosts':copy.deepcopy(HOSTS),'runners':runners,'bundle':mac,
           'bundles':{'macos':copy.deepcopy(mac),'linux_x86_64':{'filename':'symphony-'+version+'-linux_x86_64.tar.gz','architectures':['x86_64']}},
           'manager_envelope':{'schema_version':1,'manager_source_revision':core['manager_source_revision'],'base_version':core['base_version'],
                               'core_filename':CORE,'core_sha256':digest}}
    artifacts.validate(value,require_bundle=False)
    return value


def write_release(core,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/CORE).write_bytes(canonical(validate_core(core)))
    (output/'release.json').write_text(__import__('json').dumps(make_envelope(core),sort_keys=True,indent=2)+'\n')
    return output/'release.json'


def validate_release(path):
    import json
    path=Path(path);manifest=json.loads(path.read_text());artifacts.validate(manifest,require_bundle=False)
    descriptor=manifest.get('manager_envelope');keys(descriptor,{'schema_version','manager_source_revision','base_version','core_filename','core_sha256'},'invalid envelope extension')
    require(type(descriptor['schema_version']) is int and descriptor['schema_version']==1 and descriptor['core_filename']==CORE,'unsupported envelope descriptor')
    raw=(path.parent/CORE).read_bytes();core=validate_core(decode(raw));expected=make_envelope(core)
    require(manifest['version']==expected['version'] and descriptor==expected['manager_envelope'],'manager/version/core binding differs')
    for field in ('source_revision','channel','runners','host_architectures','supported_hosts'):
        require(manifest.get(field)==expected[field],'legacy/core selected identity differs: '+field)
    for platform in HOSTS:
        actual=manifest['bundles'][platform];wanted=expected['bundles'][platform]
        require(actual.get('filename')==wanted['filename'] and actual.get('architectures')==wanted['architectures'],'derived bundle identity differs')
    normalized={'schema_version':2,'source_revision':core['manager_source_revision'],'version':manifest['version'],
                'manager_base_version':core['base_version'],'storage_revision':core['legacy_source_revision'],
                'channel':core['channel'],'runners':core['original_components'],'distribution':core['distribution'],
                'compatibility':{'runner_protocol':1},'supported_hosts':core['supported_hosts'],'host_architectures':['arm64','x86_64'],
                'bundle':manifest['bundle'],'bundles':manifest['bundles'],'manager_envelope':descriptor}
    if 'package_platform' in manifest:normalized['package_platform']=manifest['package_platform']
    return normalized
