"""Execute real controller branches with inert local command/service boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

before = sys.path[:]
try:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import elixir_coverage as controller
finally:
    sys.path[:] = before


COVERAGE_FIXTURE=b'685 tests, 0 failures, 10 skipped\n| 100.00% | Total |\n'


def precoverage_fixture(outer):
    root=outer/'source';root.mkdir();state=outer/'state';state.mkdir(mode=0o700)
    (state/'native_fetch').mkdir(mode=0o700)
    (root/'elixir').mkdir();(root/'elixir/mix.lock').write_text('  "phoenix": {:hex, :phoenix, "1.0.0"}\n')
    targets={'test':root/'elixir/_build/test','hex':state/'hex'}
    for target in targets.values():target.mkdir(parents=True)
    (targets['test']/'compiled').write_bytes(b'INERT original compiler bytes')
    (targets['hex']/'cache.bin').write_bytes(b'INERT original public registry')
    for target in (root/'elixir/deps',root/'elixir/_build/dev'):target.mkdir(parents=True)
    (root/'elixir/deps/source').write_bytes(b'INERT dependency bytes')
    rows=[]
    for name,target in targets.items():
        raw,metadata=controller.cache.create(target,root,**(dict(private_root=state) if name=='hex' else {}))
        path=state/(name+'-cache.tar.gz');path.write_bytes(raw);path.chmod(0o600)
        controller.store(state/('sidecar.json' if name=='test' else 'hex-sidecar.json'),metadata,new=True)
        rows.append(metadata['cache'])
    inputs=dict(identity=dict(schema_version=2,family='full-exunit-coverage',profile_version=2,
        execution=dict(profile='full-exunit-oci-v2',source=dict(files=199)),public_inputs=rows))
    inputs['sha256']=controller.digest(inputs['identity'])
    controller.store(state/'inputs.json',inputs,new=True)
    controller.store(state/'environment.json',controller.oci.environment(),new=True)
    controller.store(state/'prepared.json',dict(source='a'*40,supported=True,ordinary=False,test_inputs='FRESH'),new=True)
    controller.store(state/'dialyzer.json',dict(source='a'*40,success=True),new=True)
    controller.store(state/'warm.json',dict(mode='CLEAN'),new=True)
    return root,state,inputs


class ProtectedStartingWorld(unittest.TestCase):
    def test_input_drift_or_prearchive_tampering_refuses_before_any_test_container(self):
        from copy import deepcopy
        for changed in ('fingerprint','archive','metadata'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as directory:
                root,state,inputs=precoverage_fixture(Path(directory));observed=deepcopy(inputs)
                if changed=='fingerprint':
                    observed['identity']['unreviewed']='changed';observed['sha256']=controller.digest(observed['identity'])
                elif changed=='archive':
                    path=state/'hex-cache.tar.gz';path.write_bytes(path.read_bytes()+b'changed')
                else:
                    metadata=controller.read(state/'sidecar.json');metadata['cache']['sha256']='f'*64
                    controller.store(state/'sidecar.json',metadata)
                with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller.profile,'fingerprint',return_value=observed),patch.object(controller,'phase_run') as run,self.assertRaises(ValueError):controller.execute()
                run.assert_not_called();self.assertFalse((state/'execution.json').exists())

    def test_successful_execution_preserves_start_world_while_live_outputs_may_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root,state,inputs=precoverage_fixture(Path(directory));outputs=[];messages=[]
            original=(state/'test-cache.tar.gz').read_bytes()
            def run(source,private,phase,arguments,**options):
                start=controller.read(state/'execution.json')
                self.assertEqual(start['start_inputs_sha256'],inputs['sha256']);self.assertEqual(start['mode'],'EXECUTING')
                (root/'elixir/_build/test/compiled').write_bytes(b'INERT changed post-test output')
                (state/'hex/cache.bin').write_bytes(b'INERT changed post-test registry')
                options['log'].write_bytes(COVERAGE_FIXTURE);return 4.0
            with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller.profile,'fingerprint',return_value=inputs),patch.object(controller,'phase_run',side_effect=run),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary',side_effect=messages.append),patch.object(controller,'client'),patch.object(controller,'context',return_value=dict(eligible_producer=True,repository_id=77,run_id=101,attempt=2,job_id=501)):
                controller.execute();controller.seal()
            self.assertEqual(outputs[-1],dict(available=True))
            receipt=controller.read(state/'proof/receipt.json');self.assertEqual(receipt['schema_version'],4)
            self.assertEqual(receipt['execution']['start_inputs_sha256'],inputs['sha256'])
            self.assertEqual((state/'proof/test-cache.tar.gz').read_bytes(),original)
            self.assertEqual(set(receipt['sidecar']),{'test','hex','dev','deps'})

    def test_protected_archive_tampering_after_success_refuses_sealing(self):
        with tempfile.TemporaryDirectory() as directory:
            root,state,inputs=precoverage_fixture(Path(directory));outputs=[];messages=[]
            def run(*args,**options):options['log'].write_bytes(COVERAGE_FIXTURE);return 4.0
            with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller.profile,'fingerprint',return_value=inputs),patch.object(controller,'phase_run',side_effect=run),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary',side_effect=messages.append),patch.object(controller,'client'),patch.object(controller,'context',return_value=dict(eligible_producer=True,repository_id=77,run_id=101,attempt=2,job_id=501)):
                controller.execute();path=state/'test-cache.tar.gz';path.write_bytes(path.read_bytes()+b'tampered');controller.seal()
            self.assertEqual(outputs[-1],dict(available=False));self.assertIn('protected_start_inputs',messages[-1])
            self.assertFalse((state/'proof').exists())

    def test_actual_diagnostic_workflow_only_retains_unqualified_failures_and_always_cleans(self):
        import re
        from test_components import job,workflow
        block=job(workflow('make-all'),'elixir-checks')
        prepare=block.split('      - name: Prepare bounded unqualified public input diagnostics\n',1)[1].split('      - name:',1)[0]
        condition=re.search(r'^        if: (.+)$',prepare,re.M)[1]
        for supported in ('true','false',''):
            for proof,mode in ((proof,mode) for proof in ('true','false','') for mode in ('EXECUTED','REUSED','')):
                for failed in (True,False):
                    code=condition.replace('always()','True').replace('failure()',repr(failed)).replace('steps.profile.outputs.supported',repr(supported)).replace('steps.producer.outputs.available',repr(proof)).replace('steps.evidence.outputs.mode',repr(mode)).replace('&&',' and ').replace('||',' or ')
                    self.assertEqual(eval(code,{'__builtins__':{}}),supported=='true' and (failed or (proof!='true' and mode!='REUSED')))
        self.assertNotIn('GH_TOKEN',prepare)
        retained=block.split('      - name: Retain unqualified public input diagnostics\n',1)[1].split('      - name:',1)[0]
        self.assertIn('name: elixir-coverage-diagnostics-',retained);self.assertIn('retention-days: 3',retained)
        self.assertIn('actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9',retained)
        self.assertIn("if: always() && steps.profile.outputs.supported == 'true'",block.split('      - name: Remove owned OCI execution resources\n',1)[1])

    def test_healthy_reused_execution_never_mints_an_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root,state,_=precoverage_fixture(Path(directory));outputs=[]
            controller.store(state/'decision.json',dict(mode='REUSED',original_run_url='https://example.invalid/INERT'),new=True)
            with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary'),patch.object(controller,'context') as api:
                controller.seal()
            self.assertEqual(outputs,[dict(available=False)]);api.assert_not_called()
            self.assertEqual(controller.read(state/'seal-status.json')['stage'],'reused_original')
            self.assertFalse((state/'proof').exists())

    def test_diagnostics_are_exact_public_records_bounded_and_never_include_cache_or_ambient_values(self):
        for oversize in (False,True):
            with self.subTest(oversize=oversize),tempfile.TemporaryDirectory() as directory:
                root,state,inputs=precoverage_fixture(Path(directory));outputs=[]
                (state/'unknown.env').write_text('dummy-secret-never-copy')
                if oversize:controller.store(state/'inputs.json',dict(public_fixture='x'*10000))
                with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller,'MAX_DIAGNOSTICS',100 if oversize else 1024*1024),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary'),patch.dict(os.environ,dict(GITHUB_RUN_ID='101',GITHUB_RUN_ATTEMPT='2',GH_TOKEN='dummy-secret-never-copy')):controller.diagnostics()
                self.assertEqual(outputs,[dict(available=not oversize)])
                if oversize:self.assertFalse((state/'diagnostics').exists())
                else:
                    files=list((state/'diagnostics').iterdir());self.assertLessEqual(sum(p.stat().st_size for p in files),1024*1024)
                    self.assertTrue({p.name for p in files}<=set(controller.DIAGNOSTIC_RECORDS)|{'diagnostic-status.json'})
                    self.assertFalse(any('cache.tar' in p.name or p.suffix=='.env' for p in files))
                    self.assertNotIn(b'dummy-secret-never-copy',b''.join(p.read_bytes() for p in files))
                    self.assertFalse(controller.read(state/'diagnostics/diagnostic-status.json')['qualified'])


class PhasedReuse(unittest.TestCase):
    def pending(self,directory):
        root,state,inputs=precoverage_fixture(directory)
        prepared=controller.read(state/'prepared.json');prepared['test_inputs']='PENDING';controller.store(state/'prepared.json',prepared)
        envelope=directory/'symphony-trusted-coverage-v1';envelope.mkdir(mode=0o700)
        controller.store(envelope/'trusted-source.json',dict(controls={},trusted_dev_revision='b'*40),new=True)
        controller.store(state/'warm.json',dict(mode='RESTORED',consumer_eligible=True))
        return root,state,inputs

    def test_restored_consumer_preparation_keeps_test_cache_untouched_before_hit_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory);root=outer/'source';root.mkdir();state=outer/'state';state.mkdir(mode=0o700)
            controller.store(state/'warm.json',dict(mode='RESTORED',consumer_eligible=True),new=True)
            controller.store(outer/'selection',dict(source='a'*40,supported=True),new=True)
            calls=[]
            with patch.object(controller,'paths',return_value=(root,outer,'a'*40,state,outer/'selection')),patch.object(controller,'phase_run',side_effect=lambda *args,**kwargs:calls.append((args[2],kwargs)) or 1),patch.object(controller,'compile_test_inputs') as compile_test,patch.object(controller,'summary'):
                controller.prepare()
            compile_test.assert_not_called();self.assertEqual([name for name,_ in calls],['prepare','offline-resolution'])
            self.assertEqual(controller.read(state/'prepared.json')['test_inputs'],'PENDING')
            self.assertEqual(calls[0][1],dict(mix='dev',network=True));self.assertEqual(calls[1][1],dict(mix='dev'))

    def test_verified_hit_retains_test_inputs_without_compile_or_manifest_init(self):
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory);root,state,inputs=self.pending(outer);outputs=[]
            original=(state/'test-cache.tar.gz').read_bytes()
            with patch.object(controller,'paths',return_value=(root,outer,'a'*40,state,outer/'selection')),patch.object(controller,'client'),patch.object(controller,'context',return_value=dict(eligible_consumer=True,trusted_dev_revision='b'*40,repository_id=77)),patch.object(controller,'immutable_tree',return_value=({},{})),patch.object(controller,'tree_controls',return_value={}),patch.object(controller.profile,'fingerprint',return_value=inputs),patch.object(controller,'merged_pr_proof',return_value=dict(mode='REUSED',original_run_url='https://github.com/Jayson-Stangel/symphony/actions/runs/101/attempts/2')),patch.object(controller,'compile_test_inputs') as compile_test,patch.object(controller,'checkpoint_start') as checkpoint,patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary'),patch.dict(os.environ,dict(GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/dev')):
                controller.decide()
            compile_test.assert_not_called();checkpoint.assert_not_called()
            self.assertEqual(controller.read(state/'prepared.json')['test_inputs'],'RETAINED_VERIFIED')
            self.assertEqual(outputs[0]['mode'],'REUSED');self.assertEqual((state/'test-cache.tar.gz').read_bytes(),original)

    def test_miss_compiles_then_replaces_exact_provisional_checkpoints_before_execution(self):
        import copy
        from validation_evidence import Fresh
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory);root,state,inputs=self.pending(outer);old=(state/'test-cache.tar.gz').read_bytes();calls=[]
            def compile_test(*args):
                calls.append('compile')
                (root/'elixir/_build/test/compiled').write_bytes(b'INERT newly prepared test compiler bytes')
            def fingerprint(*args,**kwargs):
                value=copy.deepcopy(inputs)
                value['identity']['public_inputs']=[controller.cache.identity(root/'elixir/_build/test',root),controller.cache.identity(state/'hex',root,private_root=state)]
                value['sha256']=controller.digest(value['identity']);value.update(files=sum(row['files'] for row in value['identity']['public_inputs']),bytes=sum(row['bytes'] for row in value['identity']['public_inputs']))
                return value
            with patch.object(controller,'paths',return_value=(root,outer,'a'*40,state,outer/'selection')),patch.object(controller,'client'),patch.object(controller,'context',return_value=dict(eligible_consumer=True,trusted_dev_revision='b'*40,repository_id=77)),patch.object(controller,'immutable_tree',return_value=({},{})),patch.object(controller,'tree_controls',return_value={}),patch.object(controller.profile,'fingerprint',side_effect=fingerprint),patch.object(controller,'merged_pr_proof',side_effect=Fresh('INERT complete input mismatch')),patch.object(controller,'compile_test_inputs',side_effect=compile_test),patch.object(controller,'output'),patch.object(controller,'summary'),patch.dict(os.environ,dict(GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/dev')):
                controller.decide()
            self.assertEqual(calls,['compile']);self.assertEqual(controller.read(state/'prepared.json')['test_inputs'],'FRESH')
            current=controller.read(state/'inputs.json');self.assertNotEqual(current['sha256'],inputs['sha256'])
            self.assertNotEqual((state/'test-cache.tar.gz').read_bytes(),old)
            controller.protected_precheckpoints(root,state,current)
            self.assertEqual(controller.read(state/'decision.json')['mode'],'EXECUTED')

    def test_provisional_replacement_refuses_symlink_without_touching_target(self):
        from validation_inputs import UnknownInput
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory);root,state,_=self.pending(outer);other=outer/'preserve';other.write_bytes(b'preserve unrelated bytes')
            path=state/'test-cache.tar.gz';path.unlink();path.symlink_to(other)
            with patch.object(controller.profile,'fingerprint') as fingerprint,self.assertRaises(UnknownInput):controller.checkpoint_start(root,'a'*40,state,replace=True)
            fingerprint.assert_not_called();self.assertEqual(other.read_bytes(),b'preserve unrelated bytes')


class ExistingJobPhases(unittest.TestCase):
    def test_unknown_source_preserves_ordinary_full_make_and_parent_settings_without_api_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'; state.mkdir(mode=0o700)
            calls, outputs = [], []
            def run(arguments, source, environment, **options):
                calls.append((arguments, environment))
                options['log'].write_bytes(b'ordinary full qualification output')
                return 123.0
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', return_value=dict(source='a'*40, ordinary=True)), \
                 patch.object(controller, 'run', side_effect=run), \
                 patch.object(controller, 'output', side_effect=lambda **value: outputs.append(value)), \
                 patch.object(controller, 'summary'), \
                 patch.dict(os.environ, dict(PATH='/ordinary/bin', HOME='/ordinary/home', ERL_FLAGS='existing-settings',
                                            GH_TOKEN='dummy-token', GITHUB_TOKEN='dummy-token'), clear=True):
                controller.execute()
            self.assertEqual(calls[0][0], ['make', 'elixir-ci'])
            self.assertEqual(calls[0][1], dict(PATH='/ordinary/bin', HOME='/ordinary/home', ERL_FLAGS='existing-settings'))
            self.assertEqual(outputs, [dict(mode='EXECUTED', origin='')])
            self.assertEqual(json.loads((state/'execution.json').read_text())['mode'], 'EXECUTED')

    def test_unknown_source_preparation_does_not_compile_in_private_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); state = root / 'state'
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', return_value=dict(source='a'*40, supported=False)), \
                 patch.object(controller.oci, 'environment') as private, \
                 patch.object(controller, 'run') as execute:
                controller.prepare()
            private.assert_not_called(); execute.assert_not_called()
            self.assertEqual(json.loads((state/'prepared.json').read_text()),
                             dict(source='a'*40, supported=False, ordinary=True))

    def test_supported_fresh_execution_keeps_ordinary_coverage_startup_and_actual_output_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root,state,inputs=precoverage_fixture(Path(directory))
            commands,outputs=[],[]
            def run(source,private,phase,arguments,**options):
                self.assertEqual(controller.read(state/'execution.json')['mode'],'EXECUTING')
                commands.append(arguments);options['log'].write_bytes(COVERAGE_FIXTURE)
                return 4.0
            with patch.object(controller,'paths',return_value=(root,state.parent,'a'*40,state,state/'selection')),patch.object(controller.profile,'fingerprint',return_value=inputs),patch.object(controller,'phase_run',side_effect=run),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.object(controller,'summary'):
                controller.execute()
            self.assertEqual(commands,[['mix','test','--cover','--seed','0','--max-failures','1']])
            self.assertEqual(outputs,[dict(mode='EXECUTED',origin='')])
            execution=controller.read(state/'execution.json')
            self.assertEqual(execution['start_inputs_sha256'],inputs['sha256'])
            self.assertEqual(set(execution['precoverage_checkpoints']),{'test','hex'})

    def test_dialyzer_stays_fresh_in_dev_environment_without_bypassing_hash_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); state=root/'state';state.mkdir()
            calls=[]
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')), patch.object(controller,'read',return_value=dict(ordinary=False)), patch.object(controller,'phase_run',side_effect=lambda *args,**kw: calls.append((args,kw)) or 1), patch.object(controller,'summary'):
                controller.dialyzer()
            self.assertEqual(calls[0][0][3],['make','dialyzer'])
            self.assertEqual(calls[0][1]['mix'],'dev')
            self.assertNotIn('--no-check',str(calls))

    def test_real_dependency_links_restore_exactly_and_failure_rolls_back_all_roots(self):
        import validation_cache
        from validation_inputs import UnknownInput
        for corrupted in (False,True):
            with self.subTest(corrupted=corrupted),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'source';root.mkdir();state=Path(directory)/'state';state.mkdir(mode=0o700)
                targets={'deps':root/'elixir/deps','hex':state/'hex','dev':root/'elixir/_build/dev','test':root/'elixir/_build/test'}
                private=targets['deps']/'phoenix/priv';private.mkdir(parents=True)
                (private/'payload').write_bytes(b'authenticated dependency bytes')
                for name in ('dev','test'):
                    path=targets[name]/'lib/phoenix';path.mkdir(parents=True)
                    (path/'priv').symlink_to('../../../../deps/phoenix/priv')
                    (path/'compiled').write_bytes(b'authenticated compiler bytes')
                (root/'elixir/mix.lock').write_text('  \"phoenix\": {:hex, :phoenix, \"1.0.0\"}\n')
                targets['hex'].mkdir();(targets['hex']/'cache.bin').write_bytes(b'original public raw registry timestamp')
                def options(name):return dict(private_root=state) if name=='hex' else {}
                raw={};meta={}
                for name,target in targets.items():raw[name],meta[name]=validation_cache.create(target,root,**options(name))
                expected={name:validation_cache.identity(path,root,**options(name)) for name,path in targets.items()}
                for path in targets.values():
                    import shutil
                    shutil.rmtree(path);path.mkdir()
                    (path/'fresh').write_bytes(b'fresh fallback')
                fallback={name:validation_cache.identity(path,root,**options(name)) for name,path in targets.items()}
                if corrupted:meta['test']['cache']['sha256']='0'*64
                if corrupted:
                    with self.assertRaises(UnknownInput):controller.install_bundle(root,state,dict(sidecar_bytes=raw,sidecar=meta))
                else:controller.install_bundle(root,state,dict(sidecar_bytes=raw,sidecar=meta))
                for name,path in targets.items():
                    self.assertEqual(validation_cache.identity(path,root,**options(name)),fallback[name] if corrupted else expected[name])
                    self.assertFalse(path.with_name('.'+path.name+'-evidence-v2').exists())
                    self.assertFalse(path.with_name('.'+path.name+'-fresh-v2').exists())
                if not corrupted:
                    self.assertEqual((targets['test']/'lib/phoenix/priv/payload').read_bytes(),b'authenticated dependency bytes')

    def test_seal_refusal_names_first_safe_stage_without_environment_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'state';state.mkdir(mode=0o700)
            controller.store(state/'prepared.json',dict(source='a'*40,supported=False,ordinary=True),new=True)
            messages=[];outputs=[]
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'summary',side_effect=messages.append),patch.object(controller,'output',side_effect=lambda **value:outputs.append(value)),patch.dict(os.environ,dict(GH_TOKEN='dummy-secret-never-print')):
                controller.seal()
            self.assertEqual(outputs,[dict(available=False)])
            self.assertIn('stage=prepared_record',messages[0]);self.assertIn('unsupported original',messages[0])
            self.assertNotIn('dummy-secret-never-print',str(messages))

    def test_only_a_supported_profile_refuses_both_ordinary_executable_cache_restores(self):
        import re
        from test_components import job, workflow
        block=job(workflow('make-all'),'elixir-checks')
        mise=block.split('      - name: Set up mise tools\n',1)[1].split('      - name:',1)[0]
        compiled=block.split('      - name: Cache ordinary deps and build\n',1)[1].split('      - name:',1)[0]
        condition=re.search(r'^          cache: \$\{\{ (.*?) \}\}$',mise,re.M)[1]
        guard=re.search(r'^        if: (.*)$',compiled,re.M)[1]
        self.assertEqual(condition,guard)
        for supported,expected in (('true',False),('false',True),('',False),('unknown',False)):
            # This exact supported=false predicate drives both action cache
            # restore/save and the old compiled-cache restore boundary.
            observed=eval(condition.replace('steps.profile.outputs.supported',repr(supported)),{'__builtins__':{}})
            self.assertEqual(observed,expected)


    def test_actual_workflow_preserves_evidence_addresses_and_always_fresh_dialyzer(self):
        from test_components import job, workflow
        from validation_evidence import JOB, STEPS
        block = job(workflow('make-all'), 'elixir-checks')
        self.assertIn('    name: ' + JOB + '\n', block)
        for name in STEPS:
            self.assertEqual(block.count('      - name: ' + name + '\n'), 1, name)
        self.assertIn('      - name: Verify fresh Dialyzer\n        run:', block)
        self.assertNotIn('continue-on-error:', block)
        self.assertIn('steps.controller.outputs.trusted !=', block)
        self.assertIn('steps.controller.outputs.trusted ==', block)
        self.assertIn("      - name: Authenticate protected-origin cache before preparation\n        if: steps.profile.outputs.supported == 'true' && steps.controller.outputs.trusted == 'true'", block)
        self.assertIn("os.environ.get('GITHUB_REF') in ('refs/heads/dev','refs/heads/main')", block)
        self.assertIn("cache: ${{ steps.profile.outputs.supported == 'false' }}", block)
        self.assertIn("      - name: Cache ordinary deps and build\n        if: steps.profile.outputs.supported == 'false'", block)
        self.assertIn("key: ${{ runner.os }}-mix-${{ hashFiles('elixir/mix.lock') }}", block)
        self.assertIn('            ${{ runner.os }}-mix-', block)
        self.assertLess(block.index('Select audited coverage preparation'), block.index('Set up mise tools'))
        self.assertIn('retention-days: 7', block)


class OwnedOCIProtocol(unittest.TestCase):
    def test_native_preflight_program_is_valid_and_full_policy_has_no_resource_override(self):
        import ast
        oci=controller.oci
        ast.parse(oci.PREFLIGHT)
        _,args=oci.arguments(Path('/source'),Path('/private'),dict(owner='owned',image=dict(Id='sha256:'+'a'*64)),
                             'coverage',['mix','test'])
        self.assertIn('none',args)
        self.assertNotIn('--cpus',args);self.assertNotIn('--memory',args)
        self.assertIn('type=bind,src=/source,dst='+oci.SOURCE+',readonly',args)
        self.assertIn('type=bind,src=/source/elixir/log,dst='+oci.SOURCE+'/elixir/log',args)
        self.assertNotIn('/var/run/docker.sock',str(args))
        self.assertNotIn('GH_TOKEN',str(args));self.assertNotIn('--privileged',args)

    def test_actual_admission_rejects_mount_network_privilege_and_secret_drift(self):
        from copy import deepcopy
        from validation_inputs import UnknownInput
        oci=controller.oci;root=Path('/source');state=Path('/private')
        value=dict(owner='owned',image=dict(Id='sha256:'+'a'*64,Config=dict(Env=[])))
        expected={oci.SOURCE:(str(root),False)}
        expected.update({oci.SOURCE+'/'+name:(str(root/name),True) for name in oci.GENERATED})
        expected.update({path:(str(state/name),True) for name,path in oci.PRIVATE.items()})
        expected.update({'/etc/'+name:(str(state/'public'/name),False) for name in oci.PUBLIC})
        actual=dict(Image=value['image']['Id'],Config=dict(Labels={'com.symphony.ci.owner':'owned'},User='1001:1001',
            Env=[key+'='+val for key,val in oci.environment().items()],WorkingDir=oci.SOURCE+'/elixir'),
            HostConfig=dict(ReadonlyRootfs=True,Privileged=False,NetworkMode='none',CapDrop=['ALL'],SecurityOpt=['no-new-privileges']),
            Mounts=[dict(Destination=target,Source=source,RW=rw,Type='bind') for target,(source,rw) in expected.items()])
        self.assertEqual(oci.admit(actual,root,state,value,network=False)['network'],'none')
        for change in ('dns-rw','dns-source','dns-missing','dns-duplicate','network','privileged','source-rw','log-ro','extra-mount','secret','cpu-limit'):
            with self.subTest(change=change):
                wrong=deepcopy(actual)
                if change=='dns-rw':next(m for m in wrong['Mounts'] if m['Destination']=='/etc/resolv.conf')['RW']=True
                elif change=='dns-source':next(m for m in wrong['Mounts'] if m['Destination']=='/etc/resolv.conf')['Source']='/foreign/resolv.conf'
                elif change=='dns-missing':wrong['Mounts']=[m for m in wrong['Mounts'] if m['Destination']!='/etc/resolv.conf']
                elif change=='dns-duplicate':wrong['Mounts'].append(deepcopy(next(m for m in wrong['Mounts'] if m['Destination']=='/etc/resolv.conf')))
                elif change=='network':wrong['HostConfig']['NetworkMode']='bridge'
                elif change=='privileged':wrong['HostConfig']['Privileged']=True
                elif change=='source-rw':wrong['Mounts'][0]['RW']=True
                elif change=='log-ro':next(m for m in wrong['Mounts'] if m['Destination']==oci.SOURCE+'/elixir/log')['RW']=False
                elif change=='extra-mount':wrong['Mounts'].append(dict(Destination='/root',Source='/home/runner',RW=True,Type='bind'))
                elif change=='secret':wrong['Config']['Env'].append('GH_TOKEN=dummy-secret')
                else:wrong['HostConfig']['NanoCpus']=2000000000
                with self.assertRaises(UnknownInput):oci.admit(wrong,root,state,value,network=False)

    def test_public_resolver_rejects_missing_symlink_bytes_mode_and_owner(self):
        from validation_inputs import UnknownInput
        oci=controller.oci
        for kind in ('missing','symlink','bytes','mode','owner','valid'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                state=Path(directory);(state/'public').mkdir();path=state/'public/resolv.conf'
                if kind=='symlink':path.symlink_to(oci.PUBLIC['resolv.conf'])
                elif kind!='missing':
                    path.write_bytes(b'changed' if kind=='bytes' else oci.OFFLINE_RESOLVER)
                    path.chmod(0o600 if kind=='mode' else 0o644)
                if kind=='valid':self.assertEqual(oci.resolver_identity(state)['sha256'],controller.hashlib.sha256(oci.OFFLINE_RESOLVER).hexdigest())
                elif kind=='owner':
                    with patch.object(oci.os,'getuid',return_value=os.getuid()+1),self.assertRaises(UnknownInput):oci.resolver_identity(state)
                else:
                    with self.assertRaises((UnknownInput,FileNotFoundError)):oci.resolver_identity(state)

    def test_online_bridge_retains_docker_dns_and_refuses_offline_bind(self):
        oci=controller.oci
        value=dict(owner='owned',image=dict(Id='sha256:'+'a'*64))
        _,offline=oci.arguments(Path('/source'),Path('/private'),value,'preflight',['true'])
        _,online=oci.arguments(Path('/source'),Path('/private'),value,'prepare',['true'],network=True)
        resolver='type=bind,src=/private/public/resolv.conf,dst=/etc/resolv.conf,readonly'
        self.assertIn(resolver,offline);self.assertNotIn(resolver,online)
        self.assertIn('bridge',online);self.assertIn('none',offline)
        self.assertEqual(oci.PUBLIC['resolv.conf'].read_bytes(),b'nameserver 127.0.0.1\noptions attempts:1 timeout:1\n')

    def test_public_hex_registry_is_retained_for_early_offline_resolution(self):
        from validation_inputs import UnknownInput
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'state';state.mkdir(mode=0o700)
            for name in ('hex','mix','native_fetch'): (state/name).mkdir(mode=0o700)
            (state/'hex/cache.bin').write_bytes(b'public timestamped fetch metadata')
            (state/'mix/tool.beam').write_bytes(b'actual executable bytes')
            steps=[]
            def phase(source,private,name,*args,**kwargs):
                steps.append(name)
                if name=='offline-resolution':
                    self.assertEqual((state/'hex/cache.bin').read_bytes(),b'public timestamped fetch metadata')
                    self.assertEqual(args[0],['mix','deps.get']);self.assertEqual(kwargs,dict(mix='dev'))
                if name=='test-compile':self.assertIn('offline-resolution',steps)
                return 1
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=phase),patch.object(controller,'summary'):
                controller.prepare()
            self.assertEqual(steps,['prepare','offline-resolution','test-dependencies-online','test-compile','native-manifest'])
            self.assertEqual((state/'mix/tool.beam').read_bytes(),b'actual executable bytes')
            self.assertEqual(controller.oci.environment()['HEX_OFFLINE'],'1')
            self.assertNotIn('HEX_OFFLINE',controller.oci.environment(network=True))
            steps.clear()
            def unavailable(source,private,name,*args,**kwargs):
                steps.append(name)
                if name=='offline-resolution':raise UnknownInput('offline dependency registry unavailable')
                return 1
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=unavailable),patch.object(controller,'summary'),self.assertRaises(UnknownInput):
                controller.prepare()
            self.assertEqual(steps,['prepare','offline-resolution'])

    def test_online_test_dependencies_then_exact_fetch_cleanup_preserves_installed_output(self):
        from validation_inputs import UnknownInput
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'state';state.mkdir(mode=0o700)
            scratch=state/'native_fetch';scratch.mkdir(mode=0o700)
            installed=root/'elixir/_build/test/lib/lazy_html/priv/liblazy_html.so'
            installed.parent.mkdir(parents=True)
            steps=[]
            def phase(source,private,name,*args,**kwargs):
                steps.append(name)
                if name=='test-dependencies-online':
                    self.assertEqual(args[0],['mix','compile']);self.assertEqual(kwargs,dict(network=True))
                    (scratch/'lazy_html-nif-2.16-x86_64-linux-gnu-0.1.10.tar.gz').write_bytes(b'public download scratch')
                    installed.write_bytes(b'actual installed raw native bytes')
                if name=='test-compile':
                    self.assertNotIn('network',kwargs)
                    self.assertEqual(list(scratch.iterdir()),[])
                    self.assertEqual(installed.read_bytes(),b'actual installed raw native bytes')
                return 1
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=phase),patch.object(controller,'summary'):
                controller.prepare()
            self.assertEqual(steps,['prepare','offline-resolution','test-dependencies-online','test-compile','native-manifest'])
            self.assertEqual(controller.oci.environment()['ELIXIR_MAKE_CACHE_DIR'],'/cache/native-fetch')
            (scratch/'unknown').write_bytes(b'preserve unsupported input')
            with self.assertRaises(UnknownInput):controller.oci.clean_native_fetch(state)
            self.assertTrue((scratch/'unknown').is_file())
            with self.assertRaises(UnknownInput):controller.oci.require_empty_native_fetch(state)
            (scratch/'unknown').unlink();(scratch/'link').symlink_to(installed)
            with self.assertRaises(UnknownInput):controller.oci.clean_native_fetch(state)
            self.assertEqual(installed.read_bytes(),b'actual installed raw native bytes')
            (scratch/'link').unlink();steps.clear()
            def blocked(source,private,name,*args,**kwargs):
                result=phase(source,private,name,*args,**kwargs)
                if name=='test-compile':raise UnknownInput('offline installed outputs unavailable')
                return result
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=blocked),patch.object(controller,'summary'),self.assertRaises(UnknownInput):
                controller.prepare()
            self.assertEqual(steps,['prepare','offline-resolution','test-dependencies-online','test-compile'])
            self.assertEqual(installed.read_bytes(),b'actual installed raw native bytes')

    def test_default_log_is_owned_empty_initial_input_and_postexecution_output_is_not_deleted(self):
        from validation_inputs import UnknownInput
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'elixir/log';log.mkdir(parents=True)
            controller.oci.require_empty_log(root)
            output=log/'symphony.log';output.write_bytes(b'actual application output')
            with self.assertRaises(UnknownInput):controller.oci.require_empty_log(root)
            self.assertEqual(output.read_bytes(),b'actual application output')
            output.unlink();log.rmdir();other=root/'other';other.mkdir();log.symlink_to(other,target_is_directory=True)
            with self.assertRaises(UnknownInput):controller.oci.require_empty_log(root)

    def test_cleanup_entrypoint_handles_intent_without_completed_image_record(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);(state/'oci-intent.json').write_text('{}')
            with patch.object(controller,'paths',return_value=(state,state,'a'*40,state,state/'selection')), patch.object(controller.oci,'cleanup') as cleanup:
                controller.cleanup()
            cleanup.assert_called_once_with(state)

    def test_build_intent_exists_before_docker_and_public_modes_are_canonical(self):
        from validation_inputs import file_identity
        for incoming in (0o600,0o644):
            with self.subTest(incoming=incoming),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);state=root/'state';public={}
                for name in ('passwd','group'):
                    path=root/name;path.write_bytes(b'fixed public identity');path.chmod(incoming);public[name]=path
                def before_build(args,**kwargs):
                    self.assertEqual(args[:2],['buildx','build'])
                    self.assertTrue((state/'oci-intent.json').is_file())
                    for name in public:
                        actual=file_identity(state/'public'/name)
                        self.assertEqual(actual['mode'],0o644)
                        self.assertEqual((state/'public'/name).read_bytes(),b'fixed public identity')
                    raise ValueError('inert build refusal')
                with patch.object(controller.oci.os,'getuid',return_value=1001),patch.object(controller.oci.os,'getgid',return_value=1001),patch.object(controller.oci,'GENERATED',()),patch.object(controller.oci,'PUBLIC',public),patch.object(controller.oci,'docker',side_effect=before_build),self.assertRaisesRegex(ValueError,'inert build'):
                    controller.oci.initialize(root,state)
                self.assertFalse((state/'oci.json').exists())

    def test_cleanup_inspection_error_cannot_claim_resource_absence(self):
        from validation_inputs import UnknownInput
        with patch.object(controller.oci,'docker',side_effect=UnknownInput('unknown read')),self.assertRaises(UnknownInput):
            controller.oci.cleanup_phase('owned',dict(owner='owned',image=dict(Id='image')))

    def test_dev_push_warm_uses_actual_merged_origin_not_current_inprogress_history(self):
        root=Path('/inert');state=root/'state'
        with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(controls={},trusted_dev_revision='a'*40)),patch.object(controller,'immutable_tree',return_value=({},{})),patch.object(controller,'tree_controls',return_value={}),patch.object(controller,'client'),patch.object(controller,'context',return_value=dict(eligible_consumer=True,repository_id=77,trusted_dev_revision='a'*40)),patch.object(controller.oci,'preflight'),patch.object(controller.profile,'execution_identity',return_value=dict(actual=True)),patch.object(controller,'merged_pr_proof',side_effect=ValueError('inert admission stop')) as actual,patch.object(controller,'latest_proof') as history,patch.object(controller,'store'),patch.object(controller,'summary'),patch.dict(os.environ,dict(GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/dev')):
            controller.warm()
        actual.assert_called_once();history.assert_not_called()

    def test_protected_admission_precedes_prepare_and_cleanup_is_always(self):
        from test_components import job,workflow
        block=job(workflow('make-all'),'elixir-checks')
        self.assertLess(block.index('Load immutable protected-dev'),block.index('Authenticate protected-origin'))
        self.assertLess(block.index('Authenticate protected-origin'),block.index('Prepare controlled full'))
        self.assertIn("if: always() && steps.profile.outputs.supported == 'true'",block)
        source=Path(controller.__file__).read_text()
        self.assertLess(source.index('ExUnit.FailuresManifest.write!'),source.index('def measure():'))


if __name__ == '__main__':
    unittest.main()
