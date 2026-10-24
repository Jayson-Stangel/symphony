#!/usr/bin/env python3
"""Download only immutable successful dev qualification for the approved source."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import zipfile

import artifacts
from component_provenance import api, REPOSITORY
from macos_distribution import checksum, require, write_json
from manager_envelope import load_release

MAX_BYTES = 64 * 1024 * 1024


def retrieve(run_id, output, revision, fetch=api):
    require(re.fullmatch(r'[1-9][0-9]*', run_id), 'invalid qualification run')
    require(re.fullmatch(r'[a-f0-9]{40}', revision), 'invalid approved revision')
    run = fetch(f'actions/runs/{run_id}')
    require(run.get('head_sha') == revision and run.get('head_branch') == 'dev'
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('path') == '.github/workflows/runner-integration.yml'
            and run.get('head_repository', {}).get('full_name') == REPOSITORY,
            'requires successful same-repository dev Runner integration manual run at approved SHA')
    attempt = run['run_attempt']
    items = fetch(f'actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    suffix = f'{run_id}-{attempt}'
    records = []
    require(not output.exists(), 'signing input directory already exists')
    output.mkdir(parents=True)
    for kind, name, allowed in (
            ('helper', f'current-manager-helper-macos-{suffix}', {'symphony-host', 'symphony-host.sha256'}),
            ('internal', f'current-manager-inputs-{suffix}', {'release.json', 'manager-core.json'}),
            ('packages', f'current-manager-package-{suffix}', None)):
        matches = [item for item in items if item.get('name') == name]
        require(len(matches) == 1, 'missing unique qualified artifact: ' + kind)
        artifact = matches[0]
        require(artifact.get('expired') is False and 0 < artifact['size_in_bytes'] <= MAX_BYTES,
                'qualified artifact expired or oversized')
        raw = fetch(f"actions/artifacts/{artifact['id']}/zip", binary=True)
        require(len(raw) <= MAX_BYTES and 'sha256:' + hashlib.sha256(raw).hexdigest() == artifact['digest'],
                'qualified artifact digest differs')
        directory = output / kind
        directory.mkdir()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(sum(entry.file_size for entry in entries) <= MAX_BYTES, 'qualified archive expands beyond bound')
            require(len({entry.filename for entry in entries}) == len(entries), 'duplicate qualified archive entries')
            for entry in entries:
                if entry.is_dir():
                    continue
                filename = entry.filename
                require(Path(filename).name == filename and filename not in ('.', '..'), 'unsafe qualified artifact path')
                require((entry.external_attr >> 16) & 0o170000 != 0o120000, 'symlink qualified artifact')
                if allowed is not None:
                    require(filename in allowed, 'unexpected qualified input file')
                (directory / filename).write_bytes(archive.read(entry))
        records.append({key: artifact[key] for key in ('id', 'name', 'digest')})
    helper = output / 'helper/symphony-host'
    expected = (output / 'helper/symphony-host.sha256').read_text().strip()
    require(re.fullmatch(r'[a-f0-9]{64}  symphony-host', expected)
            and expected.split()[0] == checksum(helper), 'original qualified helper checksum differs')
    helper.chmod(0o755)
    manifest = output / 'internal/release.json'
    require(load_release(manifest)['source_revision'] == revision, 'qualified manager source differs')
    internal = json.loads(manifest.read_text())
    external = json.loads((output / 'packages/release.json').read_text())
    artifacts.validate(external)
    require(load_release(output / 'packages/release.json')['source_revision'] == revision,
            'qualified package source differs')
    expected_external = json.loads(json.dumps(internal))
    for platform in ('macos', 'linux_x86_64'):
        entry = external['bundles'][platform]
        require(checksum(output / 'packages' / entry['filename']) == entry['sha256'],
                'qualified native archive checksum differs')
        expected_external['bundles'][platform]['sha256'] = entry['sha256']
    expected_external['bundle'] = dict(expected_external['bundles']['macos'])
    require(expected_external == external, 'qualified external/internal metadata differs')
    # Original qualification remains original; bind its helper bytes without rewriting it.
    with tarfile.open(output / 'packages' / internal['bundle']['filename'], 'r:gz') as archive:
        entries = [entry for entry in archive if entry.name == './libexec/symphony-host']
        require(len(entries) == 1 and entries[0].isfile() and entries[0].size <= MAX_BYTES,
                'qualified archive helper missing')
        require(hashlib.sha256(archive.extractfile(entries[0]).read()).hexdigest() == checksum(helper),
                'qualified archive contains different helper bytes')
    write_json(output / 'qualification.json', {
        'repository': REPOSITORY, 'run_id': int(run_id), 'run_attempt': attempt,
        'source_revision': revision, 'original_artifacts': records,
        'original_qualified_helper_sha256': checksum(helper)})
    return internal


if __name__ == '__main__':
    try:
        retrieve(sys.argv[1], Path(sys.argv[2]), os.environ['GITHUB_SHA'])
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, tarfile.TarError):
        raise SystemExit('macOS signing inputs refused: qualification or immutable artifact binding failed') from None
