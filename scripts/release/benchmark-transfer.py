#!/usr/bin/env python3
"""Nonpublishing, build-free benchmark of verified candidate image archives."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time

SPEC = importlib.util.spec_from_file_location('image_export', Path(__file__).with_name('export-image.py'))
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


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
    parser.add_argument('mode', choices=('benchmark', 'replay'))
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
        check = archive.with_name(prefix + '.sha256').read_text().strip().split()
        if len(check) != 2 or check[1] != archive.name:
            raise ValueError('candidate archive checksum record differs')
        verified_checksum(archive, check[0])
        if index == 0 and not any(record.get('source_revision') == args.source_revision for record in records):
            raise ValueError('current record differs from selected run source')
        corruption = None
        if args.mode == 'replay':
            start = time.monotonic()
            corruption_probe(archive, check[0])
            corruption = {'refused_before_load': True, 'restored_original_sha256': check[0],
                          'wall_seconds': time.monotonic() - start}
        images = identities(archive, records, args.provider, args.architecture)
        image_ids = [value['image_id'] for value in images]
        tags = [value['tag'] for value in images]
        result = {'original_records': records, 'input_archive': str(archive),
                  'input_bytes': archive.stat().st_size, 'input_sha256': check[0], 'image_ids': image_ids,
                  'input_load': load(archive, image_ids), 'actual_corruption_probe': corruption}
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
            result['baseline']['sha256'] = EXPORT.checksum(baseline)
            EXPORT.verify_tar(baseline, image_ids)
            result['baseline']['load'] = load(baseline, image_ids)
            baseline.unlink()
            target = args.output / str(index)
            target.mkdir()
            output = target / archive.name
            result['improved'] = EXPORT.export(tags, output, target / (prefix + '.sha256'))
            if {value['image_id'] for value in result['improved']['images']} != set(image_ids):
                raise ValueError('improved export changed image config identity')
            for record_file in record_files:
                destination = target / record_file.relative_to(archive.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(record_file.read_bytes())
        else:
            # Startup and browser checks execute real loaded artifacts, with no
            # credentials, host mounts, model calls, or new registry service.
            for image_id in image_ids:
                subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'git', image_id, '--version'], check=True, timeout=30)
                subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'node', image_id, '/opt/symphony/browser/smoke.cjs'], check=True, timeout=90)
                subprocess.run(['bash', str(Path(__file__).with_name('runner-startup.sh')), image_id, args.provider], check=True, timeout=180)
            result['actual_runtime_checks'] = 'git/browser/empty-tracker startup/graceful shutdown passed'
        result['status'] = 'complete'
    (args.output / 'transfer-results.txt').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
