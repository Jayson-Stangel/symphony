"""Execute the real ephemeral-host entrypoint with owned host/daemon fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'symphony-codex-candidate-b6261d4c613296dd'
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE'])
options = json.loads(os.environ['OPTIONS'])
with (root/'calls').open('a') as log: log.write(json.dumps([name, *args])+'\n')
loaded = root/'loaded'
profile = 'symphony-codex-candidate-b6261d4c613296dd'
if name == 'uname': print({'-s':'Linux','-r':'6.14.0-fixture','-m':options.get('architecture','aarch64')}[args[0]])
elif name == 'hostname': print('owned-host')
elif name == 'git': print('a'*40)
elif name == 'stat': sys.exit(1 if options.get('missing_exec') else 0)
elif name == 'cat':
    if not args: sys.stdout.write(sys.stdin.read()); sys.exit(0)
    if args[0] == '/sys/module/apparmor/parameters/enabled': print('N' if options.get('disabled') else 'Y')
    elif args[0] == '/sys/kernel/security/lsm': print('lockdown,yama' if options.get('missing_lsm') else 'lockdown,yama,apparmor')
    elif args[0] == '/sys/kernel/security/apparmor/profiles':
        if options.get('unreadable_profiles') or (options.get('unreadable_after_remove') and (root/'removed').exists()): sys.exit(1)
        print(loaded.read_text() if loaded.exists() else '', end='')
    else: sys.stdout.write(pathlib.Path(args[0]).read_text())
elif name == 'sudo': sys.exit(subprocess.run(args[1:]).returncode)
elif name == 'apt-get':
    if options.get('install_failure'): sys.exit(9)
    if 'install' in args:
        target = root/'bin/apparmor_parser'
        target.write_bytes(pathlib.Path(__file__).read_bytes()); target.chmod(0o755)
elif name == 'apparmor_parser':
    if options.get('unloadable') and '--add' in args: sys.exit(9)
    if '--add' in args: loaded.write_text(profile + ' (' + ('complain' if options.get('complain') else 'enforce') + ')\n')
    elif '--remove' in args: loaded.unlink(missing_ok=True); (root/'removed').touch()
    else: sys.exit(8)
elif name == 'docker':
    if (options.get('context_failure') and args[0] == 'context') or (options.get('info_failure') and args[0] == 'info'):
        print('connection refused: ssh://user:synthetic-secret@remote', file=sys.stderr); sys.exit(9)
    if args[:2] == ['context','inspect']: print(options.get('endpoint','unix:///var/run/docker.sock'))
    elif args[0] == 'info':
        fmt = args[-1]
        if fmt == '{{.Name}}': print('foreign-host' if options.get('foreign_host') else 'owned-host')
        elif fmt == '{{.OSType}}': print('linux')
        else: print('name=seccomp,profile=builtin' + ('\nname=apparmor' if not options.get('no_apparmor') else ''))
    else: sys.exit(8)
else: sys.exit(8)
'''


class AppArmorPreparationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        scripts = self.root / 'scripts'
        (scripts / 'ci').mkdir(parents=True)
        for name in ('symphony', 'symphony-environment.sh', 'symphony-apparmor.sh', 'codex-apparmor.profile', 'codex-seccomp.json'):
            shutil.copy2(ROOT / 'scripts' / name, scripts / name)
        self.helper = scripts / 'ci/prepare-codex-apparmor.sh'
        shutil.copy2(ROOT / 'scripts/ci/prepare-codex-apparmor.sh', self.helper)
        binary = self.root / 'bin'
        binary.mkdir()
        for name in ('uname', 'hostname', 'cat', 'stat', 'docker', 'sudo', 'apparmor_parser', 'git', 'apt-get'):
            path = binary / name
            path.write_text(FAKE)
            path.chmod(0o755)
        # Do not inherit host directories: removing a fake parser must not
        # expose a real privileged parser installed on the qualification host.
        for name in ('bash', 'dirname', 'grep', 'rm', 'sha256sum', 'shasum'):
            executable = shutil.which(name)
            if executable:
                (binary / name).symlink_to(executable)
        (binary / 'python3').symlink_to(sys.executable)
        self.env = self.fixture_environment(binary)

    def fixture_environment(self, binary):
        environment = dict(os.environ, PATH=str(binary),
                           FIXTURE=str(self.root), OPTIONS='{}', RUNNER_TEMP=str(self.root),
                           GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted')
        for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'SYMPHONY_HOME'):
            environment.pop(name, None)
        return environment

    def run_helper(self, mode='prepare', **options):
        self.env['OPTIONS'] = json.dumps(options)
        return subprocess.run(['bash', str(self.helper), mode], env=self.env,
                              capture_output=True, text=True)

    def calls(self):
        path = self.root / 'calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_prepares_exact_verified_profile_and_cleans_only_owned_profile(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('loaded=enforce', result.stdout)
        self.assertIn('architecture=aarch64', result.stdout)
        self.assertIn(['apparmor_parser', '--add', str(self.root / 'scripts/codex-apparmor.profile')], self.calls())
        result = self.run_helper('cleanup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'loaded').exists())
        self.assertFalse((self.root / 'symphony-codex-apparmor').exists())

    def test_same_exact_profile_prepares_on_native_x86_64(self):
        result = self.run_helper(architecture='x86_64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('architecture=x86_64', result.stdout)
        self.assertIn('loaded=enforce', result.stdout)
        self.assertEqual(self.run_helper('cleanup', architecture='x86_64').returncode, 0)

    def test_no_apparmor_retains_shipped_selection_without_administrator_access(self):
        result = self.run_helper(no_apparmor=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no-AppArmor integration retained', result.stdout)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))

    def test_remote_daemons_refuse_before_administrator_access_without_leaking_endpoint(self):
        for options in ({'endpoint': 'ssh://user:secret@remote'}, {'foreign_host': True}):
            with self.subTest(options=options):
                result = self.run_helper(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('secret', result.stderr)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))

    def test_failed_daemon_probes_redact_raw_connection_errors(self):
        for options in ({'context_failure': True}, {'info_failure': True}):
            result = self.run_helper(**options)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('synthetic-secret', result.stdout + result.stderr)
            self.assertIn('CI AppArmor:', result.stderr)
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))

    def test_docker_host_override_and_context_precedence(self):
        self.env['DOCKER_HOST'] = 'tcp://secret@remote:2376'
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('secret', result.stderr)
        self.env['DOCKER_CONTEXT'] = 'selected-local'
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['docker', 'context', 'inspect', 'selected-local', '--format', '{{.Endpoints.docker.Host}}'], self.calls())

    def test_preexisting_policy_is_never_replaced_or_removed(self):
        (self.root / 'loaded').write_text(PROFILE + ' (enforce)\n')
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown provenance', result.stderr)
        self.assertEqual(self.run_helper('cleanup').returncode, 0)
        self.assertTrue((self.root / 'loaded').exists())
        self.assertFalse(any(call[0] == 'apparmor_parser' for call in self.calls()))

    def test_kernel_lsm_runtime_and_parser_failures_remain_distinct(self):
        for options, message in (({'disabled': True}, 'host kernel'),
                                 ({'missing_lsm': True}, 'LSM inventory'),
                                 ({'missing_exec': True}, 'per-thread exec'),
                                 ({'unloadable': True}, 'could not be loaded'),
                                 ({'complain': True}, 'not in enforce mode')):
            with self.subTest(options=options):
                result = self.run_helper(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                # Clean a parser attempt using its real ownership receipt.
                self.run_helper('cleanup')

    def test_missing_corrupt_and_symlink_profile_refuse_before_loading(self):
        profile = self.root / 'scripts/codex-apparmor.profile'
        original = profile.read_bytes()
        for mode in ('corrupt', 'missing', 'symlink'):
            profile.unlink(missing_ok=True)
            if mode == 'corrupt': profile.write_bytes(original + b'\n')
            if mode == 'symlink': profile.symlink_to(ROOT / 'scripts/codex-apparmor.profile')
            result = self.run_helper()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('runtime profile', result.stderr)
        self.assertFalse(any(call[0] == 'apparmor_parser' for call in self.calls()))

    def test_cleanup_verification_failure_preserves_receipt_and_refuses_success(self):
        self.assertEqual(self.run_helper().returncode, 0)
        result = self.run_helper('cleanup', unreadable_after_remove=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot verify profile removal', result.stderr)
        self.assertTrue((self.root / 'symphony-codex-apparmor').exists())
        self.assertNotIn('owned profile removed', result.stdout)

    def test_parser_install_is_explicit_and_follows_host_and_integrity_guards(self):
        (self.root / 'bin/apparmor_parser').unlink()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        install = next(i for i, call in enumerate(calls) if call[0] == 'apt-get')
        self.assertLess(next(i for i, call in enumerate(calls) if call[0] == 'stat'), install)
        self.assertLess(next(i for i, call in enumerate(calls) if call[:2] == ['docker', 'info']), install)

    def test_ambient_host_parser_cannot_escape_the_fixture(self):
        external = self.root / 'external-host-tools'
        external.mkdir()
        sentinel = self.root / 'external-parser-was-run'
        parser = external / 'apparmor_parser'
        parser.write_text('#!/bin/sh\nprintf unsafe > "' + str(sentinel) + '"\nexit 91\n')
        parser.chmod(0o755)
        (self.root / 'bin/apparmor_parser').unlink()
        with patch.dict(os.environ, PATH=str(external) + os.pathsep + os.environ['PATH']):
            self.assertEqual(shutil.which('apparmor_parser'), str(parser))
            self.env = self.fixture_environment(self.root / 'bin')
        self.assertIsNone(shutil.which('apparmor_parser', path=self.env['PATH']))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[0] == 'apt-get' for call in self.calls()))
        self.assertFalse(sentinel.exists())

    def test_failed_parser_install_refuses_qualification(self):
        (self.root / 'bin/apparmor_parser').unlink()
        result = self.run_helper(install_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot install AppArmor parser', result.stderr)
        self.assertFalse((self.root / 'symphony-codex-apparmor').exists())

    def test_self_hosted_and_non_ci_refuse(self):
        for key, value in (('RUNNER_ENVIRONMENT', 'self-hosted'), ('GITHUB_ACTIONS', 'false')):
            self.env[key] = value
            result = self.run_helper()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('disposable GitHub-hosted Linux', result.stderr)
            self.env[key] = 'github-hosted' if key == 'RUNNER_ENVIRONMENT' else 'true'
        self.assertEqual(self.calls(), [])


class AppArmorWorkflowTest(unittest.TestCase):
    def test_every_actual_protected_native_entrypoint_prepares_and_cleans_host(self):
        import re
        jobs = {
            'runner-release.yml': {
                'candidate': 'Build exact native runner candidates and verify digest pulls',
                'ubuntu-package-lifecycle': 'Real source-independent Ubuntu 26.04 manager lifecycle',
                'lifecycle-diagnostic': 'Diagnose actual Ubuntu lifecycle using selected real artifacts',
                'transfer-replay': 'Real checksum, Docker load and model-free runtime proof',
            },
            'runner-integration.yml': {
                'listener-and-cleanup': 'Execute the installed current manager with original immutable runners',
            },
        }
        for filename, expected in jobs.items():
            text = (ROOT / '.github/workflows' / filename).read_text()
            for job, protected in expected.items():
                with self.subTest(workflow=filename, job=job):
                    block = re.search(r'^  ' + job + r':\n(?:(?!^  [a-z]).*\n)*', text, re.M).group(0)
                    prepare = block.index('      - name: Prepare shipped Codex AppArmor on disposable host\n')
                    qualify = block.index('      - name: ' + protected + '\n')
                    cleanup = block.index('      - name: Remove only CI-owned Codex AppArmor policy\n')
                    evidence = block.index('      - name: Retain disposable host policy diagnostics\n')
                    self.assertLess(prepare, qualify)
                    self.assertLess(qualify, cleanup)
                    self.assertLess(cleanup, evidence)
                    prep = block[prepare:qualify]
                    clean = block[cleanup:evidence]
                    self.assertIn('id: codex-apparmor', prep)
                    packaged = job in ('ubuntu-package-lifecycle', 'listener-and-cleanup')
                    self.assertIn('packaged_apparmor.py prepare' if packaged else 'prepare-codex-apparmor.sh prepare', prep)
                    self.assertIn('packaged_apparmor.py cleanup' if packaged else 'prepare-codex-apparmor.sh cleanup', clean)
                    self.assertIn('always()', clean)
                    self.assertNotIn('continue-on-error', prep + clean)
                    if job in ('candidate', 'transfer-replay'):
                        self.assertIn("if: matrix.provider == 'codex'", prep)
                    if job == 'listener-and-cleanup':
                        self.assertIn("github.event_name == 'workflow_dispatch'", prep)
                    for step in (prep, clean):
                        # Explicit bash selects Actions' -e -o pipefail shell;
                        # tee must never hide a failed preparation or cleanup.
                        self.assertIn('shell: bash', step)
                        run = re.search(r'^        run: (.+)$', step, re.M).group(1)
                        with tempfile.TemporaryDirectory() as directory:
                            helper = Path(directory) / ('scripts/ci/packaged_apparmor.py' if packaged else 'scripts/ci/prepare-codex-apparmor.sh')
                            helper.parent.mkdir(parents=True)
                            helper.write_text('raise SystemExit(37)\n' if packaged else '#!/bin/sh\nexit 37\n')
                            result = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', run],
                                                    cwd=directory, env=dict(os.environ, RUNNER_TEMP=directory), capture_output=True)
                            self.assertEqual(result.returncode, 37)

    def test_actual_protected_image_entrypoint_checks_runtime_apply_and_keeps_both_network_modes(self):
        smoke = (ROOT / 'runner/codex-sandbox-smoke.sh').read_text()
        marker = smoke.index('AppArmor runtime apply verified')
        calls = [smoke.index('/opt/symphony/elixir/bin/symphony --check-codex-sandbox /tmp/' + name)
                 for name in ('qualification-WORKFLOW.md', 'wrapper-WORKFLOW.md')]
        self.assertTrue(all(marker < call for call in calls))
        self.assertIn('runner_codex_run docker --rm', smoke)
        self.assertIn('"$image_id"', smoke)
        boundaries = (ROOT / 'runner/codex-boundaries.py').read_text()
        self.assertIn('for network in (False, True):', boundaries)
        self.assertIn("raise RuntimeError('protected process/network effects failed')", boundaries)


if __name__ == '__main__':
    unittest.main()
