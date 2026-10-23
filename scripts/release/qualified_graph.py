#!/usr/bin/env python3
"""Preserve exact qualified registry bytes inside the existing image payload.

No build, compression conversion or qualification occurs here. Docker-save is
retained for historical tooling; graph-aware consumers restore these exact bytes
and pull the original digest instead of loading and repushing a different graph.
"""
import argparse
import gzip
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit, parse_qsl, urlencode

PREFIX = 'symphony-qualified-graphs/v1'
HEX = re.compile(r'sha256:[a-f0-9]{64}\Z')
REFERENCE = re.compile(r'(?:localhost|127\.0\.0\.1):([0-9]{1,5})/([a-z0-9]+(?:[._/-][a-z0-9]+)*)@(sha256:[a-f0-9]{64})\Z')
SINGLE = {'application/vnd.docker.distribution.manifest.v2+json', 'application/vnd.oci.image.manifest.v1+json'}
CONFIG = {'application/vnd.docker.container.image.v1+json', 'application/vnd.oci.image.config.v1+json'}
LAYER = {'application/vnd.docker.image.rootfs.diff.tar.gzip', 'application/vnd.oci.image.layer.v1.tar+gzip'}
MAX_METADATA = 1 << 20
MAX_BLOB = 8 << 30
MAX_TOTAL = 32 << 30
MAX_RAW = 16 << 30
MAX_MEMBERS = 16384


def require(value, message):
    if not value:
        raise ValueError('qualified graph: ' + message)


def strict(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def file_identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def regular(path):
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode) and path.resolve() == path.absolute(), 'nonregular or symlinked input')
    return value


def read(path, maximum):
    before = regular(path)
    require(before.st_size <= maximum, 'metadata bound exceeded')
    with path.open('rb') as stream:
        require(file_identity(os.fstat(stream.fileno())) == file_identity(before), 'metadata changed before open')
        raw = stream.read(maximum + 1)
    require(len(raw) == before.st_size and file_identity(regular(path)) == file_identity(before), 'metadata changed during read')
    return raw


def blob_path(root, identity):
    require(type(identity) is str and HEX.fullmatch(identity), 'invalid content digest')
    return Path(root) / 'blobs' / 'sha256' / identity[7:]


def check_blob(root, descriptor):
    require(type(descriptor.get('size')) is int and 0 < descriptor['size'] <= MAX_BLOB, 'invalid blob size')
    path = blob_path(root, descriptor.get('digest'))
    before = regular(path)
    require(before.st_size == descriptor['size'], 'blob size differs')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        require(file_identity(os.fstat(stream.fileno())) == file_identity(before), 'blob changed before open')
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    require('sha256:' + value.hexdigest() == descriptor['digest'], 'blob checksum differs')
    require(file_identity(regular(path)) == file_identity(before), 'blob changed while hashing')
    return path


def image_key(record):
    require(type(record) is dict and type(record.get('image')) is str, 'missing original image')
    require(type(record.get('image_config')) is str and HEX.fullmatch(record['image_config']), 'missing original config')
    return record['image']


def verify_config(raw, record):
    require(digest(raw) == record['image_config'], 'original config checksum differs')
    value = strict(raw)
    require(type(value) is dict and type(value.get('config')) is dict, 'invalid config object')
    labels = value['config'].get('Labels') or {}
    require(type(labels) is dict, 'invalid config labels')
    architecture = 'amd64' if record['architecture'] == 'x86_64' else record['architecture']
    require(architecture in ('amd64', 'arm64') and value.get('architecture') == architecture and value.get('os') == 'linux', 'original platform differs')
    require(record['provider'] in ('codex', 'claude') and type(record.get('source_revision')) is str and re.fullmatch('[a-f0-9]{40}', record['source_revision']), 'invalid original provider/source')
    expected = {'com.symphony.agent': record['provider'], 'com.symphony.architecture': architecture,
                'org.opencontainers.image.revision': record['source_revision']}
    if 'input_identity' in record:
        require(type(record['input_identity']) is str and re.fullmatch('[a-f0-9]{64}', record['input_identity']), 'invalid original inputs')
        expected['com.symphony.inputs.sha256'] = record['input_identity']
    if 'protocol' in record:
        require(record['protocol'] == 1, 'unsupported original protocol')
        expected['com.symphony.protocol'] = '1'
    require(all(labels.get(key) == expected_value for key, expected_value in expected.items()), 'original source/provider/input/protocol differs')
    return value


