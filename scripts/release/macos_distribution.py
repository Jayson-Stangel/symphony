#!/usr/bin/env python3
"""Opt-in Developer ID distribution; ordinary packages never need credentials.

All Apple command output is private until reduced to allowlisted evidence. Never
include subprocess arguments or raw diagnostics in an exception or CI log.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
MACHO = {bytes.fromhex(magic) for magic in
         ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
REQUIRED = ('MACOS_SIGNING_P12_BASE64', 'MACOS_SIGNING_P12_PASSWORD',
            'MACOS_SIGNING_IDENTITY_SHA1', 'MACOS_TEAM_ID',
            'MACOS_NOTARY_APPLE_ID', 'MACOS_NOTARY_APP_PASSWORD')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*args, timeout=180, env=None):
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True,
                                text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError('distribution command timed out: ' + str(args[0])) from None
    require(result.returncode == 0, 'distribution command failed: ' + str(args[0]))
    return result.stdout + result.stderr


def checksum(path):
    with Path(path).open('rb') as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def credentials(env):
    missing = [name for name in REQUIRED if not env.get(name)]
    require(not missing, 'signed distribution requires: ' + ', '.join(missing))
    require(re.fullmatch(r'[A-Fa-f0-9]{40}', env['MACOS_SIGNING_IDENTITY_SHA1']),
            'MACOS_SIGNING_IDENTITY_SHA1 must be the exact certificate SHA-1')
    require(re.fullmatch(r'[A-Z0-9]{10}', env['MACOS_TEAM_ID']), 'invalid MACOS_TEAM_ID')


@contextmanager
def signing_keychain(env, directory):
    """Never change default keychain, search lists, trust, or an existing keychain."""
    keychain = directory / 'signing.keychain-db'
    p12 = directory / 'identity.p12'
    password = secrets.token_urlsafe(32)
    search_list = shlex.split(run('security', 'list-keychains', '-d', 'user'))
    try:
        try:
            p12.write_bytes(base64.b64decode(env['MACOS_SIGNING_P12_BASE64'], validate=True))
        except ValueError:
            raise ValueError('invalid MACOS_SIGNING_P12_BASE64') from None
        p12.chmod(0o600)
        run('security', 'create-keychain', '-p', password, keychain)
        run('security', 'set-keychain-settings', '-lut', '7200', keychain)
        run('security', 'unlock-keychain', '-p', password, keychain)
        run('security', 'import', p12, '-k', keychain, '-P', env['MACOS_SIGNING_P12_PASSWORD'],
            '-T', '/usr/bin/codesign')
        run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:',
            '-s', '-k', password, keychain)
        identities = run('security', 'find-identity', '-v', '-p', 'codesigning', keychain)
        identity = env['MACOS_SIGNING_IDENTITY_SHA1'].upper()
        team = env['MACOS_TEAM_ID']
        require(re.search(r'\b' + identity + r' "Developer ID Application: [^"\n]+ \('
                          + team + r'\)"', identities),
                'expected valid Developer ID Application identity/team missing from temporary keychain')
        p12.unlink()
        yield keychain
    finally:
        p12.unlink(missing_ok=True)
        # Run even when creation/import was interrupted; never name another keychain.
        try:
            if keychain.exists():
                run('security', 'delete-keychain', keychain)
        finally:
            current = shlex.split(run('security', 'list-keychains', '-d', 'user'))
            if current != search_list:
                run('security', 'list-keychains', '-d', 'user', '-s', *search_list)


def sign(path, keychain, env, runtime=True):
    identity = env['MACOS_SIGNING_IDENTITY_SHA1'].upper()
    args = ['codesign', '--force', '--sign', identity, '--keychain', keychain, '--timestamp']
    if runtime:
        args += ['--options', 'runtime']
    run(*args, path)
    run('codesign', '--verify', '--strict', path)
    details = run('codesign', '-d', '--verbose=4', path)
    require('Authority=Developer ID Application:' in details, 'Developer ID authority missing')
    require('TeamIdentifier=' + env['MACOS_TEAM_ID'] + '\n' in details, 'signed payload team differs')
    require(re.search(r'^Timestamp=.+', details, re.M), 'secure timestamp missing')
    cdhash = re.search(r'^CDHash=([a-f0-9]{40}|[a-f0-9]{64})$', details, re.M)
    require(cdhash, 'signed code directory hash missing')
    if runtime:
        require(re.search(r'^CodeDirectory .*flags=.*\bruntime\b', details, re.M),
                'hardened runtime missing')
        # This release needs no entitlements. Reject any unexpected added rights.
        entitlements = run('codesign', '-d', '--entitlements', ':-', path)
        require('<key>' not in entitlements, 'unexpected signing entitlements')
    # Bind the actual leaf certificate, not merely a matching display name/team.
    with tempfile.TemporaryDirectory(prefix='certificate-', dir=Path(keychain).parent) as temporary:
        prefix = Path(temporary) / 'cert'
        run('codesign', '-d', '--extract-certificates', prefix, path)
        certificate = Path(str(prefix) + '0').read_bytes()
        require(hashlib.sha1(certificate).hexdigest().upper() == identity,
                'signed payload certificate differs')
    return {'sha256': checksum(path), 'cdhash': cdhash.group(1), 'certificate_sha1': identity,
            'team_id': env['MACOS_TEAM_ID'], 'hardened_runtime': runtime,
            'secure_timestamp': True}


def sign_payload(bundle, keychain, env):
    helper = bundle / 'libexec/symphony-host'
    original = checksum(helper)
    natives = []
    for path in bundle.rglob('*'):
        if path.is_file():
            require(not path.is_symlink(), 'symlink in signing payload')
            with path.open('rb') as source:
                if source.read(4) in MACHO:
                    natives.append(path)
    # Explicit inventory: a future native addition requires deliberate signing policy.
    require(natives == [helper], 'unexpected native payload inventory')
    signed = sign(helper, keychain, env)
    revision = run(helper, 'build-revision').strip()
    require(revision == run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip(),
            'signed helper source differs')
    write_json(bundle / 'signing.json', {
        'status': 'developer-id-signed-not-yet-notarized', 'source_revision': revision,
        'original_qualified_helper_sha256': original, 'payloads': {'libexec/symphony-host': signed}})


def create_dmg(bundle, destination):
    """Same installer and Terminal entry point, carried in a mountable image."""
    with tempfile.TemporaryDirectory(prefix='dmg-content-') as temporary:
        content = Path(temporary)
        shutil.copytree(bundle, content / 'Symphony')
        (content / 'Read Me.txt').write_text(
            'Open Terminal and run the existing per-user installer:\n'
            '  /bin/bash "/Volumes/Symphony/Symphony/install.sh"\n'
            'Use the actual mounted volume path if macOS gave it another name.\n'
            'No sudo or host build tools are needed. Docker must be installed separately.\n'
            'After installation, eject this disk. Do not move or modify files within Symphony.\n'
            'Unsigned candidates are not evidence of Apple trust. Verify the distribution receipt.\n')
        run('hdiutil', 'create', '-fs', 'HFS+', '-format', 'UDZO', '-volname', 'Symphony',
            '-srcfolder', content, destination, timeout=300)
        run('hdiutil', 'verify', destination)


def submit(archive, keychain, env, evidence, label='notary'):
    profile = 'symphony-distribution'
    run('xcrun', 'notarytool', 'store-credentials', profile, '--keychain', keychain,
        '--apple-id', env['MACOS_NOTARY_APPLE_ID'], '--team-id', env['MACOS_TEAM_ID'],
        '--password', env['MACOS_NOTARY_APP_PASSWORD'])
    auth = ['--keychain-profile', profile, '--keychain', keychain]
    response = json.loads(run('xcrun', 'notarytool', 'submit', archive, *auth,
                              '--no-wait', '--output-format', 'json', timeout=600))
    submission = str(uuid.UUID(response['id']))
    receipt_path = evidence / (label + '-submission.json')
    receipt = {'id': submission, 'status': 'Submitted', 'submitted_sha256': checksum(archive)}
    write_json(receipt_path, receipt)
    # Persist the ID before waiting: Apple can keep processing after timeout.
    try:
        response = json.loads(run('xcrun', 'notarytool', 'wait', submission, *auth,
                                  '--timeout', '30m', '--output-format', 'json', timeout=1900))
    except ValueError:
        receipt['status'] = 'Wait failed or timed out; not accepted'
        write_json(receipt_path, receipt)
        raise
    require(str(uuid.UUID(response['id'])) == submission, 'notary wait returned another submission')
    status = response['status']
    require(status in ('Accepted', 'Invalid', 'Rejected', 'In Progress'), 'unknown notarization status')
    receipt['status'] = status
    write_json(receipt_path, receipt)
    log = json.loads(run('xcrun', 'notarytool', 'log', submission, *auth, '--output-format', 'json'))
    require(log.get('jobId') == submission and log.get('sha256') == checksum(archive),
            'Apple log does not bind submitted archive')
    require(log.get('status') in ('Accepted', 'Invalid', 'Rejected', 'In Progress')
            and type(log.get('statusCode')) is int, 'invalid Apple log status')
    # Omit upload URLs, arbitrary messages/paths and account details from Apple logs.
    write_json(evidence / (label + '-log.json'), {
        'id': submission, 'status': log.get('status'), 'statusCode': log.get('statusCode'),
        'sha256': log.get('sha256'), 'issues_count': len(log.get('issues') or [])})
    require(status == 'Accepted' and log.get('status') == 'Accepted', 'Apple did not accept distribution')
    return submission


def notarize(dmg, keychain, env, evidence):
    submission = submit(dmg, keychain, env, evidence)
    run('xcrun', 'stapler', 'staple', dmg)
    run('xcrun', 'stapler', 'validate', dmg)
    run('codesign', '--verify', '--strict', dmg)
    run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4', dmg)
    return submission


def build(manifest, helper, output, env):
    credentials(env)  # Refuse before touching keychains or invoking unqualified code.
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'requires native Apple Silicon macOS')
    require(not output.exists(), 'signed output must be a new directory')
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip()
    require(not run('git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=all').strip(),
            'signed distribution requires a clean committed checkout')
    require(helper.is_file() and not helper.is_symlink(), 'qualified helper must be a regular file')
    original = checksum(helper)
    output.mkdir(parents=True)
    evidence = output / 'evidence'
    evidence.mkdir()
    # Outputs stay private until all signatures, notarization and assessment pass.
    with tempfile.TemporaryDirectory(prefix='symphony-signing-', dir=env.get('RUNNER_TEMP')) as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        with signing_keychain(env, private) as keychain:
            package = private / 'packages'
            child_env = dict(env, SYMPHONY_MACOS_KEYCHAIN=str(keychain))
            run('bash', ROOT / 'scripts/release/build-manager-package.sh', manifest, package,
                'macos', ROOT, revision, helper, env=child_env, timeout=600)
            archives = list(package.glob('*.tar.gz'))
            require(len(archives) == 1, 'expected exactly one manager archive')
            archive = archives[0]
            bundle = private / 'bundle'
            bundle.mkdir()
            run('tar', '-xzf', archive, '-C', bundle)
            receipt = json.loads((bundle / 'signing.json').read_text())
            require(receipt['original_qualified_helper_sha256'] == original,
                    'qualified helper binding differs')
            run(bundle / 'libexec/symphony-host', 'verify-files', bundle)
            # Apple's custom-installer guidance calls for payload qualification
            # before packaging/notarizing the outer distribution. Bare command
            # line binaries receive tickets but cannot themselves be stapled.
            payload_zip = private / 'payload.zip'
            run('ditto', '-c', '-k', '--keepParent', bundle, payload_zip)
            payload_submission = submit(payload_zip, keychain, env, evidence, 'payload')
            run('codesign', '-vvvv', '-R=notarized', '--check-notarization', bundle / 'libexec/symphony-host')
            dmg = private / (archive.name.removesuffix('.tar.gz') + '.dmg')
            create_dmg(bundle, dmg)
            signed_dmg = sign(dmg, keychain, env, runtime=False)
            submission = notarize(dmg, keychain, env, evidence)
            # Notarization covers the identical native code also shipped in tar.gz.
            run('codesign', '-vvvv', '-R=notarized', '--check-notarization', bundle / 'libexec/symphony-host')
            require(checksum(helper) == original, 'original qualified helper was modified')
            distribution = {
                'status': 'notarized-stapled-assessed', 'source_revision': revision,
                'original_qualified_helper_sha256': original, 'payloads': receipt['payloads'],
                'notary_submission_id': submission, 'payload_notary_submission_id': payload_submission,
                'dmg_signature': signed_dmg,
                'artifacts': {path.name: checksum(path) for path in (archive, dmg)},
                'updater': 'tar.gz; same notarized native code; online ticket lookup required',
            }
            # Preserve exact internal metadata (runner identities and manager core).
            shutil.copyfile(manifest, output / 'internal-release.json')
            core = manifest.parent / 'manager-core.json'
            if core.exists():
                shutil.copyfile(core, output / core.name)
            shutil.copyfile(package / 'package-source.txt', evidence / 'package-source.txt')
            for path in (archive, dmg):
                shutil.copyfile(path, output / path.name)
        # Cleanup failure also refuses readiness. This is deliberately last.
        write_json(output / 'macos-distribution.json', distribution)
        (output / 'SHA256SUMS').write_text(''.join(
            f'{digest}  {name}\n' for name, digest in distribution['artifacts'].items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    signed = commands.add_parser('build')
    signed.add_argument('manifest', type=Path)
    signed.add_argument('helper', type=Path)
    signed.add_argument('output', type=Path)
    signed.add_argument('--authorize-apple-upload', action='store_true', required=True,
                        help='operator has approved this exact source/candidate and credential use')
    payload = commands.add_parser('sign-payload')
    payload.add_argument('bundle', type=Path)
    unsigned = commands.add_parser('unsigned-dmg')
    unsigned.add_argument('bundle', type=Path)
    unsigned.add_argument('output', type=Path)
    args = parser.parse_args()
    # SIGTERM/SIGINT unwind keychain/files, including Actions cancellation.
    def interrupted(signum, frame):
        raise ValueError('distribution interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.command == 'build':
            build(args.manifest.resolve(), args.helper.resolve(), args.output.resolve(), os.environ)
        elif args.command == 'sign-payload':
            credentials(os.environ)
            sign_payload(args.bundle, Path(os.environ['SYMPHONY_MACOS_KEYCHAIN']), os.environ)
        else:
            require(not args.output.exists(), 'DMG destination already exists')
            create_dmg(args.bundle, args.output)
            print('UNSIGNED DMG: construction only; no Developer ID or notarization acceptance')
    except (ValueError, OSError, KeyError) as error:
        # Do not print potentially secret-bearing OS errors/JSON from child processes.
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__
        parser.exit(1, 'macOS distribution refused: ' + message + '\n')


if __name__ == '__main__':
    main()
