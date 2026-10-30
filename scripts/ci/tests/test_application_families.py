"""Exercise family histories, actual shell joins and cross-language consumers."""
import itertools
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from test_ci import ROOT, classifier
from test_components import job, scripts, workflow
from test_pr_policy import History


class ApplicationFamilies(unittest.TestCase):
    def test_node_only_job_uses_the_changed_canonical_pin_and_refuses_mutable_versions(self):
        block = job(workflow('make-all'), 'claude-checks')
        command = scripts(block)[0]
        self.assertIn('tool_versions: node ${{ steps.node-toolchain.outputs.version }}', block)
        self.assertNotIn('working_directory: elixir', block)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'elixir').mkdir()
            (root / 'bin').mkdir()
            (root / 'bin/python3').symlink_to(sys.executable)
            for version, valid in (('24.19.0', True), ('25.0.1', True), ('24', False), ('latest', False)):
                (root / 'elixir/mise.toml').write_text('[tools]\nnode = "' + version + '"\n')
                output = root / 'outputs'; output.write_text('')
                result = subprocess.run(['bash', '-e', '-c', command], cwd=root,
                                        env=dict(os.environ, GITHUB_OUTPUT=str(output),
                                                 PATH=str(root / 'bin') + os.pathsep + os.environ['PATH']), capture_output=True)
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                self.assertEqual(output.read_text(), 'version=' + version + '\n' if valid else '')

    def test_full_event_ranges_select_only_independent_families(self):
        with tempfile.TemporaryDirectory() as folder:
            history = History(folder)
            history.put('README.md', 'baseline')
            base = history.commit()
            history.put('runner/claude/test/runtime.test.mjs', 'Node-only test')
            node = history.commit()
            history.put('elixir/lib/symphony_elixir/orchestrator.ex', 'Elixir source')
            elixir = history.commit()
            history.put('runner/claude/protocol.mjs', 'shared protocol')
            protocol = history.commit()
            original = os.getcwd(); os.chdir(folder)
            try:
                for before, after, selected in ((base, node, {'claude'}),
                        (node, elixir, {'application'}), (elixir, protocol, {'application', 'claude'}),
                        (base, elixir, {'application', 'claude'})):
                    for name, event in (('push', dict(before=before, after=after)),
                            ('pull_request', dict(pull_request=dict(base=dict(sha=before), head=dict(sha=after))))):
                        flags = classifier.decisions(classifier.changed_paths(event, name))
                        self.assertEqual({key for key in classifier.COMPONENTS if flags[key]}, selected)
            finally:
                os.chdir(original)

    def test_transitive_node_modules_consumed_by_full_exunit_keep_both_families(self):
        # ExUnit executes real JS peers/cleanup; follow their relative imports.
        # This prevents a future "Claude-only" shortcut from losing integration.
        sources = '\n'.join(path.read_text() for path in (ROOT / 'elixir/test').rglob('*.exs'))
        pending = [ROOT / path for path in re.findall(r'runner/claude/[A-Za-z0-9_./-]+\.mjs', sources)]
        self.assertTrue(pending)
        visited = set()
        while pending:
            path = pending.pop().resolve()
            if path in visited:
                continue
            visited.add(path)
            self.assertTrue({'application', 'claude'} <= classifier.consumers(str(path.relative_to(ROOT))), str(path))
            for relative in re.findall(r"(?:from\s+|import\s*)['\"](\.[^'\"]+)['\"]", path.read_text()):
                dependency = (path.parent / relative).resolve()
                if dependency.suffix == '.mjs':
                    pending.append(dependency)
        self.assertIn(ROOT / 'runner/claude/helper.mjs', visited)
        self.assertIn(ROOT / 'runner/claude/runtime.mjs', visited)
        for path in ('runner/claude/package.json', 'runner/claude/package-lock.json', 'elixir/Makefile', 'elixir/mise.toml'):
            self.assertTrue({'application', 'claude'} <= classifier.consumers(path), path)

    def test_required_join_rejects_selected_failed_cancelled_missing_or_skipped_family(self):
        command = scripts(job(workflow('make-all'), 'make-all'))[0]
        good = dict(RESULT='success', POLICY='true', READY='true', NPM='false',
                    READINESS='false', CONFIG='false', CONTRACTS='false', PROCESS_RESULT='skipped',
                    MAC_RUNNER_RESULT='skipped', GITHUB_STEP_SUMMARY=os.devnull)
        statuses = ('success', 'failure', 'cancelled', 'skipped', '')
        for elixir, claude, ready in itertools.product(('true', 'false'), repeat=3):
            for er, cr in itertools.product(statuses, repeat=2):
                env = dict(os.environ, **good, CODE=elixir, CLAUDE=claude,
                           ELIXIR_RESULT=er, CLAUDE_RESULT=cr)
                env['READY'] = ready
                env['ELIXIR_MODE'] = 'EXECUTED' if elixir == ready == 'true' else ''
                env['ELIXIR_ORIGIN'] = ''
                expected = er == ('success' if elixir == ready == 'true' else 'skipped')
                expected &= cr == ('success' if claude == ready == 'true' else 'skipped')
                actual = subprocess.run(['bash', '-e', '-c', command], env=env, capture_output=True)
                self.assertEqual(actual.returncode == 0, expected, (elixir, claude, ready, er, cr))

    def test_required_join_distinguishes_verified_reuse_executed_and_omitted(self):
        command = scripts(job(workflow('make-all'), 'make-all'))[0]
        base = dict(os.environ, RESULT='success', POLICY='true', READY='true', CODE='true', CLAUDE='false',
            NPM='false', READINESS='false', CONFIG='false', CONTRACTS='false', PROCESS_RESULT='skipped',
            MAC_RUNNER_RESULT='skipped', ELIXIR_RESULT='success', CLAUDE_RESULT='skipped', GITHUB_STEP_SUMMARY=os.devnull)
        for mode, origin, valid in (('EXECUTED', '', True), ('REUSED',
                'https://github.com/Jayson-Stangel/symphony/actions/runs/101/attempts/2', True),
                ('REUSED', '', False), ('REUSED', 'https://foreign/repo/101', False),
                ('', '', False), ('OMITTED', '', False), ('EXECUTED', 'fabricated-original', False)):
            with self.subTest(mode=mode, origin=origin):
                result = subprocess.run(['bash', '-e', '-c', command],
                    env=dict(base, ELIXIR_MODE=mode, ELIXIR_ORIGIN=origin), capture_output=True)
                self.assertEqual(result.returncode == 0, valid)

    def test_make_all_retains_full_coverage_and_claude_suite(self):
        result = subprocess.run(['make', '-n', '-C', str(ROOT / 'elixir'), 'all'], capture_output=True, text=True, check=True)
        for command in ('mix build', 'mix format --check-formatted', 'mix lint', 'mix test --cover',
                        'mix dialyzer --format short', 'npm --prefix ../runner/claude ci',
                        'npm --prefix ../runner/claude run check', 'npm --prefix ../runner/claude test'):
            self.assertIn(command, result.stdout)
        self.assertEqual(result.stdout.count('npm --prefix ../runner/claude ci'), 1)

    def test_parallel_make_cannot_test_before_dependency_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'npm'
            executable.write_text('#!/bin/sh\nset -eu\n'
                                  'if [ "$3" = ci ]; then sleep 0.1; touch "$INSTALL_RECEIPT"; '
                                  'else test -f "$INSTALL_RECEIPT"; fi\n')
            executable.chmod(0o755)
            result = subprocess.run(['make', '-j8', '-C', str(ROOT / 'elixir'), 'claude-ci'],
                env=dict(os.environ, PATH=folder + os.pathsep + os.environ['PATH'],
                         INSTALL_RECEIPT=str(Path(folder) / 'installed')), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