def validate_graphs(root, records, exact=True):
    """Offline full graph verification. Returns selected raw manifests and blobs."""
    root = Path(root)
    require(root.resolve() == root.absolute(), 'graph directory is symlinked')
    index = strict(read(root / 'index.json', MAX_METADATA))
    require(type(index) is dict and set(index) == {'schema_version', 'images'} and index['schema_version'] == 1 and type(index['images']) is list, 'invalid index')
    require(0 < len(index['images']) <= 16, 'image count bound exceeded')
    entries = {}
    for entry in index['images']:
        require(type(entry) is dict and set(entry) == {'image', 'archive_tag', 'image_config'}, 'invalid index entry')
        key = image_key(entry)
        require(key not in entries, 'duplicate graph image')
        entries[key] = entry
    if not exact:
        requested = {image_key(record) for record in records}
        require(len(requested) == len(records), 'repeated requested root')
        complete = list(records)
        for key, entry in entries.items():
            if key in requested:
                continue
            raw_manifest = read(blob_path(root, key.rsplit('@', 1)[-1]), MAX_METADATA)
            require(digest(raw_manifest) == key.rsplit('@', 1)[-1], 'unselected root checksum differs')
            other_manifest = strict(raw_manifest)
            other_raw = read(blob_path(root, entry['image_config']), MAX_METADATA)
            require(digest(other_raw) == entry['image_config'], 'unselected config checksum differs')
            other = strict(other_raw); labels = other.get('config', {}).get('Labels') or {}
            derived = dict(entry, provider=labels.get('com.symphony.agent'),
                           architecture='x86_64' if other.get('architecture') == 'amd64' else other.get('architecture'),
                           source_revision=labels.get('org.opencontainers.image.revision'))
            if 'com.symphony.inputs.sha256' in labels:
                derived['input_identity'] = labels['com.symphony.inputs.sha256']
            if 'com.symphony.protocol' in labels:
                require(labels['com.symphony.protocol'] == '1', 'unselected root protocol differs')
                derived['protocol'] = 1
            complete.append(derived)
        # Structural verification of every archived root grants no authority to
        # restore an unselected record: only requested keys are returned.
        all_graphs = validate_graphs(root, complete, exact=True)
        require(requested <= set(all_graphs), 'missing requested graph')
        return {key: all_graphs[key] for key in requested}
    selected = {}
    total = 0
    verified = {}
    for record in records:
        key = image_key(record)
        require(key not in selected and key in entries, 'missing or repeated qualified root')
        entry = entries[key]
        require(entry['image_config'] == record['image_config'] and entry['archive_tag'] == record['archive_tag'], 'original archive tag/config differs')
        identity = key.rsplit('@', 1)[-1]
        require(HEX.fullmatch(identity), 'image must be an immutable reference')
        manifest_raw = read(blob_path(root, identity), MAX_METADATA)
        require(digest(manifest_raw) == identity, 'original manifest checksum differs')
        manifest = strict(manifest_raw)
        require(type(manifest) is dict and manifest.get('schemaVersion') == 2 and manifest.get('mediaType') in SINGLE, 'not a qualified single image manifest')
        config = manifest.get('config', {})
        layers = manifest.get('layers')
        require(type(config) is dict and config.get('mediaType') in CONFIG and config.get('digest') == record['image_config'], 'manifest config differs')
        require(type(layers) is list and 0 < len(layers) <= 256, 'invalid layer count')
        for descriptor in [config] + layers:
            require(type(descriptor) is dict, 'invalid blob descriptor')
            identifier = descriptor.get('digest')
            if identifier in verified:
                require(verified[identifier]['size'] == descriptor.get('size'), 'shared blob size differs')
            else:
                check_blob(root, descriptor)
                total += descriptor['size']
                require(total <= MAX_TOTAL, 'graph size bound exceeded')
                verified[identifier] = descriptor
        config_value = verify_config(read(blob_path(root, config['digest']), MAX_METADATA), record)
        rootfs = config_value.get('rootfs', {})
        diffids = rootfs.get('diff_ids')
        require(rootfs.get('type') == 'layers' and type(diffids) is list and len(diffids) == len(layers), 'ordered DiffID membership differs')
        for layer, diffid in zip(layers, diffids):
            require(layer.get('mediaType') in LAYER and type(diffid) is str and HEX.fullmatch(diffid), 'unsupported compressed layer or DiffID')
            h = hashlib.sha256(); count = 0
            with gzip.open(blob_path(root, layer['digest']), 'rb') as stream:
                while block := stream.read(1 << 20):
                    count += len(block)
                    require(count <= MAX_RAW, 'raw layer bound exceeded')
                    h.update(block)
            require('sha256:' + h.hexdigest() == diffid, 'ordered raw layer checksum differs')
        selected[key] = {'record': record, 'manifest': manifest_raw, 'media_type': manifest['mediaType'],
                         'descriptors': [config] + layers,
                         'file_identities': {item['digest']: file_identity(regular(blob_path(root, item['digest']))) for item in [config] + layers}}
    require(not exact or set(selected) == set(entries), 'graph image set differs from qualified images')
    if exact:
        names = {'index.json'} | {'blobs/sha256/' + key[7:] for key in verified}
        names.update('blobs/sha256/' + key.rsplit('@', 1)[-1][7:] for key in selected)
        actual_names = {path.relative_to(root).as_posix() for path in root.rglob('*') if not path.is_dir()}
        require(names == actual_names, 'graph contains unreferenced or missing files')
    return selected


