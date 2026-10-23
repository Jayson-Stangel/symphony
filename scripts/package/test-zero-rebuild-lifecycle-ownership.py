#!/usr/bin/env python3
"""Local proof harness failure ownership tests; no Docker daemon operations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location("lifecycle", Path(__file__).with_name("test-zero-rebuild-lifecycle.py"))
lifecycle = importlib.util.module_from_spec(spec); spec.loader.exec_module(lifecycle)


class OwnershipTests(unittest.TestCase):
    def test_uncertain_volume_acquisition_retains_intent_and_owned_cleanup(self):
        pending, objects, calls = set(), {}, []
        def docker(*values, **kwargs):
            calls.append(values)
            if values[:2] == ('volume','create'):
                objects['owned-name'] = {'Name':'owned-name','Labels':{lifecycle.LABEL:'token'}}
                raise subprocess.TimeoutExpired(['docker'],1)
            if values[:2] == ('volume','rm'):
                objects.pop(values[-1]);return subprocess.CompletedProcess([],0,'','')
            if values[-1] in objects:return subprocess.CompletedProcess([],0,json.dumps([objects[values[-1]]]),'')
            return subprocess.CompletedProcess([],1,'','Error: No such volume')
        with self.assertRaises(subprocess.TimeoutExpired):
            lifecycle.acquire_owned_volume(docker,'owned-name','token',pending)
        self.assertEqual(pending, {'owned-name'})
        lifecycle.cleanup_owned_volume(docker,'owned-name','token')
        self.assertFalse(objects)
        self.assertEqual(calls[-1], ('volume','inspect','owned-name'))

    def test_foreign_volume_replacement_and_uncertain_daemon_are_preserved(self):
        for receipt in (subprocess.CompletedProcess([],0,json.dumps([{'Name':'owned-name','Labels':{lifecycle.LABEL:'foreign'}}]),''),
                        subprocess.CompletedProcess([],1,'','daemon unavailable')):
            calls = []
            def docker(*values, **kwargs):calls.append(values);return receipt
            with self.assertRaises(AssertionError):lifecycle.cleanup_owned_volume(docker,'owned-name','token')
            self.assertEqual(calls, [('volume','inspect','owned-name')])

    def test_only_explicitly_approved_local_socket_is_accepted(self):
        lifecycle.require_local_daemon('unix:///Users/jayson/.docker/run/docker.sock')
        for value in ('tcp://127.0.0.1:2375','ssh://example','unix:///tmp/other.sock','default'):
            with self.assertRaises(ValueError):lifecycle.require_local_daemon(value)


if __name__ == '__main__':unittest.main()
