"""Real doctor/store exclusion with synthetic engine and export boundaries only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from codex_config_fixture import EMPTY_SNAPSHOT

ROOT = Path(__file__).resolve().parents[3]


class CodexDoctorLock(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='symphony-doctor-lock-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'remote', 'add', 'origin',
                        'https://github.com/example/' + self.root.name + '.git'], check=True)
        (self.repo / 'WORKFLOW.md').write_text('synthetic workflow')
        self.canary = self.root / 'unrelated-canary'
        self.canary.write_bytes(b'preserve unrelated project data')
        self.marker, self.release = self.root / 'writer-stage', self.root / 'writer-release'
        self.hook = self.root / 'store-hook.py'
        self.hook.write_text('''import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location('actual_store', sys.argv[1])
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
original = store.write_file
def write_file(path, data):
    original(path, data)
    if path.name == 'config.toml' and os.environ.get('FIXTURE_PAUSE_STAGE') == '1':
        pathlib.Path(os.environ['FIXTURE_STAGE_MARKER']).write_text(str(path.parent))
        deadline = time.monotonic() + 20
        while not pathlib.Path(os.environ['FIXTURE_STAGE_RELEASE']).exists():
            if time.monotonic() > deadline:
                raise RuntimeError('fixture release timed out')
            time.sleep(.01)
store.write_file = write_file
try:
    store.run(sys.argv[2:])
except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError):
    print('symphony: unsafe Codex config snapshot store', file=sys.stderr)
    sys.exit(1)
''')
        self.env = dict(os.environ, SYMPHONY_HOME=str(ROOT), SYMPHONY_CONFIG_HOME=str(self.root / 'config'),
                        SYMPHONY_RUNNER_HOME=str(self.root / 'runners'),
                        SYMPHONY_RUNNER_NAMESPACE='doctor-' + hashlib.sha256(str(self.root).encode()).hexdigest()[:12],
                        FIXTURE_PROJECT=str(self.repo),
                        FIXTURE_PYTHON=sys.executable, FIXTURE_STORE_HOOK=str(self.hook),
                        FIXTURE_EMPTY_SNAPSHOT=EMPTY_SNAPSHOT, FIXTURE_STAGE_MARKER=str(self.marker),
                        FIXTURE_STAGE_RELEASE=str(self.release))
        self.definitions = r'''
source "$SYMPHONY_HOME/scripts/symphony" --help >/dev/null
container_runtime() { printf fixture_runtime; }
fixture_runtime() {
  case "$1" in info|image) return 0 ;; volume) return 1 ;; run) id -u ;; *) return 90 ;; esac
}
runner_codex_run() { printf '%s' "$FIXTURE_EMPTY_SNAPSHOT"; }
codex_config_store() { "$FIXTURE_PYTHON" -I "$FIXTURE_STORE_HOOK" "$SYMPHONY_HOME/scripts/codex-config-store.py" "$@"; }
runner_codex_sandbox_ready() { codex_config_require "$1" "$3"; printf 'PROTECTED_CHECKED\n'; }
auth_codex_check() { printf 'UNEXPECTED_AUTH\n' >&2; return 90; }
'''

    def command(self, body):
        return ['bash', '-euc', self.definitions + body]

    def run_shell(self, body, **env):
        return subprocess.run(self.command(body), env=self.env | env, capture_output=True, text=True, timeout=10)

    def test_independent_doctors_cannot_collect_an_active_snapshot_stage(self):
        self.doctor_race()

    def test_cached_doctor_cannot_inventory_another_active_snapshot(self):
        self.doctor_race(cached=True)

    def test_configuration_overrides_cannot_bypass_same_project_resource_lock(self):
        self.doctor_race(alternate_config=True)

    def doctor_race(self, cached=False, alternate_config=False):
        export = EMPTY_SNAPSHOT
        sealed = set()
        cached_state = ''
        if cached:
            selected = self.run_shell('codex_config_store_root "$FIXTURE_PROJECT"')
            self.assertEqual(selected.returncode, 0, selected.stderr)
            store = Path(selected.stdout.strip())
            seed = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/codex-config-store.py'), 'write', str(store)],
                                  input=EMPTY_SNAPSHOT, capture_output=True, text=True, check=True)
            pinned = seed.stdout.strip()
            sealed.add(pinned)
            data = b'[projects."/workspaces/task"]\ntrust_level = "trusted"\n'
            value = json.loads(EMPTY_SNAPSHOT)
            value.update(config_base64=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest(),
                         source=dict(present=True, uid=os.getuid(), gid=os.getgid(), mode=0o600,
                                     device=1, inode=1, size=len(data), mtime_ns=1, ctime_ns=1))
            export = json.dumps(value)
            cached_state = 'codex_config_sha=' + pinned + '; codex_config_owner=$(id -u); ' + \
                'codex_config_file="' + str(store / pinned / 'config.toml') + '"; codex_config_image=$(runner_image codex); '
        second_env = {}
        if alternate_config:
            second_env = {'SYMPHONY_CONFIG_HOME': str(self.root / 'other-config'),
                          'SYMPHONY_RUNNER_HOME': str(self.root / 'other-runners')}
        first = subprocess.Popen(self.command('runner_doctor "$FIXTURE_PROJECT" codex true'),
                                 env=self.env | {'FIXTURE_PAUSE_STAGE': '1', 'FIXTURE_EMPTY_SNAPSHOT': export},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not self.marker.exists():
                self.assertIsNone(first.poll(), 'writer exited before actual config.toml stage write')
                self.assertLess(time.monotonic(), deadline, 'writer did not reach actual store write')
                time.sleep(.01)
            stage = Path(self.marker.read_text())
            before = (stage.stat().st_dev, stage.stat().st_ino)
            self.assertEqual({p.name for p in stage.iterdir()}, {'config.toml'})
            second = self.run_shell(cached_state + 'if runner_doctor "$FIXTURE_PROJECT" codex true; then echo FALSE_READY; exit 99; else exit 43; fi', **second_env)
            self.assertIsNone(first.poll(), 'writer must remain paused throughout contender')
            self.assertTrue(stage.exists(), 'contender deleted active owned stage: ' + repr((second.returncode, second.stdout, second.stderr)))
            self.assertEqual((stage.stat().st_dev, stage.stat().st_ino), before)
            self.assertEqual({p.name for p in stage.iterdir()}, {'config.toml'})
            self.assertEqual(second.returncode, 43, second.stdout + second.stderr)
            self.assertIn('another updater is active', second.stderr)
            self.assertNotIn('PROTECTED_CHECKED', second.stdout)
            self.assertNotIn('FALSE_READY', second.stdout)
        finally:
            self.release.touch()
            try:
                output, error = first.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                first.kill(); first.communicate(); raise
        self.assertEqual(first.returncode, 0, output + error)
        self.assertIn('PROTECTED_CHECKED', output)
        self.assertNotIn('UNEXPECTED_AUTH', error)
        digest = json.loads(export)['sha256']
        sealed.add(digest)
        self.assertEqual({p.name for p in stage.parent.iterdir()}, sealed)
        artifact = stage.parent / digest
        self.assertEqual({p.name for p in artifact.iterdir()}, {'config.toml', 'receipt.json'})
        self.assertEqual((artifact / 'config.toml').read_bytes(), base64.b64decode(json.loads(export)['config_base64']))
        self.assertEqual(json.loads((artifact / 'receipt.json').read_text())['sha256'], digest)
        self.assertEqual(self.canary.read_bytes(), b'preserve unrelated project data')
        retry = self.run_shell('runner_doctor "$FIXTURE_PROJECT" codex true')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual({p.name for p in stage.parent.iterdir()}, sealed)
        self.assertEqual(self.canary.read_bytes(), b'preserve unrelated project data')
        if cached:
            self.assertEqual((store / pinned / 'config.toml').read_bytes(), b'')
        if alternate_config:
            self.assertFalse((self.root / 'other-runners').exists())

    def test_doctor_reuses_the_existing_project_lock_without_authentication(self):
        result = self.run_shell(r'''
runner_project_lock "$FIXTURE_PROJECT"
held_path=$runner_resource_lock_path
runner_doctor "$FIXTURE_PROJECT" codex true
[ "$runner_resource_lock_path" = "$held_path" ]
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PROTECTED_CHECKED', result.stdout)
        self.assertNotIn('UNEXPECTED_AUTH', result.stderr)

    def test_real_compatibility_contention_keeps_markers_unset_until_retry(self):
        holder_ready, holder_release = self.root / 'compat-held', self.root / 'compat-release'
        failed, retry = self.root / 'doctor-refused', self.root / 'doctor-retry'
        env = self.env | {'FIXTURE_COMPAT_HELD': str(holder_ready), 'FIXTURE_COMPAT_RELEASE': str(holder_release),
                          'FIXTURE_DOCTOR_REFUSED': str(failed), 'FIXTURE_DOCTOR_RETRY': str(retry)}
        holder = subprocess.Popen(self.command(r'''
mkdir -p "$runner_home"
runner_transition_lock "$runner_home/$(project_id "$FIXTURE_PROJECT").transition.lock"
touch "$FIXTURE_COMPAT_HELD"
for attempt in $(seq 1 1000); do [ ! -e "$FIXTURE_COMPAT_RELEASE" ] || exit 0; sleep .01; done
exit 92
'''), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        doctor = None
        try:
            self.wait_for(holder_ready, holder)
            doctor = subprocess.Popen(self.command(r'''
compat="$runner_home/$(project_id "$FIXTURE_PROJECT").transition.lock"
if runner_transition_lock "$compat"; then echo FALSE_DIRECT_LOCK; exit 99; fi
[ -z "${runner_lock_path:-}" ]
if runner_doctor "$FIXTURE_PROJECT" codex true; then echo FALSE_READY; exit 99; fi
[ -z "${runner_resource_lock_path:-}" ] && [ -z "${runner_lock_path:-}" ]
[ ! -e "$FIXTURE_STAGE_MARKER" ]
touch "$FIXTURE_DOCTOR_REFUSED"
for attempt in $(seq 1 1000); do [ ! -e "$FIXTURE_DOCTOR_RETRY" ] || break; sleep .01; done
[ -e "$FIXTURE_DOCTOR_RETRY" ] || exit 92
runner_doctor "$FIXTURE_PROJECT" codex true
[ -n "$runner_resource_lock_path" ] && [ -n "$runner_lock_path" ]
'''), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.wait_for(failed, doctor)
            self.assertEqual(list((self.root / 'runners').glob('*-codex-config')), [])
            holder_release.touch()
            output, error = holder.communicate(timeout=10)
            self.assertEqual(holder.returncode, 0, output + error)
            retry.touch()
            output, error = doctor.communicate(timeout=10)
            self.assertEqual(doctor.returncode, 0, output + error)
            self.assertEqual(error.count('another updater is active'), 2)
            self.assertEqual(output.count('PROTECTED_CHECKED'), 1)
            self.assertNotIn('FALSE_', output)
            self.assertEqual(self.canary.read_bytes(), b'preserve unrelated project data')
        finally:
            holder_release.touch(); retry.touch()
            for process in (holder, doctor):
                if process is not None and process.poll() is None:
                    process.kill(); process.communicate()

    def wait_for(self, path, process):
        deadline = time.monotonic() + 10
        while not path.exists():
            self.assertIsNone(process.poll(), 'fixture exited before ' + path.name)
            self.assertLess(time.monotonic(), deadline, 'fixture did not reach ' + path.name)
            time.sleep(.01)

    def test_failed_compatibility_lock_cannot_publish_ownership_or_allow_false_reentry(self):
        result = self.run_shell(r'''
eval "$(declare -f updater_file_lock | sed '1s/updater_file_lock/fixture_real_file_lock/')"
updater_file_lock() {
  if [ "$2" = 9 ] && [ ! -f "$FIXTURE_STAGE_MARKER" ]; then
    touch "$FIXTURE_STAGE_MARKER"
    return 37
  fi
  fixture_real_file_lock "$@"
}
runner_codex_sandbox_ready() {
  codex_config_require "$1" "$3"
  printf 'PROTECTED_CHECKED\n' >> "$FIXTURE_STAGE_RELEASE"
}
if runner_doctor "$FIXTURE_PROJECT" codex true; then echo FALSE_READY; exit 99; else [ "$?" = 37 ]; fi
[ -z "${runner_resource_lock_path:-}" ] && [ -z "${runner_lock_path:-}" ]
[ ! -e "$FIXTURE_STAGE_RELEASE" ]
runner_doctor "$FIXTURE_PROJECT" codex true
[ -n "$runner_resource_lock_path" ] && [ -n "$runner_lock_path" ]
[ "$(wc -l < "$FIXTURE_STAGE_RELEASE" | tr -d ' ')" = 1 ]
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('FALSE_READY', result.stdout)
        self.assertNotIn('UNEXPECTED_AUTH', result.stderr)

    def test_provider_and_engine_refusals_precede_project_lock(self):
        for body, expected in [
            ('runner_doctor /unused claude true', '--sandbox-only requires --agent codex'),
            ('fixture_runtime() { return 29; }; runner_doctor /unused codex', 'container engine unavailable'),
        ]:
            with self.subTest(expected=expected):
                result = self.run_shell('project_id() { echo UNEXPECTED_IDENTITY >&2; return 90; }; ' + body)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn('UNEXPECTED_IDENTITY', result.stderr)
                self.assertNotIn('UNEXPECTED_AUTH', result.stderr)


if __name__ == '__main__':
    unittest.main()
