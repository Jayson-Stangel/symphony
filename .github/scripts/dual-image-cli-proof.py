#!/usr/bin/env python3
"""Never-merge native CLI diagnostic; retained payload in, small proof out."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = 'Jayson-Stangel/symphony'
RUN = 39857032359
PROVIDER, ARCHITECTURE = 'codex', 'x86_64'
MAX_BYTES = 8 * 1024 ** 3
spec = importlib.util.spec_from_file_location('retained_benchmark', ROOT/'scripts/release/benchmark-transfer.py')
BENCH = importlib.util.module_from_spec(spec); spec.loader.exec_module(BENCH)


def api(path):
    return ['gh', 'api', 'repos/'+REPOSITORY+'/'+path]


def binary_api(path):
    command = api(path)
    help_text = subprocess.check_output(['gh', 'api', '--help'], text=True, timeout=30)
    if '--allow-escape-sequences' in help_text:
        command.append('--allow-escape-sequences')
    return command


def download(identifier, destination):
    command = binary_api(f'actions/artifacts/{identifier}/zip')
    deadline, received = time.monotonic()+1200, 0
    with destination.open('xb') as output, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0 or not ready.select(remaining):
                        raise TimeoutError('retained artifact download exceeded 1200 seconds')
                    block = os.read(process.stdout.fileno(), 1024*1024)
                    if not block: break
                    received += len(block)
                    assert received <= MAX_BYTES, 'retained ZIP exceeds bound'
                    output.write(block)
            assert process.wait(timeout=max(.01, deadline-time.monotonic())) == 0, 'retained ZIP download failed'
        finally:
            if process.poll() is None: process.kill(); process.wait()


def absent(reference):
    value = subprocess.run(['docker', 'image', 'inspect', reference], text=True, capture_output=True, timeout=30)
    if value.returncode == 0:
        return False
    assert value.returncode == 1 and value.stdout.strip() in ('', '[]') and value.stderr.strip() in (
        'Error response from daemon: No such image: '+reference, 'Error: No such image: '+reference), 'image absence is uncertain'
    return True


def main():
    evidence = ROOT/'timing-evidence'
    evidence.mkdir(exist_ok=True)
    result = {'passed': False, 'purpose': 'native repeated-image CLI only; no image build, runtime requalification, registry or large upload',
              'controller_revision': os.environ['GITHUB_SHA'], 'source_run': RUN, 'provider': PROVIDER, 'architecture': ARCHITECTURE}
    acquired = set()
    try:
        assert os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
        assert os.environ['GITHUB_REPOSITORY'] == REPOSITORY
        assert os.uname().sysname == 'Linux' and os.uname().machine == 'x86_64'
        assert os.environ['RUN_ID'] == str(RUN) and os.environ['PROVIDER'] == PROVIDER and os.environ['ARCHITECTURE'] == ARCHITECTURE
        source = json.loads((evidence/'source-run.txt').read_text())
        native = json.loads((evidence/'native-producer.txt').read_text())
        assert source['id'] == RUN and source['head_repository']['full_name'] == REPOSITORY
        assert source['head_sha'] == 'c920e5386294f209c5a11e0f298673001473d75b'
        assert native['name'] == 'runner-codex-x86_64' and native['status'] == 'completed' and native['conclusion'] == 'success'
        assert 'ubuntu-24.04' in native['labels']
        artifacts = []
        for page in range(1, 4):
            response = json.loads(subprocess.check_output(api(f'actions/runs/{RUN}/artifacts?per_page=100&page={page}'), timeout=60))
            artifacts += response['artifacts']
            if len(response['artifacts']) < 100: break
        else: raise ValueError('retained artifact inventory exceeds bounded discovery')
        selected = [item for item in artifacts if item['name'] == 'runner-candidate-codex-x86_64']
        assert len(selected) == 1 and not selected[0]['expired']
        artifact = selected[0]
        assert artifact['workflow_run']['id'] == RUN and artifact['workflow_run']['head_sha'] == source['head_sha']
        assert re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']) and 0 < artifact['size_in_bytes'] < MAX_BYTES
        result['original_artifact'] = artifact
        with tempfile.TemporaryDirectory(prefix='native-dual-cli-') as temporary:
            folder = Path(temporary)
            assert shutil.disk_usage(folder).free > artifact['size_in_bytes'] * 4 + 2 * 1024**3, 'insufficient space for bounded native CLI proof'
            zipped = folder/'original.zip'
            _, result['zip_download'] = BENCH.observed(download, artifact['id'], zipped)
            assert 'sha256:'+BENCH.EXPORT.checksum(zipped) == artifact['digest'], 'live ZIP checksum differs'
            result['verified_zip_sha256'] = artifact['digest']
            required = {'codex-x86_64.docker.tar.gz': MAX_BYTES, 'codex-x86_64.sha256': 1024,
                        'codex-x86_64.json': 4*1024**2, 'previous/codex-x86_64.json': 4*1024**2}
            with zipfile.ZipFile(zipped) as archive:
                for name, bound in required.items():
                    members = [member for member in archive.infolist() if member.filename == name]
                    assert len(members) == 1, ('missing/duplicate exact artifact member', name)
                    member = members[0]; mode = member.external_attr >> 16
                    assert not member.is_dir() and not stat.S_ISLNK(mode) and 0 < member.file_size <= bound
                    assert '..' not in PurePosixPath(name).parts and not PurePosixPath(name).is_absolute()
                    target = folder/name; target.parent.mkdir(exist_ok=True)
                    with archive.open(member) as original, target.open('xb') as output: shutil.copyfileobj(original, output, 1024*1024)
            original = folder/'codex-x86_64.docker.tar.gz'
            digest = BENCH.checksum_record(folder/'codex-x86_64.sha256', original.name)
            _, result['input_checksum'] = BENCH.observed(BENCH.verified_checksum, original, digest)
            records = [json.loads((folder/name).read_text()) for name in ('codex-x86_64.json', 'previous/codex-x86_64.json')]
            assert records[0]['source_revision'] == source['head_sha'] and records[1]['source_revision'] == '93c52c7b48a788dabba821c3b88b3e2a66143d68'
            assert records[0]['source_revision'] != records[1]['source_revision']
            images, result['input_identity_verification'] = BENCH.observed(BENCH.identities, original, records, PROVIDER, ARCHITECTURE)
            tags = ['localhost:5000/symphony-codex:candidate', 'localhost:5000/symphony-codex:previous']
            assert len(images) == 2 and {item['tag'] for item in images} == set(tags)
            with tarfile.open(original, 'r:gz') as saved:
                member = saved.getmember('manifest.json')
                assert 0 < member.size < 16 * 1024**2
                manifest = json.load(saved.extractfile(member))
                assert len(manifest) == 2 and {tag for entry in manifest for tag in entry['RepoTags']} == set(tags), 'foreign archive tag'
            identifiers = {item['image_id'] for item in images}
            assert len(identifiers) == 2
            for reference in [*tags, *identifiers]: assert absent(reference), 'refusing pre-existing image reference'
            acquired.update(identifiers)
            result['original_records'] = records
            result['verified_original_images'] = images
            result['docker_load'] = BENCH.load(original, list(identifiers))
            output, checksum = folder/'cli-output.tar.gz', folder/'cli-output.sha256'
            command = [sys.executable, str(ROOT/'scripts/release/export-image.py'), '--image', tags[0], '--image', tags[1],
                       '--output', str(output), '--checksum-output', str(checksum)]
            started = time.monotonic()
            exported = subprocess.run(command, text=True, capture_output=True, timeout=1200)
            result['actual_cli'] = {'arguments': command, 'wall_seconds': time.monotonic()-started,
                                    'returncode': exported.returncode, 'stderr': exported.stderr}
            assert exported.returncode == 0, exported.stderr
            metrics = json.loads(exported.stdout); result['export_metrics'] = metrics
            assert {item['image_id'] for item in metrics['images']} == identifiers, 'CLI metrics omit an original source'
            output_digest = BENCH.checksum_record(checksum, output.name)
            BENCH.verified_checksum(output, output_digest)
            exported_images = BENCH.identities(output, records, PROVIDER, ARCHITECTURE)
            assert len(exported_images) == 2 and {item['image_id'] for item in exported_images} == identifiers
            assert {item['tag'] for item in exported_images} == set(tags)
            result['output_sha256'] = output_digest; result['output_bytes'] = output.stat().st_size
            result['verified_exported_images'] = exported_images
            for identifier in identifiers:
                subprocess.run(['docker', 'image', 'rm', '--force', identifier], check=True, timeout=60)
            for reference in [*tags, *identifiers]:
                assert absent(reference), 'original image remains before actual output replay'
            result['output_preload_absence_verified'] = True
            result['output_docker_load'] = BENCH.load(output, list(identifiers))
            for selected in images:
                actual = BENCH.inspect(selected['tag'])
                assert actual['Id'] == selected['image_id'], 'output load changed original tag/config identity'
                assert actual['Config']['Labels']['org.opencontainers.image.revision'] == selected['record']['source_revision']
            result['output_loaded_tags_and_configs_verified'] = True
            result['passed'] = True
        result['owned_temporary_payloads_deleted'] = True
    except BaseException as error:
        result['failure'] = str(error)
        raise
    finally:
        errors = []
        for identifier in acquired:
            try:
                if not absent(identifier):
                    value = BENCH.inspect(identifier)
                    assert value['Id'] == identifier, 'cleanup config identity differs'
                    subprocess.run(['docker', 'image', 'rm', '--force', identifier], check=True, timeout=60)
                    assert absent(identifier), 'owned diagnostic image remains'
            except Exception as error: errors.append(str(error))
        result['cleanup'] = {'owned_config_ids': sorted(acquired), 'errors': errors}
        if errors: result['passed'] = False
        (evidence/'actual-dual-cli-proof.json').write_text(json.dumps(result, indent=2)+'\n')
        if errors: raise AssertionError(errors)


if __name__ == '__main__': main()
