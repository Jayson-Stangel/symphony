"""Replay real events and execute the actual consumer/allocation/receipt guards."""
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_ci import ROOT, classifier


def workflow(name):
    return (ROOT / '.github/workflows' / (name + '.yml')).read_text()


def job(text, name):
    return re.split(r'\n  [a-z][a-z0-9-]*:', text.split('  ' + name + ':\n', 1)[1])[0]


def scripts(block):
    results = []
    for body in block.split('        run: |\n')[1:]:
        lines = []
        for line in body.splitlines():
            if line.strip() and not line.startswith('          '):
                break
            lines.append(line[10:])
        results.append('\n'.join(lines))
    return results


def expression(text, values):
    text = text.removeprefix('${{').removesuffix('}}').strip()
    text = text.replace('always()', 'True').replace('!cancelled()', 'True')
    text = re.sub(r'needs\.([a-z-]+)\.(result|outputs\.[a-z_]+)',
                  lambda m: repr(values[m[0]]), text)
    return eval(text.replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}})


def env_for(flags):
    portable, macos, linux, fixtures = flags
    native = macos == 'true' or linux == 'true' or fixtures == 'true'
    platform = 'all'
    if fixtures == 'false' and macos != linux:
        platform = 'macos' if macos == 'true' else 'linux_x86_64'
    return dict(RESULT='success', CODE='true', PORTABLE_REQUIRED=portable,
                MACOS=macos, LINUX=linux, FIXTURES=fixtures, NATIVE_REQUIRED=str(native).lower(),
                PLATFORM=platform, PORTABLE='success', PACKAGE='success' if native else 'skipped',
                GITHUB_SHA='a'*40, GITHUB_STEP_SUMMARY=os.devnull)


