"""Effective inputs refuse unknown state; no fixture claims real CI reuse."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SPEC = importlib.util.spec_from_file_location('validation_inputs', Path(__file__).parents[1] / 'validation_inputs.py')
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)


class PublicInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def tree(self, root=None, **options):
        return inputs.tree_identity(root or self.root, allowed_roots=[self.root],
                                    deadline=time.monotonic() + 5, **options)[0]

    def test_raw_compiler_manifest_and_native_bytes_are_both_inputs(self):
        cache = self.root / '_build'
        cache.mkdir()
        manifest, beam, nif = [cache / name for name in ('.compile.elixir', 'App.beam', 'native.so')]
        for path in (manifest, beam, nif):
            path.write_bytes(b'original')
        baseline = self.tree()
        for path in (manifest, beam, nif):
            with self.subTest(path=path.name):
                path.write_bytes(b'changed!')
                self.assertNotEqual(self.tree()['sha256'], baseline['sha256'])
                path.write_bytes(b'original')
        self.assertEqual(self.tree()['sha256'], baseline['sha256'])

    def test_executable_mode_changes_identity_without_byte_change(self):
        executable = self.root / 'helper'
        executable.write_text('same bytes')
        original = self.tree()['sha256']
        executable.chmod(0o700)
        self.assertNotEqual(original, self.tree()['sha256'])

    def test_symlink_consumes_target_and_rejects_escape_or_cycle(self):
        target = self.root / 'actual'
        target.write_text('actual code')
        link = self.root / 'alias'
        link.symlink_to(target.name)
        first = self.tree()['sha256']
        target.write_text('different code')
        self.assertNotEqual(first, self.tree()['sha256'])
        link.unlink()
        link.symlink_to('optional-absent-directory')
        missing = self.tree()['sha256']
        (self.root / 'optional-absent-directory').mkdir()
        self.assertNotEqual(missing, self.tree()['sha256'])
        link.unlink()
        link.symlink_to('/etc/passwd')
        with self.assertRaises(inputs.UnknownInput):
            self.tree()
        link.unlink()
        link.symlink_to('.')
        with self.assertRaises(inputs.UnknownInput):
            self.tree()

    def test_missing_state_differs_from_empty_and_regular_root_must_be_audited(self):
        missing = self.tree(self.root / 'cache')['sha256']
        (self.root / 'cache').mkdir()
        self.assertNotEqual(missing, self.tree(self.root / 'cache')['sha256'])
        with self.assertRaises(inputs.UnknownInput):
            inputs.tree_identity(Path(__file__), allowed_roots=[self.root], deadline=time.monotonic() + 1)

    def test_expired_or_excess_inventory_never_returns_reusable_identity(self):
        (self.root / 'one').write_text('one')
        with self.assertRaises(inputs.UnknownInput):
            inputs.tree_identity(self.root, allowed_roots=[self.root], deadline=time.monotonic() - 1)
        with self.assertRaises(inputs.UnknownInput):
            self.tree(maximum_files=1)

    def test_special_files_are_not_silently_ignored(self):
        os.mkfifo(self.root / 'socket-like-input')
        with self.assertRaises(inputs.UnknownInput):
            self.tree()

    def test_environment_uses_private_home_and_never_inherits_credentials(self):
        source, state, public_home = (self.root / name for name in ('source', 'state', 'public'))
        source.mkdir()
        public_home.mkdir()
        parent = dict(HOME=str(public_home), PATH='/usr/bin:/bin', GH_TOKEN='fixture-secret',
                      OPENAI_API_KEY='fixture-secret', SYMPHONY_HOME='/foreign', ERL_FLAGS='-unsafe',
                      MIX_ENV='prod', GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='unsafe')
        actual = inputs.controlled_environment(source, state, parent)
        self.assertEqual(actual['HOME'], str(state.resolve() / 'home'))
        self.assertEqual(actual['MIX_ENV'], 'test')
        self.assertFalse(set(actual) & {'GH_TOKEN', 'OPENAI_API_KEY', 'SYMPHONY_HOME', 'ERL_FLAGS', 'GIT_CONFIG_KEY_0'})
        self.assertNotIn('fixture-secret', repr(actual))
        self.assertEqual((state.stat().st_mode & 0o777), 0o700)
        (state / 'home' / '.erlang').write_text('prior startup state')
        with self.assertRaises(inputs.UnknownInput):
            inputs.controlled_environment(source, state, parent)

    def test_private_state_cannot_overlap_source_or_redirect_through_symlink(self):
        source = self.root / 'source'
        source.mkdir()
        parent = dict(HOME=str(self.root), PATH='/bin')
        for state in (source, source / 'state', self.root):
            with self.subTest(state=state), self.assertRaises(inputs.UnknownInput):
                inputs.controlled_environment(source, state, parent)
        alias = self.root / 'alias'
        alias.symlink_to(source, target_is_directory=True)
        with self.assertRaises(inputs.UnknownInput):
            inputs.controlled_environment(source, alias, parent)

    def test_user_mix_configuration_and_relative_path_refuse(self):
        source, home = self.root / 'source', self.root / 'public'
        source.mkdir()
        (home / '.mix').mkdir(parents=True)
        (home / '.mix' / 'config.exs').write_text('external configuration')
        with self.assertRaises(inputs.UnknownInput):
            inputs.controlled_environment(source, self.root / 'state', dict(HOME=str(home), PATH='/bin'))
        (home / '.mix' / 'config.exs').unlink()
        with self.assertRaises(inputs.UnknownInput):
            inputs.controlled_environment(source, self.root / 'state', dict(HOME=str(home), PATH='/bin:.'))


class CandidateSourceTests(unittest.TestCase):
    def test_actual_bytes_modes_and_full_candidate_identity_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            (root / 'source.ex').write_text('original')
            (root / 'docs.txt').write_text('documentation')
            git('add', 'source.ex', 'docs.txt')
            git('commit', '-qm', 'fixture')
            (root / '.gitignore').write_text('.env\n_build/\n')
            git('add', '.gitignore')
            git('commit', '-qm', 'fixture ignore rules')
            revision = git('rev-parse', 'HEAD')
            inputs.untracked_inputs(root, ('_build',))
            (root / '_build').mkdir()
            (root / '_build' / 'App.beam').write_bytes(b'generated but separately hashed')
            inputs.untracked_inputs(root, ('_build',))
            (root / '.env').write_text('undeclared external configuration')
            with self.assertRaises(inputs.UnknownInput):
                inputs.untracked_inputs(root, ('_build',))
            (root / '.env').unlink()
            (root / 'untracked.ex').write_text('new executable source')
            with self.assertRaises(inputs.UnknownInput):
                inputs.untracked_inputs(root, ('_build',))
            (root / 'untracked.ex').unlink()
            baseline, _ = inputs.tracked_source(root, revision, lambda name: name == 'docs.txt')
            (root / 'docs.txt').write_text('irrelevant prose')
            self.assertEqual(baseline, inputs.tracked_source(root, revision, lambda name: name == 'docs.txt')[0])
            (root / 'source.ex').write_text('modified candidate')
            with self.assertRaises(inputs.UnknownInput):
                inputs.tracked_source(root, revision, lambda name: name == 'docs.txt')
            (root / 'source.ex').write_text('original')
            (root / 'source.ex').chmod(0o700)
            with self.assertRaises(inputs.UnknownInput):
                inputs.tracked_source(root, revision, lambda name: name == 'docs.txt')
            with self.assertRaises(inputs.UnknownInput):
                inputs.tracked_source(root, 'a' * 40, lambda _: False)



class AuditedCoverageProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        before = sys.path[:]
        try:
            sys.path.insert(0, str(Path(__file__).parents[1]))
            import elixir_coverage_profile
            cls.profile = elixir_coverage_profile
        finally:
            sys.path[:] = before

    def test_actual_transitive_peers_and_template_remain_inputs_but_known_node_only_does_not(self):
        excluded = self.profile.source_exclusion()
        for name in ('elixir/test/symphony_elixir/claude_adapter_test.exs',
                     'runner/claude/test/fixtures/adversarial-peer.mjs',
                     'runner/claude/helper.mjs', 'runner/claude/cleanup.mjs',
                     'runner/claude/package-lock.json', '.github/pull_request_template.md',
                     'scripts/codex-apparmor.profile', 'scripts/codex-seccomp.json',
                     '.github/media/unknown-new-media.bin', 'unknown-new-validation-input'):
            with self.subTest(name=name):
                self.assertFalse(excluded(name))
        for name in ('runner/claude/test/process-lifecycle.test.mjs',
                     'runner/claude/auth.mjs', 'README.md',
                     '.github/media/symphony-demo.mp4'):
            with self.subTest(name=name):
                self.assertTrue(excluded(name))

    def test_every_excluded_unknown_ci_control_is_bound_by_control_inventory(self):
        for name in ('scripts/ci/new-controller.py', 'scripts/ci/tests/test_unknown.py',
                     '.github/workflows/new-workflow.yml', '.github/actions/new/action.yml'):
            self.assertTrue(self.profile.source_exclusion()(name))
            self.assertTrue(self.profile.control_path(name))
        self.assertFalse(self.profile.control_path('elixir/lib/new.ex'))

    def test_reviewed_source_pin_does_not_bind_one_machine_uid_but_runtime_identity_does(self):
        first = [['lib.ex', '100644', 'a' * 64, 0o644, 1001, 1001]]
        second = [['lib.ex', '100644', 'a' * 64, 0o640, 1002, 1002]]
        self.assertEqual(self.profile.audited_code(first), self.profile.audited_code(second))
        self.assertNotEqual(inputs.digest(first), inputs.digest(second))
        second[0][2] = 'b' * 64
        self.assertNotEqual(self.profile.audited_code(first), self.profile.audited_code(second))

    def test_canonical_tool_path_removes_ambient_provider_cli_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'bin'; binary.mkdir()
            for name in ('erl', 'elixir', 'mix', 'node'):
                path = binary / name; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
            result, selected = self.profile.executable_path(dict(PATH=str(binary) + ':/foreign/provider/bin'))
            self.assertEqual(result, str(binary.resolve()) + ':/usr/bin:/bin')
            self.assertEqual(set(selected), {'erl', 'elixir', 'mix', 'node'})
            alternate = root / 'alternate'; alternate.mkdir()
            (binary / 'mix').unlink()
            path = alternate / 'mix'; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
            with self.assertRaises(self.profile.UnknownInput):
                self.profile.executable_path(dict(PATH=str(binary) + ':' + str(alternate)))

    def test_changed_source_refuses_before_platform_or_expensive_public_tree_probes(self):
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as directory:
            descriptor = Path(directory) / 'profile.json'
            descriptor.write_text(json.dumps(dict(version=1, audited_source_sha256='not-the-source')))
            with patch.object(self.profile, 'PROFILE_PATH', descriptor), \
                 patch.object(self.profile, 'source_identity', return_value=({}, [['actual-input']])), \
                 patch.object(self.profile, 'linux_platform') as platform_probe, \
                 patch.object(self.profile, 'tree_identity') as tree_probe:
                with self.assertRaisesRegex(self.profile.UnknownInput, 'changed or unaudited'):
                    self.profile.fingerprint(Path(directory), 'a' * 40, {}, {})
                platform_probe.assert_not_called()
                tree_probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
