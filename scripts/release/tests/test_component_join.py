import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

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
