#!/usr/bin/env python3
"""Local signing, private draft staging, and credential-free native admission.

Staging is transport, never a production release. Publication consumes the exact
completed import run's immutable Actions artifact, not mutable draft assets.
"""
import argparse
import base64
from datetime import datetime, timezone, timedelta
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import struct
import stat
import sys
import tarfile
import tempfile
import uuid
import warnings
import zipfile

sys.dont_write_bytecode = True

import production_release as production
from component_provenance import api, api_command, REPOSITORY, instant
from macos_distribution import checksum, require, write_json, INSTALL_INSTRUCTIONS
import package_diagnostics

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = production.MAX_BYTES
EVIDENCE = {'keychain-diagnostics.json', 'package-diagnostics.json', 'payload-log.json',
            'payload-submission.json', 'notary-log.json', 'notary-submission.json', 'package-source.txt'}
KEYCHAIN_STAGES = {'keychain.' + name for name in ('snapshot-search-list', 'decode-p12', 'create',
    'settings', 'unlock', 'configure-search-list', 'read-search-list', 'verify-search-list', 'import-p12',
    'partition-list', 'find-identity', 'match-pinned-identity', 'remove-import-file')}
KEYCHAIN_STAGES.update('cleanup.' + name for name in ('remove-p12', 'verify-p12-absent',
    'delete-keychain', 'verify-keychain-absent', 'inspect-search-list', 'restore-search-list',
    'read-restored-search-list', 'verify-search-list-restored'))


def staging_api(path, binary=False):
    if binary and path.startswith('releases/assets/'):
        command = api_command(path, binary=True)
        command.extend(['-H', 'Accept: application/octet-stream'])
        return subprocess.check_output(command, stderr=subprocess.PIPE, timeout=60)
    return api(path, binary=binary)


