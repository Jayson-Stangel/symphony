"""Host manager packaging does not relabel or rebuild compatible selected runtimes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/release'))
import artifacts
from manager_envelope import make_core,write_release,validate_core,load_release,validate_release
from manager_inputs import validate_checkout
from finite_transition import evaluate


class ManagerProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=json.loads((ROOT/'scripts/release/tests/fixtures/manager-original-68.json').read_text())
        cls.records={}
        for record in fixture['records']:cls.records.setdefault(record['provider'],{})[record['architecture']]=record
        cls.origins={p:{a:fixture['verified_origins'][p+'-'+a] for a in group} for p,group in cls.records.items()}

    def core(self, root=ROOT):return make_core(root,self.records,self.origins)

    def test_manager_only_packaging_keeps_originals_without_current_recipe_equality(self):
        # Final manager fixtures are intentionally different Docker COPY inputs.
        # The historical physical exemption must still refuse that checkout.
        with self.assertRaises(ValueError):evaluate(ROOT,self.records)
        with patch('manager_envelope.evaluate',side_effect=AssertionError('no physical equality claim')):
            core=self.core()
        self.assertEqual(core['compatibility'],{'runner_protocol':1,'selection_mode':'runner-protocol-v1'})
        self.assertEqual(core['original_components'],{p:{a:{k:v for k,v in r.items() if k not in ('provider','architecture')} for a,r in group.items()} for p,group in self.records.items()})
        self.assertEqual(core['verified_origins'],self.origins)
        with tempfile.TemporaryDirectory() as folder:
            path=write_release(core,folder)
            normalized=load_release(path)
            self.assertNotIn('component_selections',normalized)
            with patch('manager_inputs.identity',side_effect=AssertionError('no image input comparison')):
                validate_checkout(ROOT,normalized)
            wrong=copy.deepcopy(normalized);wrong['source_revision']='f'*40
            with self.assertRaisesRegex(ValueError,'actual checkout'):validate_checkout(ROOT,wrong)

    def test_unknown_protocol_mode_extra_equality_and_corruption_refuse(self):
        for mutate in (
            lambda c:c.update(compatibility=[]),
            lambda c:c['compatibility'].update(runner_protocol=2),
            lambda c:c['compatibility'].update(runner_protocol=True),
            lambda c:c['compatibility'].update(selection_mode='unknown'),
            lambda c:c['compatibility'].update(input_transition={}),
            lambda c:c['original_components']['codex']['arm64'].update(protocol=2),
            lambda c:c['original_components']['codex']['arm64'].update(source_revision='f'*40),
            lambda c:c['verified_origins']['codex']['arm64'].update(artifact_digest='sha256:'+'f'*64),
            lambda c:c['distribution']['codex'].update(arm64='invalid@sha256:'+'f'*64),
        ):
            core=self.core();mutate(core)
            with self.assertRaises((ValueError,KeyError,TypeError)):validate_core(core)
        with tempfile.TemporaryDirectory() as folder:
            path=write_release(self.core(),folder);normalized=load_release(path)
            normalized['component_selections']={}
            with self.assertRaisesRegex(ValueError,'finite input equality'):artifacts.validate(normalized,require_bundle=False)
            core_path=path.parent/'manager-core.json';core_path.unlink()
            with self.assertRaises(OSError):validate_release(path)

    def test_cli_reuses_verified_descriptor_without_image_operations(self):
        with tempfile.TemporaryDirectory() as folder:
            previous=write_release(self.core(),Path(folder)/'previous')
            output=Path(folder)/'next'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/release/manager_envelope.py'),'--root',str(ROOT),'--reuse-release',str(previous),'--output',str(output)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            next_core=json.loads((output/'manager-core.json').read_text())
            self.assertEqual(next_core['original_components'],self.core()['original_components'])
            self.assertEqual(next_core['verified_origins'],self.origins)
            self.assertEqual(next_core['manager_source_revision'],subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip())
            before=(output/'manager-core.json').read_bytes()
            repeated=subprocess.run([sys.executable,str(ROOT/'scripts/release/manager_envelope.py'),'--root',str(ROOT),'--reuse-release',str(previous),'--output',str(output)],capture_output=True,text=True)
            self.assertNotEqual(repeated.returncode,0)
            self.assertIn('refusing existing manager output',repeated.stderr)
            self.assertEqual((output/'manager-core.json').read_bytes(),before)

if __name__=='__main__':unittest.main()
