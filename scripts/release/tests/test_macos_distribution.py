"""Command peers exercise refusal and ordering, never Apple credentials/trust."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
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
    print('FAKE-SECRET-MUST-NOT-LEAK',file=sys.stderr);sys.exit(44)
if tool=='security':
    if args[0]=='list-keychains':print('"/original/login.keychain-db"')
    if args[0]=='create-keychain':pathlib.Path(args[-1]).write_text('fake keychain')
    if args[0]=='delete-keychain':pathlib.Path(args[-1]).unlink()
    if args[0]=='find-identity':
        print('1) '+os.environ['MACOS_SIGNING_IDENTITY_SHA1']+' "Developer ID Application: Fixture ('+os.environ.get('PEER_TEAM','FAKETEAM00')+')"')
elif tool=='codesign':
    if '--force' in args:
        with open(args[-1],'ab') as f:f.write(b'-fixture-signature')
    elif '--extract-certificates' in args:
        pathlib.Path(args[args.index('--extract-certificates')+1]+'0').write_bytes(
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
        self.env = dict(os.environ, PATH=str(self.tools) + os.pathsep + os.environ['PATH'],
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
        private.mkdir(exist_ok=True)
        evidence = self.root / 'evidence'
        evidence.mkdir(exist_ok=True)
        return dist.signing_keychain(self.env, private, evidence)

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

    def test_cleanup_inspect_failure_still_restores_and_verifies_saved_search_list(self):
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
        self.assertEqual(records['cleanup.restore-search-list']['status'], 'success')
        self.assertEqual(records['cleanup.verify-search-list-restored']['status'], 'success')
        self.assertTrue(any('-s' in cmd for cmd in self.commands()))

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
        real_run = dist.run

        def invoke(*args, **kwargs):
            if args[0] == 'git':
                return '' if 'status' in args else REVISION
            if args[0] == 'bash':
                output = Path(args[3])
                output.mkdir()
                content = self.root / 'packaged'
                (content / 'libexec').mkdir(parents=True, exist_ok=True)
                (content / 'libexec/symphony-host').write_bytes(b'FAKE SIGNED BYTES')
                dist.write_json(content / 'signing.json', {'original_qualified_helper_sha256': original, 'payloads': {}})
                with tarfile.open(output / 'symphony-fixture-macos.tar.gz', 'w:gz') as archive:
                    archive.add(content, arcname='.')
                (output / 'package-source.txt').write_text('fixture package source')
                return ''
            if str(args[0]).endswith('/libexec/symphony-host'):
                return ''
            return real_run(*args, **kwargs)

        for failure in ('codesign -vvvv', 'spctl --assess', 'security delete-keychain', ''):
            output = self.root / (failure.split()[0] if failure else 'success')
            with patch.object(dist.sys, 'platform', 'darwin'), patch.object(dist.os, 'uname') as uname, \
                    patch.object(dist, 'run', side_effect=invoke), patch.dict(os.environ, PEER_FAIL=failure):
                uname.return_value.machine = 'arm64'
                if failure:
                    with self.assertRaises(ValueError):
                        dist.build(manifest, helper, output, self.env)
                    self.assertFalse((output / 'macos-distribution.json').exists())
                    if not failure.startswith('security'):
                        self.assertFalse(list(output.glob('*.dmg')))
                    self.assertTrue((output / 'evidence/keychain-diagnostics.json').is_file())
                else:
                    dist.build(manifest, helper, output, self.env)
                    receipt = json.loads((output / 'macos-distribution.json').read_text())
                    self.assertEqual(receipt['status'], 'notarized-stapled-assessed')
                    for name, digest in receipt['artifacts'].items():
                        self.assertEqual(dist.checksum(output / name), digest)
                    self.assertTrue((output / 'symphony-fixture-macos.dmg').read_bytes().endswith(b'-stapled'))
                    self.assertEqual(dist.checksum(helper), original)
            self.assertFalse(list(self.root.glob('symphony-signing-*')))

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