def owned_registry(state_path):
    state = strict(read(Path(state_path), 65536))
    token, identifier = state.get('owner', ''), state.get('container_id', '')
    port = state.get('host_port')
    require(re.fullmatch('[a-f0-9]{32}', token) and re.fullmatch('[a-f0-9]{64}', identifier), 'invalid registry receipt')
    require(state.get('name') == 'symphony-native-' + token and state.get('cleanup') == 'pending'
            and type(port) is int and 1 <= port <= 65535, 'registry receipt is not active')
    raw = subprocess.check_output(['docker', 'inspect', identifier], text=True, stderr=subprocess.PIPE, timeout=30)
    values = strict(raw)
    require(type(values) is list and len(values) == 1, 'ambiguous registry inspection')
    value = values[0]
    binding = [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]
    require(value.get('Id') == identifier and value.get('Name') == '/' + state['name']
            and (value.get('Config', {}).get('Labels') or {}).get('com.symphony.native-test-owner') == token
            and value.get('State', {}).get('Running') is True, 'registry ownership differs')
    require(value.get('HostConfig', {}).get('NetworkMode') != 'host'
            and value.get('HostConfig', {}).get('PortBindings') == {'5000/tcp': binding}
            and value.get('NetworkSettings', {}).get('Ports') == {'5000/tcp': binding}, 'registry is not exclusively loopback bound')
    return state


class Registry:
    def __init__(self, port, repo):
        require(re.fullmatch('[a-z0-9]+(?:[._/-][a-z0-9]+)*', repo), 'invalid repository')
        self.port, self.repo = port, repo

    def request(self, method, suffix, body=None, size=None, expected=(200,), maximum=MAX_METADATA, output=None, content_type=None):
        require(suffix.startswith('/'), 'invalid registry suffix')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        deadline = time.monotonic() + 1200
        headers = {}
        if size is not None:
            headers['Content-Length'] = str(size)
        if content_type:
            headers['Content-Type'] = content_type
        if suffix.startswith('/manifests/'):
            headers['Accept'] = ', '.join(sorted(SINGLE))
        try:
            connection.request(method, '/v2/' + self.repo + suffix, body=body, headers=headers)
            response = connection.getresponse()
            require(response.status in expected, 'unexpected registry status ' + str(response.status))
            pieces = []; h = hashlib.sha256(); count = 0
            while True:
                require(time.monotonic() < deadline, 'registry deadline exceeded')
                block = response.read(min(1 << 20, maximum - count + 1))
                if not block:
                    break
                count += len(block)
                require(count <= maximum, 'registry response bound exceeded')
                h.update(block)
                if output is not None:
                    output.write(block)
                else:
                    pieces.append(block)
            return response.status, response.headers, b''.join(pieces), count, 'sha256:' + h.hexdigest()
        finally:
            connection.close()

    def upload_location(self, location, identity):
        value = urlsplit(location)
        require(not value.fragment and not value.username and not value.password, 'invalid upload location')
        require((not value.scheme and not value.netloc) or (value.scheme == 'http' and value.netloc == f'127.0.0.1:{self.port}'), 'foreign upload location')
        prefix = '/v2/' + self.repo + '/blobs/uploads/'
        require(value.path.startswith(prefix) and re.fullmatch('[a-zA-Z0-9-]+', value.path[len(prefix):]), 'upload path outside repository')
        query = parse_qsl(value.query, keep_blank_values=True, strict_parsing=True)
        require(len(query) <= 1 and all(key == '_state' and len(item) <= 8192 for key, item in query), 'invalid upload query')
        return value.path[len('/v2/' + self.repo):] + '?' + urlencode(query + [('digest', identity)])


