"""Credential selection through the real launcher; only synthetic secrets/runtime peers."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from codex_config_fixture import EMPTY_SNAPSHOT

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
                       FIXTURE_CREDENTIALS=credentials, FIXTURE_EMPTY_SNAPSHOT=EMPTY_SNAPSHOT)
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

    def test_candidate_acquisition_uses_shared_credential_source_without_values_in_arguments(self):
        for exported in ({}, {'GITHUB_TOKEN': 'environment_canary'}):
            with self.subTest(source='environment' if exported else 'project-file'):
                result = self.shell(r'''
. "$1/scripts/symphony-runner-provision.sh"
# Only remote acquisition/native extraction/import are peers; credential
# selection runs the actual composed project_credentials_read function.
mkdir -p "$root/scripts"
cat > "$root/scripts/symphony-runner-import.sh" <<'IMPORT'
package_runner_import() {
  [ "$(cat "$2")" = verified-graph-fixture ] || return 81
  printf imported > "$TEST_IMPORTED"
}
IMPORT
package_helper() {
  case "$1" in
    release-get) case "$3" in *.image) printf 'localhost:5000/symphony-codex@sha256:%064d' 0 ;; *.artifact_id) printf 123 ;; *) return 82 ;; esac ;;
    validate-candidate-artifact) printf 1024 ;;
    extract-candidate-artifact) printf verified-graph-fixture > "$6" ;;
    *) return 83 ;;
  esac
}
curl() {
  local config output= arg
  IFS= read -r config
  [ "$config" = "header = \"Authorization: Bearer $EXPECTED_GIT\"" ] || return 84
  for arg in "$@"; do case "$arg" in *canary*) return 85 ;; esac; done
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then output=$2; break; fi
    shift
  done
  [ -n "$output" ] || return 86
  printf synthetic-transport > "$output"
}
package_arch=arm64; package_channel=candidate
symphony_home=$root
export TEST_IMPORTED="$root/imported" TMPDIR="$root"
package_runner_provision codex
[ "$(cat "$TEST_IMPORTED")" = imported ]
[ -z "$(find "$root" -name 'symphony-runner-download.*' -print)" ]
''', 'GH_TOKEN=file_canary\n',
                    dict(exported, EXPECTED_GIT='environment_canary' if exported else 'file_canary'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('canary', result.stdout + result.stderr)

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
fixture_runtime() {
  case "$1" in
    image) printf fixture-image ;;
    info) printf '%s\\n' 'name=seccomp,profile=builtin' ;;
    run) case " $* " in
      *' --entrypoint /usr/bin/stat '*) id -u ;;
      *'test -f /etc/symphony/WORKFLOW.md && test -r /etc/symphony/WORKFLOW.md'*)
        case " $* " in *' --env '*|*' --env-file '*|*canary*) return 97 ;; esac
        case " $* " in *' --user 11001:11001 '*) : ;; *) return 98 ;; esac
        case " $* " in *" --mount type=bind,source=$root,target=/etc/symphony,readonly "*) : ;; *) return 99 ;; esac
        printf checked > "$root/access-checked" ;;
      *) return 94 ;;
    esac ;;
  esac
}
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
runner_codex_run() {
  case " $* " in
    *' --codex-config-snapshot '*) printf '%s\\n' "$FIXTURE_EMPTY_SNAPSHOT" ;;
    *)
      codex_config_require "$root" fixture-image
      [ "$(cat "$codex_config_file")" = '' ] || return 95
      case " $* " in *"source=$codex_config_file,target=/root/.codex/config.toml,readonly"*) : ;; *) return 96 ;; esac
      fixture_launch "$@" ;;
  esac
}
runner_claude_run() { fixture_launch "$@"; }
runner_start "$root" 4010 "$FIXTURE_PROVIDER"
[ "$(cat "$root/launch-checked")" = checked ]
if [ "$FIXTURE_PROVIDER" = claude ]; then [ "$(cat "$root/access-checked")" = checked ]; fi
''', 'LINEAR_API_KEY=linear-canary\nGITHUB_TOKEN=git-canary\n',
                        {'FIXTURE_PROVIDER': provider, **({} if file else {'LINEAR_API_KEY': 'linear-canary', 'GITHUB_TOKEN': 'git-canary'})}, file=file)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn('canary', result.stdout + result.stderr)


class RunnerGitCredentials(unittest.TestCase):
    """Real Git protocol/configuration; inert credentials and no network."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='symphony-git-helper-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.helper = self.home / 'symphony-git-credential'
        self.helper.write_bytes((ROOT / 'runner/claude/git-credential.sh').read_bytes())
        self.helper.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('GIT_', 'GH_', 'GITHUB_', 'SYMPHONY_'))}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home),
                        GIT_CONFIG_GLOBAL=str(self.home / '.gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                        GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='false')

    def helper_call(self, args, request, **tokens):
        return subprocess.run(['sh', str(self.helper), *args], input=request,
                              env=dict(self.env, **tokens), cwd=self.home, capture_output=True, text=True, timeout=5)

    def configure(self, provider, **tokens):
        path = ROOT / ('runner/entrypoint.sh' if provider == 'codex' else 'runner/claude/entrypoint.sh')
        # Execute the real selected-Git binding and Git configuration before the
        # manager launches. Map only the installed helper into this fixture.
        commands = '\n'.join(line for line in path.read_text().splitlines()
                             if line.startswith(('git config --global', 'export SYMPHONY_GIT_TOKEN=')))
        commands = commands.replace('/usr/local/bin/symphony-git-credential', str(self.helper))
        bound = dict(self.env, **tokens)
        result = subprocess.run(['sh', '-ec', commands + '\nprintf %s "${SYMPHONY_GIT_TOKEN:-}"'],
                                env=bound, cwd=self.home, capture_output=True, text=True, check=True)
        bound['SYMPHONY_GIT_TOKEN'] = result.stdout
        return bound

    def test_existing_helper_precedence_is_unchanged(self):
        request = 'protocol=https\nhost=github.com\n\n'
        cases = [
            ({'GH_TOKEN': 'fallback', 'SYMPHONY_GIT_TOKEN': 'selected'}, 'selected'),
            ({'GH_TOKEN': 'fallback', 'GITHUB_TOKEN': 'ignored'}, 'fallback'),
            ({'GITHUB_TOKEN': 'ignored'}, None),
        ]
        for tokens, expected in cases:
            with self.subTest(expected=expected):
                result = self.helper_call(['get'], request, **tokens)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, '')
                self.assertEqual(result.stdout, '' if expected is None else
                                 'username=x-access-token\npassword=' + expected + '\n')

    def test_codex_binds_only_the_launchers_selected_project_credential(self):
        for tokens, expected in [({'GH_TOKEN': 'primary', 'GITHUB_TOKEN': 'other'}, 'primary'),
                                 ({'GITHUB_TOKEN': 'alias'}, 'alias'),
                                 ({'GH_TOKEN': '', 'GITHUB_TOKEN': 'alias'}, 'alias'),
                                 ({}, '')]:
            with self.subTest(expected=expected):
                bound = self.configure('codex', SYMPHONY_GIT_TOKEN='unselected-ambient', **tokens)
                self.assertEqual(bound['SYMPHONY_GIT_TOKEN'], expected)
                # Match GitHub tracker's canonical-alias filtering. The real
                # local-Port regression additionally checks its custom secret.
                bound.pop('GH_TOKEN', None)
                bound.pop('GITHUB_TOKEN', None)
                result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                                        env=bound, cwd=self.home, capture_output=True, text=True)
                if expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('password=' + expected + '\n', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('unselected-ambient', result.stdout + result.stderr)

    def test_helper_refuses_foreign_requests_and_non_get_operations(self):
        for args in [[], ['store'], ['erase'], ['--unknown', 'get']]:
            result = self.helper_call(args, 'protocol=https\nhost=github.com\n\n', GH_TOKEN='synthetic')
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        for protocol, host in [('http', 'github.com'), ('ssh', 'github.com'),
                               ('https', 'elsewhere.invalid'), ('https', 'github.com.evil.invalid'),
                               ('https', 'github.com:443'), ('https', '')]:
            result = self.helper_call(['get'], f'protocol={protocol}\nhost={host}\n\n', GH_TOKEN='synthetic')
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_actual_git_fill_clean_url_routing_and_repeat_start(self):
        urls = ['https://github.com/example/private.git', 'git@github.com:example/private.git',
                'ssh://git@github.com/example/private.git']
        for provider in ['codex', 'claude']:
            with self.subTest(provider=provider):
                tokens = dict(GH_TOKEN='synthetic-selected', GITHUB_TOKEN='synthetic-other',
                              SYMPHONY_GIT_TOKEN='synthetic-claude')
                bound = self.configure(provider, **tokens)
                before = (self.home / '.gitconfig').read_bytes()
                self.configure(provider, **tokens)
                self.assertEqual((self.home / '.gitconfig').read_bytes(), before)
                self.assertNotIn(b'synthetic', before)
                self.assertNotIn(b'x-access-token:', before)
                for url in urls:
                    routed = subprocess.run(['git', 'ls-remote', '--get-url', url], env=bound,
                                            cwd=self.home, capture_output=True, text=True, check=True).stdout.strip()
                    self.assertEqual(routed, urls[0])
                    # Git lookup works before AND after raw tracker aliases are
                    # removed, through the bound selected-Git route.
                    scrubbed = {k: v for k, v in bound.items() if k not in ('GH_TOKEN', 'GITHUB_TOKEN')}
                    for environment in [bound, scrubbed]:
                        result = subprocess.run(['git', 'credential', 'fill'], input='url=' + routed + '\n\n',
                                                env=environment, cwd=self.home, capture_output=True, text=True, check=True)
                        expected = 'synthetic-selected' if provider == 'codex' else 'synthetic-claude'
                        self.assertIn('password=' + expected + '\n', result.stdout)
                        self.assertEqual(result.stderr, '')
                missing = subprocess.run(['git', 'credential', 'fill'], input='url=' + urls[0] + '\n\n',
                                         env=self.env, cwd=self.home, capture_output=True, text=True)
                self.assertNotEqual(missing.returncode, 0)
                foreign = subprocess.run(['git', 'credential', 'fill'], input='url=https://elsewhere.invalid/repo\n\n',
                                         env=bound, cwd=self.home, capture_output=True, text=True)
                self.assertNotEqual(foreign.returncode, 0)
                self.assertNotIn('synthetic', foreign.stdout + foreign.stderr)


if __name__ == '__main__':
    unittest.main()
