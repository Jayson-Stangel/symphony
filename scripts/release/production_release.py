#!/usr/bin/env python3
"""Import exact completed production preparation/signing; never publish unsigned bytes."""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import zipfile

import artifacts
from component_provenance import api, REPOSITORY, available, instant
from datetime import datetime, timezone
from macos_distribution import checksum, require, write_json
from manager_envelope import load_release
import manager_version
from signing_payload import helper_hash

MAX_BYTES = 512 * 1024 * 1024


def completed_run(run_id, attempt, revision, workflow, fetch=api):
    require(re.fullmatch(r'[1-9][0-9]*', str(run_id)) and
            re.fullmatch(r'[1-9][0-9]*', str(attempt)), 'invalid exact run/attempt')
    require(re.fullmatch(r'[a-f0-9]{40}', revision), 'invalid production revision')
    run = fetch(f'actions/runs/{run_id}')
    require(run.get('head_sha') == revision and run.get('head_branch') == 'main'
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('run_attempt') == int(attempt)
            and run.get('path') == '.github/workflows/' + workflow
            and run.get('head_repository', {}).get('full_name') == REPOSITORY,
            'requires exact successful same-repository main production run/attempt')
    exact = fetch(f'actions/runs/{run_id}/attempts/{attempt}')
    require(exact.get('run_attempt') == int(attempt) and exact.get('head_sha') == revision
            and exact.get('conclusion') == 'success', 'production attempt differs')
    jobs = fetch(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    require(jobs.get('total_count', len(jobs['jobs'])) <= 100, 'production job inventory oversized')
    required = (['Candidate qualification', 'Build release package (macos)', 'Build release package (linux_x86_64)',
                 'Assemble release packages', 'Verify engine (linux_x86_64)', 'Verify engine (macos_arm64)',
                 'Verify production preparation source'] if workflow == 'burrito-release.yml' else
                ['Verify locally signed distribution'] if workflow == 'macos-local-signing-import.yml' else
                ['Sign exact production preparation'])
    for suffix in required:
        matches = [job for job in jobs['jobs'] if job['name'] == suffix or job['name'].endswith(' / ' + suffix)]
        require(len(matches) == 1 and matches[0].get('conclusion') == 'success', 'mandatory production producer did not succeed')
    return run


def download(run_id, attempt, revision, name, destination, fetch=api):
    listing = fetch(f'actions/runs/{run_id}/artifacts?per_page=100')
    require(not listing.get('total_count', len(listing['artifacts'])) > 100,
            'ambiguous oversized production artifact inventory')
    matches = [item for item in listing['artifacts'] if item.get('name') == name]
    require(len(matches) == 1, 'missing unique production artifact')
    item = matches[0]
    available(item, name, item['digest'], int(run_id), revision, datetime.now(timezone.utc))
    exact = fetch(f'actions/runs/{run_id}/attempts/{attempt}')
    require(instant(exact['run_started_at']) <= instant(item['created_at']) <= instant(exact['updated_at']),
            'production artifact was not uploaded by the approved attempt')
    require(item['size_in_bytes'] <= MAX_BYTES, 'production artifact oversized')
    raw = fetch(f"actions/artifacts/{item['id']}/zip", binary=True)
    require(len(raw) <= MAX_BYTES and 'sha256:' + hashlib.sha256(raw).hexdigest() == item['digest'],
            'production artifact checksum differs')
    require(not destination.exists(), 'production destination exists')
    destination.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(sum(e.file_size for e in entries) <= MAX_BYTES, 'production artifact expands beyond bound')
        require(len({e.filename for e in entries}) == len(entries), 'duplicate production archive entry')
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            require(name and not path.is_absolute() and '..' not in path.parts and '\\' not in name
                    and name == str(path) + ('/' if entry.is_dir() else ''), 'unsafe production archive path')
            require((entry.external_attr >> 16) & 0o170000 not in (0o120000, 0o060000, 0o020000),
                    'nonregular production archive member')
            target = destination / name
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    return {key: item[key] for key in ('id', 'name', 'digest')}


def same_manifest(left, right):
    """Only final archive hashes may differ from the prepared internal manifest."""
    expected = copy.deepcopy(left)
    for platform in ('macos', 'linux_x86_64'):
        expected['bundles'][platform]['sha256'] = right['bundles'][platform]['sha256']
    expected['bundle'] = dict(expected['bundles']['macos'])
    require(expected == right, 'production metadata differs from prepared selection')


def verify_packages(folder, revision):
    manifest = json.loads((folder / 'release.json').read_text())
    require(load_release(folder / 'release.json')['source_revision'] == revision,
            'production package source differs')
    artifacts.validate(manifest)
    for entry in manifest['bundles'].values():
        require(Path(entry['filename']).name == entry['filename'] and
                checksum(folder / entry['filename']) == entry['sha256'], 'production package hash differs')
    plan = json.loads((folder / 'production-plan.json').read_text())
    require(plan['manager_source_revision'] == revision and plan['manifest']['source_revision'] == revision,
            'production plan source differs')
    from component_publication import validate_public_distribution
    validate_public_distribution(plan['manifest'])
    same_manifest(plan['manifest'], manifest)
    return manifest


def qualification(run_id, attempt, revision, destination, fetch=api):
    completed_run(run_id, attempt, revision, 'burrito-release.yml', fetch)
    destination.mkdir(parents=True, exist_ok=False)
    records = [download(run_id, attempt, revision, 'packaged-release', destination / 'packages', fetch),
               download(run_id, attempt, revision, 'native-host-helper-macos-' + revision,
                        destination / 'helper', fetch)]
    manifest = verify_packages(destination / 'packages', revision)
    helper = destination / 'helper/symphony-host'
    require((destination / 'helper/symphony-host.sha256').read_text().strip() ==
            checksum(helper) + '  symphony-host', 'production helper input checksum differs')
    require(helper_hash(destination / 'packages' / manifest['bundles']['macos']['filename']) == checksum(helper),
            'production package contains another helper')
    helper.chmod(0o755)
    receipt = {'repository': REPOSITORY, 'source_revision': revision, 'run_id': int(run_id),
               'run_attempt': int(attempt), 'original_artifacts': records,
               'original_qualified_helper_sha256': checksum(helper)}
    write_json(destination / 'qualification.json', receipt)
    (destination / 'internal').mkdir()
    write_json(destination / 'internal/release.json', json.loads((destination / 'packages/production-plan.json').read_text())['manifest'])
    return receipt


def seal_signed(inputs, output, revision):
    """Finalize the existing updater contract and bind signed bytes to original Q."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('finalize_packages', Path(__file__).with_name('finalize-manager-packages.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original = verify_packages(inputs / 'packages', revision)
    linux = original['bundles']['linux_x86_64']['filename']
    shutil.copyfile(inputs / 'packages' / linux, output / linux)
    shutil.copyfile(inputs / 'packages/production-plan.json', output / 'production-plan.json')
    shutil.copyfile(inputs / 'qualification.json', output / 'qualification.json')
    module.finalize(inputs / 'packages/release.json', output, revision)
    verify_signed(output, inputs, revision)
    write_json(output / 'notarization-evidence.json', {
        p.name: json.loads(p.read_text()) for p in sorted((output / 'evidence').glob('*.json'))})
    files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    (output / 'SHA256SUMS').write_text(''.join(f'{checksum(p)}  {p.name}\n' for p in files))


def verify_signed(folder, inputs, revision):
    manifest = verify_packages(folder, revision)
    original = verify_packages(inputs / 'packages', revision)
    require(json.loads((folder / 'qualification.json').read_text()) ==
            json.loads((inputs / 'qualification.json').read_text()), 'signed qualification differs')
    require((folder / 'production-plan.json').read_bytes() ==
            (inputs / 'packages/production-plan.json').read_bytes(), 'signed publication plan differs')
    same_manifest(original, manifest)
    linux = original['bundles']['linux_x86_64']['filename']
    require(checksum(folder / linux) == original['bundles']['linux_x86_64']['sha256'], 'Linux bytes changed during signing')
    receipt = json.loads((folder / 'macos-distribution.json').read_text())
    qualified = json.loads((inputs / 'qualification.json').read_text())
    require(receipt['status'] == 'notarized-stapled-assessed' and receipt['source_revision'] == revision
            and receipt['original_qualified_helper_sha256'] == qualified['original_qualified_helper_sha256'],
            'signed distribution source/qualification differs')
    mac = manifest['bundles']['macos']['filename']; dmg = mac.removesuffix('.tar.gz') + '.dmg'
    require(set(receipt['artifacts']) == {mac, dmg}, 'signed distribution file set differs')
    for name, digest in receipt['artifacts'].items():
        require(checksum(folder / name) == digest, 'signed final artifact checksum differs')
    require(helper_hash(folder / mac) == receipt['payloads']['libexec/symphony-host']['sha256'],
            'signed updater helper differs from assessed distribution')
    native = receipt['payloads']['libexec/symphony-host']
    outer = receipt['dmg_signature']
    require(native['certificate_sha1'] == outer['certificate_sha1'] and native['team_id'] == outer['team_id']
            and re.fullmatch(r'[A-Fa-f0-9]{40}', native['certificate_sha1'])
            and re.fullmatch(r'[A-Z0-9]{10}', native['team_id'])
            and native['secure_timestamp'] is True and outer['secure_timestamp'] is True
            and native['hardened_runtime'] is True, 'production signing identity/policy differs')
    for label, identifier in (('payload', receipt['payload_notary_submission_id']), ('notary', receipt['notary_submission_id'])):
        log = json.loads((folder / 'evidence' / (label + '-log.json')).read_text())
        submission = json.loads((folder / 'evidence' / (label + '-submission.json')).read_text())
        require(log['id'] == submission['id'] == identifier and log['status'] == submission['status'] == 'Accepted'
                and log['statusCode'] == 0 and log['issues_count'] == 0 and
                log['sha256'] == submission['submitted_sha256'], 'Apple acceptance evidence incomplete')
        if label == 'notary':
            require(log['sha256'] == outer['sha256'], 'DMG Apple submission differs from signed pre-staple bytes')
    import package_diagnostics
    package_diagnostics.require_complete(folder / 'evidence/package-diagnostics.json')
    operations = json.loads((folder / 'evidence/keychain-diagnostics.json').read_text())['operations']
    cleanup = {item['stage']: item['status'] for item in operations if item['stage'].startswith('cleanup.')}
    required_cleanup = {'cleanup.remove-p12', 'cleanup.verify-p12-absent', 'cleanup.delete-keychain',
                        'cleanup.verify-keychain-absent', 'cleanup.inspect-search-list',
                        'cleanup.verify-search-list-restored'}
    require(required_cleanup <= set(cleanup) and set(cleanup) <= required_cleanup | {
                'cleanup.restore-search-list', 'cleanup.read-restored-search-list',
                'cleanup.check-search-list', 'cleanup.remove-recovery-record', 'cleanup.sync-private-directory'}
            and all(status == 'success' for status in cleanup.values()),
            'production credential cleanup incomplete')
    return manifest


def signed(run_id, attempt, revision, destination, fetch=api):
    completed_run(run_id, attempt, revision, 'macos-production-signing.yml', fetch)
    record = download(run_id, attempt, revision, f'production-signed-{revision}-{run_id}-{attempt}',
                      destination / 'signed', fetch)
    qualified = json.loads((destination / 'signed/qualification.json').read_text())
    qualification(str(qualified['run_id']), str(qualified['run_attempt']), revision, destination / 'qualified', fetch)
    verify_retained(destination, revision, fetch)
    write_json(destination / 'signed-origin.json', record)


def verify_retained(destination, revision, fetch=api):
    """The same byte/evidence admission for hosted and imported local signing."""
    qualified = json.loads((destination / 'signed/qualification.json').read_text())
    verify_signed(destination / 'signed', destination / 'qualified', revision)
    sums = (destination / 'signed/SHA256SUMS').read_text().splitlines()
    expected_files = {p.name: checksum(p) for p in (destination / 'signed').iterdir()
                      if p.is_file() and p.name != 'SHA256SUMS'}
    observed = {}
    for line in sums:
        require(re.fullmatch(r'[a-f0-9]{64}  [A-Za-z0-9_.-]+', line), 'invalid production checksum inventory')
        digest, name = line.split('  ')
        require(name not in observed, 'duplicate production checksum')
        observed[name] = digest
    require(observed == expected_files, 'production checksum inventory incomplete')
    evidence = {p.name: json.loads(p.read_text()) for p in sorted((destination / 'signed/evidence').glob('*.json'))}
    require(json.loads((destination / 'signed/notarization-evidence.json').read_text()) == evidence,
            'published Apple evidence differs')
    # Engine binaries are independently qualified at Q, never advertised notarized.
    for target in ('linux_x86_64', 'macos_arm64'):
        download(str(qualified['run_id']), str(qualified['run_attempt']), revision, target,
                 destination / 'engines' / target, fetch)
        filename = f'symphony-v{manager_version.read(Path(__file__).parents[2])}-{target}'
        folder = destination / 'engines' / target
        metadata = json.loads((folder / (filename + '.source.json')).read_text())
        require(metadata == {'artifact': filename, 'sha256': checksum(folder / filename),
                             'source_revision': revision, 'target': target}, 'engine source/checksum differs')
        require((folder / (filename + '.sha256')).read_text().strip() == checksum(folder / filename) + '  ' + filename,
                'engine checksum receipt differs')


def release_files(retained):
    folder = retained / 'signed'; manifest = json.loads((folder / 'release.json').read_text())
    names = {entry['filename'] for entry in manifest['bundles'].values()}
    names.update(json.loads((folder / 'macos-distribution.json').read_text())['artifacts'])
    names.update(('release.json', 'SHA256SUMS', 'macos-distribution.json', 'qualification.json', 'notarization-evidence.json', 'internal-release.json', 'production-plan.json'))
    if (folder / 'manager-core.json').exists():
        names.add('manager-core.json')
    require({p.name for p in folder.iterdir() if p.is_file()} == names, 'unexpected retained release file')
    files = [folder / name for name in sorted(names)]
    for target in ('linux_x86_64', 'macos_arm64'):
        filename = f'symphony-v{manager_version.read(Path(__file__).parents[2])}-{target}'
        files.extend(retained / 'engines' / target / (filename + suffix) for suffix in ('', '.sha256', '.source.json'))
    require(all(p.is_file() and not p.is_symlink() and p.name not in ('.', '..') for p in files), 'release asset missing')
    require(len({p.name for p in files}) == len(files), 'duplicate local release asset')
    return files


def release_inventory(value, files, complete=False):
    require(value.get('draft') is True, 'refusing to alter a completed release')
    expected = {p.name: {'digest': 'sha256:' + checksum(p), 'size': p.stat().st_size} for p in files}
    seen = set()
    for asset in value['assets']:
        name = asset['name']
        require(name in expected and name not in seen, 'unexpected or duplicate draft release asset')
        require(asset.get('digest') == expected[name]['digest'] and asset.get('size') == expected[name]['size'],
                'draft release asset differs from assessed bytes')
        seen.add(name)
    require(not complete or seen == set(expected), 'remote release asset inventory incomplete')
    return seen


def live_tag(revision, tag, fetch=api):
    require(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', tag), 'invalid production tag')
    value = fetch('git/ref/tags/' + tag)['object']
    seen = set()
    for _ in range(8):
        sha = value.get('sha')
        require(re.fullmatch(r'[a-f0-9]{40}', sha or '') and sha not in seen, 'invalid or cyclic production tag')
        seen.add(sha)
        if value.get('type') == 'commit':
            require(sha == revision, 'live production tag moved from approved source')
            return
        require(value.get('type') == 'tag', 'production tag does not name a commit')
        value = fetch('git/tags/' + sha)['object']
    raise ValueError('production tag nesting exceeds bound')


def live_source(revision, tag=None, fetch=api):
    main = fetch('git/ref/heads/main')['object']
    require(main.get('type') == 'commit' and re.fullmatch(r'[a-f0-9]{40}', main.get('sha', '')),
            'live main source unavailable')
    if tag is None:
        require(main['sha'] == revision, 'main moved from approved signing source')
    else:
        live_tag(revision, tag, fetch)
        if main['sha'] != revision:
            comparison = fetch('compare/' + revision + '...' + main['sha'])
            require(comparison.get('merge_base_commit', {}).get('sha') == revision,
                    'approved production source is no longer on live main')


def publish_release(retained, tag, invoke=subprocess.run, verify_tag=None):
    files = release_files(retained)
    verify_tag = verify_tag or (lambda: live_source(os.environ["GITHUB_SHA"], tag))
    candidate_manifest = retained / 'signed/release.json'
    revision = os.environ.get('GITHUB_SHA', '')
    require(re.fullmatch(r'[a-f0-9]{40}', revision), 'invalid production source')
    require(json.loads(candidate_manifest.read_text())['source_revision'] == revision,
            'retained publication source differs')

    def request(path, method=None, payload=None):
        command = ['gh', 'api', f'repos/{REPOSITORY}/' + path]
        kwargs = {'capture_output': True, 'text': True, 'check': True}
        if method:
            command += ['--method', method, '--input', '-']
            kwargs['input'] = json.dumps(payload)
        result = invoke(command, **kwargs)
        require(result.returncode == 0, 'production release service unavailable')
        return json.loads(result.stdout)

    def identity(value, release_id=None, original_target=None):
        require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] > 0
                and (release_id is None or value['id'] == release_id)
                and value.get('tag_name') == tag
                and isinstance(value.get('target_commitish'), str) and 0 < len(value['target_commitish']) <= 256
                and (original_target is None or value['target_commitish'] == original_target),
                'original production release identity differs')
        return value

    # The by-tag endpoint only promises published releases. Reconcile a retry's
    # original draft by a complete bounded inventory before creating anything.
    matches = []
    for page in range(1, 11):
        listing = request(f'releases?per_page=100&page={page}')
        require(isinstance(listing, list) and len(listing) <= 100
                and all(isinstance(item, dict) for item in listing), 'invalid production release inventory')
        matches.extend(item for item in listing if item.get('tag_name') == tag)
        require(len(matches) <= 1, 'ambiguous original production draft')
        if len(listing) < 100:
            break
    else:
        raise ValueError('production release inventory exceeds reconciliation bound')
    value = identity(matches[0]) if matches else None
    if value is not None:
        require(value.get('draft') is True, 'refusing to alter a completed release')
    verify_tag()
    if value is None:
        # Capture the ORIGINAL ID once. A failed/uncertain POST is not retried.
        value = identity(request('releases', 'POST', {'tag_name': tag, 'target_commitish': revision,
                         'draft': True, 'generate_release_notes': True}))
        require(value.get('draft') is True, 'created production release is not a draft')
    release_id = value['id']
    original_target = value['target_commitish']

    def state():
        value = identity(request('releases/' + str(release_id)), release_id, original_target)
        require(value.get('draft') is True, 'refusing to alter a completed release')
        return value

    value = state()
    present = release_inventory(value, files)
    missing = [str(p) for p in files if p.name not in present]
    if missing:
        verify_tag()
        invoke(['gh', 'release', 'upload', tag, *missing], check=True)
    release_inventory(state(), files, complete=True)
    verify_tag()
    completed = identity(request('releases/' + str(release_id), 'PATCH',
                         {'draft': False, 'prerelease': False, 'make_latest': 'true'}), release_id, original_target)
    require(completed.get('draft') is False and completed.get('prerelease') is False,
            'production release completion differs')


def source(revision, tag=None):
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and os.environ.get('GITHUB_SHA') == revision,
            'production controller source/repository differs')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    require(head == revision, 'production checkout differs')
    subprocess.run(['git', 'merge-base', '--is-ancestor', revision, 'origin/main'], check=True)
    if tag:
        manager_version.validate_release_target(Path(__file__).parents[2], tag)
        live_source(revision, tag)
        require(subprocess.check_output(['git', 'rev-parse', f'refs/tags/{tag}^{{commit}}'], text=True).strip() == revision,
                'production tag differs from approved source')
    else:
        require(os.environ.get('GITHUB_REF') == 'refs/heads/main', 'production preparation requires main')
        live_source(revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('qualify', 'seal', 'signed', 'source', 'compare', 'public', 'publish'))
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args(); revision = os.environ['GITHUB_SHA']
    if args.mode == 'qualify': qualification(args.paths[0], args.paths[1], revision, Path(args.paths[2]))
    elif args.mode == 'seal': seal_signed(Path(args.paths[0]), Path(args.paths[1]), revision)
    elif args.mode == 'signed':
        origin = os.environ.get('SIGNED_ORIGIN', 'hosted')
        require(origin in ('hosted', 'local'), 'unknown signing origin')
        if origin == 'local':
            from local_distribution import retained_local
            retained_local(args.paths[0], args.paths[1], revision, Path(args.paths[2]))
        else:
            signed(args.paths[0], args.paths[1], revision, Path(args.paths[2]))
    elif args.mode == 'source': source(revision, args.paths[0] if args.paths else None)
    elif args.mode == 'public':
        from registry_closure import registry_available
        manifest = json.loads(Path(args.paths[0]).read_text())['manifest']
        from component_publication import selected_image
        for provider in artifacts.PROVIDERS:
            for architecture in artifacts.ARCHITECTURES:
                require(registry_available(selected_image(manifest, provider, architecture), full=True),
                        'production public component graph unavailable')
    elif args.mode == 'publish':
        publish_release(Path(args.paths[0]), os.environ['GITHUB_REF_NAME'])
    else:
        expected = json.loads(Path(args.paths[0]).read_text())['manifest']
        observed = json.loads(Path(args.paths[1]).read_text())
        require(expected == observed, 'published component manifest differs from signed plan')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, tarfile.TarError, subprocess.SubprocessError):
        raise SystemExit('Production release refused: exact source, qualification, signing or artifact evidence failed') from None
