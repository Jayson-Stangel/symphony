"""Native, credential-free exact candidate graph import regression fixtures."""
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import concurrent.futures
import socket
import threading
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/release/tests'))
import test_distribution

HELPER = Path(os.environ.get('SYMPHONY_TEST_HOST_HELPER', '/tmp/symphony-package-test-host'))


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RunnerImportTest(unittest.TestCase):
    def setUp(self):
        if not HELPER.is_file():
            subprocess.run([ROOT / 'scripts/release/build-host-helper.sh', HELPER, '--native'], check=True)
        self.temp = tempfile.TemporaryDirectory(prefix='symphony graph import ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.arch = 'arm64' if sys.platform == 'darwin' else 'x86_64'
        self.docker_arch = 'arm64' if self.arch == 'arm64' else 'amd64'
        self.archive = self.root / f'codex-{self.arch}.docker.tar.gz'
        self.manifest = test_distribution.DistributionTest().fixture()
        self.manifest.pop('distribution')
        self.manifest['compatibility'] = {'runner_protocol': 1, 'selection_mode': 'runner-protocol-v1'}
        self.record = self.manifest['runners']['codex'][self.arch]
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode='w') as bundle:
            raw = self.root.name.encode()
            member = tarfile.TarInfo('fixture-proof'); member.size = len(raw)
            bundle.addfile(member, io.BytesIO(raw))
        self.config = json.dumps({'architecture': self.docker_arch, 'os': 'linux',
            'config': {'Labels': {'org.opencontainers.image.revision': self.record['source_revision'],
                'com.symphony.agent': 'codex', 'com.symphony.inputs.sha256': self.record['input_identity'],
                'com.symphony.protocol': '1'}},
            'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + digest(layer.getvalue())]}}).encode()
        self.layer = gzip.compress(layer.getvalue(), mtime=0)
        self.oci = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'digest': 'sha256:' + digest(self.config), 'size': len(self.config),
                       'mediaType': 'application/vnd.oci.image.config.v1+json'},
            'layers': [{'digest': 'sha256:' + digest(self.layer), 'size': len(self.layer),
                        'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}]}).encode()
        self.record.update(image='localhost:5000/symphony-codex@sha256:' + digest(self.oci),
                           image_config='sha256:' + digest(self.config),
                           archive_tag='localhost:5000/symphony-codex:candidate')
        self.record['qualification']['image'] = self.record['image']
        self.files = {'index.json': json.dumps({'schema_version': 1, 'images': [
            {key: self.record[key] for key in ('image', 'image_config', 'archive_tag')}]}).encode(),
            **{'blobs/sha256/' + digest(raw): raw for raw in (self.oci, self.config, self.layer)}}
        self.make_archive()

    def make_archive(self, extra=None):
        with tarfile.open(self.archive, 'w:gz') as bundle:
            for name, raw in self.files.items():
                member = tarfile.TarInfo('symphony-qualified-graphs/v1/' + name)
                member.size = len(raw)
                bundle.addfile(member, io.BytesIO(raw))
            if extra is not None:
                bundle.addfile(extra)
        self.record['qualification']['archive_sha256'] = digest(self.archive.read_bytes())
        self.metadata = self.root / 'release.json'
        self.metadata.write_text(json.dumps(self.manifest))

    def run_helper(self, destination='stage', arch=None):
        arch = arch or self.arch
        folder = self.root / destination
        folder.mkdir()
        return subprocess.run([HELPER, 'prepare-runner-import', self.metadata, 'codex', arch,
                               self.archive, folder], text=True, capture_output=True)

    def import_shell(self, *, context=None, host=None, failure='', archive=None, info=False, real=False):
        docker = self.root / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + r'''
import json, os, pathlib, sys, signal
args=sys.argv[1:]
with open(os.environ['IMPORT_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
if os.environ.get('IMPORT_REAL')=='1':
    if args[0]=='pull' and os.environ.get('IMPORT_FAILURE')=='pull-signal':
        os.kill(os.getppid(),signal.SIGTERM); sys.exit(143)
    os.execvp('docker',['docker',*args])
state=pathlib.Path(os.environ['IMPORT_STATE'])
if args==['info']: pass
elif args==['context','show']: print(os.environ.get('DOCKER_CONTEXT') or 'default')
elif args[:2]==['context','inspect']:
    print('ssh://remote.example' if os.environ.get('DOCKER_CONTEXT')=='remote' else 'unix:///tmp/engine.sock')
elif args[:2]==['image','inspect']: pass
elif args[0]=='create':
    if os.environ.get('IMPORT_FAILURE')=='mount': sys.exit(22)
    state.write_text(args[args.index('--label')+1].split('=',1)[1])
    pathlib.Path(args[args.index('--cidfile')+1]).write_text('a'*64)
    if os.environ.get('IMPORT_FAILURE')=='create-signal':
        os.kill(os.getppid(), signal.SIGTERM); sys.exit(23)
    print('a'*64)
elif args[0]=='start' and os.environ.get('IMPORT_FAILURE') in ('occupied','race'):
    count=state.with_suffix('.attempts')
    value=int(count.read_text())+1 if count.exists() else 1
    count.write_text(str(value))
    if os.environ['IMPORT_FAILURE']=='occupied' or value==1: sys.exit(22)
elif args[0]=='logs':
    print(json.dumps({'msg':'listening on '+('0.0.0.0:42000' if os.environ.get('IMPORT_FAILURE')=='public' else '127.0.0.1:42000')}))
elif args[0]=='pull' and os.environ.get('IMPORT_FAILURE')=='pull': sys.exit(24)
elif args[0]=='inspect':
    print('true' if args[2]=='{{.State.Running}}' else ('foreign' if os.environ.get('IMPORT_FAILURE')=='foreign' else state.read_text()))
elif args[0] in ('start','exec','pull','rm'): pass
else: sys.exit(97)
''')
        docker.chmod(0o755)
        driver=self.root/'driver.sh'
        driver.write_text(r'''
set -euo pipefail
die() { printf '%s\n' "$*" >&2; exit 1; }
container_runtime() { printf '%s\n' "$IMPORT_DOCKER"; }
package_helper() { "$IMPORT_HELPER" "$@"; }
package_manifest_image() { "$IMPORT_HELPER" release-get "$symphony_home/release.json" "runners.codex.$IMPORT_ARCH.image"; }
package_release_image() {
  "$IMPORT_HELPER" validate-runner-reference "$symphony_home/release.json" "$3" "$IMPORT_ARCH" "$2" >/dev/null
  printf validated >> "$IMPORT_VALIDATED"
}
package_channel=candidate
package_arch=$IMPORT_ARCH
package_revision=fixture
source "$IMPORT_SOURCE"
if [ "${IMPORT_FAILURE:-}" = public ]; then sleep() { :; }; fi
if [ "$IMPORT_INFO" = true ]; then package_runner_import_info codex
else package_runner_import codex "$IMPORT_ARCHIVE"; fi
''')
        if real:
            source = (ROOT/'scripts/symphony-package.sh').read_text()
            functions = source[source.index('package_manifest_image() {'):source.index('# One compatibility admission')]
            driver.write_text(driver.read_text().replace('source "$IMPORT_SOURCE"',
                functions + '\ncontainer_engine_ready() { "$1" info >/dev/null; }\nsource "$IMPORT_SOURCE"'))
        env=dict(os.environ, IMPORT_ARCH=self.arch, IMPORT_DOCKER=str(docker), IMPORT_REAL='1' if real else '0', IMPORT_HELPER=str(HELPER),
                 IMPORT_SOURCE=str(ROOT/'scripts/symphony-runner-import.sh'),
                 IMPORT_ARCHIVE=str(archive or self.archive), IMPORT_FAILURE=failure,
                 IMPORT_CALLS=str(self.root/'docker.calls'), IMPORT_STATE=str(self.root/'docker.state'),
                 IMPORT_VALIDATED=str(self.root/'validated'), IMPORT_INFO='true' if info else 'false',
                 symphony_home=str(self.root), package_app_home=str(self.root/'app'))
        for name in ('DOCKER_HOST','DOCKER_CONTEXT'): env.pop(name,None)
        if context is not None: env['DOCKER_CONTEXT']=context
        if host is not None: env['DOCKER_HOST']=host
        return subprocess.run(['bash',driver],env=env,text=True,capture_output=True)

    def calls(self):
        path=self.root/'docker.calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_info_surfaces_original_payload_before_login_without_docker(self):
        result=self.import_shell(info=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for value in (self.record['qualification']['archive_sha256'], 'registry:3', 'localhost:5000', 'artifact_id'):
            self.assertIn(value,result.stdout)
        self.assertEqual(self.calls(),[])

    def test_remote_context_overrides_unix_host_and_host_overrides_default_context(self):
        for context,host in (('remote','unix:///tmp/stale.sock'),(None,'ssh://remote.example')):
            with self.subTest(context=context,host=host):
                result=self.import_shell(context=context,host=host)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('requires a local Unix-socket',result.stderr)
                self.assertFalse(any(call[0] in ('create','pull') for call in self.calls()))
                (self.root/'docker.calls').unlink()

    def test_cold_and_pruned_import_pull_exact_digest_and_cleanup_owned_registry(self):
        for _ in range(2):
            result=self.import_shell()
            self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls()
        self.assertEqual([call for call in calls if call[0]=='pull'],[['pull',self.record['image'].replace('localhost:5000','127.0.0.1:42000')]]*2)
        self.assertEqual(len([call for call in calls if call[0]=='rm']),2)
        self.assertTrue(all('readonly' in call[call.index('--mount')+1] for call in calls if call[0]=='create'))
        self.assertFalse(any(call[0] in ('build','load','tag','push') for call in calls))
        self.assertTrue(all(call[call.index('--network')+1]=='host' and 'REGISTRY_HTTP_ADDR=127.0.0.1:0' in call and 'REGISTRY_HTTP_DEBUG_ADDR=127.0.0.1:0' in call and '-p' not in call for call in calls if call[0]=='create'))
        result=subprocess.run([HELPER,'resolve-runner-import',self.metadata,'codex',self.arch,self.root/'app'],text=True,capture_output=True)
        self.assertEqual(result.stdout.strip(),self.record['image'].replace('localhost:5000','127.0.0.1:42000'))

    def test_occupied_port_and_corrupt_archive_do_not_pull_or_replace_other_resources(self):
        result=self.import_shell(failure='occupied')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('allocation failed after 3 attempts',result.stderr)
        calls=self.calls()
        self.assertFalse(any(call[0]=='pull' for call in calls))
        self.assertEqual([call for call in calls if call[0]=='rm'],[['rm','--force','--volumes','a'*64]]*3)
        (self.root/'docker.calls').unlink()
        self.archive.write_bytes(b'corrupt')
        result=self.import_shell()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(call[0] in ('create','start','pull','rm') for call in self.calls()))

    def test_unsafe_mount_refusal_is_actionable_without_registry_cleanup(self):
        result=self.import_shell(failure='mount')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('check local Docker bind-mount access',result.stderr)
        self.assertFalse(any(c[0] in ('start','pull','rm') for c in self.calls()))
        stage=Path(next(c[c.index('--cidfile')+1] for c in self.calls() if c[0]=='create')).parent
        self.assertFalse(stage.exists())

    def test_startup_receipt_rejects_foreign_duplicate_and_oversized_endpoints(self):
        for receipt in ('{"msg":"listening on 0.0.0.0:42000"}',
                '{"msg":"listening on 127.0.0.1:80"}',
                '{"msg":"listening on 127.0.0.1:99999"}',
                '{"msg":"listening on 127.0.0.1:42000"}\n'*2,
                'x'*65537):
            result=subprocess.run([HELPER,'runner-import-endpoint'],input=receipt,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)

    def test_allocation_race_recovers_once_and_public_binding_refuses(self):
        result=self.import_shell(failure='race')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len([c for c in self.calls() if c[0]=='create']),2)
        self.assertEqual(len([c for c in self.calls() if c[0]=='rm']),2)
        (self.root/'docker.calls').unlink()
        result=self.import_shell(failure='public')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(c[0]=='pull' for c in self.calls()))

    def test_failure_and_interrupted_acquisition_clean_only_owned_receipt(self):
        for failure in ('pull','create-signal'):
            result=self.import_shell(failure=failure)
            self.assertNotEqual(result.returncode,0)
            calls=self.calls()
            self.assertEqual([c for c in calls if c[0]=='rm'],[['rm','--force','--volumes','a'*64]])
            stage=Path(next(c[c.index('--cidfile')+1] for c in calls if c[0]=='create')).parent
            self.assertFalse(stage.exists())
            (self.root/'docker.calls').unlink()
        result=self.import_shell(failure='foreign')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('cleanup refused',result.stderr)
        self.assertFalse(any(c[0]=='rm' for c in self.calls()))
        # Fixture only: discard retained staging after proving ownership refusal.
        import shutil
        stage=Path(next(c[c.index('--cidfile')+1] for c in self.calls() if c[0]=='create')).parent
        shutil.rmtree(stage)

    def test_relocated_reference_requires_exact_original_digest_and_private_receipt(self):
        reference=self.record['image'].replace('localhost:5000','127.0.0.1:42000')
        receipt={'Id': self.record['image_config'], 'Os':'linux','Architecture':self.docker_arch,
                 'RepoDigests':[reference], 'Config': json.loads(self.config)['config']}
        for altered, accepted in ((reference,True),
                (reference.replace('127.0.0.1','localhost'),False),
                (reference.replace('127.0.0.1','192.0.2.1'),False),
                (reference.replace('42000','99999'),False),
                (reference.replace('42000','80'),False),
                (reference.replace('symphony-codex','foreign'),False),
                (reference.split('@')[0]+'@sha256:'+'a'*64,False)):
            result=subprocess.run([HELPER,'validate-runner-reference',self.metadata,'codex',self.arch,altered],text=True,capture_output=True)
            self.assertEqual(result.returncode==0,accepted,result.stderr)
        for field,value in (('Id',self.record['image_config']),('Id','sha256:'+'a'*64),('RepoDigests',[])):
            actual=dict(receipt); actual[field]=value
            result=subprocess.run([HELPER,'validate-runtime-image',self.metadata,'codex',self.arch,reference],input=json.dumps([actual]),text=True,capture_output=True)
            self.assertEqual(result.returncode==0,field=='Id' and value==self.record['image_config'],result.stderr)
        result=self.import_shell()
        self.assertEqual(result.returncode,0,result.stderr)
        stored=next((self.root/'app/runner-imports').glob('*/*'))
        stored.chmod(0o644)
        result=subprocess.run([HELPER,'resolve-runner-import',self.metadata,'codex',self.arch,self.root/'app'],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('private and bounded',result.stderr)

    @unittest.skipUnless(os.environ.get('SYMPHONY_TEST_DOCKER_IMPORT')=='1','opt-in isolated native Docker proof')
    def test_real_docker_occupied_port_concurrent_import_and_graph_identity(self):
        # This is an unrelated host service, outside all importer ownership.
        listener=socket.socket(); listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',5000)); listener.listen()
        self.addCleanup(listener.close)
        def serve():
            try:
                while True:
                    connection,_=listener.accept()
                    with connection:
                        connection.recv(4096); connection.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 9\r\n\r\nunrelated')
            except OSError: pass
        threading.Thread(target=serve,daemon=True).start()
        def probe():
            with socket.create_connection(('127.0.0.1',5000)) as connection:
                connection.sendall(b'GET / HTTP/1.0\r\n\r\n')
                self.assertIn(b'unrelated',connection.recv(4096))
        probe()
        # Separate private installs, same exact original graph, concurrent allocation.
        peers=[]
        for _ in range(2):
            peer=RunnerImportTest(); peer.setUp(); self.addCleanup(peer.doCleanups)
            peer.config=self.config; peer.layer=self.layer; peer.oci=self.oci
            peer.record.update(self.record); peer.files=dict(self.files); peer.make_archive()
            peers.append(peer)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda peer:peer.import_shell(real=True),peers))
        references=[]
        # Register all owned image cleanups before any assertion can stop the test.
        for peer in peers:
            for call in peer.calls():
                if call[0]=='pull' and call[1].endswith('@'+self.record['image'].split('@')[1]):
                    self.addCleanup(lambda ref=call[1]:subprocess.run(['docker','image','rm',ref],capture_output=True))
        for peer,result in zip(peers,results):
            self.assertEqual(result.returncode,0,result.stderr)
            reference=next((peer.root/'app/runner-imports').glob('*/*')).read_text().strip()
            references.append(reference)
            self.addCleanup(lambda ref=reference:subprocess.run(['docker','image','rm',ref],capture_output=True))
            inspected=json.loads(subprocess.check_output(['docker','image','inspect',reference]))[0]
            self.assertIn(reference,inspected['RepoDigests'])
            self.assertEqual(reference.split('@')[1],self.record['image'].split('@')[1])
            self.assertIn(inspected['Id'],(self.record['image_config'],self.record['image'].split('@')[1]))
            if inspected['Id'] != self.record['image_config']:
                self.assertEqual(inspected['Descriptor']['digest'],self.record['image'].split('@')[1])
        self.assertEqual(len(set(ref.split('/')[0] for ref in references)),2)
        for peer in peers:
            for call in peer.calls():
                if call[0]=='create':
                    owner=call[call.index('--name')+1]
                    self.assertNotEqual(subprocess.run(['docker','container','inspect',owner],capture_output=True).returncode,0)
                    self.assertFalse(Path(call[call.index('--cidfile')+1]).parent.exists())
        probe()
        interrupted=RunnerImportTest(); interrupted.setUp(); self.addCleanup(interrupted.doCleanups)
        result=interrupted.import_shell(real=True,failure='pull-signal')
        self.assertEqual(result.returncode,143,result.stderr)
        for call in interrupted.calls():
            if call[0]=='create':
                self.assertNotEqual(subprocess.run(['docker','container','inspect',call[call.index('--name')+1]],capture_output=True).returncode,0)
                self.assertFalse(Path(call[call.index('--cidfile')+1]).parent.exists())
        probe()
        peers[0].archive.write_bytes(b'corrupt')
        result=peers[0].import_shell(real=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('archive verification failed',result.stderr)
        probe()
        print(json.dumps({'native_docker_import':True,'graph':self.record['image'],
            'concurrent_endpoints':[r.split('/')[0] for r in references],
            'unrelated_host_port_5000':'preserved','corrupt_archive':'refused','interrupted_registry':'removed',
            'scope':'synthetic graph; no provider/model or release qualification'}))

    def test_original_manifest_and_compressed_blobs_prepare_registry_without_conversion(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.record['image'])
        storage = self.root / 'stage/registry/docker/registry/v2'
        for raw in (self.oci, self.config, self.layer):
            identity = digest(raw)
            self.assertEqual((storage / 'blobs/sha256' / identity[:2] / identity / 'data').read_bytes(), raw)
        link = storage / 'repositories/symphony-codex/_manifests/revisions/sha256' / digest(self.oci) / 'link'
        self.assertEqual(link.read_text(), 'sha256:' + digest(self.oci))
        # A pruned-cache repeat stages the same exact original bytes.
        repeated = self.run_helper('pruned')
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(repeated.stdout, result.stdout)

    def test_checksum_wrong_platform_and_missing_graph_fail_before_registry_storage(self):
        original = self.archive.read_bytes()
        self.archive.write_bytes(original + b'changed')
        failed = self.run_helper('checksum')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('archive checksum differs', failed.stderr)
        self.assertFalse((self.root / 'checksum/registry').exists())
        self.archive.write_bytes(original)
        failed = self.run_helper('platform', 'x86_64' if self.arch == 'arm64' else 'arm64')
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((self.root / 'platform/registry').exists())
        self.files.pop('blobs/sha256/' + digest(self.layer))
        self.make_archive()
        failed = self.run_helper('missing')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('blob closure differs', failed.stderr)
        self.assertFalse((self.root / 'missing/registry').exists())

    def test_archive_links_and_foreign_paths_refuse_before_registry(self):
        for index, name in enumerate(('../../foreign', 'symphony-qualified-graphs/v1/blobs/sha256/' + 'a' * 64)):
            with self.subTest(name=name):
                extra = tarfile.TarInfo(name)
                extra.type = tarfile.SYMTYPE; extra.linkname = '/tmp/foreign'
                self.make_archive(extra)
                result = self.run_helper('unsafe' + str(index))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / ('unsafe' + str(index)) / 'registry').exists())

    def test_stable_and_distribution_reference_refuse(self):
        for index, change in enumerate(('stable', 'public')):
            metadata = copy.deepcopy(self.manifest)
            if change == 'stable':
                metadata['channel'] = 'stable'
            else:
                metadata['distribution'] = {p: {a: 'ghcr.io/test/' + p + '@' + r['image'].split('@')[1]
                    for a, r in group.items()} for p, group in metadata['runners'].items()}
            self.metadata.write_text(json.dumps(metadata))
            result = self.run_helper('refused' + str(index))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / ('refused' + str(index)) / 'registry').exists())


if __name__ == '__main__':
    unittest.main()
