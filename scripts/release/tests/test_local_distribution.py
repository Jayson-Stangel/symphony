"""Real transport/selection controllers with fake service bytes; no signing credentials."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import local_distribution as local
import production_release as production
import test_production_release as fixtures


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


class LocalTransportTest(unittest.TestCase):
    def test_resume_native_refusal_precedes_all_secret_prompts(self):
        from test_signing_checkpoint import ContinuationTests
        peer = ContinuationTests(); peer.setUp(); self.addCleanup(peer.doCleanups)
        manifest = {'source_revision': 'a' * 40, 'bundles': {'macos': {'filename': peer.qualified.name}}}
        peer.manifest.write_text(json.dumps(manifest))
        receipt = {'repository': production.REPOSITORY, 'source_revision': 'a' * 40,
                   'run_id': 123, 'run_attempt': 2, 'original_artifacts': [],
                   'original_qualified_helper_sha256': production.checksum(peer.helper)}
        peer.checkpoint.value['context']['qualification'] = receipt
        peer.checkpoint.value['input_manifest_sha256'] = production.checksum(peer.manifest)
        peer.checkpoint.save()
        def qualification(run, attempt, revision, destination):
            for name in ('internal', 'helper', 'packages'): (destination / name).mkdir(parents=True)
            (destination / 'internal/release.json').write_bytes(peer.manifest.read_bytes())
            (destination / 'helper/symphony-host').write_bytes(peer.helper.read_bytes())
            (destination / 'packages' / peer.qualified.name).write_bytes(peer.qualified.read_bytes())
            production.write_json(destination / 'qualification.json', receipt)
        argv = ['local_distribution.py', 'resume', '123', '2', str(peer.root / 'new'),
                '--checkpoint', str(peer.checkpoint.path), '--checkpoint-sha256',
                production.checksum(peer.checkpoint.path / 'checkpoint.json'), '--authorize-apple-upload']
        with patch.object(sys, 'argv', argv), patch.object(local.subprocess, 'check_output', return_value='a' * 40 + '\n'), \
                patch.object(local, 'exact_source'), patch.object(local, 'no_parallel_signing'), \
                patch.object(local.sys, 'platform', 'darwin'), patch.object(local.os, 'uname') as uname, \
                patch.object(local.sys.stdin, 'isatty', return_value=True), patch.object(local.sys.stdout, 'isatty', return_value=True), \
                patch.object(production, 'qualification', side_effect=qualification), \
                patch('builtins.input', side_effect=['A' * 40, 'FAKETEAM00']) as public, \
                patch.object(local.getpass, 'getpass') as secret, \
                patch('macos_distribution.validate_retained_native', side_effect=ValueError('invalid retained native signature')) as native:
            uname.return_value.machine = 'arm64'
            with self.assertRaisesRegex(ValueError, 'invalid retained native signature'): local.main()
            native.assert_called_once(); self.assertEqual(public.call_count, 2); secret.assert_not_called()

    def test_asset_download_requests_original_binary_and_keeps_escape_bytes(self):
        raw=b'PK\x03\x04\x1b[31m BINARY MUST NOT BE FILTERED'
        command=['gh','api','--allow-escape-sequences','repos/'+production.REPOSITORY+'/releases/assets/71']
        with patch.object(local,'api_command',return_value=command.copy()) as build, \
             patch.object(local.subprocess,'check_output',return_value=raw) as run:
            self.assertEqual(local.staging_api('releases/assets/71',binary=True),raw)
        build.assert_called_once_with('releases/assets/71',binary=True)
        self.assertIn('Accept: application/octet-stream',run.call_args[0][0])

    def fixture(self, root):
        fetch, run, jobs, inventory, payloads, *_ = fixtures.ProductionInputsTest().fixture()
        inputs, signed = fixtures.SignedSelectionTest().fixture(root)
        distribution = json.loads((signed/'macos-distribution.json').read_text())
        distribution['updater'] = 'tar.gz; same notarized native code; online ticket lookup required'
        distribution['payloads']['libexec/symphony-host']['cdhash'] = 'c'*40
        distribution['dmg_signature'].update(cdhash='d'*40, hardened_runtime=False)
        production.write_json(signed/'macos-distribution.json',distribution)
        production.seal_signed(inputs,signed,'a'*40)
        (signed / 'evidence/package-source.txt').write_text('manager_source_revision=' + 'a' * 40 + '\ncontroller_revision=' + 'a'*40 + '\nplatform=macos\n')
        version = production.manager_version.read(Path(__file__).parents[3])
        for i, target in enumerate(('linux_x86_64', 'macos_arm64'), 40):
            name = f'symphony-v{version}-{target}'
            raw = b'OBVIOUS FAKE EXACT QUALIFIED ENGINE ' + target.encode()
            files = {name: raw, name + '.sha256': (hashlib.sha256(raw).hexdigest() + '  ' + name + '\n').encode(),
                     name + '.source.json': json.dumps({'artifact': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                                       'source_revision': 'a'*40, 'target': target}).encode()}
            data = zip_bytes(files)
            inventory.append({'id': i, 'name': target, 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                'expired': False, 'size_in_bytes': len(data), 'expires_at': '2099-01-01T00:00:00Z',
                'created_at': '2026-01-01T00:01:00Z', 'workflow_run': {'id': 123, 'head_sha': 'a'*40}})
            payloads[f'actions/artifacts/{i}/zip'] = data
        release = {}; staged = {}; commands = []
        def invoke(args, **kwargs):
            commands.append(args)
            if args[1:4] == ['api', '--method', 'POST']:
                self.assertEqual(args[4:], ['repos/' + production.REPOSITORY + '/releases', '--input', '-'])
                payload = json.loads(kwargs['input'])
                self.assertIs(payload['draft'], True)
                release.update(id=70, draft=True, tag_name=payload['tag_name'], target_commitish=payload['target_commitish'], assets=[])
                return subprocess.CompletedProcess(args, 0, json.dumps(release), '')
            elif args[1:3] == ['release', 'upload']:
                path = Path(args[4]); data = path.read_bytes()
                staged[71] = data
                release['assets'] = [{'id': 71, 'name': path.name, 'state': 'uploaded', 'size': len(data),
                                     'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                                     'created_at': datetime.now(timezone.utc).isoformat()}]
            else:
                raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        def service(path, binary=False):
            if path.startswith('releases/tags/'):
                raise AssertionError('Private draft tag lookup must never be called (404)')
            if path == 'releases/70':
                return copy.deepcopy(release)
            if path == 'releases/assets/71':
                return staged[71] if binary else copy.deepcopy(release['assets'][0])
            return fetch(path, binary=binary)
        return inputs, signed, service, invoke, release, staged, commands, inventory, payloads

    def native_peer(self, folder, identity, team, qualified):
        receipt = json.loads((folder / 'macos-distribution.json').read_text())
        return {'schema': 1, 'source_revision': 'a'*40, 'certificate_sha1': identity, 'team_id': team,
                'normalized_helper_sha256': 'b'*64, 'payload_inventory_sha256': 'c'*64,
                'scope': 'COMMAND PEER, not native Apple proof',
                'files': {name: production.checksum(folder / name) for name in receipt['artifacts']}}

    def test_upload_import_immutable_retention_and_publication_keep_same_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, signed, fetch, invoke, release, _, commands, inventory, payloads = self.fixture(root)
            before = {p.name: p.read_bytes() for p in signed.iterdir() if p.is_file()}
            receipt = local.upload(signed, 'a'*40, root / 'local.zip', invoke, fetch)
            self.assertTrue(release['draft']); self.assertFalse(release['tag_name'].startswith('v'))
            self.assertFalse(any('clobber' in arg for cmd in commands for arg in cmd))
            imported = root / 'imported'
            local.import_local('70', '71', receipt['digest'], 'a'*40, imported, 'A'*40, 'FAKETEAM01', fetch, self.native_peer)
            for name, raw in before.items():
                self.assertEqual((imported / 'signed' / name).read_bytes(), raw)
            # Model immutable Actions retention of exactly the imported files.
            files = {'signed/' + p.relative_to(imported/'signed').as_posix(): p.read_bytes()
                     for p in (imported/'signed').rglob('*') if p.is_file()}
            files.update({p: (imported/p).read_bytes() for p in ('local-origin.json', 'native-verification.json')})
            raw = zip_bytes(files)
            artifact = {'id': 90, 'name': 'production-local-signed-' + 'a'*40 + '-456-1',
                        'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'expired': False,
                        'size_in_bytes': len(raw), 'expires_at': '2099-01-01T00:00:00Z',
                        'created_at': '2026-01-01T00:01:00Z', 'workflow_run': {'id':456, 'head_sha':'a'*40}}
            run = {'head_sha':'a'*40, 'head_branch':'main', 'event':'workflow_dispatch', 'status':'completed',
                   'conclusion':'success', 'run_attempt':1, 'path':'.github/workflows/macos-local-signing-import.yml',
                   'head_repository':{'full_name':production.REPOSITORY},
                   'run_started_at':'2026-01-01T00:00:00Z', 'updated_at':'2026-01-01T01:00:00Z'}
            def retained_fetch(path, binary=False):
                if path in ('actions/runs/456', 'actions/runs/456/attempts/1'): return run
                if path == 'actions/runs/456/attempts/1/jobs?per_page=100':
                    return {'jobs':[{'name':'Verify locally signed distribution', 'conclusion':'success'}]}
                if path == 'actions/runs/456/artifacts?per_page=100': return {'artifacts':[artifact]}
                if path == 'actions/artifacts/90/zip': return raw
                return fetch(path, binary=binary)
            retained = root / 'retained'
            local.retained_local('456', '1', 'a'*40, retained, retained_fetch)
            self.assertEqual(json.loads((retained/'signed-origin.json').read_text())['origin'], 'local')
            # Later mutable draft changes cannot replace admitted Actions bytes.
            release['assets'].clear()
            for name, data in before.items(): self.assertEqual((retained/'signed'/name).read_bytes(), data)
            publisher = fixtures.PublicationPeer()
            with patch.dict(os.environ, GITHUB_SHA='a' * 40):
                production.publish_release(retained, 'v0.0.6', publisher.invoke, verify_tag=publisher.verify)
            self.assertEqual([name for name, _ in publisher.mutations], ['upload', 'complete'])
            self.assertFalse(publisher.release['draft'])
            self.assertFalse(any('build' in cmd or 'codesign' in cmd for cmd in publisher.calls))
            published = {p.name:p.read_bytes() for p in production.release_files(retained)}
            for name,data in before.items():self.assertEqual(published[name],data)

    def test_original_creation_id_is_used_for_both_reads_without_a_tag_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, signed, fetch, invoke, release, _, commands, *_ = self.fixture(root)
            reads = []
            def service(path, binary=False):
                reads.append(path)
                return fetch(path, binary=binary)
            receipt = local.upload(signed, 'a'*40, root/'local.zip', invoke, service)
            self.assertEqual(reads, ['releases/70', 'releases/70'])
            self.assertEqual(receipt['release_id'], 70)
            self.assertEqual(commands[0][1:4], ['api', '--method', 'POST'])
            self.assertEqual(commands[1][1:4], ['release', 'upload', release['tag_name']])
            self.assertEqual(len(commands), 2)

    def test_original_release_identity_and_private_inventory_refuse_before_or_after_upload(self):
        changes = (lambda r:r.update(id=71), lambda r:r.update(id=True), lambda r:r.update(draft=False),
                   lambda r:r.update(tag_name='replacement'), lambda r:r.update(target_commitish='b'*40),
                   lambda r:r.update(assets=[{}] if not r['assets'] else []), lambda r:r.update(assets={}))
        for after_upload in (False, True):
            for change in changes:
                with self.subTest(after_upload=after_upload, change=change), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _, signed, fetch, invoke, _, _, commands, *_ = self.fixture(root)
                    reads = []
                    def service(path, binary=False):
                        result = fetch(path, binary=binary)
                        reads.append(path)
                        if len(reads) == (2 if after_upload else 1):
                            change(result)
                        return result
                    archive = root/'local.zip'
                    with self.assertRaises(ValueError):
                        local.upload(signed, 'a'*40, archive, invoke, service)
                    self.assertTrue(archive.is_file())
                    self.assertEqual(len(commands), 2 if after_upload else 1)
                    before = archive.read_bytes()
                    with self.assertRaises(ValueError):
                        local.upload(signed, 'a'*40, archive, invoke, service)
                    self.assertEqual(archive.read_bytes(), before)
                    self.assertEqual(len(commands), 2 if after_upload else 1)

    def test_uncertain_or_malformed_creation_never_retries_or_uploads(self):
        results = (subprocess.TimeoutExpired('gh', 1), subprocess.CalledProcessError(1, 'gh'),
                   'not JSON', '{}', '[]', 'null', '{"id":true}', '{"id":0}', '{"id":-1}', '{"id":"70"}')
        for result in results:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, signed, _, _, _, _, _, *_ = self.fixture(root)
                calls = []
                def invoke(args, **kwargs):
                    calls.append(args)
                    if isinstance(result, Exception):
                        raise result
                    return subprocess.CompletedProcess(args, 0, result, '')
                def refuse_read(*args, **kwargs):
                    self.fail('Uncertain creation must not discover/retry a release')
                archive = root/'local.zip'
                with self.assertRaises((ValueError, KeyError, subprocess.SubprocessError)):
                    local.upload(signed, 'a'*40, archive, invoke, refuse_read)
                self.assertTrue(archive.is_file())
                with self.assertRaises(ValueError):
                    local.upload(signed, 'a'*40, archive, invoke, refuse_read)
                self.assertEqual(len(calls), 1)

    def test_uploaded_asset_tamper_refuses_original_id_receipt(self):
        changes = (lambda a:a.update(name='replacement.zip'), lambda a:a.update(state='new'),
                   lambda a:a.update(size=a['size']+1), lambda a:a.update(digest='sha256:'+'0'*64))
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                _, signed, fetch, invoke, _, _, _, *_ = self.fixture(root)
                reads=[]
                def service(path, binary=False):
                    value=fetch(path,binary=binary);reads.append(path)
                    if len(reads)==2:change(value['assets'][0])
                    return value
                with self.assertRaises(ValueError):local.upload(signed,'a'*40,root/'local.zip',invoke,service)

    def test_replaced_asset_wrong_digest_expired_or_completed_staging_refuses_before_native(self):
        changes = [lambda r:r.update(draft=False), lambda r:r.update(target_commitish='b'*40),
                   lambda r:r['assets'][0].update(id=72),lambda r:r['assets'][0].update(digest='sha256:'+'0'*64),
                   lambda r:r['assets'][0].update(created_at='2000-01-01T00:00:00Z')]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);_,signed,fetch,invoke,release,*_=self.fixture(root)
                receipt=local.upload(signed,'a'*40,root/'local.zip',invoke,fetch);change(release)
                with self.assertRaises(ValueError):
                    local.import_local('70','71',receipt['digest'],'a'*40,root/'import','A'*40,'FAKETEAM01',fetch,
                                       lambda *a:self.fail('native verification must not run'))

    def test_archive_and_unexpected_credential_file_refusals(self):
        for name, mode in (('../escape',0),('/escape',0),('link',0o120777),('device',0o060600)):
            with tempfile.TemporaryDirectory() as temporary:
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w') as z:
                    entry=zipfile.ZipInfo(name);entry.external_attr=mode<<16;z.writestr(entry,b'bad')
                with self.assertRaises(ValueError):local.unpack(data.getvalue(),Path(temporary)/'out')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,signed,*_=self.fixture(root)
            (signed/'identity.p12').write_bytes(b'FAKE SECRET MUST NOT UPLOAD')
            with self.assertRaises(ValueError):local.transport_files(signed)

    def test_arbitrary_private_diagnostic_values_refuse_before_upload(self):
        changes = [('stage','PRIVATE KEY OR PASSWORD'),('outcome','PRIVATE KEY OR PASSWORD'),
                   ('reason','PRIVATE KEY OR PASSWORD'),('source_line','PRIVATE KEY OR PASSWORD'),
                   ('exit_status','PRIVATE KEY OR PASSWORD')]
        for field,value in changes:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);_,signed,*_=self.fixture(root)
                path=signed/'evidence/keychain-diagnostics.json';journal=json.loads(path.read_text())
                journal['operations'][0][field]=value;path.write_text(json.dumps(journal))
                with self.assertRaises(ValueError):
                    local.upload(signed,'a'*40,root/'local.zip',lambda *a,**k:self.fail('must refuse before GitHub'))


class NonNativeAdmissionTest(unittest.TestCase):
    def test_successful_ingress_without_native_cannot_be_used_as_signed_production_input(self):
        run={'head_sha':'a'*40,'head_branch':'main','event':'workflow_dispatch','status':'completed',
             'conclusion':'success','run_attempt':1,'path':'.github/workflows/macos-local-signing-import.yml',
             'head_repository':{'full_name':production.REPOSITORY}}
        def fetch(path,binary=False):
            if path in ('actions/runs/456','actions/runs/456/attempts/1'):return run
            if path=='actions/runs/456/attempts/1/jobs?per_page=100':
                return {'jobs':[{'name':'Read private staged distribution','conclusion':'success'},
                                {'name':'Verify locally signed distribution','conclusion':'skipped'}]}
            self.fail('Ingress alone must be refused before artifact selection: '+path)
        with tempfile.TemporaryDirectory() as temporary,self.assertRaisesRegex(ValueError,'mandatory production producer'):
            local.retained_local('456','1','a'*40,Path(temporary)/'retained',fetch)


def macho(signature_size=32):
    raw=bytearray(768+signature_size)
    struct.pack_into('<8I',raw,0,0xFEEDFACF,0x0100000C,0,2,2,88,0,0)
    struct.pack_into('<2I16s4Q4I',raw,32,0x19,72,b'__LINKEDIT',0x1000,16384,256,len(raw)-256,1,1,0,0)
    struct.pack_into('<4I',raw,104,0x1D,16,768,signature_size)
    raw[256:768]=b'X'*512
    raw[768:]=b'S'*signature_size
    return raw


class ExecutableIdentityTest(unittest.TestCase):
    def test_signature_sizes_normalize_but_code_and_load_commands_do_not(self):
        self.assertEqual(local.executable_identity(macho(32)),local.executable_identity(macho(100)))
        for offset in (16,24,40,56,130,300):
            changed=macho();changed[offset]^=1
            try:self.assertNotEqual(local.executable_identity(changed),local.executable_identity(macho()))
            except ValueError:pass

    def test_ambiguous_truncated_nontrailing_and_wrong_arch_refuse(self):
        changes=[lambda b:struct.pack_into('<I',b,0,0xCAFEBABE),lambda b:struct.pack_into('<I',b,4,0x01000007),
                 lambda b:struct.pack_into('<I',b,16,3),lambda b:struct.pack_into('<I',b,20,80),
                 lambda b:struct.pack_into('<I',b,108,24),lambda b:struct.pack_into('<I',b,112,760),
                 lambda b:struct.pack_into('<Q',b,64,32768),lambda b:b.extend(b'extra')]
        for change in changes:
            raw=macho();change(raw)
            with self.assertRaises(ValueError):local.executable_identity(raw)
        for length in (0,31,110,768):
            with self.assertRaises(ValueError):local.executable_identity(macho()[:length])


class ArchiveMetadataTest(unittest.TestCase):
    def test_special_permission_bits_pax_and_duplicate_or_link_directories_refuse(self):
        for mode,pax,kind,duplicate in ((0o4755,{},tarfile.REGTYPE,False),
                (0o2755,{},tarfile.DIRTYPE,False),(0o755,{'SCHILY.acl.access':'user:0:rwx'},tarfile.REGTYPE,False),
                (0o755,{'SCHILY.xattr.security.capability':'bad'},tarfile.REGTYPE,False),
                (0o755,{},tarfile.DIRTYPE,True),(0o755,{},tarfile.SYMTYPE,False)):
            with tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/'bad.tar.gz'
                with tarfile.open(path,'w:gz',format=tarfile.PAX_FORMAT) as archive:
                    entry=tarfile.TarInfo('item');entry.mode=mode;entry.type=kind;entry.pax_headers=pax
                    if kind==tarfile.REGTYPE:entry.size=1
                    archive.addfile(entry,io.BytesIO(b'x') if kind==tarfile.REGTYPE else None)
                    if duplicate:archive.addfile(entry)
                with self.assertRaises(ValueError):local.bundle_files(path)


class NativeAdmissionTest(unittest.TestCase):
    def fixture(self, root, mutation=None, extra_root=False, changed_instructions=False):
        inputs,signed=fixtures.SignedSelectionTest().fixture(root)
        certificate=b'OBVIOUS FAKE CERTIFICATE FOR COMMAND PEER'
        identity=hashlib.sha1(certificate).hexdigest().upper()
        original=macho(32);helper=macho(100)
        if mutation=='code':helper[300]^=1
        (inputs/'helper/symphony-host').write_bytes(original)
        receipt=json.loads((signed/'macos-distribution.json').read_text())
        receipt['original_qualified_helper_sha256']=hashlib.sha256(original).hexdigest()
        for signature in (receipt['payloads']['libexec/symphony-host'],receipt['dmg_signature']):signature['certificate_sha1']=identity
        receipt['payloads']['libexec/symphony-host']['sha256']=hashlib.sha256(helper).hexdigest()
        manifest=json.loads((signed/'release.json').read_text());mac=manifest['bundles']['macos']['filename']
        common={'scripts/symphony':(b'ORIGINAL SOURCE SCRIPT',0o755),'release.json':(b'ORIGINAL EMBEDDED METADATA',0o644)}
        original_files=dict(common,**{'libexec/symphony-host':(bytes(original),0o755),
                       'distribution-status.txt':(b'unsigned',0o644),'files.sha256':(b'old inventory',0o644)})
        payload=dict(common,**{'libexec/symphony-host':(bytes(helper),0o755)})
        if mutation=='script':payload['scripts/symphony']=(b'CHANGED SOURCE SCRIPT',0o755)
        payload['signing.json']=(json.dumps({'status':'developer-id-signed-not-yet-notarized','source_revision':'a'*40,
            'original_qualified_helper_sha256':receipt['original_qualified_helper_sha256'],'payloads':receipt['payloads']}).encode(),0o644)
        payload['files.sha256']=(''.join(hashlib.sha256(data).hexdigest()+'  ./'+name+'\n' for name,(data,_) in sorted(payload.items())).encode(),0o644)
        def archive(path,files):
            with tarfile.open(path,'w:gz') as tar:
                directories={'.'}
                for name in files:
                    directories.update(str(parent) for parent in Path(name).parents if str(parent)!='.')
                for name in sorted(directories):
                    directory=tarfile.TarInfo(name);directory.type=tarfile.DIRTYPE;directory.mode=0o755;tar.addfile(directory)
                for name,(raw,mode) in files.items():
                    entry=tarfile.TarInfo('./'+name);entry.size=len(raw);entry.mode=mode;tar.addfile(entry,io.BytesIO(raw))
        archive(inputs/'packages'/mac,original_files);archive(signed/mac,payload)
        receipt['artifacts'][mac]=production.checksum(signed/mac);production.write_json(signed/'macos-distribution.json',receipt)
        commands=[]
        def invoke(args,**kwargs):
            commands.append(args)
            stdout='';stderr=''
            if args[0]=='codesign' and '--verbose=4' in args:
                stderr='Authority=Developer ID Application: FAKE\nTeamIdentifier=FAKETEAM01\nTimestamp=FAKE TIMESTAMP\nCodeDirectory flags=0x10000(runtime)\n'
            if args[0]=='codesign' and any(a.startswith('--extract-certificates=') for a in args):
                prefix=next(a.split('=',1)[1] for a in args if a.startswith('--extract-certificates='));Path(prefix+'0').write_bytes(certificate)
            if args[:2]==['hdiutil','attach']:
                mount=Path(args[args.index('-mountpoint')+1]);bundle=mount/'Symphony';bundle.mkdir()
                (mount/'Read Me.txt').write_text('BAD INSTRUCTIONS' if changed_instructions else local.INSTALL_INSTRUCTIONS)
                if extra_root:(mount/'Unexpected.app').mkdir()
                for name,(raw,mode) in payload.items():
                    p=bundle/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(mode)
                if mutation=='dmg-directory':(bundle/'extra').mkdir()
                if mutation=='dmg-directory-mode':(bundle/'scripts').chmod(0o700)
                if mutation=='dmg-fifo':os.mkfifo(bundle/'pipe')
            return subprocess.CompletedProcess(args,0,stdout,stderr)
        return inputs,signed,identity,invoke,commands

    def test_actual_native_commands_required_and_same_team_wrong_source_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,signed,identity,invoke,commands=self.fixture(Path(temporary))
            with patch.object(local.sys,'platform','darwin'):
                proof=local.verify_native(signed,identity,'FAKETEAM01',inputs,invoke)
            self.assertEqual(proof['source_revision'],'a'*40)
            self.assertTrue(any(cmd[:3]==['xcrun','stapler','validate'] for cmd in commands))
            self.assertTrue(any(cmd[:2]==['spctl','--assess'] for cmd in commands))
            self.assertTrue(any('--check-notarization' in cmd for cmd in commands))
            self.assertEqual(commands[-1][:2],['hdiutil','detach'])
        for change in ('code','script'):
            with tempfile.TemporaryDirectory() as temporary:
                inputs,signed,identity,invoke,_=self.fixture(Path(temporary),mutation=change)
                with patch.object(local.sys,'platform','darwin'),self.assertRaises(ValueError):
                    local.verify_native(signed,identity,'FAKETEAM01',inputs,invoke)

    def test_gatekeeper_failure_dmg_extra_files_and_changed_instructions_refuse(self):
        for extra,instructions in ((True,False),(False,True)):
            with tempfile.TemporaryDirectory() as temporary:
                inputs,signed,identity,invoke,commands=self.fixture(Path(temporary),extra_root=extra,changed_instructions=instructions)
                with patch.object(local.sys,'platform','darwin'),self.assertRaises(ValueError):
                    local.verify_native(signed,identity,'FAKETEAM01',inputs,invoke)
                self.assertEqual(commands[-1][:2],['hdiutil','detach'])
        with tempfile.TemporaryDirectory() as temporary:
            inputs,signed,identity,invoke,_=self.fixture(Path(temporary))
            def refuse(args,**kwargs):
                if args[0]=='spctl':raise subprocess.CalledProcessError(1,args)
                return invoke(args,**kwargs)
            with patch.object(local.sys,'platform','darwin'),self.assertRaises(subprocess.SubprocessError):
                local.verify_native(signed,identity,'FAKETEAM01',inputs,refuse)

    def test_mounted_special_bits_directory_modes_and_nonregular_nodes_refuse(self):
        for mutation in ('dmg-mode','dmg-directory','dmg-directory-mode','dmg-fifo'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                inputs,signed,identity,invoke,commands=self.fixture(Path(temporary),mutation=mutation)
                original_lstat=Path.lstat
                def metadata(path,*args,**kwargs):
                    info=original_lstat(path,*args,**kwargs)
                    # Some sandbox filesystems clear set-id bits on creation;
                    # model the mounted filesystem's returned metadata directly.
                    if mutation=='dmg-mode' and path.name=='symphony' and path.parent.name=='scripts':
                        fields=list(info);fields[0]|=0o4000;return os.stat_result(fields)
                    return info
                with patch.object(local.sys,'platform','darwin'),patch.object(Path,'lstat',metadata),self.assertRaises(ValueError):
                    local.verify_native(signed,identity,'FAKETEAM01',inputs,invoke)
                self.assertEqual(commands[-1][:2],['hdiutil','detach'])


class WorkflowBoundaryTest(unittest.TestCase):
    def test_credential_free_ingress_and_existing_production_gates(self):
        root=Path(__file__).parents[3]
        ingress=(root/'.github/workflows/macos-local-signing-import.yml').read_text()
        publisher=(root/'.github/workflows/production-publish.yml').read_text()
        self.assertIn('runner=macos-15-intel',ingress)
        self.assertIn('runs-on: ${{ needs.mac-runner-local-import.outputs.runner }}',ingress)
        self.assertIn('environment: macos-production-signing',ingress)
        self.assertIn('inputs.approved_revision == github.sha',ingress)
        for text in (ingress,publisher):
            self.assertNotIn('secrets.',text)
            self.assertNotIn('macos_distribution.py build',text)
        self.assertEqual(publisher.count('environment: production-release'),2)
        self.assertEqual(publisher.count('SIGNED_ORIGIN:'),3)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')",publisher)



if __name__ == '__main__': unittest.main()
