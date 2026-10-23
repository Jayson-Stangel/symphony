#!/usr/bin/env python3
"""Recover only small records after a known seal-format failure; never build/export/upload images."""
import argparse
from datetime import datetime, timezone
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
import time
import zipfile

import artifacts
import component_provenance as origin
from component_provenance import require
from reuse_payload import checksum, download
import runner_inputs
import test_registry

MAX_PAYLOAD = 4 * 1024 ** 3
ROOT = Path(__file__).resolve().parents[2]


def small_json(artifact, filename, run_id, controller, fetch=origin.api):
    origin.available(artifact, artifact['name'], artifact['digest'], run_id, controller, datetime.now(timezone.utc))
    require(0 < artifact['size_in_bytes'] <= origin.MAX_RECORD_BYTES, 'original plan artifact exceeds bound')
    data = fetch(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    require(len(data) <= origin.MAX_RECORD_BYTES and 'sha256:'+hashlib.sha256(data).hexdigest() == artifact['digest'], 'original plan artifact checksum differs')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(sum(member.file_size for member in archive.infolist()) <= origin.MAX_RECORD_BYTES and
                archive.namelist().count(filename) == 1, 'original plan archive is ambiguous or oversized')
        return json.loads(archive.read(filename))


def select(run_id, attempt, provider, architecture, fetch=origin.api):
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, 'invalid original native run/attempt')
    require(provider in artifacts.PROVIDERS and architecture in artifacts.ARCHITECTURES, 'unknown original native component')
    run = fetch(f'actions/runs/{run_id}')
    controller = run.get('head_sha')
    require(artifacts.REVISION.fullmatch(controller or ''), 'missing original native source')
    origin.run_identity(run_id, controller, fetch)
    require(run.get('event') == 'workflow_dispatch', 'only an explicitly selected manual native run may be recovered')
    listing = fetch(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    require(listing.get('total_count', 0) <= 100, 'incomplete native job discovery')
    names = artifacts.runner_job_names(provider, architecture)
    matching = [item for item in listing.get('jobs', []) if item.get('name') in names]
    require(len(matching) == 1, 'original native qualification is missing or ambiguous')
    job = origin.selected_job(run_id, attempt, matching[0]['name'], architecture, fetch)
    raw_log = fetch(f"actions/jobs/{job['id']}/logs", binary=True)
    eligibility = origin.recovery_eligibility(job, raw_log)
    inventory = fetch(f'actions/runs/{run_id}/artifacts?per_page=100')
    require(inventory.get('total_count', 0) <= 100, 'original artifact discovery is incomplete')
    def exactly(name):
        found = [value for value in inventory.get('artifacts', []) if value.get('name') == name]
        require(len(found) == 1, 'original immutable artifact missing or ambiguous: '+name)
        return found[0]
    payload = exactly(f'runner-candidate-{provider}-{architecture}')
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', payload.get('digest', '')), 'missing original payload digest')
    origin.available(payload, payload['name'], payload['digest'], run_id, controller, datetime.now(timezone.utc))
    require(0 < payload['size_in_bytes'] <= MAX_PAYLOAD, 'original native payload exceeds recovery bound')
    origin.recovery_payload_binding(job, eligibility, payload)
    plan = small_json(exactly('runner-component-plan'), 'component-plan.json', run_id, controller, fetch)
    require(plan.get('manager_source_revision') == controller and plan.get('planning_complete') is True,
            'original source-bound plan identity differs')
    decision = plan['components'][provider][architecture]
    require(decision.get('action') == 'build' and decision.get('source_revision') == controller,
            'original native component was not actually planned for this source')
    native_step = next(step for step in job['steps'] if step['number'] == eligibility['qualification_step_number'])
    start, end = origin.instant(native_step['started_at']), origin.instant(native_step['completed_at'])
    digests = set()
    for line in raw_log.decode('utf-8-sig').splitlines():
        timestamp, _, message = line.partition(' ')
        match = re.fullmatch(r'candidate: digest: (sha256:[a-f0-9]{64}) size: [0-9]+', message)
        if match and start <= origin.instant(timestamp) <= end:
            digests.add(match[1])
    require(len(digests) == 1, 'original qualified OCI digest is missing or ambiguous')
    return {'run_id': run_id, 'attempt': attempt, 'controller_revision': controller, 'job': job,
            'eligibility': eligibility, 'payload': payload, 'plan': decision,
            'image': f'localhost:5000/symphony-{provider}@'+next(iter(digests))}


def extract(payload, zip_path, destination, provider, architecture):
    require('sha256:'+checksum(zip_path) == payload['digest'], 'original image payload artifact checksum differs')
    name = f'{provider}-{architecture}.docker.tar.gz'
    checksum_name = f'{provider}-{architecture}.sha256'
    archive = destination/name
    with zipfile.ZipFile(zip_path) as bundle:
        require(bundle.namelist().count(name) == 1 and bundle.namelist().count(checksum_name) == 1,
                'original archive or checksum is missing or duplicated')
        member = bundle.getinfo(name)
        require(0 < member.file_size <= MAX_PAYLOAD and bundle.getinfo(checksum_name).file_size <= 4096,
                'original archive metadata exceeds bound')
        require(shutil.disk_usage(destination).free > member.file_size+512*1024**2, 'insufficient recovery archive space')
        expected = bundle.read(checksum_name).decode().strip()
        match = re.fullmatch(r'([a-f0-9]{64})  (?:\./)?'+re.escape(name), expected)
        require(match is not None, 'original native archive checksum receipt is invalid')
        with bundle.open(member) as source, archive.open('xb') as output:
            shutil.copyfileobj(source, output, length=1024*1024)
    require(checksum(archive) == match[1], 'original native image archive checksum differs')
    return archive, match[1]


def archive_config(archive, expected_tag):
    with tarfile.open(archive, 'r:gz') as bundle:
        manifest = bundle.getmember('manifest.json')
        require(manifest.size <= 1024*1024, 'original Docker archive manifest exceeds bound')
        values = json.load(bundle.extractfile(manifest))
        require(isinstance(values, list) and len(values) == 1 and values[0].get('RepoTags') == [expected_tag],
                'original archive does not contain exactly the qualified image/tag')
        config = bundle.getmember(values[0]['Config'])
        require(config.size <= 1024*1024, 'original Docker config exceeds bound')
        data = bundle.extractfile(config).read()
        return 'sha256:'+hashlib.sha256(data).hexdigest()


def docker(*args, timeout=600):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE, timeout=timeout).strip()


