#!/usr/bin/env python3
"""Retrieve original qualified archives, without building or renaming images.

Planning proves metadata availability; this consumer independently rechecks the
origin, downloads its actual bytes and checks both artifact and archive hashes.
The existing lifecycle controller remains responsible for loading and exercising
these archives with the current manager package.
"""
import argparse
import hashlib
import json
import os
import selectors
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

from component_provenance import REPOSITORY, require, verify_origin, api_command

MAX_ARCHIVE_BYTES = 64 * 1024 ** 3
MAX_ARTIFACT_BYTES = MAX_ARCHIVE_BYTES + 1024 ** 3


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(artifact_id, path, max_bytes=None):
    require(type(artifact_id) is int and artifact_id > 0, 'invalid payload artifact ID')
    if max_bytes is None:max_bytes=MAX_ARTIFACT_BYTES
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_ARTIFACT_BYTES, 'invalid artifact download bound')
    command = api_command(f'actions/artifacts/{artifact_id}/zip', binary=True)
    with path.open('xb') as output, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            deadline, received = time.monotonic() + 1800, 0
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 1800)
                    if not ready.select(timeout=remaining):
                        raise subprocess.TimeoutExpired(command, 1800)
                    block = os.read(process.stdout.fileno(), 1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    require(received <= max_bytes, 'downloaded artifact exceeds consumer bound')
                    output.write(block)
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if code:
                raise subprocess.CalledProcessError(code, command)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def origin_verifier(baseline_run, event):
    # Only explicit manual diagnostics may use a selected pre-merge native run.
    # A downloaded plan's baseline_selection never grants this authorization.
    if baseline_run is not None:
        require(type(baseline_run) is int and baseline_run > 0 and event == 'workflow_dispatch',
                'candidate baseline override requires an explicit manual diagnostic')
    if baseline_run is not None:
        from component_selection import manual_verifier
        return manual_verifier(baseline_run)
    return lambda record:verify_origin(record,candidate_run=None)


def retrieve(record, expected_origin, destination, verify=verify_origin, fetch=download):
    """Write one source-bound archive/record pair into a new directory only."""
    origin = verify(record)
    require(origin == expected_origin, 'original payload origin changed since planning')
    require(not destination.exists(), 'refusing to overwrite an existing payload destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.reuse-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        payload = staging / 'artifact.zip'
        fetch(origin['artifact_id'], payload)
        require('sha256:' + checksum(payload) == origin['artifact_digest'], 'downloaded artifact checksum differs')
        archive_name = origin['archive_filename']
        # Selection is an exact member lookup, never archive-controlled extraction.
        require(archive_name in (f"{record['provider']}-{record['architecture']}.docker.tar.gz",
                                f"previous/{record['provider']}-{record['architecture']}.docker.tar.gz"),
                'unexpected qualified archive member')
        prepared = staging / 'prepared'
        prepared.mkdir()
        archive = prepared / Path(archive_name).name
        with zipfile.ZipFile(payload) as bundle:
            members = [member for member in bundle.infolist() if member.filename == archive_name]
            require(len(members) == 1 and not members[0].is_dir(), 'missing or duplicate original archive member')
            require(0 < members[0].file_size <= MAX_ARCHIVE_BYTES, 'original archive exceeds the consumer bound')
            require(shutil.disk_usage(staging).free > members[0].file_size, 'insufficient space for original archive')
            with bundle.open(members[0]) as source, archive.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        require(checksum(archive) == origin['archive_sha256'], 'original image archive checksum differs')
        record_name = f"{record['provider']}-{record['architecture']}.json"
        (prepared / record_name).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        archive.with_name(archive.name.removesuffix('.docker.tar.gz') + '.sha256').write_text(
            origin['archive_sha256'] + '  ' + archive.name + '\n')
        (prepared / 'origin.json').write_text(json.dumps(origin, indent=2, sort_keys=True) + '\n')
        prepared.rename(destination)
    return destination / archive.name


def selected(plan, provider, architecture, revision):
    require(plan.get('planning_complete') is True and plan.get('manager_source_revision') == revision,
            'reuse plan is incomplete or bound to another manager revision')
    entry = plan['components'][provider][architecture]
    require(entry.get('action') == 'reuse', 'component requires a verified-input rebuild')
    mode=entry.get('selection_mode')
    if mode=='finite-input-transition-v1':
        from finite_transition import validate_evidence
        evidence=validate_evidence(entry['input_transition'],revision)
        old=evidence['source_input_identities'][provider][architecture]
        desired=evidence['manager_input_identities'][provider][architecture]
    elif mode=='finite-component-v1':
        from finite_transition import validate_component_evidence
        evidence=validate_component_evidence(entry['input_transition'],revision,provider,architecture)
        old=evidence['selected_input_identity'];desired=evidence['desired_input_identity']
    else:
        require(mode is None,'unknown component selection authority')
        old=desired=entry['input_identity']
    if mode is not None:require(entry['record']['source_revision']==evidence['source_revision'],'selected original source differs from finite authority')
    require(entry['record']['input_identity']==entry.get('selected_input_identity',old)==old and
            entry['input_identity']==entry.get('desired_input_identity',desired)==desired,
            'component selected/desired closure differs from plan')
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--root', default='.')
    parser.add_argument('--provider', choices=('codex', 'claude'), required=True)
    parser.add_argument('--architecture', choices=('x86_64', 'arm64'), required=True)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--prior', action='store_true')
    parser.add_argument('--baseline-run', type=int, help='explicit authorized manual candidate run only')
    args = parser.parse_args()
    try:
        verify = origin_verifier(args.baseline_run, os.environ.get('GITHUB_EVENT_NAME'))
        revision = subprocess.check_output(['git', '-C', args.root, 'rev-parse', 'HEAD'], text=True).strip()
        entry = selected(json.loads(args.plan.read_text()), args.provider, args.architecture, revision)
        from runner_inputs import identity
        require(identity(args.root, args.provider, args.architecture)['input_identity'] == entry['input_identity'],
                'effective inputs changed after planning')
        if entry.get('selection_mode') in ('finite-component-v1','finite-input-transition-v1'):
            from finite_transition import evaluate_component
            measured=evaluate_component(args.root,entry['record'],args.provider,args.architecture)
            require(measured['selected_input_identity']==entry['selected_input_identity'] and
                    measured['desired_input_identity']==entry['desired_input_identity'], 'actual finite input transition changed')
        record_key, origin_key = ('prior_record', 'prior_origin') if args.prior else ('record', 'origin')
        require(record_key in entry and origin_key in entry, 'distinct original prior qualification is unavailable')
        if args.prior:
            require(entry[record_key]['image'] != entry['record']['image'], 'prior qualification must select a distinct image')
        print(retrieve(entry[record_key], entry[origin_key], args.destination, verify=verify))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, f'runner payload reuse: {type(error).__name__}; actual original payload not authorized\n')


if __name__ == '__main__':
    main()
