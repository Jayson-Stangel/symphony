"""Exact automatic acquisition fixtures: no network, Docker or provider credentials."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import struct
import unittest

from runner_import_test import RunnerImportTest, HELPER, ROOT, digest


class RunnerProvisionTest(RunnerImportTest):
    # Inherit native graph setup, not the importer's behavioral tests.
    def make_zip(self, *, compression=zipfile.ZIP_STORED, name=None, zip64=False):
        self.payload = self.root / 'artifact.zip'
        name = name or self.record['qualification']['archive_filename']
        with zipfile.ZipFile(self.payload, 'w', compression=compression) as bundle:
            if zip64:
                with bundle.open(name, 'w', force_zip64=True) as out:
                    out.write(self.archive.read_bytes())
            else:
                bundle.writestr(name, self.archive.read_bytes())
            bundle.writestr('unrelated.json', b'{}')
        if zip64:
            raw = self.payload.read_bytes(); end = raw[-22:]
            count, length, offset = struct.unpack_from('<HII', end, 10)
            position = len(raw)-22
            wide = struct.pack('<IQHHIIQQQQ', 0x06064b50, 44, 45, 45, 0, 0, count, count, length, offset)
            locator = struct.pack('<IIQI', 0x07064b50, 0, position, 1)
            final = struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, 65535, 65535, 0xffffffff, 0xffffffff, 0)
            self.payload.write_bytes(raw[:-22]+wide+locator+final)
        self.record['qualification']['artifact_digest'] = 'sha256:' + digest(self.payload.read_bytes())
        self.metadata.write_text(json.dumps(self.manifest))
        proof = self.record['qualification']
        self.api = {'id': proof['artifact_id'], 'name': proof['artifact_name'], 'digest': proof['artifact_digest'],
                    'expired': False, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'size_in_bytes': self.payload.stat().st_size,
                    'workflow_run': {'id': proof['run_id'], 'head_sha': proof['controller_revision']}}
        self.api_path = self.root / 'artifact.json'
        self.api_path.write_text(json.dumps(self.api))

    def native(self, op, *args):
        return subprocess.run([HELPER, op, self.metadata, 'codex', 'arm64', *args], text=True, capture_output=True)

    def test_exact_metadata_and_zip64_stored_member(self):
        for wide in (False, True):
            self.make_zip(zip64=wide)
            checked = self.native('validate-candidate-artifact', self.api_path)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            out = self.root / ('result' + str(wide))
            result = self.native('extract-candidate-artifact', self.payload, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_bytes(), self.archive.read_bytes())

    def test_metadata_identity_access_expiry_and_size_refusals(self):
        self.make_zip()
        for key, value in [('id', 1), ('name', 'unrelated'), ('digest', 'sha256:'+'0'*64), ('expired', True),
                           ('expires_at', '2000-01-01T00:00:00Z'), ('size_in_bytes', 33 << 30),
                           ('workflow_run', {'id': 1, 'head_sha': '0'*40})]:
            with self.subTest(key=key):
                api = copy.deepcopy(self.api); api[key] = value
                self.api_path.write_text(json.dumps(api))
                result = self.native('validate-candidate-artifact', self.api_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unavailable, expired or differs', result.stderr)

    def test_zip_checksum_member_compression_and_archive_refusals(self):
        for scenario in ('zip-hash', 'missing', 'compressed', 'archive-hash'):
            with self.subTest(scenario=scenario):
                self.make_zip(compression=zipfile.ZIP_DEFLATED if scenario == 'compressed' else zipfile.ZIP_STORED,
                              name='other' if scenario == 'missing' else None)
                if scenario == 'zip-hash': self.payload.write_bytes(self.payload.read_bytes()+b'corrupt')
                if scenario == 'archive-hash':
                    self.record['qualification']['archive_sha256'] = '0'*64
                    self.metadata.write_text(json.dumps(self.manifest))
                result = self.native('extract-candidate-artifact', self.payload, self.root / ('bad-'+scenario))
                self.assertNotEqual(result.returncode, 0)

    def provision(self, *, cached=False, fail='', credentials=True):
        self.make_zip()
        (self.root/'scripts').mkdir(exist_ok=True)
        # Transport is owned/tested by JAY-736; assert the exact verified bytes
        # delivered to its unchanged interface, without simulating its internals.
        (self.root/'scripts/symphony-runner-import.sh').write_text('''
package_runner_import() {
  [ "$1" = codex ]
  cmp "$2" "$TEST_ARCHIVE"
  printf imported >> "$TEST_IMPORTED"
}
''')
        (self.root/'projects').mkdir(exist_ok=True)
        private = self.root/'projects/project.env'
        private.write_text('GH_TOKEN=fixture_secret_never_in_argv\n' if credentials else 'GH_TOKEN=\n')
        curl = self.root/'curl'
        curl.write_text('#!'+sys.executable+'\n'+r'''
import json, os, pathlib, sys
args=sys.argv[1:]
config=sys.stdin.read()
assert 'fixture_secret_never_in_argv' in config
assert not any('fixture_secret' in arg for arg in args)
with open(os.environ['TEST_REQUESTS'], 'a') as out: out.write(json.dumps(args)+'\n')
if os.environ['TEST_FAIL']=='network': sys.exit(22)
if os.environ['TEST_FAIL'] in ('disk','interrupted') and args[-1].endswith('/zip'):
    pathlib.Path(args[args.index('--output')+1]).write_bytes(b'partial')
    sys.exit(23 if os.environ['TEST_FAIL']=='disk' else 130)
source='artifact.zip' if args[-1].endswith('/zip') else 'artifact.json'
if os.environ['TEST_FAIL']=='expired' and source=='artifact.json':
    value=json.loads(pathlib.Path(os.environ['TEST_ROOT'],source).read_text()); value['expired']=True
    pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(value))
else: pathlib.Path(args[args.index('--output')+1]).write_bytes(pathlib.Path(os.environ['TEST_ROOT'],source).read_bytes())
''')
        curl.chmod(0o755)
        driver = self.root/'provision.sh'
        driver.write_text(r'''
set -euo pipefail
die() { printf '%s\n' "$*" >&2; exit 1; }
package_helper() { "$TEST_HELPER" "$@"; }
repo_root() { printf '%s\n' "$TEST_ROOT"; }
project_id() { printf project; }
config_home=$TEST_ROOT
package_image() { "$TEST_HELPER" release-get "$symphony_home/release.json" runners.codex.arm64.image; }
container_runtime() { printf runtime; }
container_engine_ready() { return 0; }
runtime() { [ "$TEST_CACHED" = true ]; }
package_channel=candidate; package_arch=arm64
source "$TEST_SOURCE"
package_ensure_candidate_image codex
''')
        env = dict(os.environ, PATH=str(self.root)+os.pathsep+os.environ['PATH'], TEST_ROOT=str(self.root),
                   TEST_HELPER=str(HELPER), TEST_SOURCE=str(ROOT/'scripts/symphony-runner-provision.sh'),
                   TEST_ARCHIVE=str(self.archive), TEST_IMPORTED=str(self.root/'imported'),
                   TEST_REQUESTS=str(self.root/'requests'), TEST_FAIL=fail, TEST_CACHED=str(cached).lower(),
                   symphony_home=str(self.root), TMPDIR=str(self.root))
        return subprocess.run(['bash', driver], env=env, text=True, capture_output=True)

    def test_cold_pruned_retry_and_cache_reuse(self):
        for _ in range(2):
            result = self.provision()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'imported').read_text(), 'importedimported')
        requests = (self.root/'requests').read_text()
        self.assertEqual(len(requests.splitlines()), 4)
        result = self.provision(cached=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'requests').read_text(), requests)
        self.assertEqual(list(self.root.glob('symphony-runner-download.*')), [])
        self.assertNotIn('fixture_secret', result.stdout+result.stderr+requests)

    def test_missing_access_network_and_expired_fail_before_import(self):
        for fail, credentials in (('', False), ('network', True), ('expired', True), ('disk', True), ('interrupted', True)):
            with self.subTest(fail=fail, credentials=credentials):
                result = self.provision(fail=fail, credentials=credentials)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root/'imported').exists())
                self.assertEqual(list(self.root.glob('symphony-runner-download.*')), [])
                self.assertNotIn('fixture_secret', result.stdout+result.stderr)

    def test_normal_selection_keeps_explicit_and_retained_images_without_acquisition(self):
        source = (ROOT/'scripts/symphony-package.sh').read_text()
        use = source.split('package_use_project_image() {', 1)[1].split('\n}', 1)[0]
        transition = source.split('package_transition_image() {', 1)[1].split('\n}', 1)[0]
        metadata = self.root/'retained.env'
        image = 'sha256:'+'a'*64
        metadata.write_text('SYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID='+image+'\n')
        for selected, retained in ((True, False), (False, True), (False, False)):
            env = dict(os.environ, TEST_ROOT=str(self.root), TEST_RETAINED=str(retained).lower(),
                       TEST_SELECTED=str(selected).lower(), TEST_HELPER=str(HELPER))
            driver = r'''
set -euo pipefail
package_helper() { "$TEST_HELPER" "$@"; }
environment_use_project() { if [ "$TEST_SELECTED" = true ]; then export SYMPHONY_RUNNER_IMAGE=explicit; fi; }
runner_env_file() { if [ "$TEST_RETAINED" = true ]; then printf '%s/retained.env' "$TEST_ROOT"; else printf '%s/missing.env' "$TEST_ROOT"; fi; }
package_ensure_candidate_image() { printf acquired; }
package_image() { printf default; }
selected_environment_image() { if [ "$TEST_SELECTED" = true ]; then printf explicit; fi; }
'''+ 'package_use_project_image() {'+use+'\n}\npackage_transition_image() {'+transition+'\n}\n'+'''
package_use_project_image fixture codex
unset SYMPHONY_RUNNER_IMAGE
package_transition_image fixture codex
'''
            result = subprocess.run(['bash','-c',driver],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.rstrip(), 'explicit' if selected else image if retained else 'acquiredacquireddefault')


# Keep graph setup reuse without duplicating the importer's independent tests.
for _name in list(vars(RunnerImportTest)):
    if _name.startswith('test_') and _name not in vars(RunnerProvisionTest):
        setattr(RunnerProvisionTest, _name, None)

del RunnerImportTest

if __name__ == '__main__': unittest.main()
