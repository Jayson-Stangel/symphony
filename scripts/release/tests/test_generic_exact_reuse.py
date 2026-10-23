"""Actual Git C/D closures with explicitly SYNTHETIC native metadata controls.

No image C is built, qualified, relabelled, exported or claimed to exist. The
injected verifier tests selection; production verify_origin remains unchanged.
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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import artifacts
from component_join import join,validate_join_checkout
from manager_inputs import validate_checkout
from runner_inputs import identity
from runner_reuse import plan

ROOT=Path(__file__).resolve().parents[3]


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.PIPE).strip()


def commit(root,message):
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m',message)
    return git(root,'rev-parse','HEAD')


class GenericExactReuse(unittest.TestCase):
    def test_real_runtime_C_then_manager_only_D_has_zero_builds_and_native_valid_shape(self):
        with tempfile.TemporaryDirectory(prefix='generic-exact-reuse-') as temporary:
            root=Path(temporary)/'checkout'
            subprocess.run(['git','clone','--shared','--quiet',str(ROOT),str(root)],check=True)
            entrypoint=root/'runner/entrypoint.sh'
            first,body=entrypoint.read_bytes().split(b'\n',1)
            entrypoint.write_bytes(first+b'\nprintf "generic-runtime-C-control\\n" >&2\n'+body)
            source=commit(root,'Change actual common runtime input C')
            inputs={p:{a:identity(root,p,a)['input_identity'] for a in artifacts.ARCHITECTURES} for p in artifacts.PROVIDERS}
            templates=json.loads((ROOT/'scripts/release/tests/fixtures/manager-original-68.json').read_text())['records']
            records=[]
            for template in templates:
                record=copy.deepcopy(template);p,a=record['provider'],record['architecture'];q=inputs[p][a]
                # These positive structural controls are intentionally fictitious.
                # Every actual68 production record remains byte-for-byte unchanged.
                digest=lambda label:hashlib.sha256((source+p+a+label).encode()).hexdigest()
                record.update(source_revision=source,input_identity=q,image='fixture.invalid/symphony-'+p+'@sha256:'+digest('oci'),image_config='sha256:'+digest('config'))
                proof=record['qualification']
                for key in ('record_origin','payload_origin','native_step_recovery'):proof.pop(key,None)
                proof.update(source_revision=source,input_identity=q,image=record['image'],controller_revision=source,
                             run_id=1,run_attempt=1,artifact_id=1,artifact_digest='sha256:'+digest('artifact'),archive_sha256=digest('archive'))
                artifacts.validate_component(record,p,a);records.append(record)
            version_file=root/'scripts/release/manager-version'
            major,minor,patch=map(int,version_file.read_text().strip().split('-',1)[0].split('.'))
            manager_version=f'{major}.{minor}.{patch+1}'
            version_file.write_text(manager_version+'\n')
            manager=commit(root,'Release actual manager-only version D')
            self.assertNotEqual(source,manager)
            for p,g in inputs.items():
                for a,q in g.items():self.assertEqual(identity(root,p,a)['input_identity'],q)
            def verifier(record):
                self.assertIn(record,records)
                return {'test_only':True,'source_revision':source}
            selected=plan(root,{},verify=verifier,candidates=records)
            self.assertFalse(selected['has_builds']);self.assertEqual(selected['build_matrix']['include'],[])
            self.assertEqual(len(selected['reuse_matrix']['include']),4)
            self.assertTrue(all('selection_mode' not in d for g in selected['components'].values() for d in g.values()))
            joined=join(selected,'/no-build-records',manager,verifier)
            validate_join_checkout(root,selected,joined)
            wrong_source=copy.deepcopy(selected);wrong_source['manager_source_revision']=source
            with self.assertRaisesRegex(ValueError,'joined plan differs from actual manager checkout'):validate_join_checkout(root,wrong_source,joined)
            metadata=Path(temporary)/'metadata';metadata.mkdir();files=[]
            for record in records:
                path=metadata/(record['provider']+'-'+record['architecture']+'.json');path.write_text(json.dumps(record));files.append(str(path))
            from types import SimpleNamespace
            manifest_path=metadata/'release.json'
            artifacts.assemble(SimpleNamespace(records=files,schema_version=2,version=manager_version,revision=manager,channel='candidate',bundle=None,platform_bundles=True,output=str(manifest_path)),verifier=verifier)
            manifest=json.loads(manifest_path.read_text());self.assertNotIn('component_selections',manifest)
            validate_checkout(root,manifest)
            cli=subprocess.run([sys.executable,str(ROOT/'scripts/release/manager_inputs.py'),str(manifest_path),'--root',str(root)],capture_output=True,text=True)
            self.assertEqual(cli.returncode,0,cli.stderr)
            output=os.environ.get('SYMPHONY_TEST_GENERIC_OUTPUT')
            if output:
                output=Path(output);output.mkdir(parents=True,exist_ok=False)
                subprocess.run(['git','clone','--shared','--dissociate','--quiet',str(root),str(output/'checkout')],check=True)
                (output/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
                (output/'records.json').write_text(json.dumps(records,indent=2)+'\n')
                (output/'component-plan.json').write_text(json.dumps(selected,indent=2)+'\n')
                (output/'scope.json').write_text(json.dumps({'scope':'actual Git inputs; SYNTHETIC native metadata; no image qualification', 'runtime_source_C':source,'manager_source_D':manager,'input_identities':inputs},indent=2)+'\n')
            entrypoint.write_bytes(entrypoint.read_bytes()+b'\n# changed runtime after D\n')
            changed=commit(root,'Change actual runtime after manager D')
            altered=copy.deepcopy(manifest);altered['source_revision']=changed
            with self.assertRaisesRegex(ValueError,'selected exact runner inputs differ'):validate_checkout(root,altered)
            forged=copy.deepcopy(selected);forged['manager_source_revision']=changed
            for provider,group in forged['components'].items():
                for architecture,decision in group.items():
                    decision.update(input_identity=identity(root,provider,architecture)['input_identity'],
                                    selected_input_identity=decision['record']['input_identity'],
                                    desired_input_identity=identity(root,provider,architecture)['input_identity'])
            with self.assertRaisesRegex(ValueError,'component selected/desired closure differs'):join(forged,'/no-build-records',changed,verifier)
            with self.assertRaisesRegex(ValueError,'component selected/desired closure differs'):validate_join_checkout(root,forged,joined)
            import runpy
            prepare=runpy.run_path(str(ROOT/'scripts/release/prepare-manager-inputs.py'))['prepare']
            destination=Path(temporary)/'forged-manager'
            with self.assertRaisesRegex(ValueError,'component selected/desired closure differs'):
                prepare(root,forged,None,destination,None,verifier)
            self.assertFalse(destination.exists())
            smuggled=copy.deepcopy(selected)
            smuggled['components']['codex']['arm64']['input_transition']={'authority_sha256':'0'*64}
            with self.assertRaisesRegex(ValueError,'generic exact selection cannot carry finite authority'):
                join(smuggled,'/no-build-records',manager,verifier)
            unknown=copy.deepcopy(selected)
            unknown['components']['codex']['arm64']['selection_mode']='unreviewed-exception'
            with self.assertRaisesRegex(ValueError,'unknown component selection authority'):join(unknown,'/no-build-records',manager,verifier)
            stale=copy.deepcopy(selected);stale['manager_source_revision']=changed
            with self.assertRaisesRegex(ValueError,'actual manager checkout input differs'):validate_join_checkout(root,stale,joined)
            manifest_path.write_text(json.dumps(altered))
            failed=subprocess.run([sys.executable,str(ROOT/'scripts/release/manager_inputs.py'),str(manifest_path),'--root',str(root)],capture_output=True,text=True)
            self.assertNotEqual(failed.returncode,0);self.assertIn('selected exact runner inputs differ',failed.stderr)

if __name__=='__main__':unittest.main()
