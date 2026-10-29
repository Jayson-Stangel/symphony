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
            root = Path(directory); state = root / 'state'; state.mkdir(mode=0o700)
            commands, outputs = [], []
            def read(path):
                if path.name == 'prepared.json':
                    return dict(source='a'*40, ordinary=False)
                return dict(MIX_ENV='test', PATH='/fixed/toolchain:/usr/bin:/bin')
            def run(source, state, phase, arguments, **options):
                commands.append(arguments); options['log'].write_bytes(b'full suite output')
                return 340.2
            with patch.object(controller, 'paths', return_value=(root, root, 'a'*40, state, root/'selection')), \
                 patch.object(controller, 'read', side_effect=read), patch.object(controller, 'phase_run', side_effect=run), \
                 patch.object(controller, 'output', side_effect=lambda **value: outputs.append(value)), \
                 patch.object(controller, 'summary'):
                controller.execute()
            self.assertEqual(commands, [['mix', 'test', '--cover', '--seed', '0', '--max-failures', '1']])
            self.assertNotIn('--no-compile', commands[0])
            self.assertEqual(outputs, [dict(mode='EXECUTED', origin='')])

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
                root=Path(directory)
                targets={'deps':root/'elixir/deps','dev':root/'elixir/_build/dev','test':root/'elixir/_build/test'}
                private=targets['deps']/'phoenix/priv';private.mkdir(parents=True)
                (private/'payload').write_bytes(b'authenticated dependency bytes')
                for name in ('dev','test'):
                    path=targets[name]/'lib/phoenix';path.mkdir(parents=True)
                    (path/'priv').symlink_to('../../../../deps/phoenix/priv')
                    (path/'compiled').write_bytes(b'authenticated compiler bytes')
                raw={};meta={}
                for name,target in targets.items():raw[name],meta[name]=validation_cache.create(target,root)
                expected={name:validation_cache.identity(path,root) for name,path in targets.items()}
                for path in targets.values():
                    import shutil
                    shutil.rmtree(path);path.mkdir()
                    (path/'fresh').write_bytes(b'fresh fallback')
                fallback={name:validation_cache.identity(path,root) for name,path in targets.items()}
                if corrupted:meta['test']['cache']['sha256']='0'*64
                if corrupted:
                    with self.assertRaises(UnknownInput):controller.install_bundle(root,root,dict(sidecar_bytes=raw,sidecar=meta))
                else:controller.install_bundle(root,root,dict(sidecar_bytes=raw,sidecar=meta))
                for name,path in targets.items():
                    self.assertEqual(validation_cache.identity(path,root),fallback[name] if corrupted else expected[name])
                    self.assertFalse(path.with_name('.'+path.name+'-evidence-v2').exists())
                    self.assertFalse(path.with_name('.'+path.name+'-fresh-v2').exists())
                if not corrupted:
                    self.assertEqual((targets['test']/'lib/phoenix/priv/payload').read_bytes(),b'authenticated dependency bytes')

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
        for change in ('network','privileged','source-rw','extra-mount','secret','cpu-limit'):
            with self.subTest(change=change):
                wrong=deepcopy(actual)
                if change=='network':wrong['HostConfig']['NetworkMode']='bridge'
                elif change=='privileged':wrong['HostConfig']['Privileged']=True
                elif change=='source-rw':wrong['Mounts'][0]['RW']=True
                elif change=='extra-mount':wrong['Mounts'].append(dict(Destination='/root',Source='/home/runner',RW=True,Type='bind'))
                elif change=='secret':wrong['Config']['Env'].append('GH_TOKEN=dummy-secret')
                else:wrong['HostConfig']['NanoCpus']=2000000000
                with self.assertRaises(UnknownInput):oci.admit(wrong,root,state,value,network=False)

    def test_owned_hex_fetch_scratch_is_recreated_before_any_offline_compilation(self):
        from validation_inputs import UnknownInput
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'state';state.mkdir()
            for name in ('hex','mix'): (state/name).mkdir()
            (state/'hex/cache.bin').write_bytes(b'public timestamped fetch metadata')
            (state/'mix/tool.beam').write_bytes(b'actual executable bytes')
            steps=[]
            def phase(source,private,name,*args,**kwargs):
                steps.append(name)
                if name=='test-compile':self.assertEqual(list((state/'hex').iterdir()),[])
                return 1
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=phase),patch.object(controller,'summary'):
                controller.prepare()
            self.assertEqual(steps,['prepare','test-compile','native-manifest'])
            self.assertEqual((state/'mix/tool.beam').read_bytes(),b'actual executable bytes')
            self.assertEqual(controller.oci.environment()['HEX_OFFLINE'],'1')
            self.assertNotIn('HEX_OFFLINE',controller.oci.environment(network=True))
            (state/'hex').rmdir();(state/'hex').symlink_to(state/'mix',target_is_directory=True)
            with self.assertRaises(UnknownInput):controller.oci.clean_hex(state)
            self.assertEqual((state/'mix/tool.beam').read_bytes(),b'actual executable bytes')
            steps.clear()
            with patch.object(controller,'paths',return_value=(root,root,'a'*40,state,root/'selection')),patch.object(controller,'read',return_value=dict(source='a'*40,supported=True)),patch.object(controller,'phase_run',side_effect=phase),patch.object(controller,'summary'),self.assertRaises(UnknownInput):
                controller.prepare()
            self.assertEqual(steps,['prepare'])

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
