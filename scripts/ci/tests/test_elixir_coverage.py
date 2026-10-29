"""Execute real controller branches with inert local command/service boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

before = sys.path[:]
try:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import elixir_coverage as controller
finally:
    sys.path[:] = before


class ExistingJobPhases(unittest.TestCase):
    def test_unknown_source_preserves_ordinary_full_make_and_parent_settings_without_api_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'; state.mkdir(mode=0o700)
            calls, outputs = [], []
            def run(arguments, source, environment, **options):
                calls.append((arguments, environment))
                options['log'].write_bytes(b'ordinary full qualification output')
                return 123.0
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', return_value=dict(source='a'*40, ordinary=True)), \
                 patch.object(controller, 'run', side_effect=run), \
                 patch.object(controller, 'output', side_effect=lambda **value: outputs.append(value)), \
                 patch.object(controller, 'summary'), \
                 patch.dict(os.environ, dict(PATH='/ordinary/bin', HOME='/ordinary/home', ERL_FLAGS='existing-settings',
                                            GH_TOKEN='dummy-token', GITHUB_TOKEN='dummy-token'), clear=True):
                controller.execute()
            self.assertEqual(calls[0][0], ['make', 'elixir-ci'])
            self.assertEqual(calls[0][1], dict(PATH='/ordinary/bin', HOME='/ordinary/home', ERL_FLAGS='existing-settings'))
            self.assertEqual(outputs, [dict(mode='EXECUTED', origin='')])
            self.assertEqual(json.loads((state/'execution.json').read_text())['mode'], 'EXECUTED')

    def test_unknown_source_preparation_does_not_compile_in_private_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', return_value=dict(source='a'*40, supported=False)), \
                 patch.object(controller, 'controlled_environment') as private, \
                 patch.object(controller, 'run') as execute:
                controller.prepare()
            private.assert_not_called(); execute.assert_not_called()
            self.assertEqual(json.loads((state/'prepared.json').read_text()),
                             dict(source='a'*40, supported=False, ordinary=True))

    def test_supported_fresh_execution_keeps_ordinary_coverage_startup_and_actual_output_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'; state.mkdir(mode=0o700)
            commands, outputs = [], []
            def read(path):
                if path.name == 'prepared.json':
                    return dict(source='a'*40, ordinary=False)
                return dict(MIX_ENV='test', PATH='/fixed/toolchain:/usr/bin:/bin')
            def run(arguments, source, environment, **options):
                commands.append(arguments); options['log'].write_bytes(b'full suite output')
                return 340.2
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', side_effect=read), patch.object(controller, 'run', side_effect=run), \
                 patch.object(controller, 'output', side_effect=lambda **value: outputs.append(value)), \
                 patch.object(controller, 'summary'):
                controller.execute()
            self.assertEqual(commands, [['mix', 'test', '--cover', '--seed', '0', '--max-failures', '1']])
            self.assertNotIn('--no-compile', commands[0])
            self.assertEqual(outputs, [dict(mode='EXECUTED', origin='')])

    def test_dialyzer_remains_fresh_and_does_not_inherit_test_mix_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'; state.mkdir(mode=0o700)
            calls = []
            def read(path):
                if path.name == 'prepared.json':
                    return dict(source='a'*40, ordinary=False)
                return dict(MIX_ENV='test', HEX_OFFLINE='1', PATH='/fixed/toolchain:/usr/bin:/bin')
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', side_effect=read), \
                 patch.object(controller, 'run', side_effect=lambda *args, **options: calls.append(args)):
                controller.dialyzer()
            self.assertEqual(calls[0][0], ['make', 'dialyzer'])
            self.assertEqual(calls[0][2]['MIX_ENV'], 'dev')
            self.assertNotIn('HEX_OFFLINE', calls[0][2])
            self.assertEqual(json.loads((state/'dialyzer.json').read_text()), dict(source='a'*40, success=True))


    def test_actual_workflow_preserves_evidence_addresses_and_always_fresh_dialyzer(self):
        from test_components import job, workflow
        from validation_evidence import JOB, STEPS
        block = job(workflow('make-all'), 'elixir-checks')
        self.assertIn('    name: ' + JOB + '\n', block)
        for name in STEPS:
            self.assertEqual(block.count('      - name: ' + name + '\n'), 1, name)
        self.assertIn('      - name: Verify fresh Dialyzer\n        run:', block)
        self.assertNotIn('continue-on-error:', block)
        self.assertIn('steps.controller.outputs.trusted !=', block)
        self.assertIn('steps.controller.outputs.trusted ==', block)
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/dev'", block)
        self.assertIn('retention-days: 7', block)


if __name__ == '__main__':
    unittest.main()
