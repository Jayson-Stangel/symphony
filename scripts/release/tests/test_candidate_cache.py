"""Actual shell callback routing; native image qualification is separate CI evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class CandidateCacheTest(unittest.TestCase):
    def test_current_and_previous_keep_true_source_args_and_separate_cache_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            docker = root/'docker'
            docker.write_text('#!' + sys.executable + '\nimport json,os,sys\nopen(os.environ["PROOF"],"w").write(json.dumps(sys.argv[1:]))\n')
            docker.chmod(0o755)
            uname = root/'uname'; uname.write_text('#!/bin/sh\necho x86_64\n'); uname.chmod(0o755)
            source = root/'actual prior source'; source.mkdir()
            (source/'runner').mkdir(); (source/'runner/Dockerfile').write_text('FROM source-fixture\n')
            subprocess.run(['git','init','--quiet',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'add','runner'],check=True)
            subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','source'],check=True)
            revision = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
            env = dict(os.environ, PATH=str(root)+':'+os.environ['PATH'], PROOF=str(root/'proof'),
                       SYMPHONY_GHA_RUNNER_CACHE='true', ACTIONS_RUNTIME_TOKEN='fixture-cache-token',
                       ACTIONS_RESULTS_URL='https://results.invalid', GITHUB_EVENT_NAME='pull_request',
                       GITHUB_REF='refs/pull/42/merge', PR_NUMBER='42', GITHUB_STEP_SUMMARY=str(root/'summary'))
            for selection in ('current','previous'):
                result = subprocess.run(['bash', str(ROOT/'scripts/release/build-runner-cached.sh'), 'codex', 'x86_64',
                                         str(source), 'localhost:5000/symphony-codex:'+('candidate' if selection=='current' else 'previous'), '0.0.4', revision, selection],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads((root/'proof').read_text())
                self.assertEqual(args[:3], ['buildx','build','--load'])
                self.assertEqual(args[args.index('--platform')+1], 'linux/amd64')
                self.assertEqual(args[-1], str(source.resolve()))
                self.assertIn('SYMPHONY_REVISION='+revision, args)
                self.assertIn('type=gha,scope=runner-v1-amd64-pr-42-claude', args)
                self.assertIn('type=gha,scope=runner-v1-amd64-trusted-codex-prior', args)
                export = args[args.index('--cache-to')+1]
                self.assertEqual(export, 'type=gha,scope=runner-v1-amd64-pr-42-codex'+('-prior' if selection=='previous' else '')+',mode=max')
                self.assertNotIn('fixture-cache-token', ' '.join(args)+result.stdout+result.stderr)
            for altered in ({'SYMPHONY_GHA_RUNNER_CACHE':''}, {'ACTIONS_RUNTIME_TOKEN':''}):
                (root/'proof').unlink(missing_ok=True)
                result = subprocess.run(['bash', str(ROOT/'scripts/release/build-runner-cached.sh'), 'codex', 'x86_64',
                                         str(source), 'localhost:5000/symphony-codex:candidate', '0.0.4', revision, 'current'], env=dict(env, **altered), capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root/'proof').exists())

    def test_runtime_action_masks_token_and_refuses_unknown_context(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required to verify the local Actions runtime bridge')
        script = ROOT/'.github/actions/runner-cache-runtime/runtime.js'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'environment'
            env = dict(os.environ, GITHUB_ACTIONS='true', GITHUB_ENV=str(path),
                       ACTIONS_RUNTIME_TOKEN='fixture-runtime-token', ACTIONS_RESULTS_URL='https://results.invalid')
            result = subprocess.run([node,str(script)], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '::add-mask::fixture-runtime-token\n')
            self.assertIn('ACTIONS_CACHE_SERVICE_V2=true', path.read_text())
            self.assertIn('SYMPHONY_GHA_RUNNER_CACHE=true', path.read_text())
            for changed in ({'GITHUB_ACTIONS':'false'}, {'ACTIONS_RUNTIME_TOKEN':''}, {'ACTIONS_RESULTS_URL':'invalid\nvalue'}):
                result = subprocess.run([node,str(script)], env=dict(env,**changed), capture_output=True)
                self.assertNotEqual(result.returncode, 0)
