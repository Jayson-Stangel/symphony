"""Command peers exercise refusal and ordering, never Apple credentials/trust."""
import base64
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import macos_distribution as dist

CERTIFICATE = b'OBVIOUS FAKE CERTIFICATE - NOT A CREDENTIAL'
IDENTITY = hashlib.sha1(CERTIFICATE).hexdigest().upper()
TEAM = 'FAKETEAM00'
REVISION = 'a' * 40
SUBMISSION = '11111111-2222-3333-4444-555555555555'
PEER = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
tool=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['PEER_LOG'],'a') as log:log.write(json.dumps([tool,*args])+'\n')
operation=tool+' '+(' '.join(args[:2]))
if (os.environ.get('PEER_FAIL') and os.environ['PEER_FAIL'] in operation) or any(
        failure in operation for failure in json.loads(os.environ.get('PEER_FAILURES','[]'))):
    print('FAKE-SECRET-MUST-NOT-LEAK '+os.environ.get('PEER_ERROR', ''),file=sys.stderr);sys.exit(44)
if tool=='security':
    if args[0]=='list-keychains':
        state=pathlib.Path(os.environ['PEER_LOG']+'.keychains')
        if '-s' in args:
            if not os.environ.get('PEER_IGNORE_SEARCH_LIST'):
                state.write_text(json.dumps(args[args.index('-s')+1:]))
        else:
            for entry in json.loads(state.read_text()) if state.exists() else ['/original/login.keychain-db']:
                print(json.dumps(entry))
    if args[0]=='create-keychain':pathlib.Path(args[-1]).write_text('fake keychain')
    if args[0]=='delete-keychain':pathlib.Path(args[-1]).unlink()
    if args[0]=='find-identity':
        print('1) '+os.environ['MACOS_SIGNING_IDENTITY_SHA1']+' "Developer ID Application: Fixture ('+os.environ.get('PEER_TEAM','FAKETEAM00')+')"')
elif tool=='codesign':
    if '--force' in args:
        if os.environ.get('PEER_REQUIRE_SEARCH_LIST'):
            state=pathlib.Path(os.environ['PEER_LOG']+'.keychains')
            entries=json.loads(state.read_text()) if state.exists() else ['/original/login.keychain-db']
            if args[args.index('--keychain')+1] not in entries:
                print('no identity found FAKE-SECRET-MUST-NOT-LEAK',file=sys.stderr);sys.exit(44)
        with open(args[-1],'ab') as f:f.write(b'-fixture-signature')
    elif any(arg.split('=',1)[0]=='--extract-certificates' for arg in args):
        # codesign's optional prefix is accepted only as --option=value.
        # A separate prefix is another code path, not an option argument.
        options=[arg for arg in args if arg.split('=',1)[0]=='--extract-certificates']
        targets=[arg for arg in args if not arg.startswith('-')]
        if len(options)!=1 or len(targets)!=1 or not pathlib.Path(targets[0]).is_file():
            sys.exit(1)
        prefix=options[0].partition('=')[2] if '=' in options[0] else 'codesign'
        pathlib.Path(prefix+'0').write_bytes(
            b'WRONG FAKE CERTIFICATE' if os.environ.get('PEER_WRONG_CERT') else b'OBVIOUS FAKE CERTIFICATE - NOT A CREDENTIAL')
    elif '--entitlements' not in args and '-d' in args:
        print('Authority=Developer ID Application: Fixture (FAKETEAM00)')
        print('TeamIdentifier='+os.environ.get('PEER_TEAM','FAKETEAM00'))
        print('CDHash='+'a'*40)
        if not os.environ.get('PEER_NO_TIMESTAMP'):print('Timestamp=FAKE timestamp')
        if not os.environ.get('PEER_NO_RUNTIME'):print('CodeDirectory v=20500 flags=0x10000(runtime) hashes=1')
elif tool=='hdiutil' and args[0]=='create':pathlib.Path(args[-1]).write_bytes(b'FAKE-DMG')
elif tool=='ditto':pathlib.Path(args[-1]).write_bytes(b'FAKE-PAYLOAD-ZIP')
elif tool=='xcrun':
    if args[:2]==['notarytool','submit']:
        pathlib.Path(os.environ['PEER_LOG']+'.submission').write_text(args[2])
        print(json.dumps({'id':'11111111-2222-3333-4444-555555555555','status':os.environ.get('PEER_STATUS','Accepted')}))
    elif args[:2]==['notarytool','wait']:
        print(json.dumps({'id':'11111111-2222-3333-4444-555555555555','status':os.environ.get('PEER_STATUS','Accepted')}))
    elif args[:2]==['notarytool','log']:
        import hashlib
        submitted=pathlib.Path(pathlib.Path(os.environ['PEER_LOG']+'.submission').read_text()).read_bytes()
        print(json.dumps({'jobId':os.environ.get('PEER_LOG_ID','11111111-2222-3333-4444-555555555555'),'status':os.environ.get('PEER_STATUS','Accepted'),'statusCode':0,'sha256':os.environ.get('PEER_LOG_HASH',hashlib.sha256(submitted).hexdigest()),'issues':[],
                          'message':'FAKE-SECRET-MUST-NOT-LEAK','uploadUrl':'PRIVATE URL'}))
    elif args[:2]==['stapler','staple']:
        with open(args[-1],'ab') as f:f.write(b'-stapled')