def registry_for(reference, state):
    match = REFERENCE.fullmatch(reference)
    require(match and int(match[1]) == state['host_port'], 'reference is not the owned registry')
    return Registry(state['host_port'], match[2]), match[3]


def qualified_config(reference, registry_state, inspection=None):
    """Resolve actual config from the exact single manifest, never infer from Id."""
    state = owned_registry(registry_state)
    if inspection is None:
        values = strict(subprocess.check_output(['docker', 'image', 'inspect', reference], text=True, timeout=30))
        require(type(values) is list and len(values) == 1, 'ambiguous image inspection')
        inspection = values[0]
    if REFERENCE.fullmatch(reference):
        original = reference
    else:
        matches = [value for value in inspection.get('RepoDigests', []) if REFERENCE.fullmatch(value) and int(REFERENCE.fullmatch(value)[1]) == state['host_port']]
        require(len(matches) == 1, 'image has no unique owned qualified RepoDigest')
        original = matches[0]
    client, identity = registry_for(original, state)
    _, headers, raw, _, actual = client.request('GET', '/manifests/' + identity)
    require(actual == identity and headers.get('Docker-Content-Digest') == identity, 'qualified manifest checksum differs')
    value = strict(raw)
    require(type(value) is dict and value.get('schemaVersion') == 2 and value.get('mediaType') in SINGLE, 'qualified root is not a single image')
    config = value.get('config', {})
    require(config.get('mediaType') in CONFIG and type(config.get('size')) is int and 0 < config['size'] <= MAX_METADATA and HEX.fullmatch(config.get('digest', '')), 'invalid qualified config descriptor')
    _, _, config_raw, count, actual = client.request('GET', '/blobs/' + config['digest'])
    require(count == config['size'] and actual == config['digest'], 'qualified config checksum differs')
    # Classic stores expose config ID; manifest stores must expose this exact
    # single-image root AND its exact RepoDigest, never a transport-only root.
    classic = inspection.get('Id') == config['digest']
    descriptor = inspection.get('Descriptor') or {}
    manifest_store = (inspection.get('Id') == identity and descriptor.get('digest') == identity
                      and descriptor.get('mediaType') == value['mediaType'] and original in inspection.get('RepoDigests', []))
    require(classic or manifest_store, 'inspect identity is neither qualified config nor exact qualified manifest')
    return original, config['digest'], strict(config_raw)


def capture_graphs(records, destination, registry_state):
    """Capture exact manifest/config/compressed bytes before registry cleanup."""
    state = owned_registry(registry_state)
    destination = Path(destination)
    destination.mkdir(mode=0o700)  # exclusive; incomplete staging is never published
    (destination / 'blobs' / 'sha256').mkdir(parents=True)
    index = {'schema_version': 1, 'images': []}
    seen = set(); total = 0
    for record in records:
        reference = image_key(record)
        require(reference not in seen, 'duplicate qualified image')
        seen.add(reference)
        match = REFERENCE.fullmatch(reference)
        require(match, 'qualified original reference is not loopback')
        client, identity = registry_for(f'localhost:{state["host_port"]}/{match[2]}@{match[3]}', state)
        _, headers, raw, count, actual = client.request('GET', '/manifests/' + identity)
        require(actual == identity and headers.get('Docker-Content-Digest') == identity, 'registry manifest checksum differs')
        value = strict(raw)
        require(type(value) is dict and value.get('schemaVersion') == 2 and value.get('mediaType') in SINGLE, 'registry root is not a single image')
        config, layers = value.get('config', {}), value.get('layers')
        require(config.get('digest') == record['image_config'] and type(layers) is list and 0 < len(layers) <= 256, 'registry config/layer membership differs')
        blob_path(destination, identity).write_bytes(raw)
        for descriptor in [config] + layers:
            path = blob_path(destination, descriptor.get('digest'))
            require(type(descriptor.get('size')) is int and 0 < descriptor['size'] <= MAX_BLOB, 'invalid registry blob size')
            if path.exists():
                check_blob(destination, descriptor)
                continue
            total += descriptor['size']
            require(total <= MAX_TOTAL, 'captured graph size bound exceeded')
            with path.open('xb') as target:
                _, _, _, count, actual = client.request('GET', '/blobs/' + descriptor['digest'], maximum=descriptor['size'], output=target)
            require(count == descriptor['size'] and actual == descriptor['digest'], 'registry blob checksum differs')
        require(type(config) is dict and config.get('mediaType') in CONFIG and config.get('size', MAX_METADATA + 1) <= MAX_METADATA, 'invalid captured config')
        index['images'].append({key: record[key] for key in ('image', 'archive_tag', 'image_config')})
    (destination / 'index.json').write_text(json.dumps(index, sort_keys=True, separators=(',', ':')) + '\n')
    validate_graphs(destination, records)
    return index


