#!/usr/bin/env python3
"""Restore selected immutable runner graphs without Docker archive conversion."""
import hashlib
import json
from pathlib import Path
import re


def archive_path(folder, provider, architecture):
    archive = Path(folder) / f'{provider}-{architecture}.docker.tar.gz'
    sidecar = Path(folder) / f'{provider}-{architecture}.sha256'
    if not archive.is_file() or archive.is_symlink() or not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError('qualified archive/checksum missing or unsafe')
    fields = sidecar.read_text().strip().split()
    if len(fields) != 2 or not re.fullmatch('[a-f0-9]{64}', fields[0]) or fields[1] not in (archive.name, './' + archive.name):
        raise ValueError('qualified archive checksum identity differs')
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): digest.update(block)
    if digest.hexdigest() != fields[0]: raise ValueError('qualified archive checksum differs')
    return archive


def prepare_graphs(images, current, initial, destination, current_only=False, legacy=False, extract=None):
    """Verify every selected archive/graph before the first registry write."""
    if extract is None:
        from qualified_graph import extract_archive
        extract = extract_archive
    destination = Path(destination)
    if destination.exists(): raise ValueError('refusing existing graph preparation destination')
    destination.mkdir()
    entries, groups = {}, {}
    for role, manifest in ([('candidate', current)] if current_only else [('candidate', current), ('previous', initial)]):
        for provider in ('codex', 'claude'):
            components = manifest.get('original_components', manifest['runners'])
            record = dict(components[provider]['x86_64'], provider=provider, architecture='x86_64')
            if 'archive_tag' not in record:
                if manifest.get('schema_version') != 1:
                    raise ValueError('qualified archive role is missing')
                # Historical schema1 package metadata selected the CURRENT
                # candidate role. This transport-only view never rewrites its
                # original record or fabricates a qualification/graph.
                record['archive_tag'] = f'localhost:5000/symphony-{provider}:candidate'
            folder = Path(images)
            if role == 'previous':
                separate = folder / ('legacy' if legacy else 'previous')
                if separate.exists():
                    if separate.is_symlink() or not separate.is_dir(): raise ValueError('unsafe prior graph directory')
                    folder = separate
            archive = archive_path(folder, provider, 'x86_64')
            group = groups.setdefault(archive, {'directory': destination / str(len(groups)), 'records': []})
            if record not in group['records']: group['records'].append(record)
            entries[(role, provider)] = {'directory': group['directory'], 'record': record}
    for archive, group in groups.items():
        extract(archive, group['records'], group['directory'])
    return entries


def verify_runtime_image(value, record, reference):
    """Accept exact config IDs or typed original single-manifest IDs only."""
    labels = value.get('Config', {}).get('Labels') or {}
    architecture = 'amd64' if record['architecture'] == 'x86_64' else 'arm64'
    if value.get('Os') != 'linux' or value.get('Architecture') != architecture:
        raise ValueError('restored image platform differs')
    expected = {'org.opencontainers.image.revision': record['source_revision'], 'com.symphony.agent': record['provider'], 'com.symphony.architecture': architecture}
    if 'input_identity' in record: expected['com.symphony.inputs.sha256'] = record['input_identity']
    if 'protocol' in record: expected['com.symphony.protocol'] = str(record['protocol'])
    if any(labels.get(key) != value for key, value in expected.items()): raise ValueError('restored image source/input/provider/protocol differs')
    qualified = record['image'].rsplit('@', 1)[-1]
    if reference.rsplit('@', 1)[-1] != qualified or reference not in value.get('RepoDigests', []):
        raise ValueError('restored image qualified digest differs')
    config = record.get('image_config')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', config or ''): raise ValueError('qualified image config unavailable')
    descriptor = value.get('Descriptor') or {}
    classic = value.get('Id') == config
    typed = (value.get('Id') == descriptor.get('digest') == qualified and descriptor.get('mediaType') in (
        'application/vnd.docker.distribution.manifest.v2+json', 'application/vnd.oci.image.manifest.v1+json'))
    if not (classic or typed): raise ValueError('restored runtime identity differs from qualified graph')


def restore_component(run, selected, registry_state, destination=None, restore=None):
    if restore is None:
        from qualified_graph import restore_graphs
        restore = restore_graphs
    record = selected['record']
    reference = destination or record['image']
    if reference.rsplit('@', 1)[-1] != record['image'].rsplit('@', 1)[-1]:
        raise ValueError('distribution cannot change qualified image digest')
    restored = restore(selected['directory'], [record], registry_state,
                       destinations=None if destination is None else {record['image']: reference})
    reference = restored[record['image']]
    if reference.rsplit('@', 1)[-1] != record['image'].rsplit('@', 1)[-1]:
        raise ValueError('restoration changed qualified image digest')
    run(['docker', 'pull', reference])
    value = json.loads(run(['docker', 'image', 'inspect', reference]).stdout)[0]
    verify_runtime_image(value, record, reference)
    return value
