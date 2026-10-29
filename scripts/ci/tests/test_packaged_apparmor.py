"""Portable refusal/input tests only; never native AppArmor acceptance."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('packaged_apparmor', ROOT / 'scripts/ci/packaged_apparmor.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
REVISION = 'a' * 40


def manifest(archive):
    runners = {provider: {arch: {'platform': platform, 'source_revision': REVISION,
                                'image': 'ghcr.io/fixture/' + provider + '@sha256:' + 'b' * 64,
                                'tool_versions': {name: '1.0' for name in (provider, 'git', 'node', 'playwright')}}
                         for arch, platform in (('arm64', 'linux/arm64'), ('x86_64', 'linux/amd64'))}
               for provider in ('codex', 'claude')}
    mac = {'filename': 'symphony-1.2.3-macos.tar.gz', 'sha256': 'c' * 64, 'architectures': ['arm64']}
    linux = {'filename': archive.name, 'sha256': gate.digest(archive), 'architectures': ['x86_64']}
    return {'schema_version': 1, 'version': '1.2.3', 'source_revision': REVISION, 'channel': 'candidate',
            'host_architectures': ['arm64', 'x86_64'],
            'supported_hosts': {'macos': ['arm64'], 'linux_x86_64': ['x86_64']},
            'runners': runners, 'bundle': mac, 'bundles': {'macos': mac, 'linux_x86_64': linux}}


class PackagedPreparationGuardTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.archive = self.root / 'symphony-1.2.3-linux_x86_64.tar.gz'
        self.write_archive()
        self.manifest = self.root / 'release.json'
        self.manifest.write_text(json.dumps(manifest(self.archive)))

    def write_archive(self, name='scripts/symphony', kind=tarfile.REGTYPE, mode=0o755):
        with tarfile.open(self.archive, 'w:gz') as archive:
            member = tarfile.TarInfo(name)
            member.type, member.mode = kind, mode
            data = b'fixture bytes; not an executable acceptance package'
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            member.linkname = '/etc/passwd'
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)

    def test_source_and_archive_corruption_refuse_before_privilege(self):
        self.assertEqual(gate.selected_archive(self.root, self.manifest, REVISION)[0], self.archive)
        with self.assertRaisesRegex(ValueError, 'source differs'):
            gate.selected_archive(self.root, self.manifest, 'd' * 40)
        self.archive.write_bytes(self.archive.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            gate.selected_archive(self.root, self.manifest, REVISION)

    def test_unknown_manifest_shape_and_linked_archive_refuse(self):
        self.manifest.write_text(json.dumps({'source_revision': REVISION,
                                            'bundles': {'linux_x86_64': {'filename': self.archive.name}}}))
        with self.assertRaises(ValueError):
            gate.selected_archive(self.root, self.manifest, REVISION, assembled=True)
        self.manifest.write_text(json.dumps(manifest(self.archive)))
        original = self.root / 'original.tar.gz'
        self.archive.rename(original)
        self.archive.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'missing or linked'):
            gate.selected_archive(self.root, self.manifest, REVISION)

    def test_tar_links_traversal_and_shared_writes_refuse(self):
        for name, kind, mode in (('../escape', tarfile.REGTYPE, 0o644),
                                 ('/absolute', tarfile.REGTYPE, 0o644),
                                 ('linked', tarfile.SYMTYPE, 0o755),
                                 ('hardlink', tarfile.LNKTYPE, 0o644),
                                 ('unsafe', tarfile.REGTYPE, 0o666),
                                 ('setuid', tarfile.REGTYPE, 0o4755)):
            with self.subTest(name=name):
                self.write_archive(name, kind, mode)
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    with self.assertRaisesRegex(ValueError, 'unsafe native archive entry'):
                        gate.extract_bundle(self.archive, Path(directory) / 'bundle')
        self.assertFalse((self.root / 'escape').exists())

    def test_real_current_source_policy_identity_is_used(self):
        profile, checksum = gate.policy_identity()
        self.assertRegex(profile, gate.PROFILE_RE)
        self.assertEqual(checksum, hashlib.sha256((ROOT / 'scripts/codex-apparmor.profile').read_bytes()).hexdigest())

    def test_omitted_and_tampered_receipt_cannot_verify(self):
        profile, checksum = gate.policy_identity()
        evidence = self.root / 'receipt.json'
        receipt = {'source_revision': REVISION, 'approved_revision': REVISION, 'package_sha256': gate.digest(self.archive),
                   'package_filename': self.archive.name, 'profile': profile, 'policy_sha256': checksum,
                   'cleanup': 'verified owned profile/persistence removal; Docker not restarted',
                   **dict.fromkeys(('profile_absent_before', 'prepared', 'repeated', 'boot_guard', 'archive_unchanged'), True)}
        with patch.object(gate, 'native_context', return_value=REVISION):
            with self.assertRaises(FileNotFoundError):gate.verify(self.root, self.manifest, evidence)
            for field in ('source_revision', 'approved_revision', 'package_sha256', 'prepared', 'profile_absent_before', 'cleanup'):
                changed = dict(receipt)
                changed[field] = False if isinstance(changed[field], bool) else 'changed'
                evidence.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, 'missing or mismatched'):
                    gate.verify(self.root, self.manifest, evidence)
            evidence.write_text(json.dumps(receipt))
            self.assertEqual(gate.verify(self.root, self.manifest, evidence), receipt)

    def test_stale_cleanup_refuses_before_any_privileged_command(self):
        evidence = self.root / 'receipt.json'
        evidence.write_text(json.dumps({'source_revision': 'd' * 40}))
        with patch.object(gate, 'native_context', return_value=REVISION), \
                patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privileged operation')):
            with self.assertRaisesRegex(ValueError, 'cleanup identity differs'):
                gate.cleanup(evidence)

    def cleanup_receipt(self):
        profile, checksum = gate.policy_identity()
        work = self.root / 'symphony-packaged-apparmor-owned'
        work.mkdir()
        marker = work / 'ownership.json'
        marker.write_text('owned marker')
        evidence = self.root / 'receipt.json'
        receipt = {'source_revision': REVISION, 'approved_revision': REVISION, 'profile': profile, 'policy_sha256': checksum,
                   'parser': '/usr/sbin/apparmor_parser', 'work': str(work),
                   'work_marker_sha256': gate.digest(marker), 'service_enabled_before': 'enabled',
                   'dropdir_existed_before': True, 'profile_absent_before': True}
        evidence.write_text(json.dumps(receipt))
        return evidence, receipt

    def test_changed_ownership_marker_refuses_before_cleanup_inspection_or_mutation(self):
        evidence, receipt = self.cleanup_receipt()
        Path(receipt['work'], 'ownership.json').write_text('changed marker')
        with patch.object(gate, 'native_context', return_value=REVISION), \
                patch.object(gate, 'tool', return_value='/usr/sbin/apparmor_parser'), \
                patch.dict(os.environ, RUNNER_TEMP=str(self.root)), \
                patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privilege')):
            with self.assertRaisesRegex(ValueError, 'owned extraction identity differs'):
                gate.cleanup(evidence)
        self.assertTrue(evidence.is_file())
        self.assertTrue(Path(receipt['work']).is_dir())

    def test_modified_policy_bytes_refuse_before_unload_or_removal(self):
        evidence, receipt = self.cleanup_receipt()
        calls = []
        def privilege(*args, **kwargs):
            calls.append(args)
            if args[0].endswith('/stat'):return '0 644 regular file'
            if args[0].endswith('/cat'):return 'changed policy bytes\n'
            raise AssertionError('unexpected mutation')
        with patch.object(gate, 'native_context', return_value=REVISION), \
                patch.object(gate, 'tool', side_effect=lambda name: '/usr/sbin/apparmor_parser' if name == 'apparmor_parser' else '/usr/bin/' + name), \
                patch.dict(os.environ, RUNNER_TEMP=str(self.root)), \
                patch.object(gate, 'exists', return_value=True), \
                patch.object(gate, 'privilege', side_effect=privilege):
            with self.assertRaisesRegex(ValueError, 'owned file changed'):
                gate.cleanup(evidence)
        self.assertEqual([Path(args[0]).name for args in calls], ['stat', 'cat'])
        self.assertTrue(evidence.is_file())

    def test_manual_consent_guard_rejects_pr_push_fork_and_missing_consent(self):
        base = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted',
                    GITHUB_SHA=REVISION, PACKAGED_HOST_PREPARATION='true', PACKAGED_HOST_REVISION=REVISION,
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='Jayson-Stangel/symphony',
                    GITHUB_ACTOR='Jayson-Stangel', GITHUB_TRIGGERING_ACTOR='Jayson-Stangel')
        cases = ({'PACKAGED_HOST_PREPARATION': 'false'}, {'PACKAGED_HOST_PREPARATION': ''},
                 {'GITHUB_EVENT_NAME': 'pull_request'}, {'GITHUB_EVENT_NAME': 'push'},
                 {'GITHUB_REPOSITORY': 'foreign/fork'}, {'GITHUB_ACTOR': 'foreign'},
                 {'GITHUB_TRIGGERING_ACTOR': 'foreign'})
        with patch.object(gate.platform, 'system', return_value='Linux'), \
                patch.object(gate.platform, 'machine', return_value='x86_64'), \
                patch.object(gate.os, 'geteuid', return_value=1001), \
                patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privilege')):
            for changed in cases:
                with self.subTest(changed=changed), patch.dict(os.environ, dict(base, **changed)):
                    with self.assertRaisesRegex(ValueError, 'consent is required'):gate.native_context()
            with patch.dict(os.environ, base):self.assertEqual(gate.native_context(), REVISION)

    def test_malformed_archive_and_json_exit_closed_without_traceback(self):
        errors = (tarfile.ReadError('untrusted archive detail'),
                  json.JSONDecodeError('invalid receipt', 'x', 0))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch.object(sys, 'argv', ['packaged_apparmor.py', 'prepare', '--packages', str(self.root),
                        '--manifest', str(self.manifest), '--evidence', str(self.root / 'proof.json')]), \
                    patch.object(gate, 'prepare', side_effect=error), \
                    patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privilege')), \
                    patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
                self.assertEqual(gate.main(), 1)
                self.assertNotIn('Traceback', stderr.getvalue())
                if isinstance(error, tarfile.TarError):
                    self.assertIn('inspect owned local evidence', stderr.getvalue())
                    self.assertNotIn('untrusted archive detail', stderr.getvalue())

    def test_approved_revision_is_mandatory_and_exact_before_helper_privilege(self):
        base = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted',
                    GITHUB_SHA=REVISION, PACKAGED_HOST_PREPARATION='true',
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='Jayson-Stangel/symphony',
                    GITHUB_ACTOR='Jayson-Stangel', GITHUB_TRIGGERING_ACTOR='Jayson-Stangel')
        with patch.object(gate.platform, 'system', return_value='Linux'), \
                patch.object(gate.platform, 'machine', return_value='x86_64'), \
                patch.object(gate.os, 'geteuid', return_value=1001), \
                patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privilege')):
            for approved in (None, '', 'd' * 40, 'a' * 39, 'A' * 40):
                env = dict(base)
                if approved is not None:env['PACKAGED_HOST_REVISION'] = approved
                with self.subTest(approved=approved), patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(ValueError, 'approved revision differs'):gate.native_context()
            with patch.dict(os.environ, dict(base, PACKAGED_HOST_REVISION=REVISION), clear=True):
                self.assertEqual(gate.native_context(), REVISION)

    def test_actual_workflow_revision_guards_fail_closed_before_fanout(self):
        import re
        base = dict(os.environ, PACKAGED_HOST_PREPARATION='true', GITHUB_SHA=REVISION,
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='Jayson-Stangel/symphony',
                    GITHUB_ACTOR='Jayson-Stangel', GITHUB_TRIGGERING_ACTOR='Jayson-Stangel')
        for filename in ('burrito-release.yml', 'runner-release.yml', 'runner-integration.yml'):
            text = (ROOT / '.github/workflows' / filename).read_text()
            block = text.split('      - name: Require approved packaged host preparation revision\n', 1)[1].split('      - name:', 1)[0]
            # False retains ordinary CI compatibility: Actions omits this manual capability guard.
            self.assertIn('        if: inputs.packaged_host_preparation\n', block)
            script = '\n'.join(line[10:] for line in block.split('        run: |\n', 1)[1].splitlines() if line.startswith('          '))
            for approved, expected in ((None, False), ('', False), ('d' * 40, False),
                                       ('a' * 39, False), ('A' * 40, False), (REVISION, True)):
                env = dict(base)
                if approved is None:env.pop('PACKAGED_HOST_REVISION', None)
                else:env['PACKAGED_HOST_REVISION'] = approved
                with self.subTest(workflow=filename, approved=approved):
                    result = subprocess.run(['bash', '-e', '-c', script], env=env, capture_output=True)
                    self.assertEqual(result.returncode == 0, expected, result.stderr)
            if filename != 'runner-release.yml':
                self.assertIn('packaged_host_revision: ${{ inputs.packaged_host_revision }}', text)
            if filename == 'burrito-release.yml':
                self.assertIn('needs: production-source', text)
            else:
                portable = re.search(r'^  portable:\n(?:(?!^  [a-z]).*\n)*', text, re.M).group(0)
                self.assertIn('packaged-host-consent', portable)
                if filename == 'runner-release.yml':
                    qualification = re.search(r'^  qualification:\n(?:(?!^  [a-z]).*\n)*', text, re.M).group(0)
                    self.assertIn("needs.packaged-host-consent.result == 'success'", qualification)
        for filename, names in (('runner-release.yml', ('packaged-candidate', 'packaged-release-platform', 'ubuntu-package-lifecycle')),
                                ('runner-integration.yml', ('listener-and-cleanup',))):
            text = (ROOT / '.github/workflows' / filename).read_text()
            for name in names:
                block = re.search(r'^  ' + name + r':\n(?:(?!^  [a-z]).*\n)*', text, re.M).group(0)
                for step in block.split('      - name:'):
                    if 'packaged_apparmor.py' in step:
                        self.assertIn('PACKAGED_HOST_REVISION: ${{ inputs.packaged_host_revision }}', step)

    def test_installed_preparation_receipt_is_not_cleaned_assembly_acceptance(self):
        profile, checksum = gate.policy_identity()
        receipt = {'source_revision': REVISION, 'approved_revision': REVISION, 'package_sha256': gate.digest(self.archive),
                   'package_filename': self.archive.name, 'profile': profile, 'policy_sha256': checksum,
                   **dict.fromkeys(('profile_absent_before', 'prepared', 'repeated', 'boot_guard', 'archive_unchanged'), True)}
        evidence = self.root / 'prepared.json'
        evidence.write_text(json.dumps(receipt))
        with patch.object(gate, 'native_context', return_value=REVISION):
            self.assertEqual(gate.verify(self.root, self.manifest, evidence, cleanup_required=False), receipt)
            with self.assertRaisesRegex(ValueError, 'missing or mismatched'):
                gate.verify(self.root, self.manifest, evidence)
            self.archive.write_bytes(self.archive.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum differs'):
                gate.verify(self.root, self.manifest, evidence, cleanup_required=False)

    def test_workflows_default_refuse_and_bind_all_four_native_sites(self):
        import re
        root = ROOT / '.github/workflows'
        release = (root / 'runner-release.yml').read_text()
        integration = (root / 'runner-integration.yml').read_text()
        burrito = (root / 'burrito-release.yml').read_text()
        for filename, text, declarations in (('release', release, 2), ('integration', integration, 1), ('burrito', burrito, 1)):
            self.assertEqual(len(re.findall(r'      packaged_host_preparation:.*?default: false', text, re.S)), declarations, filename)
            self.assertIn('test "$GITHUB_EVENT_NAME" = workflow_dispatch', text)
            self.assertIn('test "$GITHUB_TRIGGERING_ACTOR" = Jayson-Stangel', text)
            self.assertIn('test "$PACKAGED_HOST_PREPARATION" = true', text)
        for text in (integration, burrito):
            self.assertIn('packaged_host_preparation: ${{ inputs.packaged_host_preparation }}', text)
        for name in ('packaged-candidate', 'packaged-release-platform', 'ubuntu-package-lifecycle'):
            block = re.search(r'^  ' + name + r':\n(?:(?!^  [a-z]).*\n)*', release, re.M).group(0)
            self.assertIn('packaged_apparmor.py prepare', block)
            self.assertIn('packaged_apparmor.py cleanup', block)
            self.assertIn('packaged_apparmor.py verify', block)
            self.assertNotIn('continue-on-error', block)
            if name != 'ubuntu-package-lifecycle':self.assertIn("matrix.platform == 'linux_x86_64'", block)
        for name in ('packaged-candidate-metadata', 'packaged-release'):
            block = re.search(r'^  ' + name + r':\n(?:(?!^  [a-z]).*\n)*', release, re.M).group(0)
            self.assertIn('Reject missing or mismatched Linux package preparation receipt', block)
            self.assertIn('packaged_apparmor.py verify --packages', block)
            self.assertNotIn('continue-on-error', block)
        block = re.search(r'^  listener-and-cleanup:\n(?:(?!^  [a-z]).*\n)*', integration, re.M).group(0)
        self.assertIn('packaged_apparmor.py prepare', block)
        self.assertIn('packaged_apparmor.py cleanup', block)
        self.assertIn('PACKAGED_APPARMOR_EVIDENCE:', block)

    def test_non_native_or_non_hosted_controller_refuses_before_package_or_sudo(self):
        for system, machine, uid, hosted in (('Darwin', 'arm64', 501, 'github-hosted'),
                                             ('Linux', 'x86_64', 0, 'github-hosted'),
                                             ('Linux', 'x86_64', 1001, 'self-hosted')):
            with self.subTest(system=system, uid=uid), \
                    patch.object(gate.platform, 'system', return_value=system), \
                    patch.object(gate.platform, 'machine', return_value=machine), \
                    patch.object(gate.os, 'geteuid', return_value=uid), \
                    patch.dict(os.environ, GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT=hosted, GITHUB_SHA=REVISION), \
                    patch.object(gate, 'privilege', side_effect=AssertionError('unexpected privileged operation')):
                with self.assertRaises(ValueError):gate.native_context()


if __name__ == '__main__':
    unittest.main()
