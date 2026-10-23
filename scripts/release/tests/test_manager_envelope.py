"""Real retained-record and physical Git-input migration regressions."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from component_join import join
from finite_transition import evaluate
from manager_contract import canonical,decode
from manager_envelope import make_core,validate_core,validate_release,write_release,load_release
from runner_reuse import compatible_plan

ROOT=Path(__file__).resolve().parents[3]
FIXTURE=Path(__file__).with_name('fixtures')/'manager-original-68.json'

class EnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads(FIXTURE.read_text());cls.records={}
        for r in cls.fixture['records']:cls.records.setdefault(r['provider'],{})[r['architecture']]=r
        cls.origins={p:{a:cls.fixture['verified_origins'][p+'-'+a] for a in g} for p,g in cls.records.items()}
    def verify(self,r):return self.origins[r['provider']][r['architecture']]
    def core(self,root=ROOT):return make_core(root,self.records,self.origins,evaluate(root,self.records))
    def test_actual_originals_zero_build_join_keeps_two_real_input_identities(self):
        calls=[]
        def verified(r):calls.append((r['provider'],r['architecture']));return self.verify(r)
        p=compatible_plan(ROOT,{},verify=verified,candidates=self.fixture['records'])
        self.assertFalse(p['has_builds']);self.assertEqual(p['build_matrix']['include'],[])
        self.assertEqual(set(calls),{(p,a) for p,g in self.records.items() for a in g})
        for group in p['components'].values():
            for d in group.values():self.assertNotEqual(d['selected_input_identity'],d['desired_input_identity'])
        self.assertEqual(len(join(p,'/does-not-exist',p['manager_source_revision'],verified)),4)
        broken=copy.deepcopy(p);broken['components']['codex']['arm64']['selected_input_identity']='0'*64
        with self.assertRaises(ValueError):join(broken,'/does-not-exist',p['manager_source_revision'],verified)
    def test_missing_original_origin_refuses_without_build_fallback(self):
        def missing(r):raise ValueError('expired original')
        with self.assertRaisesRegex(ValueError,'without image builds'):compatible_plan(ROOT,{},verify=missing,candidates=self.fixture['records'])
    def test_roundtrip_preserves_originals_and_same_digest_distribution(self):
        core=self.core();original=copy.deepcopy(core['original_components'])
        for p,g in core['distribution'].items():
            for a,ref in g.items():g[a]='localhost:5019/distribution/symphony-'+p+'@'+ref.split('@')[1]
        with tempfile.TemporaryDirectory() as t:
            path=write_release(core,t);result=validate_release(path)
            self.assertEqual(result['runners'],original)
            self.assertEqual(result['source_revision'],core['manager_source_revision'])
            self.assertNotEqual(result['source_revision'],result['storage_revision'])
            outer=json.loads(path.read_text());outer['version']=outer['version'].replace('.manifest.','.manifest.0')
            path.write_text(json.dumps(outer))
            with self.assertRaises(ValueError):validate_release(path)
    def test_stripped_envelope_cannot_become_legacy_identity(self):
        with tempfile.TemporaryDirectory() as t:
            path=write_release(self.core(),t);value=json.loads(path.read_text())
            del value['manager_envelope'];path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'requires its envelope'):load_release(path)

    def test_record_origin_every_crossview_refuses(self):
        core=self.core()
        for key,value in [('record_run_id',1),('record_run_attempt',99),('record_controller_revision','0'*40),
                          ('record_job_name','wrong'),('record_filename','foreign.json'),
                          ('native_qualification_scope','successful-native-job'),('original_job_conclusion','success')]:
            broken=copy.deepcopy(core);broken['verified_origins']['codex']['arm64'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_core(broken)
        broken=copy.deepcopy(core);broken['verified_origins']['codex']['arm64']['unknown']=1
        with self.assertRaises(ValueError):validate_core(broken)
    def test_canonical_rejects_duplicate_float_non_ascii_null_controls(self):
        for raw in (b'{"x":1,"x":1}\n',b'{"x":1.0}\n',b'{"x":null}\n',b'{"x":"\\u007f"}\n',b'{"x":"\\u00e9"}\n'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):decode(raw)
        self.assertEqual(decode(canonical({'x':True,'a':[1,'line\n']})),{'x':True,'a':[1,'line\n']})
    def test_committed_runtime_dependency_and_operational_inputs_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t)/'repo'
            subprocess.run(['git','clone','--shared','--quiet',str(ROOT),str(repo)],check=True)
            for name in ('elixir/lib/symphony_elixir.ex','elixir/mix.lock','elixir/WORKFLOW.md','.codex/skills/commit/SKILL.md'):
                path=repo/name
                self.assertTrue(path.is_file(),name)
                before=path.read_bytes()
                path.write_bytes(before+b'\n# committed effective input mutation\n')
                subprocess.run(['git','-C',str(repo),'add',name],check=True)
                subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                                'commit','--quiet','-m','Change actual consumed input'],check=True)
                with self.subTest(path=name),self.assertRaisesRegex(ValueError,'runtime, recipe, options, mode or unknown input differs'):
                    evaluate(repo,self.records)
                path.write_bytes(before)
                subprocess.run(['git','-C',str(repo),'add',name],check=True)
                subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Restore fixture input'],check=True)

    def test_real_physical_runtime_unknown_mode_and_doc_changes_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t)/'repo'
            subprocess.run(['git','clone','--shared','--quiet',str(ROOT),str(repo)],check=True)
            # Every probe mutates a real consumed working-tree input. Git provenance
            # and physical hashing must refuse, rather than trusting policy paths.
            probes=[('runner/Dockerfile',b'\n# different recipe\n'),('elixir/mix.exs',b'\n# dependency input\n'),
                    ('elixir/README.md',b'\nunknown prose\n'),('elixir/WORKFLOW.md',b'\nnew operational behavior\n')]
            for name,addition in probes:
                path=repo/name;before=path.read_bytes();path.write_bytes(before+addition)
                with self.subTest(path=name),self.assertRaises(ValueError):evaluate(repo,self.records)
                path.write_bytes(before)
            path=repo/'elixir/README.md';path.chmod(0o755)
            with self.assertRaises(ValueError):evaluate(repo,self.records)
            path.chmod(0o644)
            extra=repo/'elixir/unknown-runtime.txt';extra.write_text('unknown')
            with self.assertRaises(ValueError):evaluate(repo,self.records)
            extra.unlink();path.unlink()
            with self.assertRaises(ValueError):evaluate(repo,self.records)

if __name__=='__main__':unittest.main()
