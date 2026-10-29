"""Replay real events and execute the actual consumer/allocation/receipt guards."""
import ast
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
    return dict(MAC_RUNNER_RESULT='success', RESULT='success', CODE='true', PORTABLE_REQUIRED=portable,
                MACOS=macos, LINUX=linux, FIXTURES=fixtures, NATIVE_REQUIRED=str(native).lower(),
                PLATFORM=platform, PORTABLE='success', PACKAGE='success' if native else 'skipped',
                READY='true', GITHUB_SHA='a'*40, GITHUB_STEP_SUMMARY=os.devnull)


class Consumers(unittest.TestCase):
    def test_existing_release_modules_have_an_explicit_consumer_audit(self):
        # Routing still falls back safely for unknown files. This audit guard
        # asks contributors to classify new shipped modules deliberately, including
        # controller-only recovery and signed-output importer inputs.
        for path in sorted((ROOT / 'scripts/release').glob('*.py')):
            selected, rule = classifier.consumer_selection(str(path.relative_to(ROOT)))
            self.assertNotIn('unknown', rule, str(path))
            self.assertIn('portable', selected, str(path))

    def test_portable_test_imports_retain_their_executor(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('suite_inventory', ROOT / 'scripts/ci/release_tests.py')
        suites = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(suites)
        directory = ROOT / 'scripts/release/tests'
        for filename in suites.PORTABLE:
            self.assertIn('portable', classifier.consumers('scripts/release/tests/' + filename))
            tree = ast.parse((directory / filename).read_text())
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    for parent in (directory, ROOT / 'scripts/release'):
                        dependency = parent / ((name or '').split('.')[0] + '.py')
                        if dependency.is_file():
                            self.assertIn('portable', classifier.consumers(str(dependency.relative_to(ROOT))),
                                          (filename, str(dependency)))

    def test_release_repairs_do_not_allocate_unrelated_consumers(self):
        for path in ('scripts/release/local_distribution.py',
                     'scripts/release/ghcr_transport_recovery.py',
                     'scripts/release/recover_ec2_registry.py',
                     'scripts/release/ec2-registry-recovery-files.json',
                     'scripts/release/tests/test_registry_transport_recovery.py',
                     '.github/workflows/macos-local-signing-import.yml',
                     '.github/workflows/ec2-registry-transport-repair.yml'):
            self.assertEqual(classifier.consumers(path), {'portable'}, path)
        self.assertEqual(classifier.consumers('scripts/ci/release_tests.py'),
                         classifier.PACKAGES | {'fixtures', 'contracts', 'codex_config'})

    def test_dependency_table(self):
        cases = {
            'docs/development.md': set(),
            'elixir/lib/symphony_elixir/orchestrator.ex': {'application'},
            'elixir/mix.lock': {'application'},
            'scripts/release/manager_inputs.py': {'portable', 'package_macos', 'package_linux'},
            'runner/templates/team-codex.Dockerfile': {'portable', 'package_macos', 'package_linux', 'application'},
            'runner/browser/package-lock.json': {'portable'},
            'scripts/package/linux/Package.resolved': {'portable', 'package_linux'},
            'scripts/package/host.swift': {'portable', 'package_macos', 'package_linux'},
            'scripts/symphony-apparmor.sh': {'portable', 'package_macos', 'package_linux', 'application', 'contracts'},
            'scripts/symphony-runner-import.sh': {'portable', 'package_macos', 'package_linux', 'application', 'contracts'},
            'scripts/package/runner_import_test.py': {'portable', 'package_macos', 'package_linux'},
            'scripts/symphony-package-update.sh': {'portable', 'package_macos', 'package_linux', 'application', 'fixtures', 'contracts', 'codex_config'},
            '.codex/skills/land/SKILL.md': {'portable', 'package_macos', 'package_linux', 'application'},
            'elixir/WORKFLOW.md': {'portable', 'package_macos', 'package_linux', 'application'},
            'runner/claude/runtime.mjs': {'application', 'claude'},
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
                # Existing tag moves have valid nonzero before/after revisions.
                # Selected release artifacts still require full qualification.
                tag = dict(ref='refs/tags/v1.2.3', before=linux, after=docs)
                self.assertTrue(all(route(tag, 'push')[key] for key in classifier.COMPONENTS))
                self.assertTrue(classifier.browser_required(classifier.classify(tag, 'push')[2], tag, 'push'))
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
        for key in (*classifier.COMPONENTS, 'native', 'native_platform', 'policy'):
            self.assertIn('value: ${{ jobs.classify.outputs.' + key + ' }}', text)
            self.assertIn(key + ': ${{ steps.changes.outputs.' + key + ' }}', text)
        text = workflow('make-all')
        self.assertIn("Workflow and metadata regressions\n        if: needs.changes.outputs.policy == 'true'\n        run:", text)
        self.assertIn('"$RUNNER_TEMP/actionlint"', text)
        # Application setup follows its component; policy and workflow checks omit pure prose.
        for family, output in (('elixir-checks', 'application'), ('claude-checks', 'claude')):
            block = job(text, family)
            self.assertIn("needs: changes", block)
            self.assertNotIn('platform-contracts', block)
            self.assertIn("if: needs.changes.outputs." + output + " == 'true' && needs.changes.outputs.ready == 'true'", block)
        self.assertIn('run: /usr/bin/python3 -B scripts/ci/elixir_coverage.py execute', job(text, 'elixir-checks'))
        self.assertIn("steps.controller.outputs.trusted != 'true' || steps.evidence.outputs.mode != 'REUSED'", job(text, 'elixir-checks'))
        self.assertNotIn('make all', job(text, 'elixir-checks'))
        self.assertIn('run: make -C elixir claude-ci', job(text, 'claude-checks'))

    def test_native_allocations_follow_transitive_inputs(self):
        text = workflow('package-checks')
        condition = re.search(r'^    if: (.+)$', job(text, 'native-host-package'), re.M)[1]
        for path in ('docs/a.md', 'elixir/lib/app.ex', 'runner/browser/smoke.cjs',
                     'scripts/package/linux/Package.resolved', 'scripts/package/host.swift',
                     'scripts/release/engine-smoke.py', 'scripts/ci/classify_changes.py'):
            route = classifier.decisions([path])
            values = {'needs.changes.outputs.native': str(route['native']).lower(),
                      'needs.changes.result': 'success', 'needs.qualification.result': 'success',
                      'needs.host-policy.result': 'success', 'needs.mac-runner.result': 'success', 'needs.changes.outputs.ready': 'true'}
            self.assertEqual(expression(condition, values), route['native'], path)
            for key in ('needs.changes.result', 'needs.qualification.result', 'needs.host-policy.result', 'needs.mac-runner.result'):
                for status in ('failure', 'cancelled', 'skipped', ''):
                    self.assertFalse(expression(condition, dict(values, **{key: status})), (path, key, status))
            if path == 'scripts/package/linux/Package.resolved':
                self.assertEqual(route['native_platform'], 'linux_x86_64')
        self.assertIn('platform: ${{ needs.changes.outputs.native_platform }}', text)
        self.assertNotIn('  native:\n', text)
        self.assertIn("if: needs.changes.outputs.fixtures == 'true'", text)
        self.assertIn("if: matrix.platform == 'macos' && needs.changes.outputs.package_macos == 'true'", text)

    def test_actual_native_and_classification_guards(self):
        guards = scripts(job(workflow('package-checks'), 'package-qualification'))[:2]
        self.assertEqual(len(guards), 2)
        statuses = ('success', 'failure', 'cancelled', 'skipped', '')
        command = '\n'.join(guards)
        for flags in itertools.product(('true', 'false'), repeat=4):
            env = env_for(flags)
            for classification, portable, native in itertools.product(statuses, repeat=3):
                values = dict(os.environ, **dict(env, RESULT=classification, PORTABLE=portable, PACKAGE=native))
                actual = subprocess.run(['bash', '-e', '-c', command], env=values, capture_output=True)
                expected = (classification == portable == 'success' and native == env['PACKAGE']
                            and (flags[0] == 'true' or env['NATIVE_REQUIRED'] == 'false'))
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
                       CALLER_REVISION=caller, GITHUB_SHA='a'*40, GITHUB_STEP_SUMMARY=os.devnull,
                       MACOS='false', LINUX='false', FIXTURES='false')
            actual = subprocess.run(['bash', '-e', '-c', guard], env=env, capture_output=True)
            expected = result == 'success' and ((code == 'false' and local == 'skipped' and not caller) or
                        (code == 'true' and ((caller == 'a'*40 and local == 'skipped') or (not caller and local == 'success'))))
            self.assertEqual(actual.returncode == 0, expected, (result, code, local, caller))

    def test_package_python_dependencies_select_both_native_consumers(self):
        # Read the actual packer and Python import graph, rather than copying its
        # dependency list into a test. New local imports cannot become portable-only.
        source = (ROOT / 'scripts/release/package.sh').read_text()
        pending = list(re.findall(r'\$root/(scripts/release/[a-z_-]+\.py)', source))
        self.assertIn('scripts/release/manager_inputs.py', pending)
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            self.assertTrue({'portable', 'package_macos', 'package_linux'} <= classifier.consumers(path), path)
            tree = ast.parse((ROOT / path).read_text())
            for node in ast.walk(tree):
                imports = [item.name for item in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) else [])
                for name in imports:
                    dependency = ROOT / 'scripts/release' / ((name or '').split('.')[0] + '.py')
                    if dependency.is_file():
                        pending.append(str(dependency.relative_to(ROOT)))

    def test_signing_payload_dependency_is_native_without_publication_imports(self):
        shared = 'scripts/release/signing_payload.py'
        self.assertEqual(classifier.consumers(shared), {'portable', 'package_macos', 'package_linux'})
        tree = ast.parse((ROOT / shared).read_text())
        names = set()
        for node in ast.walk(tree):
            names.update(item.name for item in node.names) if isinstance(node, ast.Import) else None
            if isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertTrue(names <= {'hashlib', 'pathlib', 're', 'stat', 'tarfile'})
        distribution = ast.parse((ROOT / 'scripts/release/macos_distribution.py').read_text())
        imports = {node.module for node in ast.walk(distribution) if isinstance(node, ast.ImportFrom)}
        self.assertIn('signing_payload', imports)
        self.assertTrue(imports.isdisjoint({'local_distribution', 'production_release'}))
        for filename in ('production_release.py', 'component_publication.py', 'graph_publication.py', 'registry_closure.py'):
            self.assertEqual(classifier.consumers('scripts/release/' + filename), {'portable'})

    def test_native_work_cannot_bypass_portable_preflight(self):
        guard = scripts(job(workflow('package-checks'), 'qualification'))[0]
        for key in ('MACOS', 'LINUX', 'FIXTURES'):
            for bad in ('true', '', 'invalid'):
                env = dict(os.environ, CLASSIFICATION='success', CODE='false', LOCAL_RESULT='skipped',
                           LOCAL_REVISION='', CALLER_REVISION='', MACOS='false', LINUX='false',
                           FIXTURES='false', GITHUB_STEP_SUMMARY=os.devnull)
                env[key] = bad
                self.assertNotEqual(subprocess.run(['bash', '-e', '-c', guard], env=env, capture_output=True).returncode, 0)

    def test_manual_and_release_requests_remain_complete(self):
        self.assertNotIn('outputs.application', workflow('runner-release'))
        self.assertNotIn('outputs.native', workflow('burrito-release'))
        self.assertNotIn('environment: production-release', workflow('burrito-release'))
        self.assertIn('SYMPHONY_PRODUCTION_RELEASES', workflow('production-publish'))
        self.assertIn("cancel-in-progress: ${{ github.event_name != 'workflow_dispatch'", workflow('runner-integration'))
        self.assertIn("github.event_name == 'workflow_dispatch' && github.run_id", workflow('runner-integration'))


if __name__ == '__main__':
    unittest.main()