'''


@contextmanager
def private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


class DistributionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        for tool in ('security', 'codesign', 'hdiutil', 'xcrun', 'spctl', 'ditto'):
            path = self.tools / tool
            path.write_text(PEER)
            path.chmod(0o755)
        self.log = self.root / 'commands.jsonl'
        self.env = dict(os.environ, RUNNER_TEMP=str(self.root), PATH=str(self.tools) + os.pathsep + os.environ['PATH'],
                        MACOS_SIGNING_P12_BASE64=base64.b64encode(b'FAKE P12').decode(),
                        MACOS_SIGNING_P12_PASSWORD='FAKE password', MACOS_SIGNING_IDENTITY_SHA1=IDENTITY,
                        MACOS_TEAM_ID=TEAM, MACOS_NOTARY_APPLE_ID='fixture@example.invalid',
                        MACOS_NOTARY_APP_PASSWORD='FAKE app password', PEER_LOG=str(self.log))
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_missing_credentials_refuse_before_any_command(self):
        for name in dist.REQUIRED:
            env = dict(self.env)
            del env[name]
            with self.assertRaisesRegex(ValueError, name):
                dist.build(self.root / 'manifest', self.root / 'helper', self.root / 'output', env)
        self.assertFalse(self.log.exists())

    def keychain(self):
        private = self.root / 'private'
        private.mkdir(mode=0o700, exist_ok=True)
        evidence = self.root / 'evidence'
        evidence.mkdir(exist_ok=True)
        return dist.signing_keychain(self.env, private, evidence)

    def test_signing_keychain_is_visible_only_inside_owned_scope(self):
        helper = self.root / 'helper'
        helper.write_bytes(b'FAKE HELPER')
        state = Path(str(self.log) + '.keychains')
        originals = ['/original/login.keychain-db', '/original/with spaces.keychain-db']
        state.write_text(json.dumps(originals))
        with patch.dict(os.environ, PEER_REQUIRE_SEARCH_LIST='1'):
            # A specific --keychain alone does not satisfy this command peer.
            with self.assertRaises(dist.CommandFailure):
                dist.sign(helper, self.root / 'private/signing.keychain-db', self.env)
            with self.keychain() as keychain:
                self.assertEqual(json.loads(state.read_text()), [str(keychain), *originals])
                dist.sign(helper, keychain, self.env)
        self.assertEqual(json.loads(state.read_text()), originals)
        self.assertFalse(keychain.exists())
        self.assertFalse(any(c[:2] == ['security', 'default-keychain'] for c in self.commands()))
        self.assert_private_diagnostics()

    def test_search_list_setup_failures_refuse_before_signing_and_restore(self):
        real_run = dist.run
        for failed in ('keychain.configure-search-list', 'keychain.read-search-list'):
            def invoke(*args, **kwargs):
                if kwargs.get('stage') == failed:
                    # Model partial mutation before failure of the setup command.
                    if failed == 'keychain.configure-search-list':
                        real_run(*args, **kwargs)
                    raise dist.CommandFailure(failed, 'failed', 45)
                return real_run(*args, **kwargs)
            with self.subTest(stage=failed), patch.object(dist, 'run', side_effect=invoke):
                with self.assertRaisesRegex(dist.CommandFailure, failed):
                    with self.keychain():
                        self.fail('search-list setup must complete before P12 import/signing')
            self.assertEqual(json.loads(Path(str(self.log)+'.keychains').read_text()),
                             ['/original/login.keychain-db'])
            self.assert_private_diagnostics()
        self.assertFalse(any(c[0] in ('codesign', 'xcrun') for c in self.commands()))

    def test_search_list_readback_mismatch_refuses_before_import(self):
        with patch.dict(os.environ, PEER_IGNORE_SEARCH_LIST='1'):
            with self.assertRaisesRegex(ValueError, 'search list differs'):
                with self.keychain():
                    self.fail('search-list readback must match')
        records = {x['stage']: x for x in self.diagnostics()}
        self.assertEqual(records['keychain.verify-search-list']['status'], 'failed')
        self.assertFalse(any(c[0] in ('codesign', 'xcrun') or c[:2] == ['security', 'import']
                             for c in self.commands()))
        self.assert_private_diagnostics()

    def test_search_list_restore_failure_refuses_readiness(self):
        real_run = dist.run
        def invoke(*args, **kwargs):
            if kwargs.get('stage') == 'cleanup.restore-search-list':
                raise dist.CommandFailure('cleanup.restore-search-list', 'failed', 45)
            return real_run(*args, **kwargs)
        with patch.object(dist, 'run', side_effect=invoke):
            with self.assertRaisesRegex(dist.CommandFailure, 'cleanup.restore-search-list'):
                with self.keychain():
                    pass
        records = {x['stage']: x for x in self.diagnostics()}
        self.assertEqual(records['cleanup.verify-search-list-restored']['status'], 'failed')
        self.assertEqual(records['cleanup.verify-keychain-absent']['status'], 'success')
        self.assertEqual(records['cleanup.verify-p12-absent']['status'], 'success')
        self.assert_private_diagnostics()

    def test_recovery_record_is_private_and_durable_before_p12_decode(self):
        decode = dist.base64.b64decode
        def inspect(*args, **kwargs):
            record = self.root / 'private' / dist.CLEANUP_RECORD
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
            self.assertEqual(json.loads(record.read_text()),
                             {'schema': 1, 'search_list': ['/original/login.keychain-db']})
            self.assertFalse((record.parent / 'identity.p12').exists())
            return decode(*args, **kwargs)
        with patch.object(dist.base64, 'b64decode', side_effect=inspect):
            with self.keychain():
                pass
        self.assertFalse((self.root / 'private' / dist.CLEANUP_RECORD).exists())

    def test_primary_failure_and_cancellation_preserve_uncertain_cleanup_for_hook(self):
        real_run = dist.run
        def invoke(*args, **kwargs):
            if kwargs.get('stage') == 'cleanup.restore-search-list':
                raise dist.CommandFailure('cleanup.restore-search-list', 'failed', 45)
            return real_run(*args, **kwargs)
        for failure in (ValueError('primary refused'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), patch.object(dist, 'run', side_effect=invoke):
                with self.assertRaises(type(failure)) as caught:
                    with dist.signing_directory({'RUNNER_TEMP': str(self.root)}) as private:
                        with dist.signing_keychain(self.env, private):
                            raise failure
                self.assertIs(caught.exception, failure)
                self.assertTrue(private.is_dir())
                self.assertTrue((private / dist.CLEANUP_RECORD).is_file())
                # Ordinary retry must not overwrite unresolved cleanup metadata.
                count = len(self.commands())
                with self.assertRaisesRegex(ValueError, 'unreconciled'):
                    with dist.signing_keychain(self.env, private):
                        self.fail('must refuse')
                self.assertEqual(len(self.commands()), count)
                # Repair only this inert command-peer state for the next case.
                Path(str(self.log) + '.keychains').write_text(json.dumps(['/original/login.keychain-db']))

    def test_verified_cleanup_removes_private_directory_after_primary_error(self):
        with self.assertRaisesRegex(ValueError, 'primary refused'):
            with dist.signing_directory({'RUNNER_TEMP': str(self.root)}) as private:
                with dist.signing_keychain(self.env, private):
                    raise ValueError('primary refused')
        self.assertFalse(private.exists())

    def test_wrong_team_import_failure_and_interruption_cleanup(self):
        for failure in ('wrong-team', 'security import', 'interrupt'):
            with self.subTest(failure=failure):
                with patch.dict(os.environ, {'PEER_TEAM': 'OTHERTEAM0'} if failure == 'wrong-team'
                                else {'PEER_FAIL': failure}):
                    with self.assertRaises((ValueError, KeyboardInterrupt)):
                        with self.keychain():
                            if failure == 'interrupt':
                                raise KeyboardInterrupt()
                self.assertFalse((self.root / 'private/identity.p12').exists())
                self.assertFalse((self.root / 'private/signing.keychain-db').exists())
        self.assertEqual(len([cmd for cmd in self.commands() if cmd[:2] == ['security', 'delete-keychain']]), 3)
        self.assertFalse(any(cmd[:2] == ['security', 'default-keychain'] for cmd in self.commands()))

    def diagnostics(self):
        return json.loads((self.root / 'evidence/keychain-diagnostics.json').read_text())['operations']

    def assert_private_diagnostics(self, error=''):
        text = (self.root / 'evidence/keychain-diagnostics.json').read_text() + str(error)
        for secret in ('FAKE-SECRET-MUST-NOT-LEAK', 'FAKE password', 'FAKE app password',
                       'fixture@example.invalid', self.env['MACOS_SIGNING_P12_BASE64'],
                       str(self.root), IDENTITY, TEAM):
            self.assertNotIn(secret, text)
        for operation in self.diagnostics():
            self.assertLessEqual(set(operation), {'stage', 'status', 'outcome', 'exit_status'})

    def test_each_security_setup_failure_has_exact_stage_and_status(self):
        cases = [('list-keychains', 'snapshot-search-list'), ('create-keychain', 'create'),
                 ('set-keychain-settings', 'settings'), ('unlock-keychain', 'unlock'),
                 ('import', 'import-p12'), ('set-key-partition-list', 'partition-list'),
                 ('find-identity', 'find-identity')]
        for command, stage in cases:
            with self.subTest(stage=stage), patch.dict(os.environ, PEER_FAIL='security ' + command):
                with self.assertRaisesRegex(ValueError, 'keychain.' + stage) as error:
                    with self.keychain():
                        self.fail('failed setup must not yield a signing keychain')
                record = next(x for x in self.diagnostics() if x['stage'] == 'keychain.' + stage)
                self.assertEqual(record['exit_status'], 44)
                self.assertEqual(record['status'], 'failed')
                self.assert_private_diagnostics(error.exception)
                self.assertFalse((self.root / 'private/identity.p12').exists())
                self.assertFalse((self.root / 'private/signing.keychain-db').exists())
        self.assertTrue(all(cmd[0] == 'security' for cmd in self.commands()))

    def test_primary_error_survives_cleanup_failure_and_all_cleanup_is_attempted(self):
        with patch.dict(os.environ, PEER_FAILURES=json.dumps(['security import', 'security delete-keychain'])):
            with self.assertRaisesRegex(ValueError, 'keychain.import-p12') as error:
                with self.keychain():
                    self.fail('must not sign')
        records = {x['stage']: x for x in self.diagnostics()}
        self.assertEqual(records['cleanup.delete-keychain']['exit_status'], 44)
        self.assertEqual(records['cleanup.verify-keychain-absent']['status'], 'failed')
        self.assertEqual(records['cleanup.verify-search-list-restored']['status'], 'success')
        self.assertEqual(records['cleanup.verify-p12-absent']['status'], 'success')
        self.assert_private_diagnostics(error.exception)

    def test_cleanup_failure_refuses_success_and_preserves_interruption(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), patch.dict(os.environ, PEER_FAIL='security delete-keychain'):
                expected = KeyboardInterrupt if interrupted else ValueError
                with self.assertRaises(expected):
                    with self.keychain():
                        if interrupted:
                            raise KeyboardInterrupt()
                self.assertEqual(next(x for x in self.diagnostics()
                                      if x['stage'] == 'cleanup.verify-search-list-restored')['status'], 'success')
                self.assert_private_diagnostics()
                (self.root / 'private').rename(self.root / ('retained-' + str(interrupted)))

    def test_evidence_io_failure_does_not_block_cleanup_or_mask_primary_error(self):
        with patch.object(dist, 'write_json', side_effect=OSError('FAKE-SECRET-MUST-NOT-LEAK')):
            with patch.dict(os.environ, PEER_FAIL='security import'):
                with self.assertRaisesRegex(ValueError, 'keychain.import-p12') as error:
                    with self.keychain():
                        self.fail('must not sign')
                self.assertNotIn('FAKE-SECRET-MUST-NOT-LEAK', str(error.exception))
            with self.assertRaisesRegex(ValueError, 'keychain cleanup failed'):
                with self.keychain():
                    pass
        self.assertFalse((self.root / 'private/identity.p12').exists())
        self.assertFalse((self.root / 'private/signing.keychain-db').exists())
        self.assertEqual(len([c for c in self.commands() if c[:2] == ['security', 'delete-keychain']]), 2)

    def test_cleanup_inspect_failure_does_not_overwrite_unobserved_search_list(self):
        real_run = dist.run

        def invoke(*args, **kwargs):
            if kwargs.get('stage') == 'cleanup.inspect-search-list':
                raise dist.CommandFailure('cleanup.inspect-search-list', 'failed', 45)
            return real_run(*args, **kwargs)

        with patch.object(dist, 'run', side_effect=invoke):
            with self.assertRaisesRegex(ValueError, 'cleanup.inspect-search-list'):
                with self.keychain():
                    pass
        records = {x['stage']: x for x in self.diagnostics()}
        self.assertNotIn('cleanup.restore-search-list', records)
        self.assertEqual(records['cleanup.check-search-list']['status'], 'failed')
        self.assertTrue((self.root / 'private' / dist.CLEANUP_RECORD).exists())

    def test_cleanup_does_not_remove_unrelated_concurrent_keychain_entry(self):
        with self.assertRaisesRegex(ValueError, 'keychain cleanup failed'):
            with self.keychain():
                state = Path(str(self.log) + '.keychains')
                observed = json.loads(state.read_text()) + ['/unrelated/added.keychain-db']
                state.write_text(json.dumps(observed))
        self.assertEqual(json.loads(state.read_text()), observed)
        self.assertTrue((self.root / 'private' / dist.CLEANUP_RECORD).exists())

    def test_keychain_stat_failure_does_not_mask_import_or_skip_search_list_cleanup(self):
        real_exists = Path.exists

        def exists(path):
            if path.name == 'signing.keychain-db':
                raise OSError('FAKE-SECRET-MUST-NOT-LEAK')
            return real_exists(path)

        with patch.object(Path, 'exists', exists), patch.dict(os.environ, PEER_FAIL='security import'):
            with self.assertRaisesRegex(ValueError, 'keychain.import-p12') as error:
                with self.keychain():
                    self.fail('must not sign')
        records = {x['stage']: x for x in self.diagnostics()}
        self.assertEqual(records['cleanup.delete-keychain']['status'], 'failed')
        self.assertEqual(records['cleanup.verify-search-list-restored']['status'], 'success')
        self.assert_private_diagnostics(error.exception)

    def test_identity_mismatch_has_no_private_identity_output(self):
        with patch.dict(os.environ, PEER_TEAM='OTHERTEAM0'), self.assertRaisesRegex(ValueError, 'identity/team') as error:
            with self.keychain():
                self.fail('must not sign')
        self.assertEqual(next(x for x in self.diagnostics()
                              if x['stage'] == 'keychain.match-pinned-identity')['status'], 'failed')
        self.assert_private_diagnostics(error.exception)

    def test_timeout_and_spawn_failure_diagnostics_suppress_raw_details(self):
        evidence = self.root / 'evidence'
        evidence.mkdir()
        for exception, outcome in ((subprocess.TimeoutExpired(['SECRET-ARGV'], 1, output='FAKE-SECRET-MUST-NOT-LEAK'), 'timed out'),
                                   (OSError('FAKE-SECRET-MUST-NOT-LEAK'), 'could not start')):
            journal = dist.KeychainDiagnostics(evidence)
            with patch.object(dist.subprocess, 'run', side_effect=exception):
                with self.assertRaisesRegex(ValueError, 'keychain.import-p12') as error:
                    journal.command('keychain.import-p12', 'security', 'SECRET-ARGV')
            self.assertEqual(self.diagnostics()[0]['outcome'], outcome)
            self.assertNotIn('exit_status', self.diagnostics()[0])
            self.assert_private_diagnostics(error.exception)

    def test_native_signature_options_identity_and_staged_copy_binding(self):
        bundle = self.root / 'bundle'
        (bundle / 'libexec').mkdir(parents=True)
        original = self.root / 'qualified-helper'
        original.write_bytes(bytes.fromhex('cffaedfe') + b'FAKE native payload')
        helper = bundle / 'libexec/symphony-host'
        helper.write_bytes(original.read_bytes())
        real_run = dist.run

        def invoke(*args, **kwargs):
            if str(args[0]) == str(helper) or args[0] == 'git':
                return REVISION
            return real_run(*args, **kwargs)

        with self.keychain() as keychain, patch.object(dist, 'run', side_effect=invoke):
            dist.sign_payload(bundle, keychain, self.env)
        receipt = json.loads((bundle / 'signing.json').read_text())
        self.assertEqual(receipt['original_qualified_helper_sha256'], dist.checksum(original))
        self.assertEqual(receipt['payloads']['libexec/symphony-host']['sha256'], dist.checksum(helper))
        self.assertNotEqual(dist.checksum(original), dist.checksum(helper))
        sign = next(cmd for cmd in self.commands() if '--force' in cmd)
        self.assertIn('--timestamp', sign)
        self.assertEqual(sign[sign.index('--options') + 1], 'runtime')
        self.assertNotIn('--deep', sign)
        extract = next(cmd for cmd in self.commands()
                       if any(arg.startswith('--extract-certificates=') for arg in cmd))
        self.assertEqual(len(extract), 4)
        self.assertEqual(extract[-1], str(helper))
        prefix = extract[2].partition('=')[2]
        self.assertFalse(Path(prefix).parent.exists())
        self.assertEqual(receipt['payloads']['libexec/symphony-host']['certificate_sha1'], IDENTITY)

    def test_certificate_peer_models_optional_prefix_and_code_targets(self):
        payload = self.root / 'native'
        payload.write_bytes(b'fake')
        prefix = self.root / 'certificate with spaces-'
        with self.assertRaises(dist.CommandFailure):
            dist.run('codesign', '-d', '--extract-certificates', prefix, payload,
                     stage='signature.certificate')
        self.assertFalse(Path(str(prefix) + '0').exists())
        dist.run('codesign', '-d', '--extract-certificates=' + str(prefix), payload,
                 stage='signature.certificate')
        self.assertEqual(Path(str(prefix) + '0').read_bytes(), CERTIFICATE)

    def test_signature_failure_never_becomes_receipt(self):
        payload = self.root / 'native'
        payload.write_bytes(b'fake')
        for failure in ('codesign --force', 'codesign --verify'):
            with patch.dict(os.environ, PEER_FAIL=failure), self.keychain() as keychain:
                with self.assertRaisesRegex(ValueError, 'distribution command failed') as error:
                    dist.sign(payload, keychain, self.env)
                self.assertNotIn('FAKE-SECRET-MUST-NOT-LEAK', str(error.exception))
        with patch.dict(os.environ, PEER_TEAM='OTHERTEAM0'):
            with self.assertRaisesRegex(ValueError, 'team differs'):
                dist.sign(payload, self.root / 'keychain', self.env)

    def notary(self):
        dmg = self.root / 'distribution.dmg'
        dmg.write_bytes(b'fake distribution')
        evidence = self.root / 'evidence'
        evidence.mkdir(exist_ok=True)
        return dmg, evidence

    def test_wrong_leaf_certificate_missing_timestamp_or_runtime_refuse(self):
        payload = self.root / 'native'
        payload.write_bytes(b'fake')
        for changed, reason in (({'PEER_WRONG_CERT': '1'}, 'certificate differs'),
                                ({'PEER_NO_TIMESTAMP': '1'}, 'timestamp missing'),
                                ({'PEER_NO_RUNTIME': '1'}, 'runtime missing')):
            with patch.dict(os.environ, changed), self.assertRaisesRegex(ValueError, reason):
                dist.sign(payload, self.root / 'keychain', self.env)

    def test_wrong_apple_submission_or_archive_binding_refuses_before_stapling(self):
        for changed in ({'PEER_LOG_HASH': '0'*64}, {'PEER_LOG_ID': 'foreign-submission'}):
            dmg, evidence = self.notary()
            before = dist.checksum(dmg)
            with patch.dict(os.environ, changed), self.assertRaisesRegex(ValueError, 'bind submitted archive'):
                dist.notarize(dmg, self.root / 'keychain', self.env, evidence)
            self.assertEqual(dist.checksum(dmg), before)

    def test_notary_rejection_timeout_stapling_and_assessment_fail_closed(self):
        for status, failure in (('Invalid', ''), ('In Progress', ''), ('Accepted', 'xcrun notarytool submit'),
                                ('Accepted', 'xcrun notarytool wait'),
                                ('Accepted', 'xcrun stapler staple'), ('Accepted', 'xcrun stapler validate'),
                                ('Accepted', 'spctl --assess')):
            with self.subTest(status=status, failure=failure):
                dmg, evidence = self.notary()
                with patch.dict(os.environ, PEER_STATUS=status, PEER_FAIL=failure):
                    with self.assertRaises(ValueError):
                        dist.notarize(dmg, self.root / 'keychain', self.env, evidence)
                for path in evidence.glob('*.json'):
                    self.assertNotIn('PRIVATE URL', path.read_text())
                    self.assertNotIn('FAKE-SECRET-MUST-NOT-LEAK', path.read_text())
                if failure == 'xcrun notarytool wait':
                    receipt = json.loads((evidence / 'notary-submission.json').read_text())
                    self.assertEqual(receipt['id'], SUBMISSION)
                    self.assertIn('timed out', receipt['status'])

    def test_accepted_staples_validates_assesses_then_final_hash_changes(self):
        dmg, evidence = self.notary()
        before = dist.checksum(dmg)
        self.assertEqual(dist.notarize(dmg, self.root / 'keychain', self.env, evidence), SUBMISSION)
        self.assertNotEqual(before, dist.checksum(dmg))
        receipt = json.loads((evidence / 'notary-submission.json').read_text())
        self.assertEqual(receipt['submitted_sha256'], before)
        operations = [(cmd[0], *cmd[1:3]) for cmd in self.commands()]
        self.assertLess(operations.index(('xcrun', 'stapler', 'staple')),
                        operations.index(('xcrun', 'stapler', 'validate')))
        self.assertEqual(self.commands()[-1][0], 'spctl')

    def test_real_subprocess_timeout_is_sanitized(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            dist.run(sys.executable, '-c', 'import time; time.sleep(1)', timeout=0.01)

    def test_build_publishes_only_after_stapling_and_cleanup(self):
        helper = self.root / 'qualified-helper'
        helper.write_bytes(b'ORIGINAL FAKE QUALIFIED BYTES')
        original = dist.checksum(helper)
        manifest = self.root / 'release.json'
        manifest.write_text('{}')
        qualified = self.root / 'qualified-package.tar.gz'
        original_bundle = self.root / 'original-bundle'
        (original_bundle / 'libexec').mkdir(parents=True)
        (original_bundle / 'libexec/symphony-host').write_bytes(helper.read_bytes())
        (original_bundle / 'libexec/symphony-host').chmod(0o755)
        (original_bundle / 'source.txt').write_bytes(b'unchanged source')
        (original_bundle / 'distribution-status.txt').write_text('unsigned')
        (original_bundle / 'files.sha256').write_text('original inventory')
        with tarfile.open(qualified, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
            archive.add(original_bundle, arcname='.')
        real_run = dist.run

        def invoke(*args, **kwargs):
            if args[0] == 'git':
                return '' if 'status' in args else REVISION
            if args[0] == 'bash':
                diagnostic_path = Path(kwargs['env'][dist.package_evidence.VARIABLE])
                if failure != 'package evidence':
                    diagnostic_path.write_text(json.dumps({'schema': 1, 'operations': [
                        {'stage': label, 'status': 'success'} for label in dist.package_evidence.STAGES]}))
                output = Path(args[3])
                output.mkdir()
                content = self.root / 'packaged'
                (content / 'libexec').mkdir(parents=True, exist_ok=True)
                content.chmod(0o700 if failure == 'directory modes' else 0o755)
                (content / 'libexec').chmod(0o755)
                (content / 'libexec/symphony-host').write_bytes(b'FAKE SIGNED BYTES')
                (content / 'libexec/symphony-host').chmod(0o755)
                (content / 'source.txt').write_bytes(b'unchanged source')
                (content / 'source.txt').chmod(0o600 if failure == 'payload modes' else 0o644)
                dist.write_json(content / 'signing.json', {'original_qualified_helper_sha256': original,
                    'payloads': {'libexec/symphony-host': {'sha256': dist.checksum(content / 'libexec/symphony-host')}}})
                (content / 'signing.json').chmod(0o644)
                inventory = ''.join(dist.checksum(path) + '  ./' + str(path.relative_to(content)) + '\n'
                                    for path in sorted(content.rglob('*')) if path.is_file() and path.name != 'files.sha256')
                (content / 'files.sha256').write_text(inventory)
                (content / 'files.sha256').chmod(0o600 if failure == 'inventory modes' else 0o644)
                with tarfile.open(output / 'symphony-fixture-macos.tar.gz', 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
                    archive.add(content, arcname='.')
                (output / 'package-source.txt').write_text('fixture package source')
                return ''
            if args[:2] == ('hdiutil', 'create'):
                tree = Path(args[args.index('-srcfolder') + 1])
                self.assertEqual(stat.S_IMODE(tree.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((tree / 'Read Me.txt').stat().st_mode), 0o644)
                for path in (tree / 'Symphony').rglob('*'):
                    expected = 0o755 if path.is_dir() or path.name == 'symphony-host' else 0o644
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path))
                self.assertEqual(stat.S_IMODE((tree / 'Symphony').stat().st_mode), 0o755)
            if str(args[0]).endswith('/libexec/symphony-host'):
                return ''
            return real_run(*args, **kwargs)

        retained = set()
        for failure in ('package evidence', 'payload modes', 'directory modes', 'inventory modes', 'checkpoint retention', 'codesign -vvvv', 'spctl --assess', 'security delete-keychain', ''):
            output = self.root / (failure.split()[0] if failure else 'success')
            retention_failure = (patch.object(dist.SignedCheckpoint, 'retain', side_effect=OSError('FAKE SECRET /PRIVATE/PATH'))
                                 if failure == 'checkpoint retention' else nullcontext())
            with patch.object(dist.sys, 'platform', 'darwin'), patch.object(dist.os, 'uname') as uname, \
                    patch.object(dist, 'run', side_effect=invoke), patch.dict(os.environ, PEER_FAIL=failure), retention_failure, private_umask():
                uname.return_value.machine = 'arm64'
                if failure:
                    with self.assertRaises(OSError if failure == 'checkpoint retention' else ValueError):
                        dist.build(manifest, helper, output, self.env, qualified)
                    self.assertFalse((output / 'macos-distribution.json').exists())
                    if not failure.startswith('security'):
                        self.assertFalse(list(output.glob('*.dmg')))
                    self.assertTrue((output / 'evidence/keychain-diagnostics.json').is_file())
                    if failure in ('package evidence', 'payload modes', 'directory modes', 'inventory modes'):
                        self.assertFalse(any(command[0] == 'xcrun' for command in self.commands()))
                    if failure == 'checkpoint retention':
                        self.assertFalse(any(command[:3] == ['xcrun', 'notarytool', 'submit'] for command in self.commands()))
                        operations = json.loads((output / 'evidence/keychain-diagnostics.json').read_text())['operations']
                        self.assertTrue(all(item['status'] == 'success' for item in operations if item['stage'].startswith('cleanup.')))
                else:
                    dist.build(manifest, helper, output, self.env, qualified)
                    self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
                    checkpoint = output.with_name(output.name + '.checkpoint')
                    self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o700)
                    self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in checkpoint.iterdir()))
                    receipt = json.loads((output / 'macos-distribution.json').read_text())
                    self.assertEqual(receipt['status'], 'notarized-stapled-assessed')
                    for name, digest in receipt['artifacts'].items():
                        self.assertEqual(dist.checksum(output / name), digest)
                    self.assertTrue((output / 'symphony-fixture-macos.dmg').read_bytes().endswith(b'-stapled'))
                    self.assertEqual(dist.checksum(helper), original)
            current = set(self.root.glob('symphony-signing-*'))
            if failure == 'security delete-keychain':
                self.assertEqual(len(current - retained), 1)
                private = next(iter(current - retained))
                self.assertTrue((private / dist.CLEANUP_RECORD).is_file())
                self.assertTrue((private / 'signing.keychain-db').is_file())
            else:
                self.assertEqual(current, retained)
            retained = current

    def test_package_shell_failure_retains_nested_stage_without_output(self):
        evidence = self.root / 'package.json'
        script = dist.ROOT / 'scripts/release/package-diagnostics.sh'
        child = self.root / 'child.sh'
        child.write_text('source "$1"\ntrap \'package_diagnostics_exit "$?" "$package_diagnostics_line"\' EXIT\n'
                         'package_stage package.inputs\necho FAKE-SECRET-MUST-NOT-LEAK >&2\nexit 47\n')
        process = subprocess.run(['bash', '-e', '-c',
            'source "$1"; trap \'package_diagnostics_exit "$?" "$package_diagnostics_line"\' EXIT; '
            'package_stage assembly.source; package_stage assembly.package; bash -e "$2" "$1"',
            'fixture', str(script), str(child)], env=dict(self.env, **{dist.package_evidence.VARIABLE: str(evidence)}),
            capture_output=True, text=True)
        self.assertEqual(process.returncode, 47)
        value = json.loads(evidence.read_text())
        failed = [v for v in value['operations'] if v['status'] == 'failed']
        self.assertEqual([(v['stage'], v['exit_status']) for v in failed],
                         [('package.inputs', 47), ('assembly.package', 47)])
        self.assertEqual(failed[0]['source_line'], 5)
        self.assertNotIn('FAKE-SECRET', evidence.read_text())
        self.assertNotIn(str(self.root), evidence.read_text())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            dist.package_evidence.require_complete(evidence)

    def test_package_cleanup_preserves_command_line_and_exit_status(self):
        script = dist.ROOT / 'scripts/release/package-diagnostics.sh'
        for status in (0, 47):
            with self.subTest(status=status):
                stage = self.root / ('owned-' + str(status))
                stage.mkdir()
                evidence = self.root / ('cleanup-' + str(status) + '.json')
                driver = self.root / ('driver-' + str(status) + '.sh')
                driver.write_text('source "$1"\nstage=$2\n'
                    'trap \'package_diagnostics_cleanup "$?"\' EXIT\n'
                    'package_stage package.archive\n'
                    'echo FAKE-SECRET-MUST-NOT-LEAK > "$stage/private"\n'
                    'bash -c "exit ' + str(status) + '"\n')
                process = subprocess.run(['bash', '-e', str(driver), str(script), str(stage)],
                    env=dict(self.env, **{dist.package_evidence.VARIABLE: str(evidence)}),
                    capture_output=True, text=True)
                self.assertEqual(process.returncode, status)
                self.assertFalse(stage.exists())
                latest = json.loads(evidence.read_text())['operations'][-1]
                self.assertEqual(latest['status'], 'failed' if status else 'success')
                if status:
                    self.assertEqual(latest['source_line'], 6)
                    self.assertEqual(latest['exit_status'], status)
                self.assertNotIn('FAKE-SECRET', evidence.read_text())

    def test_payload_command_failure_records_safe_stage_and_status(self):
        evidence = self.root / 'package.json'
        helper = self.root / 'helper'
        helper.write_bytes(b'FAKE HELPER')
        with patch.dict(os.environ, {dist.package_evidence.VARIABLE: str(evidence), 'PEER_FAIL': 'codesign --force'}):
            with self.assertRaisesRegex(dist.CommandFailure, 'signature.sign.*44'):
                dist.sign(helper, self.root / 'fake-keychain', self.env)
        failed = json.loads(evidence.read_text())['operations'][-1]
        self.assertEqual(failed, {'stage': 'signature.sign', 'status': 'failed', 'outcome': 'failed', 'exit_status': 44, 'reason': 'unclassified'})
        self.assertNotIn('FAKE-SECRET', evidence.read_text())
        self.assertNotIn(IDENTITY, evidence.read_text())
        self.assertNotIn(str(helper), evidence.read_text())

    def test_network_error_takes_priority_over_generic_notarization_requirement(self):
        generic = 'code failed to satisfy specified code requirement(s)'
        for network in ('NSURLErrorDomain', 'could not connect to server', 'the network connection was lost'):
            with self.subTest(network=network):
                output = generic + '\n' + network + ' FAKE-SECRET /private/secret'
                result = subprocess.CompletedProcess([], 1, '', output)
                with patch.object(dist.subprocess, 'run', return_value=result):
                    with self.assertRaises(dist.CommandFailure) as caught:
                        dist.run('codesign', 'fixture', stage='notarization.payload-helper')
                self.assertEqual(caught.exception.reason, 'notarization-lookup-unavailable')
                self.assertNotIn('FAKE-SECRET', str(caught.exception))

    def test_codesign_error_categories_discard_private_output(self):
        evidence = self.root / 'reason.json'
        helper = self.root / 'private-helper'
        helper.write_bytes(b'FAKE HELPER')
        cases = (
            ('unable to build chain to self-signed root; errSecInternalComponent', 'certificate-chain'),
            ('CSSMERR_TP_NOT_TRUSTED', 'certificate-trust'),
            ('CSSMERR_TP_CERT_EXPIRED', 'certificate-expired'),
            ('User interaction is not allowed', 'interaction-denied'),
            ('errSecInteractionNotAllowed', 'interaction-denied'),
            ('The specified item could not be found in the keychain', 'identity-unavailable'),
            ('no identity found', 'identity-unavailable'),
            ('The timestamp service is not available', 'timestamp-unavailable'),
            ('A timestamp was expected but was not found', 'timestamp-missing'),
            ('errSecInternalComponent', 'security-internal'),
            ('unexpected private error', 'unclassified'),
        )
        for output, reason in cases:
            with self.subTest(reason=reason), patch.dict(os.environ, {
                    dist.package_evidence.VARIABLE: str(evidence),
                    'PEER_FAIL': 'codesign --force',
                    'PEER_ERROR': output + ' /private/secret user@example.invalid ' + IDENTITY}):
                with self.assertRaises(dist.CommandFailure) as caught:
                    dist.sign(helper, self.root / 'private-keychain', self.env)
                failed = json.loads(evidence.read_text())['operations'][-1]
                self.assertEqual(failed['reason'], reason)
                public = evidence.read_text() + str(caught.exception)
                for private in ('FAKE-SECRET', output, '/private/secret', 'user@example.invalid', IDENTITY, str(helper)):
                    self.assertNotIn(private, public)
        with self.assertRaisesRegex(ValueError, 'invalid package diagnostic reason'):
            dist.package_evidence.record('signature.sign', 'failed', reason='FAKE-SECRET')
        with self.assertRaisesRegex(ValueError, 'invalid package diagnostic reason'):
            dist.package_evidence.record('package.copy', 'failed', reason='unclassified')

    def test_payload_policy_failure_is_distinct_from_codesign_failure(self):
        evidence = self.root / 'package.json'
        helper = self.root / 'helper'
        helper.write_bytes(b'FAKE HELPER')
        with patch.dict(os.environ, {dist.package_evidence.VARIABLE: str(evidence), 'PEER_NO_RUNTIME': '1'}):
            with self.assertRaisesRegex(ValueError, 'hardened runtime missing'):
                dist.sign(helper, self.root / 'fake-keychain', self.env)
        failed = json.loads(evidence.read_text())['operations'][-1]
        self.assertEqual(failed, {'stage': 'signature.policy', 'status': 'failed', 'outcome': 'operation refused'})

    def test_package_evidence_failure_preserves_primary_exception(self):
        error = dist.CommandFailure('signature.sign', 'timed out')
        with patch.object(dist.package_evidence, 'record', side_effect=[None, ValueError('fixture write failure')]):
            with self.assertRaises(dist.CommandFailure) as caught:
                with dist.package_evidence.stage('signature.sign'):
                    raise error
        self.assertIs(caught.exception, error)
        evidence = self.root / 'malformed-package-evidence.json'
        with patch.dict(os.environ, {dist.package_evidence.VARIABLE: str(evidence)}):
            with self.assertRaises(dist.CommandFailure) as caught:
                with dist.package_evidence.stage('signature.sign'):
                    evidence.write_text('{"schema": 1, "operations": {}}')
                    raise error
        self.assertIs(caught.exception, error)
        with patch.dict(os.environ, {dist.package_evidence.VARIABLE: str(self.root / 'missing/evidence')}):
            with self.assertRaisesRegex(ValueError, 'evidence unavailable'):
                with dist.package_evidence.stage('signature.sign'):
                    self.fail('must refuse before work when initial receipt cannot be written')

    def test_package_journal_refuses_arbitrary_labels_and_incomplete_success(self):
        evidence = self.root / 'package.json'
        with patch.dict(os.environ, {dist.package_evidence.VARIABLE: str(evidence)}):
            with self.assertRaisesRegex(ValueError, 'invalid package diagnostic label'):
                dist.package_evidence.record('FAKE-SECRET', 'failed')
            self.assertFalse(evidence.exists())
            for label in dist.package_evidence.STAGES:
                with dist.package_evidence.stage(label):
                    pass
            dist.package_evidence.require_complete(evidence)
            dist.package_evidence.record('signature.sign', 'running')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                dist.package_evidence.require_complete(evidence)

    def test_package_signing_precedes_inventory_and_archive(self):
        source = (dist.ROOT / 'scripts/release/package.sh').read_text()
        self.assertLess(source.index('macos_distribution.py'), source.index('find . -type f'))
        self.assertLess(source.index('find . -type f'), source.index('tar -C "$bundle"'))
        workflow = (dist.ROOT / '.github/workflows/macos-signing.yml').read_text()
        self.assertIn("github.ref == 'refs/heads/dev'", workflow)
        self.assertIn('inputs.approved_revision == github.sha', workflow)
        self.assertIn('environment: macos-signing', workflow)
        self.assertNotIn('pull_request:', workflow)
        self.assertNotIn('packages: write', workflow)
