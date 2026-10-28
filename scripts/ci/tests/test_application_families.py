"""Exercise family histories, actual shell joins and cross-language consumers."""
import itertools
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_ci import ROOT, classifier
from test_components import job, scripts, workflow
from test_pr_policy import History


class ApplicationFamilies(unittest.TestCase):
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
                    MAC_RUNNER_RESULT='success', GITHUB_STEP_SUMMARY=os.devnull)
        statuses = ('success', 'failure', 'cancelled', 'skipped', '')
        for elixir, claude, ready in itertools.product(('true', 'false'), repeat=3):
            for er, cr in itertools.product(statuses, repeat=2):
                env = dict(os.environ, **good, CODE=elixir, CLAUDE=claude,
                           ELIXIR_RESULT=er, CLAUDE_RESULT=cr)
                env['READY'] = ready
                expected = er == ('success' if elixir == ready == 'true' else 'skipped')
                expected &= cr == ('success' if claude == ready == 'true' else 'skipped')
                actual = subprocess.run(['bash', '-e', '-c', command], env=env, capture_output=True)
                self.assertEqual(actual.returncode == 0, expected, (elixir, claude, ready, er, cr))

    def test_make_all_retains_full_coverage_and_claude_suite(self):
        result = subprocess.run(['make', '-n', '-C', str(ROOT / 'elixir'), 'all'], capture_output=True, text=True, check=True)
        for command in ('mix build', 'mix format --check-formatted', 'mix lint', 'mix test --cover',
                        'mix dialyzer --format short', 'npm --prefix ../runner/claude ci',
                        'npm --prefix ../runner/claude run check', 'npm --prefix ../runner/claude test'):
            self.assertIn(command, result.stdout)
        self.assertEqual(result.stdout.count('npm --prefix ../runner/claude ci'), 1)

