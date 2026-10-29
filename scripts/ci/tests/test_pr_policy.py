"""Execute real event comparisons and required shell gates at draft/ready boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_ci import ROOT, classifier
from test_components import env_for, expression, job, scripts, workflow
from test_release_tests import RELEASE
from test_browser_routing import RECIPE


class History:
    def __init__(self, folder):
        self.root = Path(folder)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')


class EventSelection(unittest.TestCase):
    def test_pr134_equivalent_three_file_diff_selects_fast_tests_without_native_or_elixir(self):
        before = '''name: Application checks
on:
  pull_request:
jobs:
  make-all:
    runs-on: ubuntu-latest
    steps:
      - name: Codex protected-command failure regressions
        if: needs.changes.outputs.application != 'false'
        run: |
          python3 runner/test_codex_readiness.py
          python3 scripts/ci/release_tests.py codex-config
          python3 runner/test_npm_cache.py
'''
        after = before + '          python3 runner/test_npm_cache_lifecycle.py\n'
        with tempfile.TemporaryDirectory() as folder:
            history = History(folder)
            history.put('runner/Dockerfile', RECIPE)
            history.put('.github/workflows/make-all.yml', before)
            history.put('runner/test_npm_cache.py', 'original npm test')
            base = history.commit()
            history.put('.github/workflows/make-all.yml', after)
            history.put('runner/test_npm_cache.py', 'repaired npm test')
            history.put('runner/test_npm_cache_lifecycle.py', 'new lifecycle tests')
            head = history.commit()
            event = {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}, 'draft': False}}
            previous = os.getcwd()
            os.chdir(folder)
            try:
                paths = classifier.changed_paths(event, 'pull_request')
                route = classifier.event_routing(paths, event, 'pull_request')[0]
                self.assertEqual({key for key in classifier.COMPONENTS if route[key]}, {'npm', 'readiness', 'codex_config'})
                self.assertFalse(route['native'])
                selection = classifier.browser_selection(paths, event, 'pull_request')
                self.assertFalse(selection[0], selection)
            finally:
                os.chdir(previous)

    def test_current_application_jobs_preserve_fast_command_routing_and_control_boundaries(self):
        path = '.github/workflows/make-all.yml'
        before = (ROOT / path).read_text()
        fast = {'npm', 'readiness', 'codex_config'}
        cases = []
        for command in ('python3 runner/test_codex_readiness.py',
                        'python3 scripts/ci/release_tests.py codex-config',
                        'python3 runner/test_npm_cache.py'):
            line = '        run: ' + command
            self.assertEqual(before.count(line), 1)
            changed = before.replace(line, '        run: |\n          ' + command + '\n          ' + command, 1)
            cases.append((command, changed, fast, False))
        cases.extend([
            ('unknown job', cases[0][1].replace('jobs:\n',
                'jobs:\n  unknown-job:\n    runs-on: ubuntu-latest\n    steps: []\n', 1), classifier.ALL, True),
            ('selector timeout', before.replace('timeout-minutes: 8', 'timeout-minutes: 9', 1),
                classifier.consumers(path), False),
        ])
        with tempfile.TemporaryDirectory() as folder:
            history = History(folder)
            history.put('runner/Dockerfile', RECIPE)
            history.put(path, before)
            base = history.commit()
            for name, changed, expected, browser in cases:
                history.put(path, changed)
                head = history.commit()
                events = (
                    ('pull_request', {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}, 'draft': False}}),
                    ('push', {'before': base, 'after': head}),
                )
                previous = os.getcwd()
                os.chdir(folder)
                try:
                    for event_name, event in events:
                        paths = classifier.changed_paths(event, event_name)
                        route, _, _, _, fallbacks = classifier.event_routing(paths, event, event_name)
                        selected = {key for key in classifier.COMPONENTS if route[key]}
                        with self.subTest(change=name, event=event_name):
                            self.assertEqual(selected, expected)
                            self.assertEqual(classifier.browser_selection(paths, event, event_name)[0], browser)
                            self.assertEqual(bool(fallbacks), name == 'unknown job')
                            comparison = classifier.workflow_comparison(path, base, head)
                            if name == 'selector timeout':
                                self.assertNotIn('fast_workflow_families', comparison)
                finally:
                    os.chdir(previous)

    def test_known_policy_tests_and_actual_consumer_families(self):
        cases = {
            'runner/test_npm_cache.py': {'npm'},
            'runner/test_npm_cache_lifecycle.py': {'npm'},
            'runner/test_codex_readiness.py': {'readiness'},
            'elixir/priv/codex_readiness.py': {'application', 'readiness', 'codex_config'},
            'elixir/priv/codex_config_boundary.py': {'application', 'codex_config'},
            'elixir/priv/codex_config_snapshot.py': {'application', 'codex_config'},
            'elixir/priv/codex_launch.py': {'application', 'codex_config'},
            'runner/npm-cache.npmrc': {'portable', 'npm'},
            'elixir/test/codex_config_snapshot_test.py': {'codex_config'},
            'scripts/release/tests/test_codex_doctor_lock.py': {'contracts'},
            'scripts/release/tests/test_project_credentials.py': {'contracts'},
            'scripts/release/tests/test_readiness_stages.py': {'contracts'},
            'scripts/release/tests/codex_config_fixture.py': {'contracts'},
            'scripts/ci/tests/test_pr_policy.py': set(),
            'scripts/ci/tests/test_validation_inputs.py': set(),
            'scripts/ci/tests/test_validation_evidence.py': set(),
            'scripts/ci/tests/test_elixir_coverage.py': set(),
            'scripts/ci/elixir_coverage.py': classifier.ALL,
            'scripts/ci/elixir_coverage_bootstrap.py': classifier.ALL,
            'scripts/ci/elixir_coverage_profile.py': classifier.ALL,
            'scripts/ci/elixir-coverage-profile.json': classifier.ALL,
            'scripts/ci/validation_inputs.py': classifier.ALL,
            'scripts/ci/validation_evidence.py': classifier.ALL,
            'scripts/release/macos_signing_inputs.py': {'portable'},
            '.github/workflows/macos-signing.yml': {'portable'},
        }
        for path, selected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classifier.consumers(path), selected)
        for path in ('scripts/ci/tests/test_unknown.py', 'scripts/release/tests/test_unknown.py'):
            self.assertEqual(classifier.consumers(path), classifier.ALL)
        mixed = classifier.decisions(['runner/test_npm_cache_lifecycle.py', 'scripts/package/linux/Package.resolved'])
        self.assertTrue(mixed['npm'] and mixed['package_linux'] and mixed['portable'])
        self.assertFalse(mixed['package_macos'] or mixed['application'] or mixed['fixtures'])

    def test_manual_only_integration_changes_do_not_build_browser_but_global_changes_do(self):
        source = (ROOT/'.github/workflows/runner-integration.yml').read_text()
        recipe = (ROOT/'runner/Dockerfile').read_text()
        variants = (
            (source.replace("inputs.baseline_run }}", "inputs.baseline_run || '123' }}"), False),
            (source.replace('timeout-minutes: 30', 'timeout-minutes: 29', 1), True),
            (source.replace('actions: read', 'actions: write'), True),
            (source.replace('  changes:\n', '  unknown-job:\n    runs-on: ubuntu-latest\n    steps: []\n  changes:\n'), True),
            (source + '\npermissions: {}\n', True),
        )
        for changed, expected in variants:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                history = History(folder)
                history.put('runner/Dockerfile', recipe)
                history.put('.github/workflows/runner-integration.yml', source)
                base = history.commit()
                history.put('.github/workflows/runner-integration.yml', changed)
                head = history.commit()
                previous = os.getcwd()
                os.chdir(folder)
                try:
                    event = dict(before=base, after=head)
                    self.assertEqual(classifier.browser_required(classifier.changed_paths(event, 'push'), event, 'push'), expected)
                    route = classifier.event_routing(classifier.changed_paths(event, 'push'), event, 'push')[0]
                    if 'unknown-job' in changed or changed.endswith('permissions: {}\n'):
                        self.assertTrue(all(route[key] for key in classifier.COMPONENTS))
                finally:
                    os.chdir(previous)

    def test_cli_readiness_is_strict_and_does_not_change_full_consumer_fallback(self):
        for name, draft, ready in (('push', None, 'true'), ('workflow_dispatch', None, 'true'),
                                   ('pull_request', True, 'false'), ('pull_request', False, 'true')):
            with self.subTest(name=name, draft=draft), tempfile.TemporaryDirectory() as folder:
                event = {'pull_request': {'draft': draft}} if name == 'pull_request' else {}
                path, output = Path(folder)/'event', Path(folder)/'outputs'
                path.write_text(json.dumps(event))
                result = subprocess.run([sys.executable, str(ROOT/'scripts/ci/classify_changes.py')],
                    env=dict(os.environ, GITHUB_EVENT_NAME=name, GITHUB_EVENT_PATH=str(path), GITHUB_OUTPUT=str(output)), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                flags = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(flags['ready'], ready)
                self.assertTrue(all(flags[key] == 'true' for key in classifier.COMPONENTS))
        for value in ({}, {'draft': 'false'}, {'draft': None}):
            with tempfile.TemporaryDirectory() as folder:
                path, output = Path(folder)/'event', Path(folder)/'outputs'
                path.write_text(json.dumps({'pull_request': value}))
                result = subprocess.run([sys.executable, str(ROOT/'scripts/ci/classify_changes.py')],
                    env=dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(path), GITHUB_OUTPUT=str(output)), capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


class DraftGates(unittest.TestCase):
    def execute(self, script, **values):
        return subprocess.run(['bash', '-e', '-c', script], env=dict(os.environ, GITHUB_STEP_SUMMARY=os.devnull, **values), capture_output=True).returncode

    def test_each_deferred_required_context_remains_failed_at_the_same_head(self):
        text = workflow('make-all').split('      - name: Require ready application qualification\n', 1)[1]
        application = scripts(text)[0]
        for selected in ('true', 'false'):
            for ready in ('true', 'false'):
                self.assertEqual(self.execute(application, APPLICATION=selected, CLAUDE='false', READY=ready) == 0,
                                 selected == 'false' or ready == 'true')
        browser = scripts(job(workflow('runner-integration'), 'chromium'))[0]
        for selected in ('true', 'false'):
            for ready in ('true', 'false', '', 'invalid'):
                self.assertEqual(self.execute(browser, RESULT='success', CODE='true', BROWSER=selected, READY=ready) == 0,
                                 ready in ('true', 'false') and (selected == 'false' or ready == 'true'))
        package = '\n'.join(scripts(job(workflow('package-checks'), 'package-qualification'))[:2])
        for selected in ('true', 'false'):
            values = env_for(('true', selected, selected, 'false'))
            values.pop('GITHUB_STEP_SUMMARY')
            for ready in ('true', 'false', '', 'invalid'):
                self.assertEqual(self.execute(package, **(values | {'READY': ready})) == 0,
                                 ready in ('true', 'false') and (selected == 'false' or ready == 'true'))

    def test_required_application_join_refuses_failed_cancelled_missing_or_wrongly_skipped_fast_work(self):
        guard = scripts(job(workflow('make-all'), 'make-all'))[0]
        good = dict(RESULT='success', CODE='false', POLICY='true', READY='true', NPM='true',
                    READINESS='false', CONFIG='false', CONTRACTS='false', PROCESS_RESULT='success', MAC_RUNNER_RESULT='success',
                    CLAUDE='false', ELIXIR_RESULT='skipped', CLAUDE_RESULT='skipped')
        self.assertEqual(self.execute(guard, **good), 0)
        for field in ('RESULT', 'PROCESS_RESULT', 'MAC_RUNNER_RESULT'):
            for status in ('failure', 'cancelled', 'skipped', ''):
                self.assertNotEqual(self.execute(guard, **(good | {field: status})), 0)
        for field in ('READY', 'NPM', 'READINESS', 'CONFIG', 'CONTRACTS'):
            for value in ('', 'invalid', 'TRUE'):
                self.assertNotEqual(self.execute(guard, **(good | {field: value})), 0)
        self.assertNotEqual(self.execute(guard, **(good | {'POLICY': 'false'})), 0)
        self.assertEqual(self.execute(guard, **(good | {'NPM': 'false', 'PROCESS_RESULT': 'skipped'})), 0)
        self.assertNotEqual(self.execute(guard, **(good | {'NPM': 'false'})), 0)

    def test_cheap_tests_precede_expensive_deferral_and_readiness_events_share_cancellation(self):
        application = workflow('make-all')
        self.assertLess(application.index('Npm configuration semantics'), application.index('Require ready application qualification'))
        self.assertLess(application.index('Require ready application qualification'), application.index('Set up mise tools'))
        for name in ('make-all', 'package-checks', 'runner-integration'):
            text = workflow(name)
            for event in ('opened', 'synchronize', 'reopened', 'ready_for_review', 'converted_to_draft'):
                self.assertIn(event, text.split('concurrency:', 1)[0])
            group = text.split('concurrency:', 1)[1].split('jobs:', 1)[0]
            self.assertIn('github.event.pull_request.number || github.ref', group)
            self.assertNotIn('github.event.action', group)
        self.assertIn("github.event_name != 'workflow_dispatch'", workflow('runner-integration').split('jobs:', 1)[0])

    def test_partial_ordinary_checks_never_emit_full_artifact_qualification(self):
        block = job(workflow('package-checks'), 'package-qualification')
        self.assertIn("github.event_name != 'pull_request'", block)
        guard = scripts(block)[-1]
        values = dict(RESULT='success', PORTABLE_REQUIRED='true', MACOS='true', LINUX='true',
                      FIXTURES='true', GITHUB_SHA='a'*40, QUALIFIED_REVISION='', ORDINARY='true')
        self.assertEqual(self.execute(guard, **values), 0)
        self.assertNotEqual(self.execute(guard, **(values | {'QUALIFIED_REVISION': 'a'*40})), 0)
        self.assertNotEqual(self.execute(guard, **(values | {'ORDINARY': 'false'})), 0)
        self.assertEqual(self.execute(guard, **(values | {'ORDINARY': 'false', 'QUALIFIED_REVISION': 'a'*40})), 0)


class SuiteInventory(unittest.TestCase):
    def test_cross_platform_executor_exists_before_native_inventory_is_partitioned(self):
        matrix = job(workflow('make-all'), 'platform-contracts')
        self.assertIn('os: [ubuntu-24.04, macos-14]', matrix)
        self.assertIn('python3 scripts/ci/release_tests.py contracts', matrix)
        self.assertIn('python3 runner/test_npm_cache_lifecycle.py', matrix)
        self.assertNotIn('build-host-helper', matrix)
        self.assertNotIn('build-linux-host-helper', matrix)
        self.assertIn('python3 scripts/ci/release_tests.py process', workflow('package-checks'))
        self.assertIn('python3 scripts/ci/release_tests.py native', workflow('package-checks'))
        self.assertIn('python3 scripts/ci/release_tests.py portable', workflow('release-preflight'))
        self.assertIn('test_npm_cache.py --protected', (ROOT/'runner/codex-sandbox-smoke.sh').read_text())

    def test_portable_policy_does_not_reexecute_separately_selected_fast_families(self):
        with patch.object(RELEASE.sys, 'argv', ['release_tests.py', 'release-policy']), \
                patch.object(RELEASE, 'codex_config_contracts') as contracts, \
                patch.object(RELEASE.subprocess, 'run') as commands, \
                patch.object(RELEASE, 'run', return_value=True) as suites:
            with self.assertRaises(SystemExit) as result:
                RELEASE.main()
            self.assertEqual(result.exception.code, 0)
            contracts.assert_not_called()
            suites.assert_called_once_with('portable', RELEASE.ROOT/'scripts/release/tests')
            self.assertFalse(any('unittest' in call.args[0] for call in commands.call_args_list))


if __name__ == '__main__':
    unittest.main()