def exact_source(revision, invoke=subprocess.run):
    require(re.fullmatch(r'[a-f0-9]{40}', revision or ''), 'invalid local source')
    head = invoke(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    dirty = invoke(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all'], capture_output=True, text=True, check=True).stdout
    require(head == revision and not dirty.strip(), 'local signing requires exact clean committed source')
    production.live_source(revision)


def no_parallel_signing(revision, fetch=api):
    listing = fetch('actions/workflows/macos-production-signing.yml/runs?event=workflow_dispatch&per_page=100')
    require(listing['total_count'] <= 100, 'signing history requires reconciliation')
    for run in listing['workflow_runs']:
        if run['head_sha'] == revision:
            require(run['status'] == 'completed' and run['conclusion'] != 'success',
                    'hosted signing is active or succeeded; local signing refused')


def signed_names(folder):
    manifest = json.loads((folder / 'release.json').read_text())
    names = {item['filename'] for item in manifest['bundles'].values()}
    names.update(json.loads((folder / 'macos-distribution.json').read_text())['artifacts'])
    names.update(('release.json', 'SHA256SUMS', 'macos-distribution.json', 'qualification.json',
                  'notarization-evidence.json', 'internal-release.json', 'production-plan.json'))
    if (folder / 'manager-core.json').exists():
        names.add('manager-core.json')
    return names


def transport_files(folder):
    expected = signed_names(folder) | {'evidence/' + name for name in EVIDENCE}
    observed = set()
    for path in folder.rglob('*'):
        require(not path.is_symlink(), 'symlink in local distribution')
        if path.is_file():
            observed.add(path.relative_to(folder).as_posix())
    require(observed == expected, 'local distribution contains missing or unexpected files')
    receipt = json.loads((folder / 'macos-distribution.json').read_text())
    require(set(receipt) == {'status', 'source_revision', 'original_qualified_helper_sha256', 'payloads',
        'notary_submission_id', 'payload_notary_submission_id', 'dmg_signature', 'artifacts', 'updater'},
        'unexpected distribution receipt field')
    require(receipt['updater'] == 'tar.gz; same notarized native code; online ticket lookup required' and
            set(receipt['payloads']) == {'libexec/symphony-host'}, 'unexpected distribution policy')
    for signature, runtime in ((receipt['payloads']['libexec/symphony-host'], True), (receipt['dmg_signature'], False)):
        require(set(signature) == {'sha256', 'cdhash', 'certificate_sha1', 'team_id', 'hardened_runtime', 'secure_timestamp'}
                and re.fullmatch(r'[a-f0-9]{64}', signature['sha256']) and
                re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', signature['cdhash']) and
                re.fullmatch(r'[A-F0-9]{40}', signature['certificate_sha1']) and
                re.fullmatch(r'[A-Z0-9]{10}', signature['team_id']) and
                signature['hardened_runtime'] is runtime and signature['secure_timestamp'] is True,
                'invalid or unsanitized signature metadata')
    # Reject arbitrary diagnostic fields rather than uploading incidental secrets.
    for label in ('payload', 'notary'):
        submission = json.loads((folder / 'evidence' / (label + '-submission.json')).read_text())
        log = json.loads((folder / 'evidence' / (label + '-log.json')).read_text())
        require(set(submission) == {'id', 'status', 'submitted_sha256'} and
                set(log) == {'id', 'status', 'statusCode', 'sha256', 'issues_count'},
                'unsanitized Apple evidence')
        require(str(uuid.UUID(submission['id'])) == submission['id'] == log['id'] and
                submission['status'] == log['status'] == 'Accepted' and
                re.fullmatch(r'[a-f0-9]{64}', submission['submitted_sha256']) and
                submission['submitted_sha256'] == log['sha256'] and
                type(log['statusCode']) is int and log['statusCode'] == 0 and
                type(log['issues_count']) is int and log['issues_count'] == 0, 'invalid Apple evidence fields')
    for name in ('keychain-diagnostics.json', 'package-diagnostics.json'):
        journal = json.loads((folder / 'evidence' / name).read_text())
        require(set(journal) == {'schema', 'operations'} and journal['schema'] == 1 and
                isinstance(journal['operations'], list) and 0 < len(journal['operations']) <= 1000,
                'unsanitized diagnostic journal')
        for operation in journal['operations']:
            require(set(operation) <= {'stage', 'status', 'exit_status', 'outcome', 'source_line', 'reason'},
                    'unsanitized diagnostic operation')
            stages = KEYCHAIN_STAGES if name.startswith('keychain') else package_diagnostics.STAGES
            require(operation['stage'] in stages and operation['status'] in ('running', 'success', 'failed'),
                    'invalid diagnostic labels')
            require(operation.get('outcome') in (None, 'failed', 'timed out', 'could not start', 'operation refused', 'interrupted'),
                    'invalid diagnostic outcome')
            require(operation.get('reason') is None or (operation['reason'] in package_diagnostics.REASONS and
                    operation['stage'] == 'signature.sign' and operation['status'] == 'failed'), 'invalid diagnostic reason')
            for field, limit in (('source_line', 10000000), ('exit_status', 65535)):
                require(field not in operation or (type(operation[field]) is int and 0 <= operation[field] <= limit),
                        'invalid diagnostic numeric field')
    revision = json.loads((folder / 'release.json').read_text())['source_revision']
    require((folder / 'evidence/package-source.txt').read_text() ==
            f'manager_source_revision={revision}\ncontroller_revision={revision}\nplatform=macos\n',
            'unexpected package source evidence')
    return sorted(expected)


def unpack(raw, destination):
    require(0 < len(raw) <= MAX_BYTES and not destination.exists(), 'invalid local archive destination/size')
    destination.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 64 and sum(e.file_size for e in entries) <= MAX_BYTES,
                'local archive expands beyond bound')
        require(len({e.filename for e in entries}) == len(entries), 'duplicate local archive member')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            require(not entry.is_dir() and not path.is_absolute() and '..' not in path.parts
                    and '\\' not in entry.filename and str(path) == entry.filename
                    and ((entry.external_attr >> 16) & 0o170000) in (0, 0o100000),
                    'unsafe local archive member')
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))


def bundle_files(archive_path, with_directories=False):
    result = {}
    total = 0; names = set(); directories = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(not archive.pax_headers, 'extended archive metadata refused')
        for member in archive:
            name = member.name.removeprefix('./')
            path = PurePosixPath(name)
            require((member.isfile() or member.isdir()) and not path.is_absolute() and '..' not in path.parts
                    and '\\' not in name and name == str(path) and name not in names
                    and (member.isdir() or name != '.') and not member.pax_headers and member.mode & 0o7000 == 0,
                    'unsafe or duplicate package member')
            names.add(name)
            if member.isdir():
                directories[name] = stat.S_IMODE(member.mode)
                continue
            total += member.size
            require(total <= MAX_BYTES and len(result) < 10000, 'package payload exceeds bound')
            result[name] = (archive.extractfile(member).read(), stat.S_IMODE(member.mode))
    return (result, directories) if with_directories else result


