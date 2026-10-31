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
import stat
import subprocess
import sys
import tempfile
import tarfile
import zipfile
import time

import package_diagnostics as package_evidence

ROOT = Path(__file__).resolve().parents[2]
MACHO = {bytes.fromhex(magic) for magic in
         ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
REQUIRED = ('MACOS_SIGNING_P12_BASE64', 'MACOS_SIGNING_P12_PASSWORD',
            'MACOS_SIGNING_IDENTITY_SHA1', 'MACOS_TEAM_ID',
            'MACOS_NOTARY_APPLE_ID', 'MACOS_NOTARY_APP_PASSWORD')
NOTARIZATION_STAGES = ('notarization.payload-helper', 'notarization.dmg-helper')
CLEANUP_RECORD = '.keychain-cleanup.json'
NOTARIZATION_REASONS = package_evidence.REASONS | {'signature-invalid', 'notarization-requirement-unmet',
    'notarization-lookup-unavailable', 'verification-timeout', 'signature-policy-failed',
    'retained-binding-changed', 'acceptance-binding-missing', 'verification-interrupted'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def notarization_id(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', value),
            'invalid Apple submission identifier')
    return value.lower()


class CommandFailure(ValueError):
    """Only fixed labels, reasons and numeric process status leave this boundary."""
    def __init__(self, stage, outcome, returncode=None, reason=None):
        self.outcome = outcome
        self.returncode = returncode
        self.reason = reason
        suffix = f' (exit {returncode})' if returncode is not None else ''
        super().__init__(f'distribution command {outcome}: {stage}{suffix}')


def codesign_failure_reason(output):
    """Reduce private command output to a fixed category; never retain excerpts."""
    # Prefer specific evidence over the generic Security framework error.
    for token, reason in (
        ('unable to build chain to self-signed root', 'certificate-chain'),
        ('CSSMERR_TP_NOT_TRUSTED', 'certificate-trust'),
        ('CSSMERR_TP_CERT_EXPIRED', 'certificate-expired'),
        ('User interaction is not allowed', 'interaction-denied'),
        ('errSecInteractionNotAllowed', 'interaction-denied'),
        ('The specified item could not be found in the keychain', 'identity-unavailable'),
        ('no identity found', 'identity-unavailable'),
        ('The timestamp service is not available', 'timestamp-unavailable'),
        ('A timestamp was expected but was not found', 'timestamp-missing'),
        ('errSecInternalComponent', 'security-internal'),
    ):
        if token.casefold() in output.casefold():
            return reason
    return 'unclassified'


def notarization_failure_reason(output):
    """Classify only; Accepted is not proof that a ticket lookup has propagated."""
    lower = output.casefold()
    for token in ('code or signature have been modified', 'invalid signature',
                  'a sealed resource is missing or invalid', 'code object is not signed at all'):
        if token in lower:
            return 'signature-invalid'
    reason = codesign_failure_reason(output)
    if reason != 'unclassified':
        return reason
    if any(token in lower for token in ('nsurlerrordomain', 'could not connect to server',
                                        'the network connection was lost')):
        return 'notarization-lookup-unavailable'
    if 'code failed to satisfy specified code requirement(s)' in lower:
        return 'notarization-requirement-unmet'
    return 'unclassified'


def run(*args, timeout=180, env=None, stage=None):
    label = stage or str(args[0])
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True,
                                text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise CommandFailure(label, 'timed out', reason='verification-timeout' if stage in NOTARIZATION_STAGES else None) from None
    except OSError:
        if stage:
            raise CommandFailure(label, 'could not start') from None
        raise
    if result.returncode != 0:
        reason = (notarization_failure_reason(result.stdout + result.stderr) if stage in NOTARIZATION_STAGES else
                  codesign_failure_reason(result.stdout + result.stderr) if stage == 'signature.sign' else None)
        raise CommandFailure(label, 'failed', result.returncode, reason)
    return result.stdout + result.stderr


class KeychainDiagnostics:
    """An allowlisted journal: no argv, output, exception text, identity or paths."""
    def __init__(self, evidence):
        self.path = evidence / 'keychain-diagnostics.json' if evidence else None
        self.operations = []
        self.write_failed = False

    def operation(self, stage, action):
        record = {'stage': stage, 'status': 'running'}
        self.operations.append(record)
        self.save()
        try:
            result = action()
        except BaseException as error:
            record['status'] = 'failed'
            if isinstance(error, CommandFailure):
                record['outcome'] = error.outcome
                if error.returncode is not None:
                    record['exit_status'] = error.returncode
            elif isinstance(error, (KeyboardInterrupt, SystemExit)):
                record['outcome'] = 'interrupted'
            else:
                record['outcome'] = 'operation refused'
            self.save()
            raise
        record['status'] = 'success'
        self.save()
        return result

    def command(self, stage, *args):
        return self.operation(stage, lambda: run(*args, stage=stage))

    def save(self):
        if self.path:
            try:
                write_json(self.path, {'schema': 1, 'operations': self.operations})
            except OSError:
                # Evidence I/O must never prevent credential cleanup or mask its cause.
                self.write_failed = True


def checksum(path):
    with Path(path).open('rb') as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


class SignedCheckpoint:
    """Private, failure-preserving bytes; never a ready/public distribution."""
    def __init__(self, output, revision, manifest, original_helper):
        self.path = output.with_name(output.name + '.checkpoint')
        self.path.mkdir(mode=0o700)  # Exclusive: never resume or overwrite another attempt.
        info = self.path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe private signing checkpoint')
        self.sync_directory(self.path.parent)
        self.value = {'schema': 1, 'status': 'not-ready', 'source_revision': revision,
                      'input_manifest_sha256': checksum(manifest),
                      'original_qualified_helper_sha256': original_helper,
                      'artifacts': {}, 'submissions': {}, 'checks': []}
        self.save()

    @staticmethod
    def sync_directory(path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def save(self):
        descriptor, name = tempfile.mkstemp(prefix='.checkpoint-', dir=self.path)
        try:
            with os.fdopen(descriptor, 'w') as target:
                json.dump(self.value, target, indent=2, sort_keys=True)
                target.write('\n'); target.flush(); os.fsync(target.fileno())
            os.replace(name, self.path / 'checkpoint.json')
            self.sync_directory(self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def retain(self, archive, payload_zip, helper):
        for name, original in (('signed-updater.tar.gz', archive), ('payload.zip', payload_zip)):
            require(original.is_file() and not original.is_symlink(), 'invalid checkpoint source')
            target = self.path / name
            with original.open('rb') as source, target.open('xb') as destination:
                os.chmod(target, 0o600)
                shutil.copyfileobj(source, destination)
                destination.flush(); os.fsync(destination.fileno())
            digest = checksum(original)
            require(checksum(target) == digest, 'private checkpoint copy differs')
            self.value['artifacts'][name] = {'sha256': digest}
        self.value['signed_helper_sha256'] = checksum(helper)
        self.save()  # Any retention failure stops before the first Apple upload.

    @classmethod
    def load(cls, path, revision, manifest, original_helper, digest, context):
        """Validate retained state without mutating it or consuming credentials."""
        info = path.lstat()
        require(path.resolve() == path and stat.S_ISDIR(info.st_mode)
                and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'unsafe original checkpoint directory')
        require(not os.path.lexists(path / '.continuation-claimed'),
                'original checkpoint already continued; use its retained child or reconcile the incomplete handoff')
        record = path / 'checkpoint.json'
        info = record.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024 * 1024
                and re.fullmatch(r'[a-f0-9]{64}', digest or '') and checksum(record) == digest,
                'original checkpoint digest or ownership differs')
        original = cls.__new__(cls); original.path = path
        original.value = json.loads(record.read_text())
        value = original.value
        require(isinstance(value, dict) and type(value.get('schema')) is int
                and value.get('schema') == 1 and value.get('status') == 'not-ready'
                and value.get('source_revision') == revision
                and value.get('input_manifest_sha256') == checksum(manifest)
                and value.get('original_qualified_helper_sha256') == original_helper,
                'checkpoint source or qualified helper differs')
        require(set(value) <= {'schema', 'status', 'source_revision', 'input_manifest_sha256',
                'original_qualified_helper_sha256', 'artifacts', 'submissions', 'checks', 'signed_helper_sha256',
                'context', 'archive_name', 'intents', 'dmg_state', 'dmg_signature', 'continuations'},
                'unknown checkpoint fields')
        previous = value.get('context', {})
        require(isinstance(previous, dict) and previous and {k: v for k, v in previous.items() if k != 'origin'} ==
                {k: v for k, v in context.items() if k != 'origin'},
                'checkpoint qualification or signing identity differs')
        require(isinstance(previous.get('origin'), dict) and previous['origin'].get('kind') == context['origin']['kind'],
                'checkpoint signing origin differs')
        origin = previous['origin']
        require(origin == {'kind': 'local'} or set(origin) == {'kind', 'repository', 'workflow', 'run_id', 'run_attempt'}
                and origin['kind'] == 'actions' and origin['repository'] == 'Jayson-Stangel/symphony'
                and origin['workflow'] in ('Jayson-Stangel/symphony/.github/workflows/macos-production-signing.yml',
                                           'Jayson-Stangel/symphony/.github/workflows/macos-signing.yml')
                and all(type(origin[k]) is int and origin[k] > 0 for k in ('run_id', 'run_attempt')),
                'invalid original signing origin')
        require(isinstance(value.get('artifacts'), dict) and isinstance(value.get('checks'), list)
                and isinstance(value.get('continuations', []), list) and len(value.get('continuations', [])) < 64,
                'invalid checkpoint inventory')
        for check in value['checks']:
            require(isinstance(check, dict) and set(check) <= {'stage', 'attempt', 'status', 'outcome', 'reason', 'exit_status'}
                    and check.get('stage') in NOTARIZATION_STAGES and type(check.get('attempt')) is int
                    and 1 <= check['attempt'] <= 3 and check.get('status') in ('success', 'failed')
                    and check.get('reason', 'unclassified') in NOTARIZATION_REASONS | {'unclassified'}
                    and check.get('outcome') in (None, 'failed', 'timed out', 'could not start', 'interrupted')
                    and ('exit_status' not in check or type(check['exit_status']) is int),
                    'invalid retained verification diagnostic')
        require({'package-source.txt', 'package-diagnostics.json'} <= set(value['artifacts'])
                and value.get('archive_name') == context['archive_name'], 'incomplete checkpoint package evidence')
        original.verify_bytes()
        require(set(p.name for p in path.iterdir()) == {'checkpoint.json', *value['artifacts']},
                'incomplete or unexpected checkpoint files; reconcile original attempt')
        require(value.get('dmg_state') not in ('preparing', 'stapling'),
                'interrupted DMG mutation; reconcile retained original before continuing')
        require(value.get('dmg_state') in (None, 'signed', 'stapled')
                and ('stapled.dmg' not in value['artifacts'] or
                     'outer.dmg' in value['artifacts'] and value.get('dmg_state') == 'stapled'),
                'inconsistent retained DMG stage')
        require(isinstance(value.get('intents', {}), dict) and isinstance(value.get('submissions'), dict)
                and set(value.get('intents', {})) <= {'payload', 'notary'}
                and set(value['submissions']) <= {'payload', 'notary'}, 'invalid checkpoint submission inventory')
        for label, receipt in value['submissions'].items():
            name = 'payload.zip' if label == 'payload' else 'outer.dmg'
            require(isinstance(receipt, dict) and set(receipt) == {'id', 'status', 'submitted_sha256'}
                    and notarization_id(receipt['id']) == receipt['id']
                    and receipt['status'] in ('Submitted', 'Accepted', 'Invalid', 'Rejected', 'In Progress',
                                              'Wait failed or timed out; not accepted')
                    and receipt['submitted_sha256'] == value['artifacts'].get(name, {}).get('sha256'),
                    'invalid retained submission ID/status/hash')
        for label, intent in value.get('intents', {}).items():
            require(isinstance(intent, str) and re.fullmatch(r'[a-f0-9]{64}', intent)
                    and value['submissions'].get(label, {}).get('submitted_sha256') == intent,
                    'submission outcome uncertain; reconcile Apple ID before continuing; never resubmit')
        return original

    @classmethod
    def reopen(cls, path, output, revision, manifest, original_helper, digest, context, admission=None):
        """Copy an explicitly admitted original into a fresh continuation attempt."""
        original = cls.load(path, revision, manifest, original_helper, digest, context)
        value = original.value; previous = value['context']
        require(not os.path.lexists(output) and not os.path.lexists(output.with_name(output.name + '.checkpoint')),
                'continuation output or checkpoint already exists')
        if context['origin']['kind'] == 'actions':
            require(admission and admission.get('origin') == previous['origin']
                    and admission.get('checkpoint_sha256') == digest
                    and re.fullmatch(r'sha256:[a-f0-9]{64}', admission.get('artifact', {}).get('digest', '')),
                    'authenticated original Actions checkpoint admission required')
        else:
            require(admission is None, 'local continuation cannot claim Actions artifact admission')
            # A fresh output alone cannot prevent replay of an immutable local
            # parent. One exclusive, private claim survives failed handoffs and
            # prevents a second branch from consuming the same DMG submission.
            claim = path / '.continuation-claimed'; claim.mkdir(mode=0o700)
            cls.sync_directory(path)
            descriptor = os.open(claim / 'successor.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as target:
                json.dump({'parent_checkpoint_sha256': digest,
                           'successor_checkpoint': str(output.with_name(output.name + '.checkpoint').absolute())}, target)
                target.flush(); os.fsync(target.fileno())
            cls.sync_directory(claim)
        current = cls(output, revision, manifest, original_helper)
        current.value = json.loads(json.dumps(value))
        for name in value['artifacts']:
            target = current.path / name
            shutil.copyfile(path / name, target); target.chmod(0o600)
            with target.open('rb') as source: os.fsync(source.fileno())
        current.value['context'] = context
        current.value.setdefault('continuations', []).append({
            'checkpoint_sha256': digest, 'origin': previous['origin'], 'admission': admission})
        require(len(current.value['continuations']) <= 64, 'continuation inventory exceeds bound')
        current.save(); current.verify_bytes()
        return current

    def retain_file(self, name, original):
        require(name in ('outer.dmg', 'package-source.txt', 'package-diagnostics.json'),
                'unknown retained signing file')
        target = self.path / name
        require(original.is_file() and not original.is_symlink() and not os.path.lexists(target),
                'invalid retained signing source or existing file')
        with original.open('rb') as source, target.open('xb') as destination:
            os.chmod(target, 0o600); shutil.copyfileobj(source, destination)
            destination.flush(); os.fsync(destination.fileno())
        self.value['artifacts'][name] = {'sha256': checksum(target)}
        self.save()

    def submission(self, label, receipt):
        require(label in ('payload', 'notary'), 'unknown checkpoint submission')
        require(notarization_id(receipt['id']) == receipt['id'] and
                re.fullmatch(r'[a-f0-9]{64}', receipt['submitted_sha256']) and
                receipt['status'] in ('Submitted', 'Accepted', 'Invalid', 'Rejected', 'In Progress',
                                      'Wait failed or timed out; not accepted'), 'invalid checkpoint submission')
        if label == 'payload':
            require(receipt['submitted_sha256'] == self.value['artifacts']['payload.zip']['sha256'],
                    'payload submission differs from retained bytes')
        previous = self.value['submissions'].get(label)
        if previous:
            require(previous['id'] == receipt['id'] and previous['submitted_sha256'] == receipt['submitted_sha256']
                    and (previous['status'] != 'Accepted' or receipt['status'] == 'Accepted'),
                    'conflicting or regressed original Apple submission')
        self.value['submissions'][label] = dict(receipt)
        self.save()

    def verify_bytes(self):
        require({'signed-updater.tar.gz', 'payload.zip'} <= set(self.value['artifacts'])
                <= {'signed-updater.tar.gz', 'payload.zip', 'outer.dmg', 'stapled.dmg',
                    'package-source.txt', 'package-diagnostics.json'}
                and re.fullmatch(r'[a-f0-9]{64}', self.value.get('signed_helper_sha256', '')),
                'incomplete retained signing bytes')
        for name, entry in self.value['artifacts'].items():
            require(isinstance(entry, dict) and set(entry) == {'sha256'}, 'invalid retained artifact receipt')
            path = self.path / name
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 512 * 1024 * 1024
                    and re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')) and checksum(path) == entry['sha256'],
                    'retained signing bytes changed')

    def check(self, stage, attempt, status, error=None):
        require(stage in NOTARIZATION_STAGES and 1 <= attempt <= 3, 'invalid verification stage/attempt')
        item = {'stage': stage, 'attempt': attempt, 'status': status}
        if isinstance(error, CommandFailure):
            item.update(outcome=error.outcome if error.outcome in ('failed', 'timed out', 'could not start', 'interrupted') else 'failed',
                        reason=error.reason if error.reason in NOTARIZATION_REASONS else 'unclassified')
            if type(error.returncode) is int:
                item['exit_status'] = error.returncode
        self.value['checks'].append(item)
        self.save()


def credentials(env):
    missing = [name for name in REQUIRED if not env.get(name)]
    require(not missing, 'signed distribution requires: ' + ', '.join(missing))
    require(re.fullmatch(r'[A-Fa-f0-9]{40}', env['MACOS_SIGNING_IDENTITY_SHA1']),
            'MACOS_SIGNING_IDENTITY_SHA1 must be the exact certificate SHA-1')
    require(re.fullmatch(r'[A-Z0-9]{10}', env['MACOS_TEAM_ID']), 'invalid MACOS_TEAM_ID')


@contextmanager
def signing_directory(env):
    """Keep the private recovery record alive if credential cleanup is uncertain."""
    private = Path(tempfile.mkdtemp(prefix='symphony-signing-', dir=env.get('RUNNER_TEMP'))).resolve()
    try:
        yield private
    finally:
        if not os.path.lexists(private / CLEANUP_RECORD):
            shutil.rmtree(private)


@contextmanager
def signing_keychain(env, directory, evidence=None):
    """Preserve the primary failure and try every owned-resource cleanup step."""
    keychain = directory / 'signing.keychain-db'
    p12 = directory / 'identity.p12'
    recovery = directory / CLEANUP_RECORD
    info = directory.lstat()
    require(directory.resolve() == directory and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'unsafe private signing directory')
    require(not any(os.path.lexists(path) for path in (recovery, keychain, p12)),
            'unreconciled signing credentials; inspect original private directory')
    password = secrets.token_urlsafe(32)
    diagnostics = KeychainDiagnostics(evidence)
    search_list = None
    primary_failure = False
    cleanup_errors = []
    try:
        search_list = shlex.split(diagnostics.command(
            'keychain.snapshot-search-list', 'security', 'list-keychains', '-d', 'user'))
        require(len(search_list) <= 64 and len(set(search_list)) == len(search_list)
                and str(keychain) not in search_list
                and all(isinstance(path, str) and Path(path).is_absolute() and len(path) <= 4096
                        and not any(c in path for c in ('\x00', '\n', '\r')) for path in search_list),
                'unsafe signing search list')
        # Fixed private metadata, not an artifact. The existing job hook computes
        # deletion targets itself and may only restore paths it already observes.
        encoded = json.dumps({'schema': 1, 'search_list': search_list}).encode()
        require(len(encoded) <= 65536, 'signing recovery metadata exceeds bound')
        descriptor = os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(encoded)
            target.flush(); os.fsync(target.fileno())
        SignedCheckpoint.sync_directory(directory)  # Before even creating P12.

        def decode_identity():
            try:
                p12.write_bytes(base64.b64decode(env['MACOS_SIGNING_P12_BASE64'], validate=True))
            except ValueError:
                raise ValueError('invalid MACOS_SIGNING_P12_BASE64') from None
            p12.chmod(0o600)

        diagnostics.operation('keychain.decode-p12', decode_identity)
        diagnostics.command('keychain.create', 'security', 'create-keychain', '-p', password, keychain)
        diagnostics.command('keychain.settings', 'security', 'set-keychain-settings', '-lut', '7200', keychain)
        diagnostics.command('keychain.unlock', 'security', 'unlock-keychain', '-p', password, keychain)
        # --keychain narrows identity selection, but codesign still requires the
        # identity keychain on the user's search list (including chain lookup).
        # Preserve all original entries and restore the exact snapshot in finally.
        expected_search_list = [str(keychain), *search_list]
        diagnostics.command('keychain.configure-search-list', 'security', 'list-keychains',
                            '-d', 'user', '-s', *expected_search_list)

        def verify_search_list_visibility():
            observed = shlex.split(diagnostics.command(
                'keychain.read-search-list', 'security', 'list-keychains', '-d', 'user'))
            require(observed == expected_search_list, 'temporary signing keychain search list differs')

        diagnostics.operation('keychain.verify-search-list', verify_search_list_visibility)
        diagnostics.command('keychain.import-p12', 'security', 'import', p12, '-k', keychain,
                            '-P', env['MACOS_SIGNING_P12_PASSWORD'], '-T', '/usr/bin/codesign')
        diagnostics.command('keychain.partition-list', 'security', 'set-key-partition-list',
                            '-S', 'apple-tool:,apple:,codesign:', '-s', '-k', password, keychain)
        identities = diagnostics.command('keychain.find-identity', 'security', 'find-identity',
                                         '-v', '-p', 'codesigning', keychain)
        identity = env['MACOS_SIGNING_IDENTITY_SHA1'].upper()
        team = env['MACOS_TEAM_ID']
        diagnostics.operation('keychain.match-pinned-identity', lambda: require(
            re.search(r'\b' + identity + r' "Developer ID Application: [^"\n]+ \('
                      + team + r'\)"', identities),
            'expected valid Developer ID Application identity/team missing from temporary keychain'))
        diagnostics.operation('keychain.remove-import-file', p12.unlink)
        yield keychain
    except BaseException:
        primary_failure = True
        raise
    finally:
        def cleanup(stage, action):
            try:
                diagnostics.operation(stage, action)
            except BaseException as error:
                cleanup_errors.append(error)

        cleanup('cleanup.remove-p12', lambda: p12.unlink(missing_ok=True))
        cleanup('cleanup.verify-p12-absent', lambda: require(not p12.exists(), 'P12 cleanup incomplete'))
        def delete_keychain():
            if keychain.exists():
                run('security', 'delete-keychain', keychain, stage='cleanup.delete-keychain')

        cleanup('cleanup.delete-keychain', delete_keychain)
        cleanup('cleanup.verify-keychain-absent', lambda: require(
            not keychain.exists(), 'temporary keychain cleanup incomplete'))

        if search_list is not None:
            current = {}

            def inspect_search_list():
                current['value'] = shlex.split(run('security', 'list-keychains', '-d', 'user',
                                                  stage='cleanup.inspect-search-list'))

            cleanup('cleanup.inspect-search-list', inspect_search_list)
            # Never overwrite a concurrent user's list mutation or restore from
            # unobserved state. Leave the recovery record for fail-closed handling.
            cleanup('cleanup.check-search-list', lambda: require(
                current.get('value') in (search_list, [str(keychain), *search_list]),
                'search list changed; private signing cleanup requires reconciliation'))
            if current.get('value') == [str(keychain), *search_list]:
                cleanup('cleanup.restore-search-list', lambda: run(
                    'security', 'list-keychains', '-d', 'user', '-s', *search_list,
                    stage='cleanup.restore-search-list'))

            def verify_search_list():
                restored = shlex.split(diagnostics.command(
                    'cleanup.read-restored-search-list', 'security', 'list-keychains', '-d', 'user'))
                require(restored == search_list, 'keychain search list cleanup incomplete')

            cleanup('cleanup.verify-search-list-restored', verify_search_list)
        if not cleanup_errors:
            cleanup('cleanup.remove-recovery-record', lambda: recovery.unlink(missing_ok=True))
            cleanup('cleanup.sync-private-directory', lambda: SignedCheckpoint.sync_directory(directory))
        if diagnostics.write_failed:
            cleanup_errors.append(ValueError('keychain diagnostics could not be retained'))
        if cleanup_errors and not primary_failure:
            # Never expose arbitrary OS/exception text from cleanup.
            first = cleanup_errors[0]
            if isinstance(first, CommandFailure):
                raise first
            raise ValueError('keychain cleanup failed; see sanitized keychain diagnostics') from None


def sign(path, keychain, env, runtime=True):
    identity = env['MACOS_SIGNING_IDENTITY_SHA1'].upper()
    args = ['codesign', '--force', '--sign', identity, '--keychain', keychain, '--timestamp']
    if runtime:
        args += ['--options', 'runtime']
    with package_evidence.stage('signature.sign'):
        run(*args, path, stage='signature.sign')
    return inspect_signature(path, env, Path(keychain).parent, runtime)


def inspect_signature(path, env, certificate_parent, runtime=True, deadline=None, clock=time.monotonic):
    """Read-only signature/certificate policy, shared with bounded ticket checks."""
    identity = env['MACOS_SIGNING_IDENTITY_SHA1'].upper()
    def checked(*args, stage):
        timeout = 180
        if deadline is not None:
            remaining = deadline - clock()
            if remaining <= 0:
                raise CommandFailure(stage, 'timed out', reason='verification-timeout')
            timeout = min(20, remaining)
        return run(*args, stage=stage, timeout=timeout)
    with package_evidence.stage('signature.verify'):
        checked('codesign', '--verify', '--strict', path, stage='signature.verify')
    with package_evidence.stage('signature.inspect'):
        details = checked('codesign', '-d', '--verbose=4', path, stage='signature.inspect')
    with package_evidence.stage('signature.policy'):
        require('Authority=Developer ID Application:' in details, 'Developer ID authority missing')
        require('TeamIdentifier=' + env['MACOS_TEAM_ID'] + '\n' in details, 'signed payload team differs')
        require(re.search(r'^Timestamp=.+', details, re.M), 'secure timestamp missing')
        cdhash = re.search(r'^CDHash=([a-f0-9]{40}|[a-f0-9]{64})$', details, re.M)
        require(cdhash, 'signed code directory hash missing')
        if runtime:
            require(re.search(r'^CodeDirectory .*flags=.*\bruntime\b', details, re.M),
                    'hardened runtime missing')
    if runtime:
        # This release needs no entitlements. Reject any unexpected added rights.
        with package_evidence.stage('signature.entitlements'):
            entitlements = checked('codesign', '-d', '--entitlements', ':-', path, stage='signature.entitlements')
            require('<key>' not in entitlements, 'unexpected signing entitlements')
    # Bind the actual leaf certificate, not merely a matching display name/team.
    with package_evidence.stage('signature.certificate'), tempfile.TemporaryDirectory(prefix='certificate-', dir=certificate_parent) as temporary:
        prefix = Path(temporary) / 'cert'
        checked('codesign', '-d', '--extract-certificates=' + str(prefix), path, stage='signature.certificate')
        certificate = Path(str(prefix) + '0').read_bytes()
        require(hashlib.sha1(certificate).hexdigest().upper() == identity,
                'signed payload certificate differs')
    return {'sha256': checksum(path), 'cdhash': cdhash.group(1), 'certificate_sha1': identity,
            'team_id': env['MACOS_TEAM_ID'], 'hardened_runtime': runtime,
            'secure_timestamp': True}


def verify_notarized_helper(helper, env, checkpoint, stage, clock=time.monotonic, pause=time.sleep):
    """At most three read-only checks in 60s, never another sign or submission."""
    require(stage in NOTARIZATION_STAGES, 'unknown notarization verification stage')
    accepted = checkpoint.value['submissions'].get('payload', {})
    if not (accepted.get('status') == 'Accepted' and
            accepted.get('submitted_sha256') == checkpoint.value['artifacts'].get('payload.zip', {}).get('sha256') and
            (stage != 'notarization.dmg-helper' or checkpoint.value['submissions'].get('notary', {}).get('status') == 'Accepted')):
        error = CommandFailure(stage, 'failed', reason='acceptance-binding-missing')
        checkpoint.check(stage, 1, 'failed', error)
        raise error
    deadline = clock() + 60
    expected = checkpoint.value['signed_helper_sha256']
    for attempt in range(1, 4):
        try:
            try:
                checkpoint.verify_bytes()
                require(checksum(helper) == expected, 'signed helper changed before ticket check')
            except (ValueError, OSError):
                raise CommandFailure(stage, 'failed', reason='retained-binding-changed') from None
            try:
                inspect_signature(helper, env, helper.parent, deadline=deadline, clock=clock)
            except (ValueError, OSError) as error:
                reason = 'verification-timeout' if getattr(error, 'outcome', None) == 'timed out' else 'signature-policy-failed'
                raise CommandFailure(stage, getattr(error, 'outcome', 'failed'), getattr(error, 'returncode', None), reason) from None
            remaining = deadline - clock()
            if remaining <= 0:
                raise CommandFailure(stage, 'timed out', reason='verification-timeout')
            run('codesign', '-vvvv', '-R=notarized', '--check-notarization', helper,
                stage=stage, timeout=min(20, remaining))
            try:
                unchanged = checksum(helper) == expected
            except OSError:
                unchanged = False
            if not unchanged:
                raise CommandFailure(stage, 'failed', reason='retained-binding-changed')
            if clock() > deadline:
                raise CommandFailure(stage, 'timed out', reason='verification-timeout')
        except CommandFailure as error:
            try:
                unchanged = checksum(helper) == expected
            except OSError:
                unchanged = False
            if not unchanged:
                error = CommandFailure(stage, 'failed', reason='retained-binding-changed')
            checkpoint.check(stage, attempt, 'failed', error)
            remaining = deadline - clock()
            if error.reason != 'notarization-requirement-unmet' or attempt == 3 or remaining <= 0:
                raise error
            pause(min(5, remaining))
        except (KeyboardInterrupt, SystemExit, ValueError, OSError) as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            checkpoint.check(stage, attempt, 'failed', CommandFailure(stage,
                'interrupted' if interrupted else 'failed',
                reason='verification-interrupted' if interrupted else 'unclassified'))
            raise
        else:
            checkpoint.check(stage, attempt, 'success')
            return


def sign_payload(bundle, keychain, env):
    with package_evidence.stage('payload.inventory'):
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
    with package_evidence.stage('payload.revision'):
        revision = run(helper, 'build-revision', stage='payload.revision').strip()
        require(revision == run('git', '-C', ROOT, 'rev-parse', 'HEAD', stage='payload.revision').strip(),
                'signed helper source differs')
    with package_evidence.stage('payload.receipt'):
        write_json(bundle / 'signing.json', {
            'status': 'developer-id-signed-not-yet-notarized', 'source_revision': revision,
            'original_qualified_helper_sha256': original, 'payloads': {'libexec/symphony-host': signed}})


INSTALL_INSTRUCTIONS = (
    'Open Terminal and run the existing per-user installer:\n'
    '  /bin/bash "/Volumes/Symphony/Symphony/install.sh"\n'
    'Use the actual mounted volume path if macOS gave it another name.\n'
    'No sudo or host build tools are needed. Docker must be installed separately.\n'
    'After installation, eject this disk. Do not move or modify files within Symphony.\n'
    'Unsigned candidates are not evidence of Apple trust. Verify the distribution receipt.\n')


def create_dmg(bundle, destination):
    """Same installer and Terminal entry point, carried in a mountable image."""
    with tempfile.TemporaryDirectory(prefix='dmg-content-') as temporary:
        content = Path(temporary)
        shutil.copytree(bundle, content / 'Symphony')
        (content / 'Read Me.txt').write_text(INSTALL_INSTRUCTIONS)
        (content / 'Read Me.txt').chmod(0o644)
        run('hdiutil', 'create', '-fs', 'HFS+', '-format', 'UDZO', '-volname', 'Symphony',
            '-srcfolder', content, destination, timeout=300)
        run('hdiutil', 'verify', destination)


def submit(archive, keychain, env, evidence, label='notary', checkpoint=None):
    if checkpoint:
        checkpoint.verify_bytes()
        if label == 'payload':
            require(archive == checkpoint.path / 'payload.zip', 'submit must use retained payload ZIP')
        else:
            require(archive == checkpoint.path / 'outer.dmg', 'submit must use retained outer DMG')
    digest = checksum(archive)
    known = checkpoint.value['submissions'].get(label) if checkpoint else None
    if checkpoint:
        intent = checkpoint.value.get('intents', {}).get(label)
        require(not intent or known and known['submitted_sha256'] == intent == digest,
                'submission outcome uncertain; reconcile original Apple ID; never resubmit')
        if known:
            require(known['submitted_sha256'] == digest, 'known submission bytes differ')
    profile = 'symphony-distribution'
    run('xcrun', 'notarytool', 'store-credentials', profile, '--keychain', keychain,
        '--apple-id', env['MACOS_NOTARY_APPLE_ID'], '--team-id', env['MACOS_TEAM_ID'],
        '--password', env['MACOS_NOTARY_APP_PASSWORD'])
    auth = ['--keychain-profile', profile, '--keychain', keychain]
    if known:
        submission = notarization_id(known['id'])
        response = json.loads(run('xcrun', 'notarytool', 'info', submission, *auth,
                                  '--output-format', 'json', timeout=180))
        require(notarization_id(response['id']) == submission, 'notary info returned another submission')
        require(response['status'] in ('Accepted', 'Invalid', 'Rejected', 'In Progress'), 'unknown notarization status')
        receipt = dict(known, status=response['status'])
    else:
        if checkpoint:
            checkpoint.value.setdefault('intents', {})[label] = digest
            checkpoint.save()  # Durable before POST; a lost response cannot retry.
        response = json.loads(run('xcrun', 'notarytool', 'submit', archive, *auth,
                                  '--no-wait', '--output-format', 'json', timeout=600))
        submission = notarization_id(response['id'])
        receipt = {'id': submission, 'status': 'Submitted', 'submitted_sha256': digest}
    receipt_path = evidence / (label + '-submission.json')
    if checkpoint:
        checkpoint.submission(label, receipt)
    write_json(receipt_path, receipt)
    # Persist the ID before waiting: Apple can keep processing after timeout.
    if receipt['status'] not in ('Accepted', 'Invalid', 'Rejected'):
        try:
            response = json.loads(run('xcrun', 'notarytool', 'wait', submission, *auth,
                                      '--timeout', '30m', '--output-format', 'json', timeout=1900))
        except ValueError:
            receipt['status'] = 'Wait failed or timed out; not accepted'
            if checkpoint:
                checkpoint.submission(label, receipt)
            write_json(receipt_path, receipt)
            raise
    require(notarization_id(response['id']) == submission, 'notary wait returned another submission')
    status = response['status']
    require(status in ('Accepted', 'Invalid', 'Rejected', 'In Progress'), 'unknown notarization status')
    receipt['status'] = status
    if checkpoint:
        checkpoint.submission(label, receipt)
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


def notarize(dmg, keychain, env, evidence, checkpoint=None):
    submission = submit(dmg, keychain, env, evidence, checkpoint=checkpoint) if checkpoint else submit(dmg, keychain, env, evidence)
    run('xcrun', 'stapler', 'staple', dmg)
    run('xcrun', 'stapler', 'validate', dmg)
    run('codesign', '--verify', '--strict', dmg)
    run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4', dmg)
    return submission


def signing_context(qualified_archive, env, qualification=None):
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz', qualified_archive.name),
            'unsafe qualified archive name')
    origin = {'kind': 'local'}
    if env.get('GITHUB_ACTIONS') == 'true':
        require(env.get('GITHUB_REPOSITORY') == 'Jayson-Stangel/symphony'
                and all(re.fullmatch(r'[1-9][0-9]*', env.get(k, ''))
                        for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')),
                'invalid signing Actions origin')
        origin = {'kind': 'actions', 'repository': env['GITHUB_REPOSITORY'],
                  'workflow': env['GITHUB_WORKFLOW_REF'].split('@')[0],
                  'run_id': int(env['GITHUB_RUN_ID']), 'run_attempt': int(env['GITHUB_RUN_ATTEMPT'])}
    return {'qualified_package_sha256': checksum(qualified_archive),
            'archive_name': qualified_archive.name,
            'identity': {'certificate_sha1': env['MACOS_SIGNING_IDENTITY_SHA1'].upper(),
                         'team_id': env['MACOS_TEAM_ID']},
            'qualification': json.loads(qualification.read_text()) if qualification else None,
            'origin': origin}


def validate_retained_payload(checkpoint, qualified_archive, env):
    from signing_payload import bundle_files, compare_package_payload, compare_payload_zip
    files = compare_package_payload(checkpoint.path / 'signed-updater.tar.gz', qualified_archive)
    _, directories = bundle_files(checkpoint.path / 'signed-updater.tar.gz', with_directories=True)
    compare_payload_zip(checkpoint.path / 'payload.zip', files, directories)
    receipt = json.loads(files['signing.json'][0])
    require(receipt['source_revision'] == checkpoint.value['source_revision']
            and receipt['original_qualified_helper_sha256'] == checkpoint.value['original_qualified_helper_sha256'],
            'retained package receipt source differs')
    signature = receipt['payloads']['libexec/symphony-host']
    require(signature['sha256'] == checkpoint.value['signed_helper_sha256'] and
            signature['sha256'] == hashlib.sha256(files['libexec/symphony-host'][0]).hexdigest() and
            signature['certificate_sha1'] == env['MACOS_SIGNING_IDENTITY_SHA1'].upper() and
            signature['team_id'] == env['MACOS_TEAM_ID'] and signature['hardened_runtime'] is True
            and signature['secure_timestamp'] is True, 'retained helper identity differs')
    package_evidence.require_complete(checkpoint.path / 'package-diagnostics.json')
    return files, directories, signature


def validate_retained_native(checkpoint, qualified_archive, helper, env):
    """Public-only native input admission; no keychain/P12/account credentials."""
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64',
            'retained native admission requires Apple Silicon macOS')
    from signing_payload import extract_payload
    from local_distribution import executable_identity, verify_mounted_payload
    files, directories, signature = validate_retained_payload(checkpoint, qualified_archive, env)
    with tempfile.TemporaryDirectory(prefix='symphony-retained-check-', dir=env.get('RUNNER_TEMP')) as temporary:
        private = Path(temporary); bundle = private / 'bundle'
        extract_payload(files, directories, bundle)
        native = bundle / 'libexec/symphony-host'
        require(inspect_signature(native, env, private) == signature, 'retained native signature differs')
        require(executable_identity(helper.read_bytes()) == executable_identity(native.read_bytes()),
                'retained signed helper differs from original qualified code')
        if 'outer.dmg' in checkpoint.value['artifacts']:
            require(inspect_signature(checkpoint.path / 'outer.dmg', env, private, runtime=False)
                    == checkpoint.value['dmg_signature'], 'retained DMG signature differs')
            verify_mounted_payload(checkpoint.path / 'outer.dmg', checkpoint.path / 'signed-updater.tar.gz', run)
        if 'stapled.dmg' in checkpoint.value['artifacts']:
            inspect_signature(checkpoint.path / 'stapled.dmg', env, private, runtime=False)
            run('xcrun', 'stapler', 'validate', checkpoint.path / 'stapled.dmg')
            run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4',
                checkpoint.path / 'stapled.dmg')
            verify_mounted_payload(checkpoint.path / 'stapled.dmg', checkpoint.path / 'signed-updater.tar.gz', run)
    checkpoint.verify_bytes()
    return files, directories, signature


def finish_distribution(checkpoint, bundle, manifest, helper, output, env, keychain):
    evidence = output / 'evidence'
    archive_name = checkpoint.value['archive_name']
    receipt = json.loads((bundle / 'signing.json').read_text())
    signed_helper = bundle / 'libexec/symphony-host'
    payload_submission = submit(checkpoint.path / 'payload.zip', keychain, env, evidence, 'payload', checkpoint)
    verify_notarized_helper(signed_helper, env, checkpoint, 'notarization.payload-helper')
    if 'outer.dmg' not in checkpoint.value['artifacts']:
        checkpoint.value['dmg_state'] = 'preparing'; checkpoint.save()
        dmg = bundle.parent / 'outer.dmg'
        create_dmg(bundle, dmg)
        signed_dmg = sign(dmg, keychain, env, runtime=False)
        checkpoint.retain_file('outer.dmg', dmg)
        checkpoint.value.update(dmg_signature=signed_dmg, dmg_state='signed'); checkpoint.save()
    else:
        signed_dmg = inspect_signature(checkpoint.path / 'outer.dmg', env, bundle.parent, runtime=False)
        require(signed_dmg == checkpoint.value['dmg_signature'], 'retained DMG signature differs')
    submission = submit(checkpoint.path / 'outer.dmg', keychain, env, evidence, 'notary', checkpoint)
    final = checkpoint.path / 'stapled.dmg'
    if 'stapled.dmg' not in checkpoint.value['artifacts']:
        checkpoint.value['dmg_state'] = 'stapling'; checkpoint.save()
        with (checkpoint.path / 'outer.dmg').open('rb') as source, final.open('xb') as target:
            os.chmod(final, 0o600); shutil.copyfileobj(source, target)
            target.flush(); os.fsync(target.fileno())
        run('xcrun', 'stapler', 'staple', final)
        checkpoint.value['artifacts']['stapled.dmg'] = {'sha256': checksum(final)}
        checkpoint.value['dmg_state'] = 'stapled'; checkpoint.save()
    run('xcrun', 'stapler', 'validate', final)
    inspect_signature(final, env, bundle.parent, runtime=False)
    run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=4', final)
    verify_notarized_helper(signed_helper, env, checkpoint, 'notarization.dmg-helper')
    from local_distribution import verify_mounted_payload
    verify_mounted_payload(final, checkpoint.path / 'signed-updater.tar.gz', run)
    checkpoint.verify_bytes()
    require(checksum(helper) == checkpoint.value['original_qualified_helper_sha256'],
            'original qualified helper was modified')
    distribution = {
        'status': 'notarized-stapled-assessed', 'source_revision': checkpoint.value['source_revision'],
        'original_qualified_helper_sha256': checkpoint.value['original_qualified_helper_sha256'],
        'payloads': receipt['payloads'], 'notary_submission_id': submission,
        'payload_notary_submission_id': payload_submission, 'dmg_signature': signed_dmg,
        'artifacts': {archive_name: checkpoint.value['artifacts']['signed-updater.tar.gz']['sha256'],
                      archive_name.removesuffix('.tar.gz') + '.dmg': checksum(final)},
        'updater': 'tar.gz; same notarized native code; online ticket lookup required',
    }
    shutil.copyfile(manifest, output / 'internal-release.json')
    core = manifest.parent / 'manager-core.json'
    if core.exists(): shutil.copyfile(core, output / core.name)
    shutil.copyfile(checkpoint.path / 'package-source.txt', evidence / 'package-source.txt')
    for source, name in ((checkpoint.path / 'signed-updater.tar.gz', archive_name),
                         (final, archive_name.removesuffix('.tar.gz') + '.dmg')):
        shutil.copyfile(source, output / name)
        require(checksum(output / name) == distribution['artifacts'][name], 'final signed copy differs')
    return distribution


def ready_distribution(output, distribution):
    # Only called after the credential context's complete verified cleanup.
    write_json(output / 'macos-distribution.json', distribution)
    (output / 'SHA256SUMS').write_text(''.join(
        f'{digest}  {name}\n' for name, digest in distribution['artifacts'].items()))


def build(manifest, helper, output, env, qualified_archive=None, qualification=None):
    credentials(env)  # Refuse before touching keychains or invoking unqualified code.
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'requires native Apple Silicon macOS')
    require(not output.exists(), 'signed output must be a new directory')
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip()
    require(not run('git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=all').strip(),
            'signed distribution requires a clean committed checkout')
    require(helper.is_file() and not helper.is_symlink(), 'qualified helper must be a regular file')
    original = checksum(helper)
    require(qualified_archive is not None and qualified_archive.is_file() and not qualified_archive.is_symlink(),
            'original qualified package is required before signing')
    from signing_payload import helper_hash
    require(helper_hash(qualified_archive) == original, 'qualified package helper differs')
    output.mkdir(parents=True)
    evidence = output / 'evidence'
    evidence.mkdir()
    checkpoint = SignedCheckpoint(output, revision, manifest, original)
    checkpoint.value['context'] = signing_context(qualified_archive, env, qualification)
    checkpoint.value['archive_name'] = qualified_archive.name
    checkpoint.save()
    # Outputs stay private until all signatures, notarization and assessment pass.
    with signing_directory(env) as private:
        with signing_keychain(env, private, evidence) as keychain:
            package = private / 'packages'
            child_env = dict(env, SYMPHONY_MACOS_KEYCHAIN=str(keychain),
                             SYMPHONY_MACOS_PACKAGE_DIAGNOSTICS=str(evidence / 'package-diagnostics.json'))
            run('bash', ROOT / 'scripts/release/build-manager-package.sh', manifest, package,
                'macos', ROOT, revision, helper, env=child_env, timeout=600, stage='assembly.package')
            package_evidence.require_complete(evidence / 'package-diagnostics.json')
            archives = list(package.glob('*.tar.gz'))
            require(len(archives) == 1, 'expected exactly one manager archive')
            archive = archives[0]
            bundle = private / 'bundle'
            bundle.mkdir()
            run('tar', '-xpzf', archive, '-C', bundle)
            receipt = json.loads((bundle / 'signing.json').read_text())
            require(receipt['original_qualified_helper_sha256'] == original,
                    'qualified helper binding differs')
            from signing_payload import compare_package_payload
            compare_package_payload(archive, qualified_archive)
            run(bundle / 'libexec/symphony-host', 'verify-files', bundle)
            # Apple's custom-installer guidance calls for payload qualification
            # before packaging/notarizing the outer distribution. Bare command
            # line binaries receive tickets but cannot themselves be stapled.
            payload_zip = private / 'payload.zip'
            run('ditto', '-c', '-k', '--keepParent', bundle, payload_zip)
            signed_helper = bundle / 'libexec/symphony-host'
            require(checksum(signed_helper) == receipt['payloads']['libexec/symphony-host']['sha256'],
                    'signed helper receipt differs')
            checkpoint.retain(archive, payload_zip, signed_helper)
            require(archive.name == qualified_archive.name, 'original qualified archive name differs')
            checkpoint.retain_file('package-source.txt', package / 'package-source.txt')
            checkpoint.retain_file('package-diagnostics.json', evidence / 'package-diagnostics.json')
            distribution = finish_distribution(checkpoint, bundle, manifest, helper, output, env, keychain)
        ready_distribution(output, distribution)


