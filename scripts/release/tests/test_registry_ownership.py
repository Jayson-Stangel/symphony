import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import test_registry as registry


class RegistryOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'owned.json'
        self.state = {'name': 'unique-owned-name', 'owner': 'token', 'container_id': None, 'cleanup': 'pending'}
        self.path.write_text(json.dumps(self.state))

    def test_uncertain_create_is_recovered_only_by_unique_name_and_label(self):
        value = {'Name': '/unique-owned-name', 'Id': 'exact-container-id', 'Config': {'Labels': {registry.LABEL: 'token'}}}
        with patch.object(registry, 'docker', side_effect=[json.dumps([value]), '', '']) as run:
            registry.cleanup(self.path)
        self.assertEqual(run.call_args_list[1].args, ('rm', '--force', 'exact-container-id'))
        self.assertEqual(json.loads(self.path.read_text())['cleanup'], 'removed-owned-container')

    def test_foreign_name_label_or_id_is_never_removed(self):
        for value in ({'Name': '/foreign'}, {'Name': '/unique-owned-name', 'Id': 'id', 'Config': {'Labels': {registry.LABEL: 'foreign'}}}):
            with patch.object(registry, 'docker', return_value=json.dumps([value])) as run:
                with self.assertRaises(ValueError):
                    registry.cleanup(self.path)
                self.assertEqual(run.call_count, 1)

    def test_missing_container_and_unavailable_daemon_are_distinguished(self):
        error = subprocess.CalledProcessError(1, ['docker'])
        with patch.object(registry, 'docker', side_effect=[error, '']):
            registry.cleanup(self.path)
        self.assertEqual(json.loads(self.path.read_text())['cleanup'], 'absent')
        with patch.object(registry, 'docker', side_effect=[error, error]):
            with self.assertRaises(subprocess.CalledProcessError):
                registry.cleanup(self.path)

    def test_acquisition_intent_is_retained_before_create_can_fail(self):
        with patch.object(registry, 'docker', side_effect=subprocess.CalledProcessError(1, ['docker'])):
            with self.assertRaises(subprocess.CalledProcessError):
                registry.start(self.path)
        value = json.loads(self.path.read_text())
        self.assertEqual(value['cleanup'], 'pending')
        self.assertTrue(value['owner'])

    def test_invalid_acquisition_receipt_retains_recoverable_owned_name(self):
        with patch.object(registry, 'docker', return_value='invalid receipt') as run:
            with self.assertRaisesRegex(ValueError, 'invalid registry acquisition'):
                registry.start(self.path)
            self.assertEqual(run.call_count, 1)
        self.assertIsNone(json.loads(self.path.read_text())['container_id'])
