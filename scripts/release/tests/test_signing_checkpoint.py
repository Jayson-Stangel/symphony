"""Failure-lifecycle peers: no Apple tools, credentials, or signing operations."""
import json
import io
import hashlib
import tarfile
import zipfile
from contextlib import contextmanager, ExitStack
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import macos_distribution as dist

REVISION = 'a' * 40
SUBMISSION = '11111111-2222-3333-4444-555555555555'
STAGES = ('notarization.payload-helper', 'notarization.dmg-helper')


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SigningCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / 'input.json'
        self.manifest.write_bytes(b'{"qualified":"exact input"}\n')
        self.helper = self.root / 'helper'
        self.helper.write_bytes(b'FAKE SIGNED HELPER')
        self.original_hash = 'b' * 64
        self.output = self.root / 'distribution'
        self.checkpoint = dist.SignedCheckpoint(self.output, REVISION, self.manifest, self.original_hash)
        self.archive = self.root / 'signed.tar.gz'
        self.archive.write_bytes(b'EXACT SIGNED UPDATER TAR BYTES')
        self.payload = self.root / 'payload.zip'
        self.payload.write_bytes(b'EXACT SUBMITTED ZIP BYTES')
        self.checkpoint.retain(self.archive, self.payload, self.helper)
        self.accept()

    def accept(self, label='payload'):
        self.checkpoint.value['submissions'].pop(label, None)  # Reset intentionally corrupted fixture state.
        self.checkpoint.submission(label, {'id': SUBMISSION, 'status': 'Accepted',
            'submitted_sha256': self.checkpoint.value['artifacts']['payload.zip']['sha256']})

    def persisted(self):
        return json.loads((self.checkpoint.path / 'checkpoint.json').read_text())

    def verify(self, stage=STAGES[0], command=None, clock=None):
        clock = clock or Clock()
        with patch.object(dist, 'inspect_signature') as inspect, \
             patch.object(dist, 'run', side_effect=command) as run, \
             patch.object(dist, 'sign', side_effect=AssertionError('must not sign')), \
             patch.object(dist, 'submit', side_effect=AssertionError('must not submit')):
            dist.verify_notarized_helper(self.helper, {}, self.checkpoint, stage, clock, clock.sleep)
        return inspect, run, clock

    def test_retained_bytes_survive_cleanup_with_private_provenance(self):
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            source = Path(scratch)
            archive, payload, helper = (source / name for name in ('signed.tar.gz', 'payload.zip', 'helper'))
            archive.write_bytes(b'preserve tar exactly')
            payload.write_bytes(b'preserve zip exactly')
            helper.write_bytes(b'signed helper')
            (source / 'identity.p12').write_bytes(b'FAKE PRIVATE MATERIAL')
            (source / 'signing.keychain-db').write_bytes(b'FAKE KEYCHAIN')
            checkpoint = dist.SignedCheckpoint(self.root / 'other', REVISION, self.manifest, self.original_hash)
            checkpoint.retain(archive, payload, helper)
            checkpoint.submission('payload', {'id': SUBMISSION, 'status': 'Accepted',
                'submitted_sha256': dist.checksum(payload)})
        checkpoint.verify_bytes()
        self.assertEqual((checkpoint.path / 'signed-updater.tar.gz').read_bytes(), b'preserve tar exactly')
        self.assertEqual((checkpoint.path / 'payload.zip').read_bytes(), b'preserve zip exactly')
        self.assertEqual({p.name for p in checkpoint.path.iterdir()},
                         {'checkpoint.json', 'signed-updater.tar.gz', 'payload.zip'})
        info = checkpoint.path.stat()
        self.assertEqual((info.st_uid, stat.S_IMODE(info.st_mode)), (os.geteuid(), 0o700))
        for path in checkpoint.path.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        receipt = json.loads((checkpoint.path / 'checkpoint.json').read_text())
        self.assertEqual(receipt['status'], 'not-ready')
        self.assertEqual(receipt['source_revision'], REVISION)
        self.assertEqual(receipt['input_manifest_sha256'], dist.checksum(self.manifest))
        self.assertEqual(receipt['original_qualified_helper_sha256'], self.original_hash)
        self.assertEqual(receipt['submissions']['payload']['status'], 'Accepted')
        self.assertEqual(receipt['submissions']['payload']['id'], SUBMISSION)
        self.assertFalse(self.output.exists())
        with self.assertRaises(FileExistsError):
            dist.SignedCheckpoint(self.root / 'other', REVISION, self.manifest, self.original_hash)

    def test_partial_retention_io_failure_cannot_verify_or_be_ready(self):
        checkpoint = dist.SignedCheckpoint(self.root / 'failed', REVISION, self.manifest, self.original_hash)
        with patch.object(dist.shutil, 'copyfileobj', side_effect=OSError('fake storage full')):
            with self.assertRaises(OSError):
                checkpoint.retain(self.archive, self.payload, self.helper)
        with self.assertRaises(ValueError):
            checkpoint.verify_bytes()
        value = json.loads((checkpoint.path / 'checkpoint.json').read_text())
        self.assertEqual(value['status'], 'not-ready')
        self.assertEqual(value['submissions'], {})
        self.assertFalse((self.root / 'failed').exists())

    def test_accepted_retry_is_read_only_and_records_both_stages(self):
        self.accept('notary')
        for stage in STAGES:
            with self.subTest(stage=stage):
                attempts = []
                def command(*args, **kwargs):
                    attempts.append((args, kwargs))
                    if len(attempts) < 3:
                        raise dist.CommandFailure(stage, 'failed', 1, 'notarization-requirement-unmet')
                inspect, run, clock = self.verify(stage, command=command)
                self.assertEqual(inspect.call_count, 3)
                self.assertEqual(run.call_count, 3)
                self.assertEqual(clock.sleeps, [5, 5])
                self.assertLessEqual(clock.now, 60)
                for args, kwargs in attempts:
                    self.assertEqual(args, ('codesign', '-vvvv', '-R=notarized', '--check-notarization', self.helper))
                    self.assertEqual(kwargs['stage'], stage)
                    self.assertLessEqual(kwargs['timeout'], 20)
                checks = self.persisted()['checks'][-3:]
                self.assertEqual([c['attempt'] for c in checks], [1, 2, 3])
                self.assertEqual([c['status'] for c in checks], ['failed', 'failed', 'success'])
        self.assertEqual(self.persisted()['status'], 'not-ready')

    def test_requirement_miss_stops_after_three_checks(self):
        failure = dist.CommandFailure(STAGES[0], 'failed', 1, 'notarization-requirement-unmet')
        with patch.object(dist, 'inspect_signature'), patch.object(dist, 'run', side_effect=failure) as run:
            clock = Clock()
            with self.assertRaises(dist.CommandFailure):
                dist.verify_notarized_helper(self.helper, {}, self.checkpoint, STAGES[0], clock, clock.sleep)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(clock.sleeps, [5, 5])

    def test_nonpropagation_failures_stop_immediately(self):
        for reason in ('unclassified', 'signature-invalid', 'certificate-trust', 'certificate-expired',
                       'notarization-lookup-unavailable', 'verification-timeout'):
            with self.subTest(reason=reason):
                error = dist.CommandFailure(STAGES[0], 'failed', 44, reason)
                clock = Clock()
                with patch.object(dist, 'inspect_signature'), patch.object(dist, 'run', side_effect=error) as run:
                    with self.assertRaises(dist.CommandFailure) as caught:
                        dist.verify_notarized_helper(self.helper, {}, self.checkpoint, STAGES[0], clock, clock.sleep)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(clock.sleeps, [])

    def test_signature_inspection_failure_stops_before_ticket_lookup(self):
        with patch.object(dist, 'inspect_signature', side_effect=ValueError('fake private detail')), \
             patch.object(dist, 'run') as run:
            with self.assertRaises(dist.CommandFailure) as caught:
                dist.verify_notarized_helper(self.helper, {}, self.checkpoint, STAGES[0])
        self.assertEqual(caught.exception.reason, 'signature-policy-failed')
        run.assert_not_called()

    def test_helper_or_archive_mutation_refuses_before_any_command(self):
        for path in (self.helper, self.checkpoint.path / 'signed-updater.tar.gz', self.checkpoint.path / 'payload.zip'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with patch.object(dist, 'inspect_signature') as inspect, patch.object(dist, 'run') as run:
                    with self.assertRaises(dist.CommandFailure) as caught:
                        dist.verify_notarized_helper(self.helper, {}, self.checkpoint, STAGES[0])
                self.assertEqual(caught.exception.reason, 'retained-binding-changed')
                inspect.assert_not_called()
                run.assert_not_called()
                path.write_bytes(original)

    def test_missing_or_mismatched_acceptance_refuses_without_commands(self):
        for stage, change in ((STAGES[0], {'status': 'Submitted'}),
                              (STAGES[0], {'submitted_sha256': 'c' * 64}), (STAGES[1], {})):
            with self.subTest(stage=stage, change=change):
                self.accept()
                self.checkpoint.value['submissions']['payload'].update(change)
                with patch.object(dist, 'inspect_signature') as inspect, patch.object(dist, 'run') as run:
                    with self.assertRaises(dist.CommandFailure) as caught:
                        dist.verify_notarized_helper(self.helper, {}, self.checkpoint, stage)
                self.assertEqual(caught.exception.reason, 'acceptance-binding-missing')
                inspect.assert_not_called()
                run.assert_not_called()

    def test_successful_command_crossing_deadline_is_rejected(self):
        clock = Clock()
        def late_success(*args, **kwargs):
            clock.now = 61
        with self.assertRaises(dist.CommandFailure) as caught:
            self.verify(command=late_success, clock=clock)
        self.assertEqual(caught.exception.reason, 'verification-timeout')
        self.assertEqual(self.persisted()['checks'][-1]['status'], 'failed')
        self.assertEqual(self.persisted()['status'], 'not-ready')

    def test_retry_budget_includes_commands_and_waits(self):
        clock = Clock()
        timeouts = []
        def slow_miss(*args, **kwargs):
            timeouts.append(kwargs['timeout'])
            clock.now += kwargs['timeout']
            raise dist.CommandFailure(STAGES[0], 'failed', 1, 'notarization-requirement-unmet')
        with self.assertRaises(dist.CommandFailure):
            self.verify(command=slow_miss, clock=clock)
        self.assertEqual(timeouts, [20, 20, 10])
        self.assertEqual(clock.now, 60)
        self.assertEqual(clock.sleeps, [5, 5])

    def test_raw_stderr_and_fake_password_do_not_enter_diagnostics(self):
        secret = 'FAKE-PASSWORD /private/fake-account/identity.p12'
        result = subprocess.CompletedProcess([], 44, '', secret + ' invalid signature')
        with patch.object(dist.subprocess, 'run', return_value=result):
            with self.assertRaises(dist.CommandFailure) as caught:
                dist.run('codesign', 'fake-helper', stage=STAGES[0])
        self.checkpoint.check(STAGES[0], 1, 'failed', caught.exception)
        encoded = (self.checkpoint.path / 'checkpoint.json').read_text()
        self.assertNotIn(secret, encoded)
        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(self.persisted()['checks'][-1], {'stage': STAGES[0], 'attempt': 1,
            'status': 'failed', 'outcome': 'failed', 'reason': 'signature-invalid', 'exit_status': 44})

    def test_accepted_submission_persists_before_log_fetch_failure(self):
        evidence = self.root / 'evidence'
        evidence.mkdir()
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[2] == 'info':
                return json.dumps({'id': SUBMISSION, 'status': 'Accepted'})
            if args[2] == 'log':
                raise dist.CommandFailure('notarytool', 'failed', 44)
            return ''
        env = {'MACOS_NOTARY_APPLE_ID': 'fake@example.invalid', 'MACOS_TEAM_ID': 'FAKETEAM00',
               'MACOS_NOTARY_APP_PASSWORD': 'FAKE-PASSWORD'}
        with patch.object(dist, 'run', side_effect=command):
            with self.assertRaises(dist.CommandFailure):
                dist.submit(self.checkpoint.path / 'payload.zip', self.root / 'fake-keychain', env,
                            evidence, label='payload', checkpoint=self.checkpoint)
        self.assertEqual(self.persisted()['submissions']['payload']['status'], 'Accepted')
        self.assertEqual([call[2] for call in calls], ['store-credentials', 'info', 'log'])
        self.assertFalse((evidence / 'payload-log.json').exists())
        self.assertFalse(self.output.exists())
        self.assertEqual(self.persisted()['status'], 'not-ready')


class ContinuationTests(unittest.TestCase):
    """Real retained tar/ZIP/state; fake native/Apple peers, never live proof."""
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(); self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.env = {name: 'INERT' for name in dist.REQUIRED}
        self.env.update(MACOS_SIGNING_IDENTITY_SHA1='A' * 40, MACOS_TEAM_ID='FAKETEAM00', RUNNER_TEMP=str(self.root))
        self.manifest = self.root / 'manifest.json'; self.manifest.write_text(json.dumps({'source_revision': REVISION}))
        self.helper = self.root / 'original-helper'; self.helper.write_bytes(b'ORIGINAL QUALIFIED CODE')
        self.signature = {'sha256': hashlib.sha256(b'SIGNED QUALIFIED CODE').hexdigest(),
                          'cdhash': 'b' * 40, 'certificate_sha1': 'A' * 40, 'team_id': 'FAKETEAM00',
                          'hardened_runtime': True, 'secure_timestamp': True}
        original = {'libexec/symphony-host': (self.helper.read_bytes(), 0o755),
                    'source.txt': (b'EXACT SOURCE', 0o644),
                    'distribution-status.txt': (b'unsigned', 0o644), 'files.sha256': (b'old inventory', 0o644)}
        receipt = {'status': 'developer-id-signed-not-yet-notarized', 'source_revision': REVISION,
                   'original_qualified_helper_sha256': dist.checksum(self.helper),
                   'payloads': {'libexec/symphony-host': self.signature}}
        self.files = {k: v for k, v in original.items() if k not in ('distribution-status.txt', 'files.sha256')}
        self.files['libexec/symphony-host'] = (b'SIGNED QUALIFIED CODE', 0o755)
        self.files['signing.json'] = (json.dumps(receipt).encode(), 0o644)
        self.files['files.sha256'] = (''.join(hashlib.sha256(raw).hexdigest() + '  ./' + name + '\n'
            for name, (raw, _) in sorted(self.files.items())).encode(), 0o644)
        self.qualified = self.root / 'symphony-fixture-macos.tar.gz'; self.tar(self.qualified, original)
        archive = self.root / 'signed.tar.gz'; self.tar(archive, self.files)
        payload = self.root / 'payload.zip'
        with zipfile.ZipFile(payload, 'w') as target:
            for name in ('.', 'libexec'):
                info = zipfile.ZipInfo('bundle/' if name == '.' else 'bundle/' + name + '/')
                info.external_attr = (stat.S_IFDIR | 0o755) << 16; target.writestr(info, b'')
            for name, (raw, mode) in self.files.items():
                info = zipfile.ZipInfo('bundle/' + name); info.external_attr = (stat.S_IFREG | mode) << 16
                target.writestr(info, raw)
        signed_helper = self.root / 'signed-helper'; signed_helper.write_bytes(self.files['libexec/symphony-host'][0])
        self.checkpoint = dist.SignedCheckpoint(self.root / 'initial', REVISION, self.manifest, dist.checksum(self.helper))
        self.checkpoint.value.update(context=dist.signing_context(self.qualified, self.env), archive_name=self.qualified.name)
        self.checkpoint.retain(archive, payload, signed_helper)
        source = self.root / 'package-source.txt'; source.write_text('manager_source_revision=' + REVISION + '\n')
        diagnostics = self.root / 'package-diagnostics.json'
        dist.write_json(diagnostics, {'schema': 1, 'operations': [
            {'stage': name, 'status': 'success'} for name in dist.package_evidence.STAGES]})
        self.checkpoint.retain_file('package-source.txt', source)
        self.checkpoint.retain_file('package-diagnostics.json', diagnostics)
        self.checkpoint.submission('payload', {'id': SUBMISSION, 'status': 'Accepted',
            'submitted_sha256': dist.checksum(payload)})
        self.calls = []; self.signs = []; self.imports = 0; self.submitted = {}
        self.wait_failure = False; self.unknown_response = False; self.gate_failure = None

    def tar(self, path, files):
        with tarfile.open(path, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
            for name in ('.', 'libexec'):
                info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = 0o755; archive.addfile(info)
            for name, (raw, mode) in files.items():
                info = tarfile.TarInfo('./' + name); info.mode = mode; info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))

    def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'git': return '' if 'status' in args else REVISION
        if args[:2] == ('xcrun', 'stapler'):
            if args[2] == 'staple':
                with Path(args[3]).open('ab') as target: target.write(b'STAPLE')
            return ''
        if args[:2] != ('xcrun', 'notarytool'): return ''
        operation = args[2]
        if operation == 'store-credentials': return ''
        identifier = '22222222-2222-4222-8222-222222222222'
        if operation == 'submit':
            self.submitted[identifier] = dist.checksum(Path(args[3]))
            if self.unknown_response: raise dist.CommandFailure('notarytool', 'timed out')
            return json.dumps({'id': identifier})
        identifier = args[3]
        if operation == 'wait' and self.wait_failure: raise dist.CommandFailure('notarytool', 'timed out')
        if operation in ('wait', 'info'): return json.dumps({'id': identifier, 'status': 'Accepted'})
        digest = (self.checkpoint.value['artifacts']['payload.zip']['sha256'] if identifier == SUBMISSION
                  else self.submitted[identifier])
        return json.dumps({'jobId': identifier, 'sha256': digest, 'status': 'Accepted', 'statusCode': 0, 'issues': []})

    def signature_for(self, path, env, parent, runtime=True):
        return dict(self.signature) if runtime else dict(self.signature, sha256=dist.checksum(path), hardened_runtime=False)

    def sign(self, path, keychain, env, runtime=True):
        self.assertFalse(runtime); self.signs.append(path)
        with path.open('ab') as target: target.write(b'SIGNATURE')
        return self.signature_for(path, env, path.parent, False)

    def gate(self, helper, env, checkpoint, stage):
        if stage == self.gate_failure: raise dist.CommandFailure(stage, 'failed', reason='notarization-requirement-unmet')

    def continue_from(self, source, output):
        @contextmanager
        def keychain(*args):
            self.imports += 1
            yield self.root / 'inert-keychain'
        def create(bundle, destination): destination.write_bytes(b'EXACT OUTER DMG')
        with ExitStack() as stack:
            stack.enter_context(patch.object(dist.sys, 'platform', 'darwin'))
            uname = stack.enter_context(patch.object(dist.os, 'uname')); uname.return_value.machine = 'arm64'
            stack.enter_context(patch.object(dist, 'run', side_effect=self.command))
            stack.enter_context(patch.object(dist, 'signing_keychain', side_effect=keychain))
            stack.enter_context(patch.object(dist, 'inspect_signature', side_effect=self.signature_for))
            stack.enter_context(patch.object(dist, 'sign', side_effect=self.sign))
            stack.enter_context(patch.object(dist, 'create_dmg', side_effect=create))
            stack.enter_context(patch.object(dist, 'verify_notarized_helper', side_effect=self.gate))
            stack.enter_context(patch('local_distribution.executable_identity', return_value='SAME QUALIFIED CODE'))
            stack.enter_context(patch('local_distribution.verify_mounted_payload'))
            dist.resume(self.manifest, self.helper, output, self.env, self.qualified, source,
                        dist.checksum(source / 'checkpoint.json'))

    def test_interrupted_dmg_wait_reuses_original_payload_and_dmg_without_new_post(self):
        before = {p.name: dist.checksum(p) for p in self.checkpoint.path.iterdir()}
        first = self.root / 'first'; self.wait_failure = True
        with self.assertRaises(dist.CommandFailure): self.continue_from(self.checkpoint.path, first)
        child = first.with_name('first.checkpoint')
        self.assertFalse((first / 'macos-distribution.json').exists())
        state = json.loads((child / 'checkpoint.json').read_text())
        self.assertEqual(state['submissions']['payload']['id'], SUBMISSION)
        self.assertEqual(state['dmg_state'], 'signed')
        self.wait_failure = False
        second = self.root / 'second'; self.continue_from(child, second)
        self.assertEqual(len(self.signs), 1)
        self.assertEqual(len([c for c in self.calls if c[:3] == ('xcrun', 'notarytool', 'submit')]), 1)
        self.assertEqual((second / self.qualified.name).read_bytes(), (self.checkpoint.path / 'signed-updater.tar.gz').read_bytes())
        self.assertEqual(before, {p.name: dist.checksum(p) for p in self.checkpoint.path.iterdir() if p.is_file()})
        self.assertEqual(json.loads((self.checkpoint.path / '.continuation-claimed/successor.json').read_text())[
            'successor_checkpoint'], str(child))

    def test_delayed_payload_ticket_and_completed_staple_are_resumable_read_only(self):
        self.gate_failure = STAGES[0]; first = self.root / 'first'
        with self.assertRaises(dist.CommandFailure): self.continue_from(self.checkpoint.path, first)
        self.assertEqual(self.signs, [])
        self.gate_failure = STAGES[1]; second = self.root / 'second'
        with self.assertRaises(dist.CommandFailure): self.continue_from(first.with_name('first.checkpoint'), second)
        self.gate_failure = None; third = self.root / 'third'
        self.continue_from(second.with_name('second.checkpoint'), third)
        self.assertEqual(len(self.signs), 1)
        self.assertEqual(len([c for c in self.calls if c[:3] == ('xcrun', 'notarytool', 'submit')]), 1)
        self.assertEqual(len([c for c in self.calls if c[:3] == ('xcrun', 'stapler', 'staple')]), 1)

    def test_lost_post_response_preserves_intent_and_refuses_before_credential_import(self):
        self.unknown_response = True; first = self.root / 'first'
        with self.assertRaises(dist.CommandFailure): self.continue_from(self.checkpoint.path, first)
        child = first.with_name('first.checkpoint'); state = json.loads((child / 'checkpoint.json').read_text())
        self.assertIn('notary', state['intents']); self.assertNotIn('notary', state['submissions'])
        before = self.imports
        with self.assertRaisesRegex(ValueError, 'outcome uncertain'): self.continue_from(child, self.root / 'second')
        self.assertEqual(self.imports, before)
        self.assertEqual(len([c for c in self.calls if c[:3] == ('xcrun', 'notarytool', 'submit')]), 1)

    def test_stale_local_parent_cannot_fork_a_second_accepted_outer_submission(self):
        self.continue_from(self.checkpoint.path, self.root / 'first')
        with self.assertRaisesRegex(ValueError, 'already continued'):
            self.continue_from(self.checkpoint.path, self.root / 'fork')
        self.assertEqual(len(self.signs), 1)
        self.assertEqual(len([c for c in self.calls if c[:3] == ('xcrun', 'notarytool', 'submit')]), 1)
        self.assertFalse((self.root / 'fork').exists())

    def test_racing_local_parent_claim_allows_only_one_successor(self):
        barrier = Barrier(2); load = dist.SignedCheckpoint.load
        def synchronized(*args):
            original = load(*args); barrier.wait(timeout=5); return original
        def reopen(index):
            try:
                return dist.SignedCheckpoint.reopen(self.checkpoint.path, self.root / ('race-' + str(index)),
                    REVISION, self.manifest, dist.checksum(self.helper),
                    dist.checksum(self.checkpoint.path / 'checkpoint.json'), self.checkpoint.value['context'])
            except FileExistsError:
                return None
        with patch.object(dist.SignedCheckpoint, 'load', side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(reopen, (1, 2)))
        self.assertEqual(sum(item is not None for item in result), 1)
        self.assertEqual(self.signs, []); self.assertEqual(self.calls, [])

    def test_failed_successor_copy_keeps_claim_and_refuses_all_external_effects(self):
        copyfile = dist.shutil.copyfile
        def fail(source, target, **kwargs):
            if Path(target).parent.name == 'failed.checkpoint': raise OSError('INERT COPY FAILURE')
            return copyfile(source, target, **kwargs)
        with patch.object(dist.shutil, 'copyfile', side_effect=fail):
            with self.assertRaises(OSError): self.continue_from(self.checkpoint.path, self.root / 'failed')
        claim = json.loads((self.checkpoint.path / '.continuation-claimed/successor.json').read_text())
        self.assertEqual(claim['successor_checkpoint'], str(self.root / 'failed.checkpoint'))
        with self.assertRaisesRegex(ValueError, 'already continued'):
            self.continue_from(self.checkpoint.path, self.root / 'another')
        self.assertEqual(self.imports, 0); self.assertEqual(self.signs, [])
        self.assertFalse(any(c[:3] == ('xcrun', 'notarytool', 'submit') for c in self.calls))

    def test_invalid_native_signature_is_rejected_before_credential_import_or_claim(self):
        inspect = self.signature_for
        self.signature_for = lambda *args, **kwargs: dict(inspect(*args, **kwargs), sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'retained native signature differs'):
            self.continue_from(self.checkpoint.path, self.root / 'invalid')
        self.assertEqual(self.imports, 0)
        self.assertFalse((self.checkpoint.path / '.continuation-claimed').exists())

    def test_malformed_apple_identifier_is_sanitized_and_keeps_uncertain_intent(self):
        command = self.command
        def malformed(*args, **kwargs):
            if args[:3] == ('xcrun', 'notarytool', 'submit'):
                return json.dumps({'id': 'SENTINEL-ACCOUNT-DETAIL-MUST-NOT-LEAK'})
            return command(*args, **kwargs)
        self.command = malformed; first = self.root / 'first'
        with self.assertRaisesRegex(ValueError, 'invalid Apple submission identifier') as caught:
            self.continue_from(self.checkpoint.path, first)
        self.assertNotIn('SENTINEL', str(caught.exception))
        state = json.loads((first.with_name('first.checkpoint') / 'checkpoint.json').read_text())
        self.assertIn('notary', state['intents']); self.assertNotIn('notary', state['submissions'])

    def test_wrong_context_corrupt_bytes_and_incomplete_state_refuse_before_credentials(self):
        for mutation in ('identity', 'qualification', 'bytes', 'intent', 'mutation'):
            with self.subTest(mutation=mutation):
                original = (self.checkpoint.path / 'checkpoint.json').read_bytes()
                state = json.loads(original)
                if mutation == 'identity': state['context']['identity']['team_id'] = 'WRONGTEAM0'
                if mutation == 'qualification': state['context']['qualification'] = {'run_id': 99}
                if mutation == 'bytes': state['artifacts']['payload.zip']['sha256'] = '0' * 64
                if mutation == 'intent': state['intents'] = {'notary': '0' * 64}
                if mutation == 'mutation': state['dmg_state'] = 'stapling'
                dist.write_json(self.checkpoint.path / 'checkpoint.json', state)
                with self.assertRaises(ValueError): self.continue_from(self.checkpoint.path, self.root / mutation)
                self.assertEqual(self.imports, 0)
                (self.checkpoint.path / 'checkpoint.json').write_bytes(original)


if __name__ == '__main__':
    unittest.main()
