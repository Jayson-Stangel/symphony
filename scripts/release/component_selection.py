#!/usr/bin/env python3
"""An explicit manual joined-artifact anchor can select exact original components.

No recursive authorization: every member still proves its own original native,
payload and small-record identities. Manager integration is a separate result.
"""
from datetime import datetime,timezone
import hashlib
import io
import json
import os
import stat
import zipfile

import artifacts
import component_provenance as proof

JOIN_STEP='Join exact planned native components without exporting reused payloads'
MAX_JOIN_BYTES=4*1024*1024


def records_from_join(data):
    artifacts.require(len(data)<=MAX_JOIN_BYTES,'joined artifact exceeds limit')
    expected={'release.json','component-plan.json'}|{p+'-'+a+'.json' for p in artifacts.PROVIDERS for a in artifacts.ARCHITECTURES}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if 'manager-core.json' in archive.namelist():expected.add('manager-core.json')
        artifacts.require(len(entries)==len(expected) and set(archive.namelist())==expected and
                          sum(item.file_size for item in entries)<=MAX_JOIN_BYTES and
                          all(not item.is_dir() and stat.S_IFMT(item.external_attr>>16) in (0,stat.S_IFREG) for item in entries),
                          'joined artifact members are incomplete, duplicated, unsafe or oversized')
        values={name:archive.read(name) for name in expected}
    raw_manifest=json.loads(values['release.json']);manifest=raw_manifest
    if 'manager_envelope' in raw_manifest:
        from manager_envelope import normalize_document
        artifacts.require('manager-core.json' in values,'joined envelope core missing')
        manifest=normalize_document(raw_manifest,values['manager-core.json'])
    else:
        artifacts.require('manager-core.json' not in values,'unexpected joined core')
        artifacts.validate(manifest,require_bundle=False)
    plan=json.loads(values['component-plan.json'])
    artifacts.require(plan.get('planning_complete') is True and plan.get('manager_source_revision')==manifest['source_revision'],
                      'joined plan source differs from manifest')
    records=[];originals=manifest.get('original_components',manifest['runners'])
    for provider in artifacts.PROVIDERS:
        for arch in artifacts.ARCHITECTURES:
            record=json.loads(values[provider+'-'+arch+'.json'])
            artifacts.validate_component(record,provider,arch)
            artifacts.require(record.get('provider')==provider and record.get('architecture')==arch and
                              {k:v for k,v in record.items() if k not in ('provider','architecture')}==originals[provider][arch],
                              'joined original record differs from manifest')
            decision=plan['components'][provider][arch]
            from reuse_payload import validate_decision_inputs
            validate_decision_inputs(record,decision,manifest['source_revision'],provider,arch)
            artifacts.require(decision.get('selected_input_identity',decision['input_identity'])==record['input_identity'],'joined plan input differs')
            if decision.get('selection_mode')=='finite-input-transition-v1':
                from finite_transition import validate_evidence
                transition=validate_evidence(decision['input_transition'],manifest['source_revision'])
                artifacts.require(decision['input_identity']==decision.get('desired_input_identity')==transition['manager_input_identities'][provider][arch] and
                                  record['input_identity']==transition['source_input_identities'][provider][arch],
                                  'joined finite selected/desired identities differ')
            if decision.get('selection_mode')=='finite-component-v1':
                from finite_transition import validate_component_evidence
                transition=validate_component_evidence(decision['input_transition'],manifest['source_revision'],provider,arch)
                artifacts.require(decision['input_identity']==decision.get('desired_input_identity')==transition['desired_input_identity'] and
                                  record['input_identity']==transition['selected_input_identity'],'joined finite component identity differs')
            if decision['action']=='reuse':
                artifacts.require(decision['record']==record,'joined reused record differs from exact plan selection')
            else:
                artifacts.require(decision['action']=='build' and record['source_revision']==manifest['source_revision'],
                                  'joined build does not belong to actual anchor source')
            records.append(record)
    return {'manifest':manifest,'raw_manifest':raw_manifest,'plan':plan,'records':records,
            'member_sha256':{name:hashlib.sha256(raw).hexdigest() for name,raw in values.items()}}