class Consumers(unittest.TestCase):
    def test_dependency_table(self):
        cases = {
            'docs/development.md': set(),
            'elixir/lib/symphony_elixir/orchestrator.ex': {'application'},
            'elixir/mix.lock': {'application'},
            'runner/browser/package-lock.json': {'portable'},
            'scripts/package/linux/Package.resolved': {'portable', 'package_linux'},
            'scripts/package/host.swift': {'portable', 'package_macos', 'package_linux'},
            'scripts/symphony-package-update.sh': {'portable', 'package_macos', 'package_linux', 'application', 'fixtures'},
            '.codex/skills/land/SKILL.md': {'portable', 'package_macos', 'package_linux', 'application'},
            'elixir/WORKFLOW.md': {'portable', 'package_macos', 'package_linux', 'application'},
            'runner/claude/runtime.mjs': {'application'},
            'runner/claude/qualification/Dockerfile': {'portable'},
            'scripts/release/tests/test_artifacts.py': {'portable'},
            'scripts/release/engine-smoke.py': {'portable', 'fixtures'},
            '.github/workflows/package-checks.yml': {'portable', 'package_macos', 'package_linux', 'fixtures'},
            '.github/actions/build-runner/action.yml': {'portable'},
            '.github/workflows/pr-description-lint.yml': set(),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual({k for k, v in classifier.decisions([path]).items()
                                  if k in classifier.COMPONENTS and v}, expected)
        for path in ('unknown.py', '.github/actions/new/action.yml', '.github/workflows/new.yml', '../README.md'):
            self.assertTrue(all(classifier.decisions([path])[key] for key in classifier.COMPONENTS), path)
        self.assertTrue(classifier.decisions([])['native'])

    def test_real_event_component_ranges(self):
        recipe = 'FROM example/base AS browser-tools\nCOPY runner/browser/ /browser/\n'
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            def commit(path, content='fixture'):
                target = Path(folder) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            base = commit('runner/Dockerfile', recipe)
            app = commit('elixir/lib/app.ex')
            linux = commit('scripts/package/linux/Package.resolved')
            docs = commit('docs/guide.md')
            original = os.getcwd(); os.chdir(folder)
            try:
                def route(event, name):
                    return classifier.decisions(classifier.classify(event, name)[2])
                self.assertFalse(route(dict(before=base, after=app), 'push')['native'])
                self.assertEqual(route(dict(before=app, after=linux), 'push')['native_platform'], 'linux_x86_64')
                self.assertFalse(route(dict(before=linux, after=docs), 'push')['native'])
                for target in ('dev', 'main'):
                    event = dict(pull_request=dict(base=dict(sha=base, ref=target), head=dict(sha=docs)))
                    result = route(event, 'pull_request')
                    self.assertTrue(result['application'] and result['package_linux'])
                    self.assertFalse(result['package_macos'])
                git('checkout', '-qb', 'stale', base)
                stale = commit('docs/base.md')
                event = dict(pull_request=dict(base=dict(sha=stale), head=dict(sha=docs)))
                self.assertTrue(route(event, 'pull_request')['package_linux'])
                git('checkout', '-q', '--detach', docs)
                git('mv', 'scripts/package/linux/Package.resolved', 'docs/moved.md'); git('commit', '-qm', 'rename')
                renamed = git('rev-parse', 'HEAD')
                self.assertEqual(route(dict(before=docs, after=renamed), 'push')['native_platform'], 'linux_x86_64')
                git('rm', 'elixir/lib/app.ex'); git('commit', '-qm', 'delete')
                deleted = git('rev-parse', 'HEAD')
                self.assertTrue(route(dict(before=renamed, after=deleted), 'push')['application'])
                for before in ('0'*40, 'f'*40, 'invalid', deleted, None):
                    self.assertTrue(all(route(dict(before=before, after=deleted), 'push')[key] for key in classifier.COMPONENTS))
                for name in ('workflow_dispatch', 'workflow_call'):
                    self.assertTrue(all(route({}, name)[key] for key in classifier.COMPONENTS))
            finally:
                os.chdir(original)

    def test_all_exports_and_cheap_exceptions(self):
        text = workflow('change-classification')
        for key in (*classifier.COMPONENTS, 'native', 'native_platform'):
            self.assertIn('value: ${{ jobs.classify.outputs.' + key + ' }}', text)
            self.assertIn(key + ': ${{ steps.changes.outputs.' + key + ' }}', text)
        text = workflow('make-all')
        self.assertIn('Workflow and metadata regressions\n        run:', text)
        self.assertIn('"$RUNNER_TEMP/actionlint"', text)
        # Setup, cache and suite remain conditional; cheap checkout/policy/lint are explicit exceptions.
        for step in text.split('      - name: Set up mise tools', 1)[1].split('      - ')[:-1]:
            self.assertIn("if: needs.changes.outputs.application != 'false'", step)

    def test_native_allocations_follow_transitive_inputs(self):
        text = workflow('package-checks')
        condition = re.search(r'^    if: (.+)$', job(text, 'native-host-package'), re.M)[1]
        for path in ('docs/a.md', 'elixir/lib/app.ex', 'runner/browser/smoke.cjs',
                     'scripts/package/linux/Package.resolved', 'scripts/package/host.swift',
                     'scripts/release/engine-smoke.py', 'scripts/ci/classify_changes.py'):
            route = classifier.decisions([path])
            values = {'needs.changes.outputs.native': str(route['native']).lower(),
                      'needs.changes.result': 'success', 'needs.qualification.result': 'success',
                      'needs.host-policy.result': 'success'}
            self.assertEqual(expression(condition, values), route['native'], path)
            for key in ('needs.changes.result', 'needs.qualification.result', 'needs.host-policy.result'):
                for status in ('failure', 'cancelled', 'skipped', ''):
                    self.assertFalse(expression(condition, dict(values, **{key: status})), (path, key, status))
            if path == 'scripts/package/linux/Package.resolved':
                self.assertEqual(route['native_platform'], 'linux_x86_64')
        self.assertIn('platform: ${{ needs.changes.outputs.native_platform }}', text)
        self.assertNotIn('  native:\n', text)
        self.assertIn("if: needs.changes.outputs.fixtures == 'true'", text)
        self.assertIn("if: matrix.platform == 'macos' && needs.changes.outputs.package_macos == 'true'", text)

    def test_actual_native_and_classification_guards(self):
        guards = scripts(job(workflow('package-checks'), 'macos-package'))
        self.assertEqual(len(guards), 2)
        statuses = ('success', 'failure', 'cancelled', 'skipped', '')
        command = '\n'.join(guards)
        for flags in itertools.product(('true', 'false'), repeat=4):
            env = env_for(flags)
            for classification, portable, native in itertools.product(statuses, repeat=3):
                values = dict(os.environ, **dict(env, RESULT=classification, PORTABLE=portable, PACKAGE=native))
                actual = subprocess.run(['bash', '-e', '-c', command], env=values, capture_output=True)
                expected = classification == portable == 'success' and native == env['PACKAGE']
                self.assertEqual(actual.returncode == 0, expected, (flags, classification, portable, native))
        for key in ('CODE', 'PORTABLE_REQUIRED', 'MACOS', 'LINUX', 'FIXTURES', 'NATIVE_REQUIRED', 'PLATFORM'):
            for bad in ('', 'garbage', 'TRUE'):
                env = dict(env_for(('true',)*4), **{key: bad})
                self.assertNotEqual(subprocess.run(['bash', '-e', '-c', command], env=dict(os.environ, **env), capture_output=True).returncode, 0)
        # Valid booleans with inconsistent derivations must fail as well.
        for overrides in (dict(NATIVE_REQUIRED='false'), dict(PLATFORM='macos'), dict(CODE='false')):
            env = dict(env_for(('true',)*4), **overrides)
            self.assertNotEqual(subprocess.run(['bash', '-e', '-c', command], env=dict(os.environ, **env), capture_output=True).returncode, 0)

    def test_actual_preflight_omission_and_exact_source_guard(self):
        guard = scripts(job(workflow('package-checks'), 'qualification'))[0]
        for result, code, local, caller in itertools.product(('success', 'failure', 'cancelled', 'skipped', ''),
                ('true', 'false', '', 'invalid'), ('success', 'failure', 'cancelled', 'skipped', ''), ('', 'a'*40, 'b'*40)):
            env = dict(os.environ, CLASSIFICATION=result, CODE=code, LOCAL_RESULT=local, LOCAL_REVISION='a'*40,
                       CALLER_REVISION=caller, GITHUB_SHA='a'*40, GITHUB_STEP_SUMMARY=os.devnull)
            actual = subprocess.run(['bash', '-e', '-c', guard], env=env, capture_output=True)
            expected = result == 'success' and ((code == 'false' and local == 'skipped' and not caller) or
                        (code == 'true' and ((caller == 'a'*40 and local == 'skipped') or (not caller and local == 'success'))))
            self.assertEqual(actual.returncode == 0, expected, (result, code, local, caller))

    def test_manual_and_release_requests_remain_complete(self):
        self.assertNotIn('outputs.application', workflow('runner-release'))
        self.assertNotIn('outputs.native', workflow('burrito-release'))
        self.assertIn('environment: production-release', workflow('burrito-release'))
        self.assertIn('SYMPHONY_PRODUCTION_RELEASES', workflow('runner-release'))
        self.assertIn('cancel-in-progress: false', workflow('runner-integration'))
        self.assertIn('${{ github.run_id }}', workflow('runner-integration'))


if __name__ == '__main__':
    unittest.main()
