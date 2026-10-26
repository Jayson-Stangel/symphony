"""Credential selection through the real launcher; only synthetic secrets/runtime peers."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class ProjectCredentials(unittest.TestCase):
    def shell(self, body, credentials='', exported=None, file=True):
        with tempfile.TemporaryDirectory(prefix='symphony-credentials-') as temp:
            repo = Path(temp) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin',
                            'https://github.com/example/credential-fixture.git'], check=True)
            (repo / 'WORKFLOW.md').write_text('---\ntracker:\n  kind: linear\n  provider:\n    api_key: $LINEAR_API_KEY\n---\npreserved workflow\n')
            env = dict(os.environ)
            for name in ('LINEAR_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'):
                env.pop(name, None)
            env.update(SYMPHONY_HOME=str(ROOT), SYMPHONY_CONFIG_HOME=str(Path(temp) / 'config'),
                       SYMPHONY_RUNNER_HOME=str(Path(temp) / 'runners'),
                       SYMPHONY_RUNNER_NAMESPACE='credential-fixture', SYMPHONY_SKILLS_DIR=str(Path(temp) / 'skills'),
                       FIXTURE_CREDENTIALS=credentials)
            env.update(exported or {})
            setup = '''source "$1/scripts/symphony" --help >/dev/null
root=$PWD
file="$config_home/projects/$(project_id "$root").env"
global_skills_ready() { return 0; }
'''
            if file:
                setup += '''(umask 077; mkdir -p "$(dirname "$file")"; printf '%s' "$FIXTURE_CREDENTIALS" > "$file")
'''
            return subprocess.run(['bash', '-c', setup + body, 'credential-test', str(ROOT)],
                                  cwd=repo, env=env, capture_output=True, text=True)

    def test_empty_and_partial_diagnostics_include_all_names_and_no_values(self):
        for credentials, missing in [('', 'LINEAR_API_KEY, GH_TOKEN or GITHUB_TOKEN'),
                                     ('LINEAR_API_KEY=linear-canary\n', 'GH_TOKEN or GITHUB_TOKEN'),
                                     ('GH_TOKEN=git-canary\n', 'LINEAR_API_KEY')]:
            with self.subTest(credentials=bool(credentials)):
                result = self.shell('project_credentials_report "$root"', credentials)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Missing required credentials: ' + missing, result.stdout)
                self.assertIn('runner namespace: credential-fixture', result.stdout)
                self.assertIn('Contents and Pull requests read/write', result.stdout)
                self.assertNotIn('canary', result.stdout + result.stderr)

    def test_source_selection_and_aliases_match_exported_runtime_values(self):
        cases = [
            ('LINEAR_API_KEY=file-linear\nGH_TOKEN=file-gh\n', {}, 'file-linear', 'file-gh', 'project-file', 'GH_TOKEN'),
            ('LINEAR_API_KEY=file-linear\nGITHUB_TOKEN=file-github\n', {}, 'file-linear', 'file-github', 'project-file', 'GITHUB_TOKEN'),
            ('LINEAR_API_KEY=file-linear\nGH_TOKEN=file-gh\nGITHUB_TOKEN=file-github\n', {}, 'file-linear', 'file-gh', 'project-file', 'GH_TOKEN'),
            ('LINEAR_API_KEY=\nGH_TOKEN=\n', {'LINEAR_API_KEY': 'env-linear', 'GITHUB_TOKEN': 'env-github'}, 'env-linear', 'env-github', 'environment', 'GITHUB_TOKEN'),
            ('LINEAR_API_KEY=file-linear\nGH_TOKEN=file-gh\n', {'LINEAR_API_KEY': 'env-linear', 'GITHUB_TOKEN': 'env-github'}, 'env-linear', 'env-github', 'environment', 'GITHUB_TOKEN'),
            ('LINEAR_API_KEY=file-linear\nGH_TOKEN=file-gh\n', {'GH_TOKEN': '', 'GITHUB_TOKEN': ''}, 'file-linear', 'file-gh', 'project-file', 'GH_TOKEN'),
            ('', {'LINEAR_API_KEY': 'env-linear', 'GH_TOKEN': 'env-gh', 'GITHUB_TOKEN': 'env-github'}, 'env-linear', 'env-gh', 'environment', 'GH_TOKEN'),
        ]
        for credentials, exported, linear, git, source, alias in cases:
            with self.subTest(source=source, alias=alias):
                body = '''project_credentials_report "$root" true
project_credentials_require "$root"
project_credentials_export
[ "$LINEAR_API_KEY" = "$EXPECTED_LINEAR" ]
[ "$GH_TOKEN" = "$EXPECTED_GIT" ]
[ "$GITHUB_TOKEN" = "$EXPECTED_GIT" ]
'''
                result = self.shell(body, credentials, dict(exported, EXPECTED_LINEAR=linear, EXPECTED_GIT=git))
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report['ready'])
                self.assertEqual(report['git_source'], source)
                self.assertEqual(report['git_name'], alias)
                self.assertEqual(report['native_authentication'], 'untested')
                self.assertNotIn(linear, result.stdout)
                self.assertNotIn(git, result.stdout)

    def test_custom_tracker_and_host_environment_remain_supported(self):
        result = self.shell('''printf '%s\\n' '---' 'tracker:' '  kind: linear' '  provider:' '    api_key: $CUSTOM_LINEAR_KEY' '---' 'Example:' '    api_key: $LINEAR_API_KEY' > WORKFLOW.md
project_credentials_report "$root" true
project_environment_export
project_credentials_export
[ "$CUSTOM_LINEAR_KEY" = custom-linear-canary ]
[ "$CUSTOM_TOOL_TOKEN" = custom-tool-canary ]
[ "$GH_TOKEN" = git-canary ]
''', 'CUSTOM_LINEAR_KEY=custom-linear-canary\nCUSTOM_TOOL_TOKEN=old-canary\nCUSTOM_TOOL_TOKEN=custom-tool-canary\nGH_TOKEN=git-canary\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['tracker_configuration'], 'custom-unchecked')
        self.assertTrue(report['ready'])
        self.assertNotIn('canary', result.stdout + result.stderr)

    def test_file_contents_are_never_executed_or_printed(self):
        result = self.shell('''project_credentials_report "$root"
[ ! -e "$root/EXECUTED" ]
''', 'LINEAR_API_KEY=$(touch EXECUTED)\nGH_TOKEN=`touch EXECUTED`\nprintf secret-canary\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('secret-canary', result.stdout + result.stderr)
        self.assertNotIn('touch EXECUTED', result.stdout + result.stderr)

    def test_init_private_template_and_reinitialization_preserve_file_and_workflow(self):
        result = self.shell('''init --linear-project fixture
[ -f "$file" ] && [ ! -L "$file" ]
[ "$(ls -l "$file" | cut -c 1-10)" = '-rw-------' ]
printf 'LINEAR_API_KEY=linear-canary\nGITHUB_TOKEN=git-canary\n' > "$file"
cp "$file" "$root/before"
init --linear-project fixture
cmp "$file" "$root/before"
grep -q '^preserved workflow$' WORKFLOW.md
''', file=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Missing required credentials: LINEAR_API_KEY, GH_TOKEN or GITHUB_TOKEN', result.stdout)
        self.assertNotIn('canary', result.stdout + result.stderr)

    def test_project_doctor_json_escapes_path_and_reports_missing_not_just_file(self):
        result = self.shell('''config_home="$config_home/quoted\\\"path"
project doctor --json
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['credentials'])
        self.assertFalse(data['project_credentials']['ready'])
        self.assertIn('LINEAR_API_KEY', data['project_credentials']['missing'])
        self.assertIn('quoted"path', data['project_credentials']['file'])

    def test_missing_stops_both_providers_before_auth_engine_or_image(self):
        for provider in ('codex', 'claude'):
            for command in ('runner_start "$root" 4010 ', 'runner_doctor "$root" '):
                with self.subTest(provider=provider, command=command):
                    result = self.shell('''runner_project_lock() { :; }
runner_require_identity() { :; }
container_runtime() { echo UNEXPECTED_ENGINE >&2; return 90; }
package_use_project_image() { echo UNEXPECTED_IMAGE >&2; return 90; }
''' + command + provider)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn('LINEAR_API_KEY, GH_TOKEN or GITHUB_TOKEN', result.stderr)
                    self.assertNotIn('UNEXPECTED', result.stdout + result.stderr)

    def test_configured_startup_for_both_providers_passes_effective_credentials_by_name(self):
        for provider in ('codex', 'claude'):
            for file in (True, False):
                with self.subTest(provider=provider, file=file):
                    result = self.shell('''
runner_project_lock() { :; }
runner_require_identity() { :; }
container_runtime() { printf fixture_runtime; }
codex_runtime_profile_id() { printf fixture-profile; }
runner_status() { printf missing; }
runner_image() { printf fixture-image; }
runner_storage_id() { printf fixture-storage; }
fixture_runtime() { case "$1" in image) printf fixture-image ;; esac; }
ensure_claude_auth_volume() { :; }
claude_image_ready() { :; }
claude_native_profile() { printf fixture-native-profile; }
runner_codex_sandbox_ready() { :; }
runner_write_metadata() { :; }
fixture_launch() {
  [ "$LINEAR_API_KEY" = linear-canary ] && [ "$GH_TOKEN" = git-canary ] && [ "$GITHUB_TOKEN" = git-canary ] || return 91
  case " $* " in *' --env LINEAR_API_KEY --env GH_TOKEN --env GITHUB_TOKEN '*) : ;; *) return 92 ;; esac
  case " $* " in *canary*) return 93 ;; esac
  printf checked > "$root/launch-checked"
}
runner_codex_run() { fixture_launch "$@"; }
runner_claude_run() { fixture_launch "$@"; }
runner_start "$root" 4010 "$FIXTURE_PROVIDER"
[ "$(cat "$root/launch-checked")" = checked ]
''', 'LINEAR_API_KEY=linear-canary\nGITHUB_TOKEN=git-canary\n',
                        {'FIXTURE_PROVIDER': provider, **({} if file else {'LINEAR_API_KEY': 'linear-canary', 'GITHUB_TOKEN': 'git-canary'})}, file=file)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn('canary', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
