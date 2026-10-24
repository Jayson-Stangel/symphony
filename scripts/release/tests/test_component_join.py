import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import component_join
from test_component_provenance import record


class ComponentJoinTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.plan = {'planning_complete': True, 'manager_source_revision': 'a'*40,
                     'components': {}}
        for provider in ('codex', 'claude'):
            self.plan['components'][provider] = {}
            for architecture in ('arm64', 'x86_64'):
                original = record(provider, architecture)
                self.plan['components'][provider][architecture] = {
                    'action': 'reuse', 'record': original, 'origin':{'exact':'origin'}, 'input_identity': original['input_identity']}
        self.calls = []

    def verify(self, value):
        self.calls.append(copy.deepcopy(value))
        return {"exact":"origin"}

    def test_reused_originals_are_verified_without_reexport_or_new_source_labels(self):
        selected = component_join.join(self.plan, self.root, 'a'*40, self.verify)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(value['source_revision'] == 'b'*40 for value in selected))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_build_must_supply_successful_exact_current_source_input_record(self):
        choice = self.plan['components']['codex']['arm64']
        choice['action'] = 'build'
        path = self.root/'codex-arm64.json'
        path.write_text(json.dumps(choice['record']))
        with self.assertRaisesRegex(ValueError, 'source differs'):
            component_join.join(self.plan, self.root, 'a'*40, self.verify)
        actual = copy.deepcopy(choice['record'])
        actual['source_revision'] = 'a'*40
        path.write_text(json.dumps(actual))
        self.assertEqual(len(component_join.join(self.plan, self.root, 'a'*40, self.verify)), 4)
        actual['input_identity'] = 'f'*64
        path.write_text(json.dumps(actual))
        with self.assertRaisesRegex(ValueError, 'differs from plan'):
            component_join.join(self.plan, self.root, 'a'*40, self.verify)

    def test_reused_native_origin_must_still_equal_the_exact_plan_selection(self):
        self.plan['components']['claude']['x86_64']['origin']={'changed':'origin'}
        with self.assertRaisesRegex(ValueError,'origin changed after planning'):
            component_join.join(self.plan,self.root,'a'*40,self.verify)

    def test_foreign_manager_plan_or_unavailable_origin_cannot_assemble(self):
        with self.assertRaisesRegex(ValueError, 'current manager'):
            component_join.join(self.plan, self.root, 'f'*40, self.verify)
        def expired(value):
            raise ValueError('payload expired')
        with self.assertRaisesRegex(ValueError, 'expired'):
            component_join.join(self.plan, self.root, 'a'*40, expired)


class ComponentJoinEntrypointTest(unittest.TestCase):
    """Actual argparse/controller/assembly with synthetic external origin receipts."""
    def test_normal_schema2_cli_assembles_exact_current_and_mixed_component_records(self):
        from runner_inputs import identity
        import manager_version
        source = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix='component-join-cli-') as folder:
            root = Path(folder) / 'checkout'
            subprocess.run(['git', 'clone', '--shared', '--quiet', str(source), str(root)], check=True)
            revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            records = root / 'native-records'; records.mkdir()
            plan = {'planning_complete': True, 'manager_source_revision': revision, 'components': {}}
            originals = {}
            for provider in ('codex', 'claude'):
                plan['components'][provider] = {}
                for architecture in ('arm64', 'x86_64'):
                    original = record(provider, architecture)
                    original['source_revision'] = revision
                    original['input_identity'] = identity(root, provider, architecture)['input_identity']
                    original['qualification'].update(source_revision=revision, controller_revision=revision,
                                                     input_identity=original['input_identity'])
                    originals[provider, architecture] = original
                    (records / f'{provider}-{architecture}.json').write_text(json.dumps(original))
                    plan['components'][provider][architecture] = {
                        'action': 'build', 'input_identity': original['input_identity']}
            plan_path = root / 'selected-plan.json'
            verified = []
            def external_origin(original, **kwargs):
                self.assertEqual(original, originals[original['provider'], original['architecture']])
                self.assertEqual(kwargs, {'candidate_run': 123})
                verified.append(copy.deepcopy(original))
                return {'scope': 'SYNTHETIC immutable native origin control', 'image': original['image']}
            previous = Path.cwd()
            try:
                os.chdir(root)
                for mode in ('all-build', 'mixed-reuse'):
                    with self.subTest(mode=mode):
                        if mode == 'mixed-reuse':
                            for architecture in ('arm64', 'x86_64'):
                                original = originals['codex', architecture]
                                original['source_revision'] = 'b' * 40
                                original['qualification']['source_revision'] = 'b' * 40
                                plan['components']['codex'][architecture].update(action='reuse', record=original,
                                    origin={'scope': 'SYNTHETIC immutable native origin control', 'image': original['image']})
                        plan_path.write_text(json.dumps(plan))
                        output = root / mode
                        verified.clear()
                        argv = ['component_join.py', '--plan', str(plan_path), '--records', str(records),
                                '--output', str(output), '--schema', '2']
                        with patch.object(sys, 'argv', argv), patch.dict(os.environ,
                                GITHUB_SHA=revision, GITHUB_RUN_ID='123', GITHUB_EVENT_NAME='workflow_dispatch'), \
                                patch.object(component_join, 'verify_origin', side_effect=external_origin):
                            component_join.main()
                        self.assertEqual(len(verified), 8, 'both join and schema2 assembly verify all four origins')
                        manifest = json.loads((output / 'release.json').read_text())
                        self.assertEqual(manifest['schema_version'], 2)
                        self.assertEqual(manifest['version'], manager_version.read(root))
                        self.assertEqual(manifest['source_revision'], revision)
                        self.assertEqual(json.loads((output / 'component-plan.json').read_text()), plan)
                        for (provider, architecture), original in originals.items():
                            self.assertEqual(json.loads((output / f'{provider}-{architecture}.json').read_text()), original)
                            self.assertEqual(manifest['runners'][provider][architecture],
                                             {k: v for k, v in original.items() if k not in ('provider', 'architecture')})
            finally:
                os.chdir(previous)