def resume(manifest, helper, output, env, qualified_archive, checkpoint_path, digest,
           qualification=None, admission_path=None):
    credentials(env)
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'requires native Apple Silicon macOS')
    require(not output.exists(), 'continuation output must be a new directory')
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip()
    require(not run('git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=all').strip(),
            'continuation requires a clean committed checkout')
    require(helper.is_file() and not helper.is_symlink() and qualified_archive.is_file()
            and not qualified_archive.is_symlink(), 'original qualified inputs must be regular files')
    from signing_payload import helper_hash, extract_payload
    original = checksum(helper)
    require(helper_hash(qualified_archive) == original, 'qualified package helper differs')
    context = signing_context(qualified_archive, env, qualification)
    admission = json.loads(admission_path.read_text()) if admission_path else None
    original_checkpoint = SignedCheckpoint.load(checkpoint_path, revision, manifest, original, digest, context)
    files, directories, signature = validate_retained_native(original_checkpoint, qualified_archive, helper, env)
    checkpoint = SignedCheckpoint.reopen(checkpoint_path, output, revision, manifest, original, digest, context, admission)
    output.mkdir(); evidence = output / 'evidence'; evidence.mkdir()
    shutil.copyfile(checkpoint.path / 'package-diagnostics.json', evidence / 'package-diagnostics.json')
    package_evidence.require_complete(evidence / 'package-diagnostics.json')
    with signing_directory(env) as private:
        bundle = private / 'bundle'; extract_payload(files, directories, bundle)
        native = bundle / 'libexec/symphony-host'
        require(inspect_signature(native, env, private) == signature, 'retained native signature differs')
        from local_distribution import executable_identity
        require(executable_identity(helper.read_bytes()) == executable_identity(native.read_bytes()),
                'retained signed helper differs from original qualified code')
        with signing_keychain(env, private, evidence) as keychain:
            distribution = finish_distribution(checkpoint, bundle, manifest, helper, output, env, keychain)
        ready_distribution(output, distribution)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    signed = commands.add_parser('build')
    signed.add_argument('manifest', type=Path)
    signed.add_argument('helper', type=Path)
    signed.add_argument('output', type=Path)
    signed.add_argument('--qualified-package', type=Path, required=True,
                        help='original package imported from the approved qualification')
    signed.add_argument('--qualification', type=Path, help='exact original qualification receipt')
    signed.add_argument('--authorize-apple-upload', action='store_true', required=True,
                        help='operator has approved this exact source/candidate and credential use')
    continued = commands.add_parser('resume', parents=[], help='continue an exact retained checkpoint into a new output')
    for name in ('manifest', 'helper', 'output'):
        continued.add_argument(name, type=Path)
    continued.add_argument('--qualified-package', type=Path, required=True)
    continued.add_argument('--qualification', type=Path)
    continued.add_argument('--checkpoint', type=Path, required=True)
    continued.add_argument('--checkpoint-sha256', required=True)
    continued.add_argument('--checkpoint-admission', type=Path,
                           help='service-verified original Actions artifact receipt; required for Actions continuation')
    continued.add_argument('--authorize-apple-upload', action='store_true', required=True)
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
            build(args.manifest.resolve(), args.helper.resolve(), args.output.resolve(), os.environ,
                  args.qualified_package.resolve(), args.qualification.resolve() if args.qualification else None)
        elif args.command == 'resume':
            resume(args.manifest.resolve(), args.helper.resolve(), args.output.resolve(), os.environ,
                   args.qualified_package.resolve(), args.checkpoint.absolute(), args.checkpoint_sha256,
                   args.qualification.resolve() if args.qualification else None,
                   args.checkpoint_admission.resolve() if args.checkpoint_admission else None)
        elif args.command == 'sign-payload':
            credentials(os.environ)
            sign_payload(args.bundle, Path(os.environ['SYMPHONY_MACOS_KEYCHAIN']), os.environ)
        else:
            require(not args.output.exists(), 'DMG destination already exists')
            create_dmg(args.bundle, args.output)
            print('UNSIGNED DMG: construction only; no Developer ID or notarization acceptance')
    except (ValueError, OSError, KeyError, TypeError, AttributeError, tarfile.TarError, zipfile.BadZipFile) as error:
        # Do not print potentially secret-bearing OS errors/JSON from child processes.
        message = str(error) if type(error) is ValueError or isinstance(error, CommandFailure) else type(error).__name__
        try:
            if args.command in ('build', 'resume'):
                path = args.output.with_name(args.output.name + '.checkpoint') / 'checkpoint.json'
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024:
                    print('Retained checkpoint SHA256: ' + checksum(path), file=sys.stderr)
                    if os.environ.get('GITHUB_ACTIONS') == 'true' and args.qualification:
                        qualified = json.loads(args.qualification.read_text())
                        fields = [os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'],
                                  str(qualified['run_id']), str(qualified['run_attempt'])]
                        require(all(re.fullmatch(r'[1-9][0-9]*', v) for v in fields)
                                and re.fullmatch(r'[a-f0-9]{40}', os.environ['GITHUB_SHA']), 'invalid continuation diagnostics')
                        print('Continue only with a new protected workflow run: gh workflow run macos-production-signing.yml'
                              ' --ref main -f approved_revision=' + os.environ['GITHUB_SHA'] +
                              ' -f qualified_run=' + fields[2] + ' -f qualified_attempt=' + fields[3] +
                              ' -f resume_run=' + fields[0] + ' -f resume_attempt=' + fields[1], file=sys.stderr)
                    else:
                        print('Use the documented resume command with this exact digest and original inputs; never rerun build.', file=sys.stderr)
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Optional continuation diagnostics must never expose raw state or hide the refusal.
        parser.exit(1, 'macOS distribution refused: ' + message + '\n')


if __name__ == '__main__':
    main()