def verify_archive(archive, records, graphdir):
    """Check emitted archive bytes against an already fully validated capture.

    This avoids staging a second full graph just to verify the transport.
    """
    validate_graphs(graphdir, records)
    expected = {PREFIX + '/' + path.relative_to(graphdir).as_posix(): path
                for path in Path(graphdir).rglob('*') if path.is_file()}
    seen = set(); total = 0
    with gzip.open(archive, 'rb') as compressed, tarfile.open(fileobj=compressed, mode='r|') as bundle:
        for number, member in enumerate(bundle):
            require(number < MAX_MEMBERS, 'archive member bound exceeded')
            require(member.name in expected and member.name not in seen and member.isfile(), 'unexpected/duplicate graph archive member')
            path = expected[member.name]
            require(member.size == path.stat().st_size, 'graph archive member size differs')
            h = hashlib.sha256(); original = hashlib.sha256()
            with bundle.extractfile(member) as stream, path.open('rb') as source:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    h.update(block)
                for block in iter(lambda: source.read(1 << 20), b''):
                    original.update(block)
            require(h.digest() == original.digest(), 'graph archive member checksum differs')
            total += member.size; require(total <= MAX_TOTAL, 'graph archive bound exceeded')
            seen.add(member.name)
        tail = bundle.fileobj.read(MAX_METADATA + 1)
        require(len(tail) <= MAX_METADATA and not any(tail), 'unexpected graph archive trailer')
    require(seen == set(expected), 'graph archive incomplete')
    return total


def extract_archive(archive, records, destination):
    """Extract only the reserved graph namespace, never arbitrary tar paths."""
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    (destination / 'blobs' / 'sha256').mkdir(parents=True)
    seen = set(); total = 0
    with gzip.open(archive, 'rb') as compressed, tarfile.open(fileobj=compressed, mode='r|') as bundle:
        for number, member in enumerate(bundle):
            require(number < MAX_MEMBERS, 'archive member bound exceeded')
            name = member.name.removeprefix('./')
            require(name.startswith(PREFIX + '/'), 'unexpected member outside graph-only namespace')
            relative = name[len(PREFIX) + 1:]
            require(relative == 'index.json' or re.fullmatch('blobs/sha256/[a-f0-9]{64}', relative), 'unknown reserved graph member')
            require(member.isfile() and name not in seen and 0 < member.size <= (MAX_METADATA if relative == 'index.json' else MAX_BLOB), 'invalid or duplicate graph member')
            seen.add(name); total += member.size
            require(total <= MAX_TOTAL, 'graph archive bound exceeded')
            with (destination / relative).open('xb') as target:
                shutil.copyfileobj(bundle.extractfile(member), target, 1 << 20)
        tail = bundle.fileobj.read(MAX_METADATA + 1)
        require(len(tail) <= MAX_METADATA and not any(tail), 'unexpected graph archive trailer')
    require(seen, 'archive has no exact qualified graph; legacy reconstruction is not authorized')
    validate_graphs(destination, records, exact=False)
    return destination


class UploadReader:
    def __init__(self, stream, expected_size):
        self.stream, self.expected_size = stream, expected_size
        self.hash = hashlib.sha256(); self.count = 0
        self.deadline = time.monotonic() + 1200

    def read(self, size=-1):
        require(time.monotonic() < self.deadline, 'upload deadline exceeded')
        block = self.stream.read(min(1 << 20, size) if size >= 0 else 1 << 20)
        self.count += len(block)
        require(self.count <= self.expected_size, 'upload grew after validation')
        self.hash.update(block)
        return block


