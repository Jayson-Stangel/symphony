#!/usr/bin/env python3
"""Nonpublishing, build-free benchmark of verified candidate image archives."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time
import uuid
from urllib.error import URLError
from urllib.request import urlopen

SPEC = importlib.util.spec_from_file_location('image_export', Path(__file__).with_name('export-image.py'))
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def observed(operation, *arguments):
    start, cpu = time.monotonic(), time.process_time()
    value = operation(*arguments)
    return value, {'wall_seconds': time.monotonic() - start,
                   'cpu_seconds': time.process_time() - cpu}


def inspect(image):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True, timeout=30))[0]


def identities(archive, records, provider, architecture):
    expected_arch = 'amd64' if architecture == 'x86_64' else 'arm64'
    results = []
    with tarfile.open(archive, 'r:gz') as bundle:
        manifest = json.load(bundle.extractfile('manifest.json'))
        if not isinstance(manifest, list) or not manifest:
            raise ValueError('missing candidate image manifest')
        for image in manifest:
            member = bundle.getmember(image['Config'])
            if member.size > 16 * 1024 * 1024:
                raise ValueError('oversized image config')
            raw = bundle.extractfile(member).read()
            config = json.loads(raw)
            labels = config['config'].get('Labels') or {}
            matches = [record for record in records if record.get('source_revision') == labels.get('org.opencontainers.image.revision')]
            if len(matches) != 1:
                raise ValueError('archive source has no unique original qualification record')
            record = matches[0]
            if (record.get('provider') != provider or record.get('architecture') != architecture or
                    record.get('platform') != 'linux/' + expected_arch or
                    config.get('architecture') != expected_arch or
                    labels.get('com.symphony.agent') != provider or
                    labels.get('com.symphony.architecture') != expected_arch or
                    not re.fullmatch('[0-9a-f]{40}', record.get('source_revision', '')) or
                    not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', record.get('image', '')) or
                    not image.get('RepoTags')):
                raise ValueError('archive differs from original native-qualified record')
            results.append({'image_id': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                            'tag': image['RepoTags'][0], 'record': record})
    if {value['record']['source_revision'] for value in results} != {record['source_revision'] for record in records}:
        raise ValueError('archive does not contain every qualified source')
    return results


def load(archive, image_ids):
    start = time.monotonic()
    with open('/dev/null', 'wb') as log:
        metrics = EXPORT.pipeline(['gzip', '-dc', str(archive)], ['docker', 'load'], log)
    metrics['decompression'] = metrics.pop('export')
    metrics['docker_load'] = metrics.pop('compression')
    for image_id in image_ids:
        if inspect(image_id)['Id'] != image_id:
            raise ValueError('Docker loaded a different image config')
    metrics['total_wall_seconds'] = time.monotonic() - start
    return metrics


@contextmanager
def digest_registry():
    """Use the existing candidate route; never contact a publication registry."""
    token = uuid.uuid4().hex
    name = 'symphony-transfer-digest-' + token
    container = None
    try:
        received = subprocess.check_output(['docker', 'run', '-d', '--name', name,
            '--label', 'com.symphony.transfer-digest=' + token,
            '-p', '127.0.0.1:5000:5000', 'registry:3'], text=True, timeout=120).strip()
        if not re.fullmatch('[a-f0-9]{64}', received):
            raise ValueError('isolated registry has no exact owned container identity')
        container = received
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen('http://127.0.0.1:5000/v2/', timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError('owned test registry did not become ready within 30 seconds')
            time.sleep(0.1)
        yield
    finally:
        if container is None:
            # A CLI timeout or lost/invalid stdout does not prove the daemon
            # failed to create the resource. Recover only this exact name AND
            # unpredictable ownership token; a failed list is never absence.
            observed = subprocess.check_output(['docker', 'ps', '-a', '--no-trunc',
                '--filter', 'label=com.symphony.transfer-digest=' + token,
                '--format', '{{.ID}} {{.Names}}'], text=True, timeout=30)
            matches = [line.split()[0] for line in observed.splitlines()
                       if len(line.split()) == 2 and line.split()[1] == name]
            if len(matches) > 1 or any(not re.fullmatch('[a-f0-9]{64}', item) for item in matches):
                raise ValueError('ambiguous owned registry startup identity')
            container = matches[0] if matches else None
        if container is not None:
            owner = subprocess.check_output(['docker', 'inspect', '--format',
                '{{index .Config.Labels "com.symphony.transfer-digest"}}', container], text=True, timeout=30).strip()
            actual_name = subprocess.check_output(['docker', 'inspect', '--format', '{{.Name}}', container], text=True, timeout=30).strip()
            if owner != token or actual_name != '/' + name:
                raise ValueError('refusing to remove foreign registry container')
            subprocess.run(['docker', 'rm', '-f', container], check=True, timeout=30)
            remaining = subprocess.check_output(['docker', 'ps', '-a', '--no-trunc',
                '--filter', 'id=' + container, '--format', '{{.ID}}'], text=True, timeout=30).strip()
            if remaining:
                raise ValueError('owned registry remains after cleanup')


def prove_digests(images, provider):
    results = []
    repository = 'localhost:5000/symphony-' + provider
    for image in images:
        expected = image['record']['image']
        if (not re.fullmatch(re.escape(repository) + r'@sha256:[a-f0-9]{64}', expected) or
                image['tag'] not in (repository + ':candidate', repository + ':previous')):
            raise ValueError('digest proof permits only the original isolated local registry route')
        subprocess.run(['docker', 'push', image['tag']], check=True, timeout=600)
        actual = inspect(image['tag']).get('RepoDigests', [])
        if expected not in actual:
            raise ValueError('transport changed the original distribution digest; refusing relabeling')
        subprocess.run(['docker', 'pull', expected], check=True, timeout=600)
        config = inspect(expected)
        if (config['Id'] != image['image_id'] or
                config['Config']['Labels']['org.opencontainers.image.revision'] != image['record']['source_revision']):
            raise ValueError('original digest resolved to a different source/config')
        results.append({'image': expected, 'observed_repo_digests': actual, 'image_id': config['Id']})
    return results


def checksum_record(path, archive_name):
    fields = path.read_text().strip().split()
    if (len(fields) != 2 or not re.fullmatch('[0-9a-f]{64}', fields[0]) or
            fields[1] not in (archive_name, './' + archive_name)):
        raise ValueError('candidate archive checksum record differs')
    return fields[0]


def verified_checksum(archive, expected):
    if EXPORT.checksum(archive) != expected:
        raise ValueError('candidate archive checksum mismatch before Docker load')


def corruption_probe(archive, expected):
    """Change one actual payload byte, prove refusal, restore before any load."""
    with archive.open('r+b') as stream:
        original = stream.read(1)
        if not original:
            raise ValueError('empty archive')
        try:
            stream.seek(0)
            stream.write(bytes([original[0] ^ 1]))
            stream.flush()
            try:
                verified_checksum(archive, expected)
            except ValueError:
                pass
            else:
                raise ValueError('corrupt actual payload was accepted')
        finally:
            stream.seek(0)
            stream.write(original)
            stream.flush()
    verified_checksum(archive, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('benchmark', 'replay', 'timing'))
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=('codex', 'claude'), required=True)
    parser.add_argument('--architecture', choices=('x86_64', 'arm64'), required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    archives = sorted(args.input.rglob('*.docker.tar.gz'))
    if not archives:
        parser.error('no actual image archives available')
    results = []
    for index, archive in enumerate(archives):
        prefix = archive.name.removesuffix('.docker.tar.gz')
        record_files = sorted(archive.parent.rglob(prefix + '.json'))
        records = [json.loads(path.read_text()) for path in record_files]
        if not records:
            raise ValueError('missing original qualification records')
        digest = checksum_record(archive.with_name(prefix + '.sha256'), archive.name)
        _, input_checksum = observed(verified_checksum, archive, digest)
        if index == 0 and not any(record.get('source_revision') == args.source_revision for record in records):
            raise ValueError('current record differs from selected run source')
        corruption = None
        if args.mode == 'replay':
            start = time.monotonic()
            corruption_probe(archive, digest)
            corruption = {'refused_before_load': True, 'restored_original_sha256': digest,
                          'wall_seconds': time.monotonic() - start}
        images, identity_verification = observed(identities, archive, records, args.provider, args.architecture)
        image_ids = [value['image_id'] for value in images]
        tags = [value['tag'] for value in images]
        result = {'original_records': records, 'input_archive': str(archive),
                  'input_bytes': archive.stat().st_size, 'input_sha256': digest, 'image_ids': image_ids,
                  'actual_corruption_probe': corruption,
                  'input_checksum': input_checksum, 'input_identity_verification': identity_verification}
        if args.mode == 'timing':
            # Measure an existing original gzip payload, never recreate the
            # deleted benchmark baseline or imply identical compressed bytes.
            _, result['archive_verification'] = observed(EXPORT.verify_tar, archive, image_ids)
            result['measurement_scope'] = 'retained original candidate gzip payload; no export/compression/load/runtime'
            result['status'] = 'complete'
            results.append(result)
            continue
        result['input_load'] = load(archive, image_ids)
        result['status'] = 'loaded; remaining qualification incomplete'
        results.append(result)
        (args.output / 'transfer-results.txt').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
        if args.mode == 'benchmark':
            # Compare actual Docker export pipelines on the same immutable image;
            # baseline is the former gzip default, including its former headers.
            baseline = args.output / ('baseline-' + str(index) + '.tar.gz')
            with baseline.open('wb') as stream:
                result['baseline'] = EXPORT.pipeline(['docker', 'save', *tags], ['gzip'], stream)
            result['baseline']['bytes'] = baseline.stat().st_size
            result['baseline']['sha256'], result['baseline']['checksum'] = observed(EXPORT.checksum, baseline)
            _, result['baseline']['archive_verification'] = observed(EXPORT.verify_tar, baseline, image_ids)
            result['baseline']['load'] = load(baseline, image_ids)
            baseline.unlink()
            target = args.output / str(index)
            target.mkdir()
            output = target / archive.name
            result['improved'] = EXPORT.export(tags, output, target / (prefix + '.sha256'), legacy_diagnostic=True)
            if {value['image_id'] for value in result['improved']['images']} != set(image_ids):
                raise ValueError('improved export changed image config identity')
            for record_file in record_files:
                destination = target / record_file.relative_to(archive.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(record_file.read_bytes())
        else:
            # Startup and browser checks execute real loaded artifacts, with no
            # credentials, host mounts or model calls. The candidate registry route
            # below proves original distribution identities without publication.
            with digest_registry():
                result['original_distribution_digest_proof'] = prove_digests(images, args.provider)
                for qualified in result['original_distribution_digest_proof']:
                    image = qualified['image']
                    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'git', image, '--version'], check=True, timeout=30)
                    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'node', image, '/opt/symphony/browser/smoke.cjs'], check=True, timeout=90)
                    subprocess.run(['bash', str(Path(__file__).with_name('runner-startup.sh')), image, args.provider], check=True, timeout=180)
            result['actual_runtime_checks'] = 'git/browser/empty-tracker startup/graceful shutdown passed'
        result['status'] = 'complete'
    (args.output / 'transfer-results.txt').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
