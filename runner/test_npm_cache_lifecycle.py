#!/usr/bin/env python3
"""Real owned-process regressions for the credential-free npm qualification helper."""
# Hosted routing probe: the process ownership regressions remain unchanged.
# Rapid successor revision requires fresh checks after obsolete runs are cancelled.
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_npm_cache as cache

NativeCommands = cache.NativeCommands
live_group_members = cache.live_group_members


FRONTEND = r'''
import json, os
from pathlib import Path
import select, signal, subprocess, sys
home = Path(os.environ['HOME'])
if sys.argv[1] == 'writer':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    folder = home / '.tmp/plugins-clone-fixture/plugins'
    folder.mkdir(parents=True, exist_ok=True)
    (home / 'writer.pid').write_text(str(os.getpid()))
    print('ready', flush=True)
    counter = 0
    while True:
        (folder / 'entry').write_text(str(counter))
        counter += 1
        select.select([], [], [], 0.01)
else:
    if sys.argv[1] == 'exit-before-initialize':
        sys.exit(17)
    for line in sys.stdin:
        value = json.loads(line)
        if value['method'] == 'initialize':
            child = subprocess.Popen([sys.executable, __file__, 'writer'], stdout=subprocess.PIPE, text=True)
            assert child.stdout.readline().strip() == 'ready'
            response = {'id': value['id']}
            if sys.argv[1] == 'initialize-error':
                response['error'] = 'synthetic initialization refusal'
            else:
                response['result'] = {}
            print(json.dumps(response), flush=True)
'''


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='npm-native-lifecycle-')).resolve()
        self.owners = []
        self.control = None
        self.addCleanup(self.cleanup_resources)
        self.frontend = self.root / 'frontend.py'
        self.frontend.write_text(FRONTEND)

    def cleanup_resources(self):
        failures = []
        for owner in self.owners:
            try:
                owner.close()
            except Exception as error:
                failures.append(error)
        if self.control is not None:
            self.control.terminate()
            self.control.wait(timeout=2)
        if failures:
            raise RuntimeError('retain lifecycle fixture: owned process did not quiesce') from failures[0]
        shutil.rmtree(self.root)

    def owner(self, name, mode='success', start=True):
        home = self.root / name
        home.mkdir(exist_ok=True)
        owner = NativeCommands(home, self.root, [sys.executable, str(self.frontend), mode])
        self.owners.append(owner)
        if start:
            owner.start()
        return owner

    def writer(self, owner):
        return int((owner.home / 'writer.pid').read_text())

    def assert_quiescent(self, owner):
        self.assertEqual(live_group_members(owner.child.pid), set())
        self.assertIsNotNone(owner.child.returncode)

    def test_old_frontend_only_wait_leaves_actual_writer_then_group_close_stops_it(self):
        owner = self.owner('old-close')
        writer = self.writer(owner)
        self.assertEqual(os.getpgid(writer), owner.child.pid)
        self.assertNotEqual(owner.child.pid, os.getpgrp())
        # Execute the old shutdown algorithm, including its closed-stdin state.
        owner.child.stdin.close()
        owner.child.wait(timeout=2)
        self.assertIn(writer, live_group_members(owner.child.pid))
        self.assertTrue((owner.home / '.tmp/plugins-clone-fixture/plugins/entry').exists())
        print(json.dumps({'old_frontend_exited': owner.child.pid, 'surviving_writer': writer,
                          'owned_group': os.getpgid(writer)}), flush=True)
        owner.close()
        self.assert_quiescent(owner)
        shutil.rmtree(owner.home)  # Strict deletion only after verified shutdown.
        self.assertFalse(owner.home.exists())

    def test_restart_has_exclusive_ownership_and_preserves_unrelated_control(self):
        self.control = subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.read()'],
                                        stdin=subprocess.PIPE, start_new_session=True)
        first = self.owner('retained-home')
        first_pid = first.child.pid
        with self.assertRaisesRegex(RuntimeError, 'cannot be started twice'):
            first.start()
        self.assertEqual(first.child.pid, first_pid)
        first.close()
        with self.assertRaisesRegex(RuntimeError, 'cannot be started twice'):
            first.start()
        self.assert_quiescent(first)
        second = self.owner('retained-home')
        self.assertNotEqual(first.child.pid, second.child.pid)
        first.close()  # Idempotence must not signal the replacement.
        self.assertIn(self.writer(second), live_group_members(second.child.pid))
        self.assertIsNone(self.control.poll())
        second.close()
        self.assert_quiescent(second)
        self.assertIsNone(self.control.poll())
        self.control.stdin.close()

    def test_initialization_refusal_stops_writer_and_closes_streams(self):
        owner = self.owner('init-failure', 'initialize-error', start=False)
        with self.assertRaisesRegex(RuntimeError, 'synthetic initialization refusal'):
            owner.start()
        self.assert_quiescent(owner)
        self.assertTrue(owner.closed)
        self.assertTrue(owner.child.stdin.closed)
        self.assertTrue(owner.child.stdout.closed)
        self.assertTrue(owner.stderr.closed)
        shutil.rmtree(owner.home)

    def test_frontend_exit_before_initialize_is_reaped_without_lost_owner(self):
        owner = self.owner('early-exit', 'exit-before-initialize', start=False)
        with self.assertRaisesRegex(RuntimeError, 'native command response unavailable'):
            owner.start()
        self.assert_quiescent(owner)
        self.assertEqual(owner.child.returncode, 17)
        self.assertTrue(owner.closed)

    def test_spawn_failure_still_closes_owned_stderr(self):
        owner = NativeCommands(self.root, self.root, [str(self.root / 'missing-executable')])
        self.owners.append(owner)
        with self.assertRaises(FileNotFoundError):
            owner.start()
        self.assertIsNone(owner.child)
        self.assertTrue(owner.closed)
        self.assertTrue(owner.stderr.closed)

    def test_failed_quiescence_retains_home_and_workspace_without_finalizers(self):
        workspace = self.root / 'task'
        workspace.mkdir()
        owner = self.owner('retained-on-failure')
        fixture = object.__new__(cache.NpmCacheTests)
        fixture.root, fixture.home, fixture.native = workspace, owner.home, owner
        with patch.object(owner, 'close', side_effect=RuntimeError('unverified process quiescence')):
            with self.assertRaisesRegex(RuntimeError, 'unverified process quiescence'):
                fixture.cleanup_resources()
        self.assertTrue(workspace.exists())
        self.assertTrue(owner.home.exists())
        self.assertIn(self.writer(owner), live_group_members(owner.child.pid))
        fixture.cleanup_resources()
        self.assert_quiescent(owner)
        self.assertFalse(workspace.exists())
        self.assertFalse(owner.home.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
