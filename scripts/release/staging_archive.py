"""Shared operator archive staging, with exact original GitHub service identity.

Callers validate their payload before this transport-only effect. No retry,
qualification, signing, publication, or release-completion authority is added.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

from component_provenance import api, REPOSITORY
from signing_payload import MAX_BYTES, require


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def stage_archive(archive_path, revision, invoke=subprocess.run, fetch=api):
    require(re.fullmatch(r'[a-f0-9]{40}', revision or '') and archive_path.is_file()
            and not archive_path.is_symlink(), 'invalid staging source/archive')
    require(archive_path.stat().st_size <= MAX_BYTES, 'local upload archive oversized')
    tag = 'local-signing-' + revision + '-' + uuid.uuid4().hex
    # Private drafts need not resolve through the public by-tag endpoint. Keep
    # the original service identity from the creation response; never retry a
    # failed/uncertain POST or discover a replacement release by its tag.
    created = invoke(['gh', 'api', '--method', 'POST', 'repos/' + REPOSITORY + '/releases', '--input', '-'],
        input=json.dumps({'tag_name': tag, 'target_commitish': revision, 'draft': True,
                         'name': 'Local signing staging (not a production release)',
                         'body': 'Private transport only. Do not publish this draft or move its target.'}),
        text=True, capture_output=True, check=True)
    creation = json.loads(created.stdout)
    require(isinstance(creation, dict) and type(creation.get('id')) is int and creation['id'] > 0,
            'invalid original staging release ID')
    release_id = creation['id']
    def original_release():
        value = fetch('releases/' + str(release_id))
        require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] == release_id and value['draft'] is True
                and value['tag_name'] == tag and value['target_commitish'] == revision and isinstance(value['assets'], list),
                'original local staging release differs')
        return value
    release = original_release()
    require(not release['assets'], 'local staging draft already has assets')
    invoke(['gh', 'release', 'upload', tag, str(archive_path), '--repo', REPOSITORY], check=True)
    release = original_release()
    require(len(release['assets']) == 1, 'local staging asset inventory differs')
    asset = release['assets'][0]
    require(asset['name'] == archive_path.name and asset['state'] == 'uploaded' and
            asset['size'] == archive_path.stat().st_size and asset['digest'] == 'sha256:' + checksum(archive_path),
            'GitHub staging asset digest/size differs')
    return {'schema': 1, 'origin': 'local', 'repository': REPOSITORY, 'source_revision': revision,
            'release_id': release_id, 'asset_id': asset['id'], 'digest': asset['digest'], 'size': asset['size'],
            'name': asset['name'], 'staging_tag': tag}
