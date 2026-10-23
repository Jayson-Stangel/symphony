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
        self.assertEqual(run.call_args_list[1].args, ('rm', '--force', '--volumes', 'exact-container-id'))
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

    def test_explicit_port_is_loopback_only_and_retained_before_acquisition(self):
        for port in (5000, 32123):
            def create(*args):
                state = json.loads(self.path.read_text())
                self.assertEqual(state['host_port'], port)
                if args[0] == 'create':
                    self.assertIn('127.0.0.1:%s:5000' % port, args)
                    self.assertIsNone(state['container_id'])
                return 'a' * 64
            with patch.object(registry, 'docker', side_effect=create) as run:
                registry.start(self.path, port)
            self.assertEqual(run.call_count, 2)

    def test_invalid_port_refuses_before_state_or_docker_mutation(self):
        original = self.path.read_bytes()
        for value in (0, -1, 65536, True, '5000', 5000.0):
            with patch.object(registry, 'docker') as run:
                with self.assertRaisesRegex(ValueError, 'host port'):
                    registry.start(self.path, value)
                run.assert_not_called()
                self.assertEqual(self.path.read_bytes(), original)

    def test_cleanup_removes_and_confirms_only_associated_anonymous_volume(self):
        volume = 'a' * 64
        value = {'Name':'/unique-owned-name','Id':'exact-container-id',
                 'Config':{'Labels':{registry.LABEL:'token'}}, 'HostConfig':{},
                 'Mounts':[{'Type':'volume','Name':volume,'Destination':'/var/lib/registry'},
                           {'Type':'volume','Name':'foreign-named-data','Destination':'/foreign'}]}
        missing = subprocess.CalledProcessError(1, ['docker','volume','inspect',volume])
        with patch.object(registry,'docker',side_effect=[json.dumps([value]),'', '', missing, '']) as run:
            registry.cleanup(self.path)
        self.assertEqual(run.call_args_list[1].args, ('rm','--force','--volumes','exact-container-id'))
        self.assertEqual(run.call_args_list[3].args, ('volume','inspect',volume))
        self.assertEqual(json.loads(self.path.read_text())['owned_anonymous_volumes'], [volume])
        self.assertFalse(any('foreign-named-data' in call.args for call in run.call_args_list))

    def test_explicit_named_hex_volume_is_not_claimed_as_anonymous(self):
        volume = 'b' * 64
        value = {'HostConfig':{'Mounts':[{'Source':volume,'Target':'/data'}]},
                 'Mounts':[{'Type':'volume','Name':volume,'Destination':'/data'}]}
        self.assertEqual(registry.anonymous_volumes(value), [])

    def test_anonymous_volume_remaining_or_daemon_uncertain_fails_cleanup(self):
        value = {'Name':'/unique-owned-name','Id':'exact-container-id','Config':{'Labels':{registry.LABEL:'token'}},
                 'Mounts':[{'Type':'volume','Name':'a'*64,'Destination':'/data'}]}
        error = subprocess.CalledProcessError(1, ['docker'])
        for tail in ([json.dumps([{'Name':'a'*64}])], [error,error]):
            with patch.object(registry,'docker',side_effect=[json.dumps([value]),'', '', *tail]):
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    registry.cleanup(self.path)
