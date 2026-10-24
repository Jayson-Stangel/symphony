"""Native, credential-free exact candidate graph import regression fixtures."""
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
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
        self.archive = self.root / 'codex-arm64.docker.tar.gz'
        self.manifest = test_distribution.DistributionTest().fixture()
        self.manifest.pop('distribution')
        self.manifest['compatibility'] = {'runner_protocol': 1, 'selection_mode': 'runner-protocol-v1'}
        self.record = self.manifest['runners']['codex']['arm64']
        self.config = b'{"architecture":"arm64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}'
        self.layer = gzip.compress(b'qualified compressed layer fixture', mtime=0)
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

    def run_helper(self, destination='stage', arch='arm64'):
        folder = self.root / destination
        folder.mkdir()
        return subprocess.run([HELPER, 'prepare-runner-import', self.metadata, 'codex', arch,
                               self.archive, folder], text=True, capture_output=True)

    def import_shell(self, *, context=None, host=None, failure='', archive=None, info=False):
        docker = self.root / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + r'''
import json, os, pathlib, sys
args=sys.argv[1:]
with open(os.environ['IMPORT_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
state=pathlib.Path(os.environ['IMPORT_STATE'])
if args==['info']: pass
elif args==['context','show']: print(os.environ.get('DOCKER_CONTEXT') or 'default')
elif args[:2]==['context','inspect']:
    print('ssh://remote.example' if os.environ.get('DOCKER_CONTEXT')=='remote' else 'unix:///tmp/engine.sock')
elif args[:2]==['image','inspect']: pass
elif args[0]=='create':
    state.write_text(args[args.index('--label')+1].split('=',1)[1]);print('a'*64)
elif args[0]=='start' and os.environ.get('IMPORT_FAILURE')=='occupied': sys.exit(22)
elif args[0]=='inspect':
    print('true' if args[2]=='{{.State.Running}}' else state.read_text())
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
package_image() { "$IMPORT_HELPER" release-get "$symphony_home/release.json" runners.codex.arm64.image; }
package_release_image() { printf validated >> "$IMPORT_VALIDATED"; }
package_channel=candidate
package_arch=arm64
package_revision=fixture
source "$IMPORT_SOURCE"
if [ "$IMPORT_INFO" = true ]; then package_runner_import_info codex
else package_runner_import codex "$IMPORT_ARCHIVE"; fi
''')
        env=dict(os.environ, IMPORT_DOCKER=str(docker), IMPORT_HELPER=str(HELPER),
                 IMPORT_SOURCE=str(ROOT/'scripts/symphony-runner-import.sh'),
                 IMPORT_ARCHIVE=str(archive or self.archive), IMPORT_FAILURE=failure,
                 IMPORT_CALLS=str(self.root/'docker.calls'), IMPORT_STATE=str(self.root/'docker.state'),
                 IMPORT_VALIDATED=str(self.root/'validated'), IMPORT_INFO='true' if info else 'false',
                 symphony_home=str(self.root))
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
        self.assertEqual([call for call in calls if call[0]=='pull'],[['pull',self.record['image']]]*2)
        self.assertEqual(len([call for call in calls if call[0]=='rm']),2)
        self.assertTrue(all('readonly' in call[call.index('--mount')+1] for call in calls if call[0]=='create'))
        self.assertFalse(any(call[0] in ('build','load','tag','push') for call in calls))

    def test_occupied_port_and_corrupt_archive_do_not_pull_or_replace_other_resources(self):
        result=self.import_shell(failure='occupied')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('cannot bind localhost:5000',result.stderr)
        calls=self.calls()
        self.assertFalse(any(call[0]=='pull' for call in calls))
        self.assertEqual([call for call in calls if call[0]=='rm'],[['rm','--force','--volumes','a'*64]])
        (self.root/'docker.calls').unlink()
        self.archive.write_bytes(b'corrupt')
        result=self.import_shell()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(call[0] in ('create','start','pull','rm') for call in self.calls()))

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
        failed = self.run_helper('platform', 'x86_64')
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
