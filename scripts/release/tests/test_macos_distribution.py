"""Command peers exercise refusal and ordering, never Apple credentials/trust."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
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
if os.environ.get('PEER_FAIL') and os.environ['PEER_FAIL'] in operation:
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
        pathlib.Path(args[args.index('--extract-certificates')+1]+'0').write_bytes(b'OBVIOUS FAKE CERTIFICATE - NOT A CREDENTIAL')
    elif '--entitlements' not in args and '-d' in args:
        print('Authority=Developer ID Application: Fixture (FAKETEAM00)')
        print('TeamIdentifier='+os.environ.get('PEER_TEAM','FAKETEAM00'))
        print('Timestamp=FAKE timestamp')
        print('CodeDirectory v=20500 flags=0x10000(runtime) hashes=1')
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
        print(json.dumps({'jobId':'11111111-2222-3333-4444-555555555555','status':os.environ.get('PEER_STATUS','Accepted'),'statusCode':0,'sha256':hashlib.sha256(submitted).hexdigest(),'issues':[],
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
        return dist.signing_keychain(self.env, private)

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

        for failure in ('spctl --assess', ''):
            output = self.root / ('failed' if failure else 'success')
            with patch.object(dist.sys, 'platform', 'darwin'), patch.object(dist.os, 'uname') as uname, \
                    patch.object(dist, 'run', side_effect=invoke), patch.dict(os.environ, PEER_FAIL=failure):
                uname.return_value.machine = 'arm64'
                if failure:
                    with self.assertRaises(ValueError):
                        dist.build(manifest, helper, output, self.env)
                    self.assertFalse((output / 'macos-distribution.json').exists())
                    self.assertFalse(list(output.glob('*.dmg')))
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