def image_present(image):
    result = subprocess.run(['docker', 'image', 'inspect', image], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode == 0:
        values = json.loads(result.stdout)
        require(isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict)
                and re.fullmatch(r'sha256:[a-f0-9]{64}', values[0].get('Id', '')),
                'invalid image inspection receipt')
        return True
    # Only Docker's exact missing-image response proves absence. Daemon,
    # permission and transport failures leave local cache state unknown.
    require(result.returncode == 1 and result.stdout.strip() in ('', '[]') and
            result.stderr.strip() == 'Error response from daemon: No such image: '+image,
            'image inspection failed without proving absence')
    return False


def remove_before_pull(tag, image):
    docker('image', 'rm', tag)
    if image_present(image):
        docker('image', 'rm', image)
    require(not image_present(image), 'original digest remains cached before replay pull')


def recover(args):
    require(os.environ.get('GITHUB_REPOSITORY') == origin.REPOSITORY and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
            'native record recovery requires an explicit manual fork diagnostic')
    controller = os.environ['GITHUB_SHA']
    require(subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() == controller,
            'recovery controller checkout differs from workflow source')
    selected = select(args.run_id, args.attempt, args.provider, args.architecture)
    original = selected['controller_revision']
    ancestry = origin.api(f'compare/{original}...{controller}')
    require(ancestry.get('status') in ('ahead','identical') and ancestry.get('merge_base_commit',{}).get('sha') == original,
            'original native source is outside selected recovery lineage')
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    state = output/f'{args.provider}-{args.architecture}.recovery-state.txt'
    evidence = {'original_native_run':args.run_id, 'original_native_attempt':args.attempt, 'original_controller_revision':original,
                'recovery_controller_revision':controller, 'payload_artifact_id':selected['payload']['id'],
                'payload_artifact_digest':selected['payload']['digest'], 'native_step_evidence':selected['eligibility'],
                'image_build_executed':False, 'image_export_executed':False, 'large_artifact_upload_executed':False,
                'native_image_qualification_repeated':False, 'replay_identity_verified':False}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='native-record-recovery-') as folder:
        temporary = Path(folder)
        source = temporary/'source'
        subprocess.run(['bash',str(ROOT/'scripts/release/prepare-candidate-source.sh'),original,str(source)],check=True,timeout=180)
        snapshot = runner_inputs.identity(source,args.provider,args.architecture)
        require(snapshot['input_identity'] == selected['plan']['input_identity'] and snapshot['inputs'] == selected['plan']['inputs'],
                'original committed build input snapshot differs from retained plan')
        snapshot_path = temporary/'inputs.json'; snapshot_path.write_text(json.dumps(snapshot))
        payload = temporary/'payload.zip'
        require(shutil.disk_usage(temporary).free > selected['payload']['size_in_bytes']*2+1024**3, 'insufficient recovery payload space')
        before=time.monotonic(); download(selected['payload']['id'],payload); evidence['download_seconds']=time.monotonic()-before
        archive, archive_sha = extract(selected['payload'],payload,temporary,args.provider,args.architecture)
        payload.unlink()
        tag=f'localhost:5000/symphony-{args.provider}:candidate'
        config = archive_config(archive,tag)
        before=time.monotonic(); docker('load','--input',str(archive),timeout=1200); evidence['load_seconds']=time.monotonic()-before
        observed = json.loads(docker('image','inspect',tag))[0]
        labels = observed.get('Config', {}).get('Labels', {})
        expected_arch = 'amd64' if args.architecture == 'x86_64' else 'arm64'
        require(observed.get('Id') == config and observed.get('Os') == 'linux' and observed.get('Architecture') == expected_arch,
                'loaded original image config/platform differs')
        require(labels.get('org.opencontainers.image.revision') == original and labels.get('com.symphony.agent') == args.provider and
                labels.get('com.symphony.architecture') == expected_arch and labels.get('com.symphony.protocol') == '1' and
                labels.get('com.symphony.inputs.sha256') == snapshot['input_identity'], 'loaded original source/input/protocol identity differs')
        try:
            test_registry.start(state)
            before=time.monotonic(); docker('push',tag); evidence['loopback_push_seconds']=time.monotonic()-before
            observed = json.loads(docker('image','inspect',tag))[0]
            require(selected['image'] in observed.get('RepoDigests',[]), 'replayed OCI digest differs from original qualified digest')
            remove_before_pull(tag,selected['image'])
            before=time.monotonic(); docker('pull',selected['image']); evidence['loopback_pull_seconds']=time.monotonic()-before
            require(json.loads(docker('image','inspect',selected['image']))[0]['Id'] == config, 'replayed config differs from retained archive')
            record_path = output/f'{args.provider}-{args.architecture}.json'
            subprocess.run(['python3',str(ROOT/'scripts/release/record.py'),'--image',selected['image'],'--provider',args.provider,
                            '--architecture',args.architecture,'--revision',original,'--inputs',str(snapshot_path),'--source-root',str(source),
                            '--archive-tag',tag,'--output',str(record_path)],check=True,timeout=600)
            record=json.loads(record_path.read_text())
            record['qualification']={'repository':origin.REPOSITORY,'run_id':args.run_id,'run_attempt':args.attempt,
                'controller_revision':original,'job_name':selected['job']['name'],'artifact_id':selected['payload']['id'],
                'artifact_name':selected['payload']['name'],'artifact_digest':selected['payload']['digest'],
                'archive_filename':archive.name,'archive_sha256':archive_sha,'record_filename':record_path.name,
                'payload_origin':{'run_id':args.run_id,'controller_revision':original},
                'record_origin':{'run_id':int(os.environ['GITHUB_RUN_ID']),'run_attempt':int(os.environ['GITHUB_RUN_ATTEMPT']),
                                 'controller_revision':controller,'job_name':f'Recover native records ({args.provider}, {args.architecture})'},
                'native_step_recovery':selected['eligibility'],
                **{key:record[key] for key in ('image','source_revision','input_identity')}}
            artifacts.validate_component(record,args.provider,args.architecture)
            record_path.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
            evidence.update(image=selected['image'],image_config=config,input_identity=record['input_identity'],archive_sha256=archive_sha,
                            archive_bytes=archive.stat().st_size,replay_identity_verified=True)
        finally:
            try:
                test_registry.cleanup(state)
            finally:
                evidence['elapsed_seconds']=time.monotonic()-started
                (output/f'{args.provider}-{args.architecture}.recovery-evidence.txt').write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',type=int,required=True)
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--provider',choices=artifacts.PROVIDERS,required=True)
    parser.add_argument('--architecture',choices=tuple(artifacts.ARCHITECTURES),required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        recover(args)
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError,zipfile.BadZipFile,tarfile.TarError) as error:
        parser.exit(1,'native record recovery: '+type(error).__name__+'; original payloads retained, no new image built/exported/uploaded\n')


if __name__=='__main__':
    main()