def joined(run_id,fetch=proof.api,verify=None,now=None):
    verify=verify or proof.verify_origin
    run=fetch(f'actions/runs/{run_id}');source=run.get('head_sha')
    proof.run_identity(run_id,source,fetch)
    artifacts.require(run.get('event')=='workflow_dispatch' and type(run.get('run_attempt')) is int and run['run_attempt']>0,
                      'joined manual anchor run/attempt differs')
    listing=fetch(f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    artifacts.require(listing.get('total_count',0)<=100,'joined stage discovery incomplete')
    jobs=[item for item in listing.get('jobs',[]) if item['name'] in artifacts.candidate_job_names('candidate-metadata')]
    artifacts.require(len(jobs)==1 and jobs[0].get('status')=='completed' and jobs[0].get('conclusion')=='success',
                      'complete original component join did not succeed')
    steps=[item for item in jobs[0].get('steps',[]) if item.get('name')==JOIN_STEP]
    artifacts.require(len(steps)==1 and steps[0].get('status')=='completed' and steps[0].get('conclusion')=='success',
                      'source-bound component join step did not succeed')
    inventory=fetch(f'actions/runs/{run_id}/artifacts?per_page=100')
    artifacts.require(inventory.get('total_count',0)<=100,'joined artifact discovery incomplete')
    found=[item for item in inventory.get('artifacts',[]) if item.get('name')=='runner-candidate-metadata']
    artifacts.require(len(found)==1,'joined artifact missing or ambiguous')
    artifact=found[0]
    artifacts.require(type(artifact.get('size_in_bytes')) is int and artifact['size_in_bytes']<=MAX_JOIN_BYTES,
                      'joined artifact unbounded')
    proof.available(artifact,'runner-candidate-metadata',artifact.get('digest'),run_id,source,now or datetime.now(timezone.utc))
    artifacts.require(proof.instant(jobs[0]['started_at'])<=proof.instant(artifact['created_at'])<=proof.instant(jobs[0]['completed_at']),
                      'joined artifact does not belong to successful selected attempt')
    data=fetch(f"actions/artifacts/{artifact['id']}/zip",binary=True)
    artifacts.require('sha256:'+hashlib.sha256(data).hexdigest()==artifact.get('digest'),'joined service artifact checksum differs')
    bundle=records_from_join(data)
    artifacts.require(bundle['manifest']['source_revision']==source,'joined artifact source differs from actual controller')
    verified={}
    for record in bundle['records']:
        native=record['qualification'];origin=native.get('record_origin',{})
        for revision in set((record['source_revision'],native['controller_revision'],origin.get('controller_revision',native['controller_revision']))):
            comparison=fetch(f'compare/{revision}...{source}')
            artifacts.require(comparison.get('status') in ('ahead','identical') and comparison.get('merge_base_commit',{}).get('sha')==revision,
                              'original component is outside selected joined source lineage')
        record_run=origin.get('run_id',native['run_id'])
        metadata_run=fetch(f'actions/runs/{record_run}')
        actual=verify(record,fetch=fetch,candidate_run=record_run if metadata_run.get('event')=='workflow_dispatch' else None)
        decision=bundle['plan']['components'][record['provider']][record['architecture']]
        if decision['action']=='reuse':
            artifacts.require(actual==decision['origin'],'joined original payload origin differs from selected plan')
        verified[json.dumps(record,sort_keys=True)]=actual
    return dict(bundle,origins=verified,anchor={'run_id':run_id,'run_attempt':run['run_attempt'],'source_revision':source,
        'job_id':jobs[0]['id'],'artifact_id':artifact['id'],'artifact_digest':artifact['digest'],
        'member_sha256':bundle['member_sha256'],'overall_run_conclusion':run.get('conclusion'),
        'qualification_scope':'successful component join; manager integration is independently required'})


def manual_verifier(run_id,fetch=proof.api):
    artifacts.require(type(run_id) is int and run_id>0 and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and
                      os.environ.get('GITHUB_REPOSITORY')==proof.REPOSITORY,'joined baseline requires an explicit manual fork selection')
    selected=None
    def load():
        nonlocal selected
        if selected is None:
            try:selected=joined(run_id,fetch=fetch)
            except (zipfile.BadZipFile,RuntimeError):raise ValueError('joined baseline archive is corrupt or unsupported') from None
        return selected
    def validate_candidates(records):
        direct={(item.get('provider'),item.get('architecture')) for item in records if
            item.get('qualification',{}).get('record_origin',{}).get('run_id',item.get('qualification',{}).get('run_id'))==run_id}
        expected={(p,a) for p in artifacts.PROVIDERS for a in artifacts.ARCHITECTURES}
        if direct!=expected:load()
    def verify(record):
        origin=record['qualification'];record_run=origin.get('record_origin',{}).get('run_id',origin['run_id'])
        if record_run==run_id:return proof.verify_origin(record,fetch=fetch,candidate_run=run_id)
        load()
        key=json.dumps(record,sort_keys=True)
        artifacts.require(key in selected['origins'],'component is not an exact selected joined original')
        return selected['origins'][key]
    verify.validate_candidates=validate_candidates
    verify.selection_anchor=lambda: selected['anchor'] if selected is not None else None
    return verify
