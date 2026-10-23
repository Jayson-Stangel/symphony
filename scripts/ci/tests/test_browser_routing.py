"""Browser routing follows actual consumed stages, independent of image provenance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('browser_change_classifier', ROOT / 'scripts/ci/classify_changes.py')
CLASSIFY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CLASSIFY)
RECIPE = ('FROM example/base@sha256:' + 'a' * 64 + ' AS browser-tools\n'
          'COPY runner/browser/package*.json /opt/browser/\n'
          'RUN npm ci\nCOPY runner/browser/smoke.cjs /opt/browser/\n'
          'FROM browser-tools AS codex\nLABEL com.symphony.agent=codex\n')


class BrowserRouting(unittest.TestCase):
    def test_real_full_event_graph_separates_manager_from_consumed_browser_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            def commit(path, content):
                target = Path(folder) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            base = commit('runner/Dockerfile', RECIPE)
            manager = commit('elixir/test/fixture.exs', 'manager test change')
            labels = commit('runner/Dockerfile', RECIPE + 'ARG SYMPHONY_INPUT_IDENTITY=unknown\nLABEL com.symphony.inputs.sha256=$SYMPHONY_INPUT_IDENTITY\n')
            node_control = commit('runner/node-smoke.sh', 'actual Node/npm validation control')
            browser_base = commit('runner/Dockerfile', RECIPE.replace('a' * 64, 'b' * 64))
            browser_npm = commit('runner/browser/package-lock.json', 'actual dependency')
            browser_smoke = commit('runner/browser/smoke.cjs', 'actual test')
            original = os.getcwd(); os.chdir(folder)
            try:
                for before, after, expected in ((base, manager, False), (manager, labels, False),
                                                (labels, node_control, True), (node_control, browser_base, True), (browser_base, browser_npm, True),
                                                (browser_npm, browser_smoke, True)):
                    event = dict(before=before, after=after)
                    paths = CLASSIFY.changed_paths(event, 'push')
                    self.assertEqual(CLASSIFY.browser_required(paths, event, 'push'), expected)
                # A full PR includes its old actual browser change even when the newest commit is a manager test.
                current = commit('elixir/test/fixture.exs', 'another manager test')
                event = dict(pull_request=dict(base=dict(sha=base), head=dict(sha=current)))
                self.assertTrue(CLASSIFY.browser_required(CLASSIFY.changed_paths(event, 'pull_request'), event, 'pull_request'))
                # Deletion and rename both sides are visible; source absence never grants an omission.
                (Path(folder) / 'docs').mkdir()
                git('mv', 'runner/browser/smoke.cjs', 'docs/moved.md'); git('commit', '-qm', 'rename')
                event = dict(before=current, after=git('rev-parse', 'HEAD'))
                self.assertTrue(CLASSIFY.browser_required(CLASSIFY.changed_paths(event, 'push'), event, 'push'))
                unknown = commit('runner/Dockerfile', RECIPE.replace('RUN npm ci', 'RUN --mount=type=cache npm ci'))
                event = dict(before=base, after=unknown)
                self.assertTrue(CLASSIFY.browser_required(CLASSIFY.changed_paths(event, 'push'), event, 'push'))
                # COPY of the context root makes even ordinary prose a browser input.
                root_copy = commit('runner/Dockerfile', RECIPE.replace('COPY runner/browser/package*.json', 'COPY .'))
                prose = commit('README.md', 'consumed browser prose')
                event = dict(before=root_copy, after=prose)
                paths = CLASSIFY.changed_paths(event, 'push')
                self.assertTrue(CLASSIFY.browser_required(paths, event, 'push'))
                event_path = Path(folder) / 'event.json'; event_path.write_text(json.dumps(event))
                output = Path(folder) / 'outputs'
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/classify_changes.py')],
                                        env=dict(os.environ, GITHUB_EVENT_NAME='push', GITHUB_EVENT_PATH=str(event_path),
                                                 GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(Path(folder) / 'summary')),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual((outputs['code'], outputs['browser']), ('true', 'true'))
            finally:
                os.chdir(original)

    def test_dot_relative_copy_and_unsafe_sources_cannot_hide_consumed_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            def commit(path, content):
                target = Path(folder) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            original = os.getcwd(); os.chdir(folder)
            try:
                for index, source in enumerate(('./runner/browser/smoke.cjs', '././runner/browser/smoke.cjs',
                                                '../runner/browser/smoke.cjs', '/runner/browser/smoke.cjs',
                                                '$BROWSER_SOURCE', 'runner/browser/[s]moke.cjs', '.')):
                    recipe = ('FROM example/base@sha256:' + 'a' * 64 + ' AS browser-tools\nCOPY ' + source + ' /smoke.cjs\n')
                    before = commit('runner/Dockerfile', recipe)
                    after = commit('runner/browser/smoke.cjs', 'browser change ' + str(index))
                    event = dict(before=before, after=after)
                    paths = CLASSIFY.changed_paths(event, 'push')
                    self.assertTrue(CLASSIFY.browser_required(paths, event, 'push'), source)
            finally:
                os.chdir(original)

    def test_nonconsuming_controls_do_not_build_browsers(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            def commit(path, content):
                target = Path(folder) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            previous = commit('runner/Dockerfile', RECIPE)
            original = os.getcwd(); os.chdir(folder)
            try:
                for path, expected in (
                    ('.github/actions/configure-runner-cache/action.yml', False),
                    ('.github/actions/runner-cache-runtime/runtime.js', False),
                    ('scripts/release/runner-cache-probe.py', False),
                    ('scripts/release/build-runner-cached.sh', False),
                    ('scripts/ci/check_pr_body.py', False),
                    ('scripts/release/runner-cache-policy.sh', True),
                    ('.github/actions/build-runner/action.yml', True),
                    ('.github/actions/unknown/action.yml', True),
                ):
                    head = commit(path, 'changed control')
                    event = dict(before=previous, after=head)
                    paths = CLASSIFY.changed_paths(event, 'push')
                    self.assertEqual(CLASSIFY.browser_required(paths, event, 'push'), expected, path)
                    previous = head
            finally:
                os.chdir(original)

    def test_missing_history_unknown_metadata_and_build_controls_require_qualification(self):
        for paths, event, name in (([], {}, 'push'), (['elixir/test/fixture.exs'], {}, 'push'),
                                    (['../runner/browser/package.json'], {}, 'push'),
                                    (['.dockerignore'], {}, 'push'), (['.github/actions/build-runner/action.yml'], {}, 'push'),
                                    (['scripts/ci/classify_changes.py'], {}, 'push'),
                                    (['runner/node-smoke.sh'], {}, 'push')):
            self.assertTrue(CLASSIFY.browser_required(paths, event, name))

    def test_guard_refuses_missing_or_failed_decisions_and_retains_both_users(self):
        workflow = (ROOT / '.github/workflows/browser-smoke.yml').read_text()
        self.assertNotIn('component-plan', workflow)
        self.assertNotIn('needs.plan', workflow)
        block = workflow.split('        run: |\n', 1)[1].split('      - name:', 1)[0]
        guard = textwrap.dedent(block)
        for code, browser, expected in (('false', 'false', True), ('false', 'true', False), ('true', 'false', True),
                                        ('true', 'true', True), ('true', '', False), ('true', 'reuse', False), ('', 'false', False)):
            for result in ('success', 'failure', 'cancelled', 'skipped', ''):
                actual = subprocess.run(['bash', '-e', '-c', guard], env=dict(os.environ, RESULT=result, CODE=code, BROWSER=browser), capture_output=True)
                self.assertEqual(actual.returncode == 0, expected and result == 'success')
        for command in ('--entrypoint playwright symphony-browser-test --version',
                        '--entrypoint node symphony-browser-test /opt/symphony/browser/smoke.cjs',
                        '--user 11001:11001 --entrypoint node', '--user 11001:11001 --workdir /tmp --entrypoint playwright symphony-browser-test screenshot'):
            self.assertIn(command, workflow)
