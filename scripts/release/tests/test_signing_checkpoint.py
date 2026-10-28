"""Failure-lifecycle peers: no Apple tools, credentials, or signing operations."""
import json
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
            if args[2] == 'submit':
                return json.dumps({'id': SUBMISSION})
            if args[2] == 'wait':
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
        self.assertEqual([call[2] for call in calls], ['store-credentials', 'submit', 'wait', 'log'])
        self.assertFalse((evidence / 'payload-log.json').exists())
        self.assertFalse(self.output.exists())
        self.assertEqual(self.persisted()['status'], 'not-ready')


if __name__ == '__main__':
    unittest.main()