def executable_identity(raw):
    """Bind every ARM64 Mach-O byte except the signature and its exact size fields.

    codesign --remove-signature can leave different LINKEDIT padding. Instead,
    validate the original layout and normalize only fields necessarily resized
    by signing. All offsets, instructions, data and other load commands remain.
    Native codesign validation independently checks the complete signed object.
    """
    require(32 <= len(raw) <= MAX_BYTES, 'invalid executable size')
    header = struct.unpack_from('<8I', raw)
    require(header[0] == 0xFEEDFACF and header[1] == 0x0100000C and header[3] == 2
            and 0 < header[4] <= 512 and 0 < header[5] <= 65536,
            'requires bounded thin ARM64 executable')
    end = 32 + header[5]
    require(end < len(raw), 'invalid executable commands')
    offset = 32; signatures = []; segments = []; other_intervals = []
    for _ in range(header[4]):
        require(offset + 8 <= end, 'truncated executable command')
        command, size = struct.unpack_from('<2I', raw, offset)
        require(size >= 8 and size % 8 == 0 and offset + size <= end, 'invalid executable command size')
        if command == 0x1D:
            require(size == 16, 'invalid signature command')
            signatures.append((offset, *struct.unpack_from('<2I', raw, offset + 8)))
        if command == 0x19:
            require(size >= 72, 'invalid segment command')
            sections = struct.unpack_from('<I', raw, offset + 64)[0]
            require(sections <= 128 and size == 72 + 80 * sections, 'invalid segment section inventory')
            if raw[offset + 8:offset + 24].rstrip(b'\0') == b'__LINKEDIT':
                require(sections == 0, 'unexpected LINKEDIT sections')
                segments.append((offset, *struct.unpack_from('<4Q', raw, offset + 24)))
            else:
                file_offset, file_size = struct.unpack_from('<2Q', raw, offset + 40)
                if file_size:
                    other_intervals.append((file_offset, file_offset + file_size))
        offset += size
    require(offset == end and len(signatures) == len(segments) == 1, 'ambiguous executable layout')
    command, start, size = signatures[0]
    segment, _, virtual_size, file_offset, file_size = segments[0]
    intervals = sorted(other_intervals)
    require(all(a < b <= file_offset for a, b in intervals) and
            all(intervals[i - 1][1] <= intervals[i][0] for i in range(1, len(intervals))),
            'overlapping segments or executable signature overlaps payload')
    require(command + 16 == end and end <= file_offset <= start and start % 16 == 0
            and size > 0 and start + size == len(raw)
            and file_offset + file_size == len(raw)
            and virtual_size == ((file_size + 16383) // 16384) * 16384,
            'executable signature/LINKEDIT layout differs from supported native producer')
    normalized = bytearray(raw[:start])
    struct.pack_into('<I', normalized, command + 12, 0)
    unsigned_size = start - file_offset
    struct.pack_into('<Q', normalized, segment + 32, ((unsigned_size + 16383) // 16384) * 16384)
    struct.pack_into('<Q', normalized, segment + 48, unsigned_size)
    return hashlib.sha256(normalized).hexdigest()


def verify_payload(folder, qualified):
    manifest = json.loads((folder / 'release.json').read_text())
    require((folder / 'internal-release.json').read_bytes() == (qualified / 'internal/release.json').read_bytes(),
            'internal source manifest differs from original qualification')
    core = qualified / 'internal/manager-core.json'
    require(core.exists() == (folder / 'manager-core.json').exists(), 'manager core inventory differs')
    if core.exists():
        require(core.read_bytes() == (folder / 'manager-core.json').read_bytes(), 'manager core differs')
    mac = manifest['bundles']['macos']['filename']
    current, directories = bundle_files(folder / mac, with_directories=True)
    original, original_directories = bundle_files(qualified / 'packages' / mac, with_directories=True)
    require(directories == original_directories, 'signed payload directory inventory/modes differ')
    require('distribution-status.txt' in original and 'signing.json' not in original,
            'original qualification is not the expected unsigned package')
    require(set(current) == (set(original) - {'distribution-status.txt'}) | {'signing.json'},
            'signed payload file inventory differs from qualification')
    for name in set(original) - {'libexec/symphony-host', 'files.sha256', 'distribution-status.txt'}:
        require(current[name] == original[name], 'signed package source bytes or permissions differ from qualification')
    require(current['libexec/symphony-host'][1] == original['libexec/symphony-host'][1], 'signed helper permissions differ')
    lines = current['files.sha256'][0].decode().splitlines()
    expected = {name: hashlib.sha256(raw).hexdigest() for name, (raw, _) in current.items() if name != 'files.sha256'}
    observed = {}
    for line in lines:
        require(re.fullmatch(r'[a-f0-9]{64}  \./[^\r\n]+', line), 'invalid signed payload inventory')
        digest, name = line.split('  ./', 1)
        require(name not in observed, 'duplicate signed payload inventory')
        observed[name] = digest
    require(observed == expected, 'signed payload inventory does not match actual files')
    signing = json.loads(current['signing.json'][0])
    distribution = json.loads((folder / 'macos-distribution.json').read_text())
    require(signing == {'status': 'developer-id-signed-not-yet-notarized',
                       'source_revision': manifest['source_revision'],
                       'original_qualified_helper_sha256': checksum(qualified / 'helper/symphony-host'),
                       'payloads': distribution['payloads']}, 'embedded signing receipt differs')
    return current


def verify_native(folder, identity, team, qualified, invoke=subprocess.run):
    """Inspect ARM64 code on either Mac architecture; never execute uploaded code."""
    require(sys.platform == 'darwin', 'native distribution verification requires macOS')
    require(re.fullmatch(r'[A-F0-9]{40}', identity or '') and re.fullmatch(r'[A-Z0-9]{10}', team or ''),
            'pinned signing identity/team missing')
    receipt = json.loads((folder / 'macos-distribution.json').read_text())
    payload = verify_payload(folder, qualified)
    mac = next(name for name in receipt['artifacts'] if name.endswith('.tar.gz'))
    dmg = next(name for name in receipt['artifacts'] if name.endswith('.dmg'))
    require(receipt['payloads']['libexec/symphony-host']['certificate_sha1'] == identity and
            receipt['payloads']['libexec/symphony-host']['team_id'] == team and
            receipt['dmg_signature']['certificate_sha1'] == identity and receipt['dmg_signature']['team_id'] == team,
            'local receipt signing identity differs')
    def run(*args):
        return invoke([str(a) for a in args], capture_output=True, text=True, check=True, timeout=180).stdout
    with tempfile.TemporaryDirectory(prefix='local-signature-check-') as temporary:
        helper = Path(temporary) / 'symphony-host'
        helper.write_bytes(payload['libexec/symphony-host'][0])
        require(checksum(helper) == receipt['payloads']['libexec/symphony-host']['sha256'], 'native helper hash differs')
        for label, path in (('helper', helper), ('dmg', folder / dmg)):
            run('codesign', '--verify', '--strict', path)
            details = invoke(['codesign', '-d', '--verbose=4', str(path)], capture_output=True,
                             text=True, check=True, timeout=180)
            detail = details.stdout + details.stderr
            require('Authority=Developer ID Application:' in detail and
                    'TeamIdentifier=' + team + '\n' in detail and re.search(r'^Timestamp=.+', detail, re.M),
                    'native signature team/timestamp missing')
            if label == 'helper':
                require(re.search(r'^CodeDirectory .*flags=.*\bruntime\b', detail, re.M), 'native helper lacks hardened runtime')
                entitlements = invoke(['codesign', '-d', '--entitlements', ':-', str(path)],
                                      capture_output=True, text=True, check=True, timeout=180)
                require('<key>' not in entitlements.stdout + entitlements.stderr, 'unexpected native entitlements')
                run('codesign', '-vvvv', '-R=notarized', '--check-notarization', path)
            prefix = Path(temporary) / (label + '-certificate')
            run('codesign', '-d', '--extract-certificates=' + str(prefix), path)
            require(hashlib.sha1(Path(str(prefix) + '0').read_bytes()).hexdigest().upper() == identity,
                    'native certificate differs from pin')
        normalized = [executable_identity(path.read_bytes()) for path in (qualified / 'helper/symphony-host', helper)]
        require(normalized[0] == normalized[1], 'signed executable code differs from original qualification')
        run('xcrun', 'stapler', 'validate', folder / dmg)
        run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4', folder / dmg)
        mount = Path(temporary) / 'mounted'
        mount.mkdir()
        attached = False
        try:
            run('hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen', '-mountpoint', mount, folder / dmg)
            attached = True
            require({p.name for p in mount.iterdir()} == {'Symphony', 'Read Me.txt'}, 'unexpected DMG root contents')
            instructions = mount / 'Read Me.txt'
            info = instructions.lstat()
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o644 and
                    info.st_size == len(INSTALL_INSTRUCTIONS.encode()) and
                    instructions.read_text() == INSTALL_INSTRUCTIONS, 'DMG instructions differ from source')
            root = mount / 'Symphony'
            require(root.is_dir() and not root.is_symlink(), 'invalid DMG payload directory')
            _, expected_directories = bundle_files(folder / mac, with_directories=True)
            actual = {}; directories = {'.': stat.S_IMODE(root.lstat().st_mode)}
            for path in root.rglob('*'):
                mode = path.lstat().st_mode
                require((stat.S_ISREG(mode) or stat.S_ISDIR(mode)) and mode & 0o7000 == 0,
                        'DMG payload special type/mode refused')
                name = path.relative_to(root).as_posix()
                if stat.S_ISREG(mode):
                    require(name in payload and path.stat().st_size == len(payload[name][0]), 'unexpected DMG payload file')
                    actual[name] = (path.read_bytes(), stat.S_IMODE(mode))
                else:
                    require(name in expected_directories, 'unexpected DMG payload directory')
                    directories[name] = stat.S_IMODE(mode)
            require(actual == payload and directories == expected_directories,
                    'DMG payload differs from exact signed updater archive')
        finally:
            if attached:
                run('hdiutil', 'detach', mount)
    return {'schema': 1, 'scope': 'native-signature-ticket-payload-assessment; ARM execution and installed acceptance separate; local cleanup is operator attestation',
            'source_revision': receipt['source_revision'], 'certificate_sha1': identity, 'team_id': team,
            'normalized_helper_sha256': normalized[0],
            'payload_inventory_sha256': hashlib.sha256(payload['files.sha256'][0]).hexdigest(),
            'files': {name: checksum(folder / name) for name in sorted(receipt['artifacts'])}}


def upload(folder, revision, archive_path, invoke=subprocess.run, fetch=api):
    """Upload only the final approved bytes to a new private draft, never v* or latest."""
    names = transport_files(folder)
    require(not archive_path.exists(), 'local upload archive already exists')
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(folder / name, name)
    require(archive_path.stat().st_size <= MAX_BYTES, 'local upload archive oversized')
    tag = 'local-signing-' + revision + '-' + uuid.uuid4().hex
    invoke(['gh', 'release', 'create', tag, '--repo', REPOSITORY, '--draft', '--target', revision,
            '--title', 'Local signing staging (not a production release)',
            '--notes', 'Private transport only. Do not publish this draft or move its target.'], check=True)
    release = fetch('releases/tags/' + tag)
    require(release['draft'] is True and release['tag_name'] == tag and release['target_commitish'] == revision
            and not release['assets'], 'local staging draft differs')
    invoke(['gh', 'release', 'upload', tag, str(archive_path), '--repo', REPOSITORY], check=True)
    release = fetch('releases/' + str(release['id']))
    require(release['draft'] is True and len(release['assets']) == 1, 'local staging asset inventory differs')
    asset = release['assets'][0]
    require(asset['name'] == archive_path.name and asset['state'] == 'uploaded' and
            asset['size'] == archive_path.stat().st_size and asset['digest'] == 'sha256:' + checksum(archive_path),
            'GitHub staging asset digest/size differs')
    return {'schema': 1, 'origin': 'local', 'repository': REPOSITORY, 'source_revision': revision,
            'release_id': release['id'], 'asset_id': asset['id'], 'digest': asset['digest'], 'size': asset['size'],
            'name': asset['name'], 'staging_tag': tag}


def import_local(release_id, asset_id, digest, revision, destination, identity, team, fetch=staging_api, native=verify_native):
    require(re.fullmatch(r'[1-9][0-9]*', str(release_id)) and re.fullmatch(r'[1-9][0-9]*', str(asset_id))
            and re.fullmatch(r'sha256:[a-f0-9]{64}', digest or ''), 'invalid local staging selection')
    release = fetch('releases/' + str(release_id))
    require(release['draft'] is True and release['target_commitish'] == revision and
            re.fullmatch('local-signing-' + revision + r'-[a-f0-9]{32}', release['tag_name']),
            'requires source-bound nonproduction local staging draft')
    require(len(release['assets']) == 1 and release['assets'][0]['id'] == int(asset_id), 'staging asset selection differs')
    asset = fetch('releases/assets/' + str(asset_id))
    binding = ('id', 'name', 'state', 'size', 'digest', 'created_at')
    require(all(asset.get(k) == release['assets'][0].get(k) for k in binding)
            and asset['state'] == 'uploaded' and asset['digest'] == digest
            and 0 < asset['size'] <= MAX_BYTES and Path(asset['name']).name == asset['name'],
            'staging asset changed or oversized')
    age = datetime.now(timezone.utc) - instant(asset['created_at'])
    require(timedelta(minutes=-5) <= age <= timedelta(days=7), 'staging asset expired or from future')
    # Explicit Accept obtains the original asset bytes, not JSON or a user URL.
    raw = fetch('releases/assets/' + str(asset_id), binary=True)
    require(len(raw) == asset['size'] and 'sha256:' + hashlib.sha256(raw).hexdigest() == digest,
            'downloaded staging asset differs')
    unpack(raw, destination / 'signed')
    folder = destination / 'signed'
    transport_files(folder)
    qualified = json.loads((folder / 'qualification.json').read_text())
    production.qualification(str(qualified['run_id']), str(qualified['run_attempt']), revision, destination / 'qualified', fetch)
    production.verify_retained(destination, revision, fetch)
    proof = native(folder, identity, team, destination / 'qualified')
    write_json(destination / 'native-verification.json', proof)
    write_json(destination / 'local-origin.json', {'schema': 1, 'origin': 'local', 'repository': REPOSITORY,
               'source_revision': revision, 'release_id': int(release_id), 'asset_id': int(asset_id),
               'digest': digest, 'size': asset['size'], 'name': asset['name'], 'staging_tag': release['tag_name']})


def retained_local(run_id, attempt, revision, destination, fetch=api):
    production.completed_run(run_id, attempt, revision, 'macos-local-signing-import.yml', fetch)
    record = production.download(run_id, attempt, revision,
        f'production-local-signed-{revision}-{run_id}-{attempt}', destination / 'imported', fetch)
    imported = destination / 'imported'
    origin = json.loads((imported / 'local-origin.json').read_text())
    require(origin['origin'] == 'local' and origin['source_revision'] == revision and origin['repository'] == REPOSITORY,
            'local import origin differs')
    require({p.name for p in imported.iterdir()} == {'signed', 'local-origin.json', 'native-verification.json'},
            'local import inventory differs')
    shutil.copytree(imported / 'signed', destination / 'signed')
    proof = json.loads((imported / 'native-verification.json').read_text())
    receipt = json.loads((destination / 'signed/macos-distribution.json').read_text())
    require(proof['schema'] == 1 and proof['source_revision'] == revision and
            proof['certificate_sha1'] == receipt['payloads']['libexec/symphony-host']['certificate_sha1'] and
            proof['team_id'] == receipt['payloads']['libexec/symphony-host']['team_id'] and
            proof['files'] == {name: checksum(destination / 'signed' / name) for name in receipt['artifacts']},
            'native import verification binding differs')
    qualified = json.loads((destination / 'signed/qualification.json').read_text())
    production.qualification(str(qualified['run_id']), str(qualified['run_attempt']), revision, destination / 'qualified', fetch)
    production.verify_retained(destination, revision, fetch)
    write_json(destination / 'signed-origin.json', {'origin': 'local', 'import_artifact': record, 'staging': origin,
                                                  'native_verification': proof})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    local = modes.add_parser('sign')
    local.add_argument('qualified_run'); local.add_argument('qualified_attempt'); local.add_argument('output', type=Path)
    local.add_argument('--authorize-apple-upload', action='store_true', required=True)
    stage = modes.add_parser('upload')
    stage.add_argument('signed', type=Path); stage.add_argument('archive', type=Path)
    stage.add_argument('--authorize-staging-upload', action='store_true', required=True)
    receive = modes.add_parser('import')
    receive.add_argument('release_id'); receive.add_argument('asset_id'); receive.add_argument('digest')
    receive.add_argument('destination', type=Path)
    args = parser.parse_args()
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    exact_source(revision)
    if args.mode == 'import':
        production.source(revision)
        import_local(args.release_id, args.asset_id, args.digest, revision, args.destination,
                     os.environ['MACOS_SIGNING_IDENTITY_SHA1'], os.environ['MACOS_TEAM_ID'])
    elif args.mode == 'upload':
        qualified = json.loads((args.signed / 'qualification.json').read_text())
        with tempfile.TemporaryDirectory(prefix='local-upload-qualification-') as temporary:
            inputs = Path(temporary) / 'qualified'
            production.qualification(str(qualified['run_id']), str(qualified['run_attempt']), revision, inputs)
            production.verify_signed(args.signed, inputs, revision)
            receipt = json.loads((args.signed / 'macos-distribution.json').read_text())
            verify_native(args.signed, receipt['payloads']['libexec/symphony-host']['certificate_sha1'],
                          receipt['payloads']['libexec/symphony-host']['team_id'], inputs)
        record = upload(args.signed, revision, args.archive)
        write_json(args.archive.with_suffix('.receipt.json'), record)
        print(json.dumps(record, indent=2))
    else:
        require(sys.platform == 'darwin' and os.uname().machine == 'arm64' and sys.stdin.isatty() and sys.stdout.isatty(),
                'local signing requires interactive Apple Silicon Terminal')
        warnings.filterwarnings('error', category=getpass.GetPassWarning)
        no_parallel_signing(revision)
        require(not args.output.exists(), 'local attempt output already exists; reconcile before retry')
        args.output.mkdir(parents=True)
        inputs = args.output / 'qualified'
        production.qualification(args.qualified_run, args.qualified_attempt, revision, inputs)
        p12 = Path(input('Existing successful .p12 file path: ').strip()).expanduser()
        require(p12.is_file() and not p12.is_symlink() and p12.stat().st_size > 0, 'invalid existing P12')
        password = getpass.getpass('P12 EXPORT PASSWORD (hidden): ')
        apple_id = getpass.getpass('APPLE DEVELOPER EMAIL (hidden): ')
        notary = getpass.getpass('APPLE APP-SPECIFIC PASSWORD (hidden): ')
        identity = input('Pinned Developer ID certificate SHA1 (public): ').strip().upper()
        team = input('Apple Team ID (public): ').strip()
        exact_source(revision); no_parallel_signing(revision)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', RUNNER_TEMP=str(args.output.resolve()),
                   MACOS_SIGNING_P12_BASE64=base64.b64encode(p12.read_bytes()).decode('ascii'),
                   MACOS_SIGNING_P12_PASSWORD=password, MACOS_NOTARY_APPLE_ID=apple_id,
                   MACOS_NOTARY_APP_PASSWORD=notary, MACOS_SIGNING_IDENTITY_SHA1=identity, MACOS_TEAM_ID=team)
        signed = args.output.resolve() / 'signed'
        subprocess.run([sys.executable, str(ROOT / 'scripts/release/macos_distribution.py'), 'build',
                        str(inputs.resolve() / 'internal/release.json'), str(inputs.resolve() / 'helper/symphony-host'),
                        str(signed), '--authorize-apple-upload'], env=env, check=True)
        production.seal_signed(inputs, signed, revision)
        verify_native(signed, identity, team, inputs)
        print('Local signed bytes verified. Explicit staging-upload authorization is a separate command.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, getpass.GetPassWarning, subprocess.SubprocessError,
            zipfile.BadZipFile, tarfile.TarError):
        sys.exit('Local distribution refused; retain sanitized evidence and reconcile before any retry.')
