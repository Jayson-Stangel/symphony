"""Shell readiness stage contracts; command peers never start a listener/model."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class ReadinessStages(unittest.TestCase):
    def run_shell(self, body):
        with tempfile.TemporaryDirectory(prefix='symphony-stages-') as temporary:
            env = dict(os.environ, SYMPHONY_HOME=str(ROOT), SYMPHONY_RUNNER_NAMESPACE='readiness-fixture',
                       SYMPHONY_CONFIG_HOME=temporary, SYMPHONY_SKILLS_DIR=temporary,
                       SYMPHONY_BIN_DIR=temporary)
            return subprocess.run(['bash', '-c', 'source "$1/scripts/symphony" --help >/dev/null\n' + body,
                                   'readiness-test', str(ROOT)], env=env, capture_output=True, text=True)

    def test_engine_failure_stops_before_image_or_login(self):
        result = self.run_shell('''
project_credentials_require() { return 0; }
container_runtime() { printf fixture_runtime; }
fixture_runtime() { [ "$1" = info ] && return 29; echo UNEXPECTED >&2; return 90; }
runner_build_image() { echo UNEXPECTED >&2; }
auth_codex_check() { echo UNEXPECTED >&2; }
runner_doctor /unused codex
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('container engine unavailable', result.stderr)
        self.assertNotIn('UNEXPECTED', result.stderr)
        self.assertNotIn('cannot pull', result.stderr)

    def test_sandbox_only_checks_image_and_protected_command_without_auth(self):
        result = self.run_shell('''
container_runtime() { printf fixture_runtime; }
fixture_runtime() { case "$1" in info|image) return 0 ;; *) echo UNEXPECTED >&2; return 90 ;; esac; }
runner_codex_sandbox_ready() { echo protected-command-checked; }
auth_codex_check() { echo UNEXPECTED >&2; return 90; }
runner_doctor /unused codex true
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('protected-command-checked', result.stdout)
        self.assertIn('Native authentication, Git preflight, tracker access and model tasks remain untested', result.stdout)
        self.assertNotIn('UNEXPECTED', result.stderr)

    def test_unsupported_command_remediation_does_not_recommend_rebuild(self):
        result = self.run_shell('''
# This test reaches the protected-command diagnosis through a valid daemon
# security selection; host-preparation behavior has its own dedicated suite.
fixture_runtime() { [ "$1" = info ] && printf '%s\\n' 'name=seccomp,profile=builtin'; }
runner_codex_run() { echo 'Codex sandbox not ready: sandbox_unsupported_command'; return 1; }
runner_codex_sandbox_ready /unused fixture_runtime fixture_image
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Project launch configuration unsupported', result.stderr)
        self.assertNotIn('rebuilt', result.stderr)
        self.assertNotIn('select a rebuilt', result.stderr)

    def test_launch_failure_is_project_setup_stage(self):
        result = self.run_shell('''
# This test reaches the protected-command diagnosis through a valid daemon
# security selection; host-preparation behavior has its own dedicated suite.
fixture_runtime() { [ "$1" = info ] && printf '%s\\n' 'name=seccomp,profile=builtin'; }
runner_codex_run() { echo 'Codex sandbox not ready: sandbox_launch_failed'; return 1; }
runner_codex_sandbox_ready /unused fixture_runtime fixture_image
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Project launch environment failed', result.stderr)
        self.assertNotIn('rebuilt', result.stderr)

    def test_failed_native_login_status_cannot_report_ready_in_conditional(self):
        result = self.run_shell('''
container_runtime() { printf fixture_runtime; }
fixture_runtime() { case "$1" in info|volume) return 0 ;; run) return 37 ;; *) return 90 ;; esac; }
if auth_codex_check; then echo FALSE_READY; else exit 37; fi
''')
        self.assertEqual(result.returncode, 37)
        self.assertNotIn('ready for isolated runners', result.stdout)
        self.assertNotIn('FALSE_READY', result.stdout)

    def test_auth_engine_failure_precedes_team_environment_selection(self):
        for action in ('auth_codex_login', 'auth_codex_check', 'claude_auth check'):
            with self.subTest(action=action):
                result = self.run_shell('''
packaged_install=true
repo_root() { printf /unused; }
container_runtime() { printf fixture_runtime; }
fixture_runtime() { [ "$1" = info ] && return 29; echo UNEXPECTED >&2; return 90; }
package_use_project_image() { echo UNEXPECTED_SELECTION >&2; return 90; }
''' + action)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('container engine unavailable', result.stderr)
                self.assertNotIn('UNEXPECTED', result.stderr)

    def test_sandbox_only_rejects_wrong_provider_before_engine(self):
        result = self.run_shell('''
container_runtime() { echo UNEXPECTED >&2; }
runner_doctor /unused claude true
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--sandbox-only requires --agent codex', result.stderr)
        self.assertNotIn('UNEXPECTED', result.stderr)


if __name__ == '__main__':
    unittest.main()
