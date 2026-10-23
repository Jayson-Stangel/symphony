#!/usr/bin/env python3
"""Native validation of generic manager envelopes using original qualified records.

Structural metadata fixtures; no hosted qualification or provider inference.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/release'))
from manager_envelope import make_core,write_release
from manager_contract import canonical
HELPER=os.environ['SYMPHONY_TEST_HOST_HELPER']


class NativeManagerProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=json.loads((ROOT/'scripts/release/tests/fixtures/manager-original-68.json').read_text());cls.records={}
        for record in fixture['records']:cls.records.setdefault(record['provider'],{})[record['architecture']]=record
        cls.origins={p:{a:fixture['verified_origins'][p+'-'+a] for a in group} for p,group in cls.records.items()}

    def test_actual_helper_accepts_distinct_manager_and_unchanged_original_runtime(self):
        core=make_core(ROOT,self.records,self.origins)
        with tempfile.TemporaryDirectory() as folder:
            path=write_release(core,folder)
            for operation in ('validate-release','validate-manager'):
                result=subprocess.run([HELPER,operation,str(path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([HELPER,'release-get',str(path),'runners.codex.arm64.source_revision'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),core['legacy_source_revision'])
            self.assertNotEqual(core['manager_source_revision'],core['legacy_source_revision'])

    def test_rehashed_invalid_mode_protocol_original_and_equality_claim_refuse(self):
        for mutate in (
            lambda c:c['compatibility'].update(selection_mode='unknown'),
            lambda c:c['compatibility'].update(runner_protocol=2),
            lambda c:c['compatibility'].update(runner_protocol=True),
            lambda c:c['compatibility'].update(input_transition={}),
            lambda c:c['original_components']['codex']['arm64'].update(protocol=2),
            lambda c:c['original_components']['codex']['arm64'].update(source_revision='f'*40),
            lambda c:c['verified_origins']['codex']['arm64'].update(artifact_digest='sha256:'+'f'*64),
            lambda c:c['distribution']['codex'].update(arm64='invalid@sha256:'+'f'*64),
        ):
            core=make_core(ROOT,self.records,self.origins)
            with tempfile.TemporaryDirectory() as folder:
                path=write_release(core,folder);outer=json.loads(path.read_text());previous=outer['version']
                mutate(core);raw=canonical(core);digest=hashlib.sha256(raw).hexdigest()
                # Rebind the self-consistency hash so content admission itself
                # must refuse; this is not a provenance/authenticity assertion.
                outer['manager_envelope']['core_sha256']=digest
                outer['version']=previous.rsplit('.manifest.',1)[0]+'.manifest.'+digest
                for bundle in [outer['bundle'],*outer['bundles'].values()]:bundle['filename']=bundle['filename'].replace(previous,outer['version'])
                (path.parent/'manager-core.json').write_bytes(raw);path.write_text(json.dumps(outer))
                result=subprocess.run([HELPER,'validate-release',str(path)],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0,(core,result.stdout))

if __name__=='__main__':unittest.main()