def restore_graphs(graphdir, records, registry_state, destinations=None):
    """Preflight all selected graphs, copy exact bytes, return pull references."""
    graphs = validate_graphs(graphdir, records, exact=False)
    state = owned_registry(registry_state)
    targets = {}
    for original, graph in graphs.items():
        original_match = REFERENCE.fullmatch(original)
        require(original_match, 'original reference is outside loopback qualification namespace')
        reference = (destinations or {}).get(original, f'localhost:{state["host_port"]}/{original_match[2]}@{original_match[3]}')
        client, identity = registry_for(reference, state)
        require(identity == original_match[3], 'destination changes original digest')
        targets[original] = (reference, client, identity)
    # Recheck every immutable staging identity before even the first HTTP call.
    for graph in graphs.values():
        for descriptor in graph['descriptors']:
            path = blob_path(graphdir, descriptor['digest'])
            require(file_identity(regular(path)) == graph['file_identities'][descriptor['digest']], 'prevalidated graph changed')
            check_blob(graphdir, descriptor)
    # All source graphs and destinations pass before the first HTTP write.
    for original, graph in graphs.items():
        reference, client, identity = targets[original]
        for descriptor in graph['descriptors']:
            path = blob_path(graphdir, descriptor['digest'])
            # Readback verifies even a preexisting blob, before publishing root.
            status, headers, _, _, _ = client.request('HEAD', '/blobs/' + descriptor['digest'], expected=(200, 404), maximum=0)
            if status == 404:
                _, headers, _, _, _ = client.request('POST', '/blobs/uploads/', size=0, expected=(202,))
                suffix = client.upload_location(headers.get('Location', ''), descriptor['digest'])
                with path.open('rb') as stream:
                    require(file_identity(os.fstat(stream.fileno())) == graph['file_identities'][descriptor['digest']], 'blob changed before upload')
                    reader = UploadReader(stream, descriptor['size'])
                    _, headers, _, _, _ = client.request('PUT', suffix, body=reader, size=descriptor['size'], content_type='application/octet-stream', expected=(201,))
                require(reader.count == descriptor['size'] and 'sha256:' + reader.hash.hexdigest() == descriptor['digest'], 'upload bytes changed')
                require(file_identity(regular(path)) == graph['file_identities'][descriptor['digest']], 'blob changed during upload')
                require(headers.get('Docker-Content-Digest') == descriptor['digest'], 'uploaded blob digest differs')
            _, _, _, count, actual = client.request('GET', '/blobs/' + descriptor['digest'], maximum=descriptor['size'], output=_Discard())
            require(count == descriptor['size'] and actual == descriptor['digest'], 'registry blob readback differs')
        _, headers, _, _, _ = client.request('PUT', '/manifests/' + identity, body=graph['manifest'], size=len(graph['manifest']), content_type=graph['media_type'], expected=(201,))
        require(headers.get('Docker-Content-Digest') == identity, 'restored root digest differs')
        _, headers, raw, _, actual = client.request('GET', '/manifests/' + identity)
        require(actual == identity and raw == graph['manifest'] and headers.get('Docker-Content-Digest') == identity, 'restored raw root differs')
    return {original: target[0] for original, target in targets.items()}


class _Discard:
    def write(self, block):
        return len(block)


def write_archive(graphdir):
    """Stream graph-only tar; shared compressed blobs occur exactly once."""
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as target:
        for path in sorted(Path(graphdir).rglob('*')):
            if path.is_dir():
                continue
            require(path.is_file() and not path.is_symlink(), 'nonregular captured graph')
            relative = path.relative_to(graphdir).as_posix()
            require(relative == 'index.json' or re.fullmatch('blobs/sha256/[a-f0-9]{64}', relative), 'unknown captured graph member')
            info = tarfile.TarInfo(PREFIX + '/' + relative)
            info.size = path.stat().st_size; info.mode = 0o600
            with path.open('rb') as stream:
                target.addfile(info, stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('archive',))
    parser.add_argument('--graph', type=Path, required=True)
    args = parser.parse_args()
    write_archive(args.graph)


if __name__ == '__main__':
    main()
