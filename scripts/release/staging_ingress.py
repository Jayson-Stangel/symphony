"""Opaque private-staging transport; never qualification or native acceptance."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile
from dataclasses import dataclass

from component_provenance import REPOSITORY, api, available, instant

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = '.github/workflows/macos-local-signing-import.yml'
PRODUCER_JOB = 'Read private staged distribution'
MAX_PAYLOAD = 512 * 1024 * 1024
MAX_MANIFEST = 16 * 1024
MAX_ARTIFACT = MAX_PAYLOAD + 1024 * 1024
SCOPE = 'private-staging-ingress-only'


@dataclass(frozen=True)
class TransportContract:
    """Trusted caller constants, never deserialized from workflow/user inputs."""
    ref: str
    scope: str
    artifact_prefix: str
    producer_job: str
    producer_key: str
    consumer_key: str


PRODUCTION = TransportContract('refs/heads/main', SCOPE, 'local-signing-ingress',
                               PRODUCER_JOB, 'staging-ingress', 'verify')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value):
    require(re.fullmatch(r'[1-9][0-9]*', str(value)) and type(value) is not bool,
            'invalid ingress service identity')
    return int(value)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def expected_artifact_name(revision, run_id, attempt, prefix='local-signing-ingress'):
    return f'{prefix}-{revision}-{run_id}-{attempt}'


def source_context(revision, job, context=None, invoke=subprocess.run, *, contract=PRODUCTION):
    env = os.environ if context is None else context
    require(isinstance(contract, TransportContract) and isinstance(contract.ref, str)
            and contract.ref.startswith('refs/heads/')
            and all(isinstance(value, str) and value and len(value) <= 256 for value in
                    (contract.scope, contract.artifact_prefix, contract.producer_job,
                     contract.producer_key, contract.consumer_key)), 'invalid trusted ingress contract')
    require(re.fullmatch(r'[a-f0-9]{40}', revision or '') and
            env.get('GITHUB_REPOSITORY') == REPOSITORY and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_REF') == contract.ref and env.get('GITHUB_SHA') == revision
            and env.get('GITHUB_WORKFLOW_SHA') == revision
            and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@' + contract.ref
            and env.get('GITHUB_JOB') == job, 'ingress workflow source differs')
    run_id = positive(env.get('GITHUB_RUN_ID'))
    attempt = positive(env.get('GITHUB_RUN_ATTEMPT'))
    head = invoke(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True)
    dirty = invoke(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all'],
                   capture_output=True, text=True, check=True)
    require(head.returncode == dirty.returncode == 0 and head.stdout.strip() == revision and not dirty.stdout.strip(),
            'ingress requires exact clean committed source')
    return {'revision': revision, 'run_id': run_id, 'attempt': attempt, 'contract': contract}


def current_run(bound, fetch):
    for path in (f"actions/runs/{bound['run_id']}",
                 f"actions/runs/{bound['run_id']}/attempts/{bound['attempt']}"):
        value = fetch(path)
        require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] == bound['run_id']
                and type(value.get('run_attempt')) is int and value['run_attempt'] == bound['attempt']
                and value.get('head_sha') == bound['revision']
                and value.get('head_branch') == bound['contract'].ref.removeprefix('refs/heads/')
                and value.get('event') == 'workflow_dispatch'
                and value.get('path') == WORKFLOW and isinstance(value.get('head_repository'), dict)
                and value['head_repository'].get('full_name') == REPOSITORY
                and (value.get('status') == 'in_progress' or
                     value.get('status') == 'completed' and value.get('conclusion') == 'success'),
                'ingress run or attempt differs')


def validate_origin(origin, release_id, asset_id, staging_digest, revision, raw):
    keys = {'schema', 'origin', 'repository', 'source_revision', 'release_id', 'asset_id',
            'digest', 'size', 'name', 'staging_tag'}
    require(isinstance(origin, dict) and set(origin) == keys and type(origin['schema']) is int and origin['schema'] == 1
            and origin['origin'] == 'local' and origin['repository'] == REPOSITORY
            and origin['source_revision'] == revision and type(origin['release_id']) is int
            and origin['release_id'] == positive(release_id) and type(origin['asset_id']) is int
            and origin['asset_id'] == positive(asset_id) and origin['digest'] == staging_digest
            and re.fullmatch(r'sha256:[a-f0-9]{64}', staging_digest or '')
            and type(origin['size']) is int and 0 < origin['size'] <= MAX_PAYLOAD
            and isinstance(origin['name'], str) and Path(origin['name']).name == origin['name']
            and origin['name'] not in ('', '.', '..')
            and isinstance(origin['staging_tag'], str)
            and re.fullmatch('local-signing-' + revision + r'-[a-f0-9]{32}', origin['staging_tag'] or ''),
            'ingress original staging identity differs')
    require(type(raw) is bytes and len(raw) == origin['size'] and 'sha256:' + digest(raw) == staging_digest,
            'ingress original staging bytes differ')


def write_transport(destination, raw, manifest):
    require(not destination.exists(), 'ingress destination already exists')
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    payload = destination / 'payload.zip'
    receipt = destination / 'ingress.json'
    for path, value in ((payload, raw), (receipt, (json.dumps(manifest, sort_keys=True) + '\n').encode())):
        with path.open('xb') as output:
            path.chmod(0o600)
            output.write(value)
    return payload, receipt


def stage(release_id, asset_id, staging_digest, revision, destination, *, download=None,
          fetch=api, context=None, invoke=subprocess.run):
    """Production entry point: the main-only contract cannot be overridden."""
    return stage_core(release_id, asset_id, staging_digest, revision, destination, contract=PRODUCTION,
                      download=download, fetch=fetch, context=context, invoke=invoke)


def stage_core(release_id, asset_id, staging_digest, revision, destination, *, contract, download=None,
               fetch=api, context=None, invoke=subprocess.run):
    require(isinstance(contract, TransportContract), 'invalid trusted ingress contract')
    bound = source_context(revision, contract.producer_key, context, invoke, contract=contract)
    current_run(bound, fetch)
    if download is None:
        from local_distribution import download_staging
        download = download_staging
    raw, origin = download(release_id, asset_id, staging_digest, revision)
    validate_origin(origin, release_id, asset_id, staging_digest, revision, raw)
    manifest = {'schema': 1, 'scope': contract.scope, 'repository': REPOSITORY, 'source_revision': revision,
                'run_id': bound['run_id'], 'run_attempt': bound['attempt'], 'original_origin': origin,
                'payload': {'filename': 'payload.zip', 'sha256': digest(raw), 'size': len(raw)}}
    payload, receipt = write_transport(Path(destination), raw, manifest)
    return {'artifact_name': expected_artifact_name(revision, bound['run_id'], bound['attempt'], contract.artifact_prefix),
            'payload': payload, 'manifest': receipt, 'origin': origin}


def receive(artifact_id, artifact_name, artifact_digest, release_id, asset_id, staging_digest,
            revision, destination, *, fetch=api, context=None, invoke=subprocess.run):
    """Production entry point: fixture contracts never enter native admission."""
    return receive_core(artifact_id, artifact_name, artifact_digest, release_id, asset_id, staging_digest,
                        revision, destination, contract=PRODUCTION, fetch=fetch, context=context, invoke=invoke)


def receive_core(artifact_id, artifact_name, artifact_digest, release_id, asset_id, staging_digest,
                 revision, destination, *, contract, fetch=api, context=None, invoke=subprocess.run):
    require(isinstance(contract, TransportContract), 'invalid trusted ingress contract')
    bound = source_context(revision, contract.consumer_key, context, invoke, contract=contract)
    current_run(bound, fetch)
    artifact_id = positive(artifact_id)
    name = expected_artifact_name(revision, bound['run_id'], bound['attempt'], contract.artifact_prefix)
    require(artifact_name == name and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact_digest or ''),
            'ingress artifact selection differs')
    jobs = fetch(f"actions/runs/{bound['run_id']}/attempts/{bound['attempt']}/jobs?per_page=100")
    require(isinstance(jobs, dict) and isinstance(jobs.get('jobs'), list)
            and type(jobs.get('total_count')) is int and 0 <= jobs['total_count'] <= 100
            and len(jobs['jobs']) == jobs['total_count'] and all(isinstance(job, dict) for job in jobs['jobs']),
            'incomplete ingress producer job inventory')
    matches = [job for job in jobs['jobs'] if job.get('name') == contract.producer_job]
    require(len(matches) == 1 and matches[0].get('status') == 'completed'
            and matches[0].get('conclusion') == 'success', 'ingress producer did not uniquely succeed')
    job = matches[0]
    positive(job.get('id'))
    started, completed = instant(job.get('started_at')), instant(job.get('completed_at'))
    require(started <= completed, 'invalid ingress producer timing')
    artifact = fetch(f'actions/artifacts/{artifact_id}')
    require(isinstance(artifact, dict) and type(artifact.get('id')) is int and artifact['id'] == artifact_id
            and artifact.get('name') == name
            and artifact.get('digest') == artifact_digest and isinstance(artifact.get('workflow_run'), dict)
            and type(artifact['workflow_run'].get('id')) is int
            and type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_ARTIFACT,
            'ingress original artifact differs')
    from datetime import datetime, timezone
    available(artifact, name, artifact_digest, bound['run_id'], revision, datetime.now(timezone.utc))
    require(started <= instant(artifact.get('created_at')) <= completed,
            'ingress artifact was not created by the selected successful producer')
    raw = fetch(f'actions/artifacts/{artifact_id}/zip', binary=True)
    require(type(raw) is bytes and len(raw) <= MAX_ARTIFACT and 'sha256:' + digest(raw) == artifact_digest,
            'ingress immutable artifact bytes differ')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 2 and {entry.filename for entry in entries} == {'payload.zip', 'ingress.json'}
                and all(not entry.is_dir() and not entry.flag_bits & 1
                        and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG) for entry in entries),
                'unsafe ingress transport archive inventory')
        require(0 < archive.getinfo('payload.zip').file_size <= MAX_PAYLOAD
                and 0 < archive.getinfo('ingress.json').file_size <= MAX_MANIFEST,
                'ingress transport expands beyond bound')
        manifest = json.loads(archive.read('ingress.json'))
        payload_bytes = archive.read('payload.zip')
    require(isinstance(manifest, dict) and set(manifest) == {'schema', 'scope', 'repository', 'source_revision',
                'run_id', 'run_attempt', 'original_origin', 'payload'}
            and type(manifest['schema']) is int and manifest['schema'] == 1
            and manifest['scope'] == contract.scope and manifest['repository'] == REPOSITORY
            and manifest['source_revision'] == revision and type(manifest['run_id']) is int
            and manifest['run_id'] == bound['run_id'] and type(manifest['run_attempt']) is int
            and manifest['run_attempt'] == bound['attempt']
            and isinstance(manifest['payload'], dict) and type(manifest['payload'].get('size')) is int
            and manifest['payload'] == {'filename': 'payload.zip', 'sha256': digest(payload_bytes), 'size': len(payload_bytes)},
            'ingress transport manifest differs')
    origin = manifest['original_origin']
    validate_origin(origin, release_id, asset_id, staging_digest, revision, payload_bytes)
    payload, receipt = write_transport(Path(destination), payload_bytes, manifest)
    proof = {key: artifact[key] for key in ('id', 'name', 'digest', 'size_in_bytes', 'created_at', 'expires_at')}
    proof.update({'source_revision': revision, 'run_id': bound['run_id'], 'run_attempt': bound['attempt'],
                  'scope': contract.scope, 'producer_job_name': job['name'],
                  'producer_job_id': job['id'], 'producer_started_at': job['started_at'],
                  'producer_completed_at': job['completed_at']})
    return {'payload': payload, 'manifest': receipt, 'origin': origin, 'artifact': proof}
