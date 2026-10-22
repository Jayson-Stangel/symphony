#!/usr/bin/env python3
"""Export qualified images in one shared-layer archive without changing its source or registry identity.

Build-time only. Export and compression stream concurrently; process CPU and
overlapping wall times are recorded honestly. No uncompressed tar is staged.
Existing outputs are never replaced. Existing Docker load consumers remain usable.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time


def pipeline(export_command, compression_command, output):
    """Observe both real child processes; wall costs overlap and include backpressure."""
    start = time.monotonic()
    metrics = {}
    children = {}
    try:
        source = subprocess.Popen(export_command, stdout=subprocess.PIPE)
        children[source.pid] = ('export', source)
        compressor_process = subprocess.Popen(compression_command, stdin=source.stdout, stdout=output)
        source.stdout.close()
        children[compressor_process.pid] = ('compression', compressor_process)
        while children:
            if time.monotonic() - start > 1200:
                raise TimeoutError('image export/compression exceeded 20 minutes')
            for pid, (name, process) in list(children.items()):
                waited, status, usage = os.wait4(pid, os.WNOHANG)
                if not waited:
                    continue
                process.returncode = os.waitstatus_to_exitcode(status)
                del children[pid]
                metrics[name] = {'wall_seconds': time.monotonic() - start,
                                 'cpu_seconds': usage.ru_utime + usage.ru_stime,
                                 'exit_code': process.returncode}
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, process.args)
            if children:
                time.sleep(0.02)
        metrics['pipeline_wall_seconds'] = time.monotonic() - start
        metrics['wall_time_definition'] = 'overlapped processes; export includes compression pipe backpressure'
        return metrics
    finally:
        for pid, (_, process) in children.items():
            process.kill()
            process.wait()
        if 'source' in locals() and source.stdout:
            source.stdout.close()


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def compressor():
    # Limit CI CPU contention; compression never determines image identity.
    pigz = shutil.which('pigz')
    if pigz:
        return [pigz, '-1', '-n', '-p', str(min(4, os.cpu_count() or 1)), '-c']
    return [shutil.which('gzip') or 'gzip', '-1', '-n', '-c']


def verify_tar(path, image_ids):
    expected = {identity.removeprefix('sha256:') for identity in ([image_ids] if isinstance(image_ids, str) else image_ids)}
    configs, manifest = {}, None
    uncompressed_bytes = 0
    with tarfile.open(path, mode='r|gz') as archive:
        for member in archive:
            uncompressed_bytes += ((member.size + 511) // 512 + 1) * 512
            if member.name == 'manifest.json':
                if member.size > 1024 * 1024:
                    raise ValueError('oversized image manifest')
                manifest = json.load(archive.extractfile(member))
            for digest in expected:
                if member.name in (digest + '.json', 'blobs/sha256/' + digest):
                    if member.size > 16 * 1024 * 1024:
                        raise ValueError('oversized image config')
                    configs[member.name] = archive.extractfile(member).read()
    if not isinstance(manifest, list) or not manifest:
        raise ValueError('export has no image manifest')
    actual = set()
    for item in manifest:
        name = item.get('Config')
        config = configs.get(name)
        if config is None:
            raise ValueError('export manifest selects a different image config')
        digest = hashlib.sha256(config).hexdigest()
        if name not in (digest + '.json', 'blobs/sha256/' + digest):
            raise ValueError('exported config differs from the qualified image')
        actual.add(digest)
    if actual != expected:
        raise ValueError('exported image set differs from qualified images')
    return uncompressed_bytes


def publish_files(pairs):
    """Publish without overwrite; on failure remove only links we still own."""
    created = []
    try:
        for source, destination in pairs:
            identity = source.stat()
            os.link(source, destination)
            created.append((destination, identity.st_dev, identity.st_ino))
    except BaseException:
        for destination, device, inode in reversed(created):
            try:
                observed = destination.lstat()
                if (observed.st_dev, observed.st_ino) == (device, inode):
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise


def export(image, output, checksum_output):
    # Keep transport telemetry outside the *.json runner-record namespace.
    metrics_output = output.with_name(output.name + '.metrics.txt')
    for path in (output, checksum_output, metrics_output):
        if path.exists() or path.is_symlink():
            raise ValueError('refusing existing output: ' + str(path))
    images = [image] if isinstance(image, str) else list(image)
    if not images:
        raise ValueError('at least one image is required')
    identities = []
    for reference in images:
        config = json.loads(subprocess.check_output(['docker', 'image', 'inspect', reference], text=True, timeout=30))[0]
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', config.get('Id', '')):
            raise ValueError('image has no immutable config identity')
        labels = config['Config'].get('Labels') or {}
        if (labels.get('com.symphony.agent') not in ('codex', 'claude') or
                not re.fullmatch(r'[0-9a-f]{40}', labels.get('org.opencontainers.image.revision', '')) or
                config.get('Architecture') not in ('arm64', 'amd64') or
                labels.get('com.symphony.architecture') != config['Architecture']):
            raise ValueError('missing or invalid production source/provider/platform identity')
        identities.append({'image_reference': reference, 'image_id': config['Id'],
                           'original_repo_digests': config.get('RepoDigests', []),
                           'source_revision': labels['org.opencontainers.image.revision'],
                           'provider': labels['com.symphony.agent'], 'architecture': config['Architecture']})
    metrics = {'images': identities, 'format': 'docker-save+gzip'}
    # Caller must provide an existing output directory. This also keeps staging
    # on the same filesystem for atomic, no-overwrite publication.
    with tempfile.TemporaryDirectory(prefix='.image-export-', dir=output.parent) as temporary:
        stage = Path(temporary)
        compressed = stage / 'image.tar.gz'
        command = compressor()
        metrics['compressor_command'] = command
        metrics['compressor_version'] = subprocess.run([command[0], '--version'], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True, timeout=10).stdout.splitlines()[0]
        with compressed.open('wb') as stream:
            metrics.update(pipeline(['docker', 'save', *images], command, stream))
        start, cpu = time.monotonic(), time.process_time()
        metrics['tar_member_bytes'] = verify_tar(compressed, [value['image_id'] for value in identities])
        metrics['archive_verification'] = {'wall_seconds': time.monotonic() - start,
                                           'cpu_seconds': time.process_time() - cpu}
        metrics['peak_payload_staging_bytes'] = compressed.stat().st_size
        start, cpu = time.monotonic(), time.process_time()
        digest = checksum(compressed)
        metrics['checksum'] = {'wall_seconds': time.monotonic() - start,
                               'cpu_seconds': time.process_time() - cpu}
        metrics['compressed_bytes'] = compressed.stat().st_size
        metrics['sha256'] = digest
        check = stage / 'checksum'
        check.write_text(digest + '  ' + output.name + '\n')
        record = stage / 'metrics'
        record.write_text(json.dumps(metrics, indent=2, sort_keys=True) + '\n')
        publish_files(((compressed, output), (check, checksum_output), (record, metrics_output)))
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checksum-output', type=Path, required=True)
    args = parser.parse_args()
    try:
        metrics = export(args.image, args.output, args.checksum_output)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, tarfile.TarError, EOFError) as error:
        parser.exit(1, 'image export: ' + str(error) + '\n')
    print(json.dumps(metrics, sort_keys=True))


if __name__ == '__main__':
    main()
