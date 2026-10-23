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
from runner_reuse import compatible_plan,plan

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
    def test_normal_plan_manager_only_and_committed_claude_only_selectivity(self):
        from reuse_payload import selected
        p=plan(ROOT,{},verify=self.verify,candidates=self.fixture['records'])
        self.assertFalse(p['has_builds'])
        for provider in ('codex','claude'):
            for arch in ('arm64','x86_64'):
                entry=selected(p,provider,arch,p['manager_source_revision'])
                self.assertEqual(entry['selection_mode'],'finite-component-v1')
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t)/'repo'
            subprocess.run(['git','clone','--shared','--quiet',str(ROOT),str(repo)],check=True)
            path=repo/'runner/claude/entrypoint.sh'
            first,body=path.read_bytes().split(b'\n',1)
            path.write_bytes(first+b'\nprintf "bounded-claude-input-probe\\n" >&2\n'+body)
            subprocess.run(['git','-C',str(repo),'add','runner/claude/entrypoint.sh'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Change actual Claude runtime input'],check=True)
            mixed=plan(repo,{},verify=self.verify,candidates=self.fixture['records'])
            self.assertEqual({(e['provider'],e['architecture']) for e in mixed['build_matrix']['include']},
                             {('claude','arm64'),('claude','x86_64')})
            self.assertEqual({(e['provider'],e['architecture']) for e in mixed['reuse_matrix']['include']},
                             {('codex','arm64'),('codex','x86_64')})
            for a in ('arm64','x86_64'):
                entry=selected(mixed,'codex',a,mixed['manager_source_revision'])
                self.assertEqual(entry['record'],self.records['codex'][a])
            with self.assertRaises(ValueError):compatible_plan(repo,{},verify=self.verify,candidates=self.fixture['records'])

    def test_ordinary_schema2_assembly_binds_finite_receipts_offline(self):
        import runpy,artifacts
        prepare=runpy.run_path(str(ROOT/'scripts/release/prepare-manager-inputs.py'))['prepare']
        p=plan(ROOT,{},verify=self.verify,candidates=self.fixture['records'])
        with tempfile.TemporaryDirectory() as t:
            output=Path(t)/'metadata'
            records=prepare(ROOT,p,None,output,None,self.verify)
            manifest=json.loads((output/'release.json').read_text())
            self.assertEqual(manifest['schema_version'],2)
            artifacts.validate(manifest,require_bundle=False)
            self.assertEqual(records,[self.records[p][a] for p in ('codex','claude') for a in ('arm64','x86_64')])
            for mutation in ('missing','wrong-provider','wrong-desired','unknown-architecture'):
                broken=copy.deepcopy(manifest)
                if mutation=='missing':del broken['component_selections']
                elif mutation=='wrong-provider':broken['component_selections']['codex']['arm64']['provider']='claude'
                elif mutation=='wrong-desired':broken['component_selections']['codex']['arm64']['desired_input_identity']='0'*64
                else:broken['component_selections']['codex']['intel']={}
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):artifacts.validate(broken,require_bundle=False)

    def test_payload_cli_finite_plan_checks_actual_checkout_before_retrieval(self):
        import contextlib,io
        from unittest.mock import patch
        import reuse_payload
        p=compatible_plan(ROOT,{},verify=self.verify,candidates=self.fixture['records'])
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'plan.json';path.write_text(json.dumps(p))
            argv=['reuse_payload.py','--plan',str(path),'--root',str(ROOT),'--provider','codex','--architecture','arm64','--destination',str(Path(t)/'payload')]
            with patch.object(sys,'argv',argv),patch.object(reuse_payload,'origin_verifier',return_value=self.verify),                 patch.object(reuse_payload,'retrieve',return_value=Path(t)/'original.tar.gz') as fetch,contextlib.redirect_stdout(io.StringIO()):
                reuse_payload.main()
                self.assertEqual(fetch.call_args.args[0],self.records['codex']['arm64'])
                self.assertEqual(fetch.call_args.args[1],self.origins['codex']['arm64'])
            p['components']['codex']['arm64']['selected_input_identity']='0'*64
            path.write_text(json.dumps(p))
            with patch.object(sys,'argv',argv),patch.object(reuse_payload,'origin_verifier',return_value=self.verify),                 patch.object(reuse_payload,'retrieve') as fetch,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                reuse_payload.main()
            fetch.assert_not_called()

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
    def test_three_manager_selection_uses_actual_sources_and_stock_descriptor_schema(self):
        import runpy
        package_selections=runpy.run_path(str(ROOT/'scripts/release/package-lifecycle.py'))['package_selections']
        with tempfile.TemporaryDirectory() as t:
            packages=Path(t)
            write_release(self.core(),packages)
            # Structural selection fixture uses the separately qualified688
            # native helper identity. This does not claim lifecycle execution.
            prior=self.core();prior['manager_source_revision']='688e702e536c84e4c1047a8dc46bbdddc85f3c16'
            prior['compatibility']['input_transition']['manager_source_revision']=prior['manager_source_revision']
            write_release(prior,packages/'previous')
            (packages/'legacy').mkdir()
            old={'schema_version':1,'source_revision':'9e1b61e4fdbf8c5a5c4154fba8d4ddecd91a7ecb'}
            (packages/'legacy/release.json').write_text(json.dumps(old))
            selected=package_selections(packages,False,self.core()['manager_source_revision'],True)
            self.assertEqual([v['source_revision'] for _,v in selected],[old['source_revision'],prior['manager_source_revision'],self.core()['manager_source_revision']])
            self.assertEqual(selected[1][1]['schema_version'],2)
            self.assertEqual(json.loads((packages/'previous/release.json').read_text())['schema_version'],1)

    def test_actual_joined_envelope_members_bind_originals_and_manager(self):
        import io,zipfile
        from component_selection import records_from_join
        plan=compatible_plan(ROOT,{},verify=self.verify,candidates=self.fixture['records'])
        with tempfile.TemporaryDirectory() as t:
            path=write_release(self.core(),t)
            def bundle(include_core=True):
                stream=io.BytesIO()
                with zipfile.ZipFile(stream,'w') as archive:
                    archive.writestr('release.json',path.read_bytes())
                    archive.writestr('component-plan.json',json.dumps(plan))
                    if include_core:archive.writestr('manager-core.json',(path.parent/'manager-core.json').read_bytes())
                    for record in self.fixture['records']:
                        archive.writestr(record['provider']+'-'+record['architecture']+'.json',json.dumps(record))
                return stream.getvalue()
            result=records_from_join(bundle())
            self.assertEqual(result['manifest']['source_revision'],plan['manager_source_revision'])
            self.assertEqual(result['records'],[self.records[p][a] for p in ('codex','claude') for a in ('arm64','x86_64')])
            with self.assertRaises(ValueError):records_from_join(bundle(False))

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
