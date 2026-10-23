"""Exercise immutable image/user qualification and actual command failures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BrowserImage(unittest.TestCase):
    def test_actual_helper_binds_image_and_runs_default_user_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            docker = root / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(args)+'\\n')
if args[0:2]==['image','inspect']:
    field=args[3]
    if field=='{{.Id}}': print(os.environ.get('IMAGE_ID', 'sha256:'+'a'*64))
    elif 'com.symphony.agent' in field: print(os.environ['PROVIDER'])
    else: print(os.environ.get('REVISION', 'b'*40))
else: sys.exit(int(os.environ.get('RUN_EXIT','0')))
''')
            docker.chmod(0o755)
            calls = root / 'calls'
            def invoke(provider, **changes):
                calls.write_text('')
                env = dict(os.environ, PATH=str(root)+':'+os.environ['PATH'], CALLS=str(calls), PROVIDER=provider)
                env.update(changes)
                return subprocess.run(['bash', str(ROOT/'runner/browser/smoke-image.sh'), 'mutable-local-tag', provider, 'b'*40],
                                      env=env, capture_output=True), [json.loads(line) for line in calls.read_text().splitlines()]
            for provider, uid in (('codex', '0'), ('claude', '11001')):
                result, observed = invoke(provider)
                self.assertEqual(result.returncode, 0, result.stderr)
                command = observed[-1]
                self.assertEqual(command[:3], ['run', '--rm', '--network'])
                self.assertIn('none', command)
                self.assertIn('sha256:'+'a'*64, command)
                self.assertNotIn('--user', command)
                self.assertNotIn('mutable-local-tag', command)
                self.assertEqual(command[-1], uid)
                self.assertIn('test "$(id -u)" = "$1"', command[-3])
                self.assertIn('node /opt/symphony/browser/smoke.cjs', command[-3])
                self.assertIn('playwright screenshot', command[-3])
                for changes in ({'IMAGE_ID':'not-an-image'}, {'REVISION':'c'*40}, {'RUN_EXIT':'37'}):
                    failed, _ = invoke(provider, **changes)
                    self.assertNotEqual(failed.returncode, 0)
            result, observed = invoke('unknown')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(observed, [])
