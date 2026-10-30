"""Credential-free local models only; never boot/register/run native guest jobs."""
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import persistent_mac_runner as runner
import mac_runner_request as routing
REAL_GUEST_ASSERT = runner.require_guest

REPO='Jayson-Stangel/symphony'
ROOT=Path(__file__).resolve().parents[3]


class Routing(unittest.TestCase):
    def setUp(self):
        self.env=dict(PERSISTENT_MAC_RUNNER=runner.NAME,GITHUB_REPOSITORY=REPO,
            GITHUB_ACTOR='Jayson-Stangel',GITHUB_TRIGGERING_ACTOR='Jayson-Stangel',
            GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/dev',GITHUB_SHA='a'*40,
            GITHUB_WORKFLOW_SHA='a'*40,GITHUB_WORKFLOW_REF=REPO+'/.github/workflows/make-all.yml@refs/heads/dev',
            GITHUB_RUN_ID='12',GITHUB_RUN_ATTEMPT='1')
    def select(self,event=None,consumer='.github/workflows/make-all.yml',key='platform-contracts',requested=True):
        return routing.persistent_runner(self.env,event or {},consumer,key,requested,verify=Mock())
    def test_actual_ordinary_targets_and_manual_reusable_targets(self):
        for flow,keys in routing.PERSISTENT_TARGETS.items():
            for key in keys:
                if key == 'packaged-release-platform':
                    continue  # Only the exact production-preparation caller admits this target.
                self.assertEqual(self.select(consumer=flow,key=key)['runner'],runner.NAME)
        self.env.update(GITHUB_REF='refs/heads/main',GITHUB_WORKFLOW_REF=REPO+'/.github/workflows/make-all.yml@refs/heads/main')
        self.assertEqual(self.select()['path'],'persistent')
        self.env['GITHUB_EVENT_NAME']='workflow_dispatch'
        self.assertEqual(self.select()['path'],'persistent')
    def test_existing_same_repository_pr_routes_and_external_exclusion(self):
        self.env.update(GITHUB_EVENT_NAME='pull_request',GITHUB_REF='refs/pull/12/merge',
                        GITHUB_WORKFLOW_REF=REPO+'/.github/workflows/make-all.yml@refs/pull/12/merge')
        event=dict(number=12,pull_request=dict(user=dict(login='Jayson-Stangel'),
            head=dict(ref='codex/feature',sha='b'*40,repo=dict(full_name=REPO)),
            base=dict(ref='dev',sha='c'*40,repo=dict(full_name=REPO))))
        self.assertEqual(self.select(event)['runner'],runner.NAME)
        event['pull_request']['head']['ref']='dev';event['pull_request']['base']['ref']='main'
        self.assertEqual(self.select(event)['runner'],runner.NAME)
        event['pull_request']['head']['repo']['full_name']='other/fork'
        self.assertIsNone(self.select(event))
        self.env['GITHUB_REPOSITORY']='other/project'
        self.assertIsNone(self.select(event))
    def test_no_capacity_for_omitted_work_or_production_or_signing(self):
        self.assertIsNone(self.select(requested=False))
        self.assertIsNone(self.select(consumer='.github/workflows/macos-local-signing-import.yml',key='verify'))
        self.assertIsNone(self.select(consumer='.github/workflows/runner-release.yml',key='packaged-release-platform'))
        for key in ('production_prepare','prepare_release','publish'):
            self.assertIsNone(self.select(dict(inputs={key:'true'})))
        self.env['PERSISTENT_MAC_RUNNER']=''
        self.assertIsNone(self.select())
        self.env['PERSISTENT_MAC_RUNNER']='foreign-label'
        with self.assertRaises(routing.Refusal):self.select()
    def test_cli_routes_without_status_wait_or_per_job_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            event=Path(d)/'event.json';event.write_text('{}')
            env=dict(self.env,GITHUB_EVENT_PATH=str(event),SELECT_WORKFLOW_PATH='.github/workflows/make-all.yml',
                     SELECT_JOB_KEY='platform-contracts')
            original=routing.persistent_runner
            def local(*a,**kw):return original(*a,**kw,verify=Mock())
            for args in ([],['--probe']):
                out=io.StringIO()
                with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['selector',*args]),                     patch.object(routing,'persistent_runner',side_effect=local),patch.object(routing,'read_statuses') as read,                     patch.object(routing,'select_owned_runner') as old,redirect_stdout(out),redirect_stderr(io.StringIO()):
                    self.assertEqual(routing.main(),0)
                    read.assert_not_called();old.assert_not_called()
                self.assertIn('persistent=true' if args else 'runner='+runner.NAME,out.getvalue())
    def test_real_workflow_selectors_keep_required_bodies_and_final_package_wiring(self):
        from test_components import job,workflow
        for name,key in (('make-all','mac-runner'),('package-checks','mac-runner'),
                         ('native-manager-helpers','mac-runner-helper'),('current-manager-package','mac-runner-package'),
                         ('runner-release','mac-runner-packaged-candidate'),('burrito-release','mac-runner-build'),
                         ('runner-release','mac-runner-packaged-release-platform')):
            block=job(workflow(name),key)
            self.assertEqual(block.count('PERSISTENT_MAC_RUNNER: ${{ vars.SYMPHONY_PERSISTENT_MAC_RUNNER }}'),2)
            self.assertIn('timeout-minutes: 8',block)
            self.assertIn('mac_runner_request.py --probe',block)
        self.assertIn('timeout-minutes: 5',job(workflow('make-all'),'platform-contracts'))
        self.assertIn('timeout-minutes: 30',job(workflow('package-checks'),'native-host-package'))
        self.assertNotIn('owned_mac_single_job',workflow('burrito-release'))


class ProductionRouting(unittest.TestCase):
    def setUp(self):
        self.env = dict(PERSISTENT_MAC_RUNNER=runner.NAME, GITHUB_REPOSITORY=REPO,
            GITHUB_ACTOR='Jayson-Stangel', GITHUB_TRIGGERING_ACTOR='Jayson-Stangel',
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
            GITHUB_SHA='a'*40, GITHUB_WORKFLOW_SHA='a'*40,
            GITHUB_WORKFLOW_REF=REPO+'/.github/workflows/burrito-release.yml@refs/heads/main',
            GITHUB_RUN_ID='12', GITHUB_RUN_ATTEMPT='2')
        self.event = dict(inputs=dict(production_prepare='true', packaged_host_preparation='true',
                                     packaged_host_revision='a'*40))
        self.verify = Mock()

    def select(self, consumer='.github/workflows/burrito-release.yml', key='build'):
        return routing.persistent_runner(self.env, self.event, consumer, key, verify=self.verify)

    def test_exact_preparation_reaches_all_actual_consumers_with_original_identity(self):
        for consumer, key in routing.PRODUCTION_PREPARE_TARGETS:
            with self.subTest(consumer=consumer, key=key):
                self.verify.reset_mock()
                self.assertEqual(self.select(consumer, key)['runner'], runner.NAME)
                identity = self.verify.call_args.args[0]
                self.assertEqual(identity['controller_workflow'], '.github/workflows/burrito-release.yml')
                self.assertEqual((identity['consumer_workflow'], identity['job_key']), (consumer, key))
                self.assertEqual((identity['source_sha'], identity['workflow_sha']), ('a'*40, 'a'*40))
                self.assertEqual((identity['run_id'], identity['run_attempt']), (12, 2))
        self.event['inputs']['production_prepare'] = True
        self.event['inputs']['packaged_host_preparation'] = True
        self.assertEqual(self.select()['path'], 'persistent')

    def test_other_consumers_events_refs_actors_and_publication_are_not_enabled(self):
        for consumer, key in (('.github/workflows/native-manager-helpers.yml', 'helper'),
                              ('.github/workflows/current-manager-package.yml', 'package'),
                              ('.github/workflows/macos-local-signing-import.yml', 'verify'),
                              ('.github/workflows/burrito-release.yml', 'controller-diagnostics'),
                              ('.github/workflows/make-all.yml', 'platform-contracts')):
            self.assertIsNone(self.select(consumer, key))
        for key, value in [('GITHUB_EVENT_NAME', 'push'), ('GITHUB_EVENT_NAME', 'pull_request'),
                           ('GITHUB_REF', 'refs/heads/dev'), ('GITHUB_REF', 'refs/tags/v0.0.7'),
                           ('GITHUB_ACTOR', 'other'), ('GITHUB_TRIGGERING_ACTOR', 'other'),
                           ('GITHUB_REPOSITORY', 'other/repo'),
                           ('GITHUB_WORKFLOW_REF', REPO+'/.github/workflows/runner-release.yml@refs/heads/main')]:
            with self.subTest(key=key, value=value), patch.dict(self.env, {key: value}):
                self.assertIsNone(self.select())
        for key in ('publish', 'prepare_release'):
            with patch.dict(self.event['inputs'], {key: 'true'}):
                self.assertIsNone(self.select())
        self.verify.assert_not_called()

    def test_malformed_eligible_identity_and_failed_live_checks_refuse(self):
        for key, value in [('packaged_host_revision', 'b'*40), ('packaged_host_revision', None),
                           ('packaged_host_preparation', 'false'), ('controller_only', 'true'),
                           ('engine_only', 'true'), ('bootstrap_previous', 'unexpected')]:
            with self.subTest(key=key), patch.dict(self.event['inputs'], {key: value}):
                with self.assertRaises(routing.Refusal): self.select()
        for key, value in [('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', 'broken'),
                           ('GITHUB_SHA', 'bad'), ('GITHUB_WORKFLOW_SHA', 'b'*40)]:
            with self.subTest(key=key), patch.dict(self.env, {key: value}):
                with self.assertRaises(routing.Refusal): self.select()
        self.verify.assert_not_called()
        self.verify.side_effect = routing.Refusal('live source differs')
        with self.assertRaisesRegex(routing.Refusal, 'live source differs'): self.select()

    def test_actual_cli_queues_without_probe_wait_and_reports_consumer(self):
        with tempfile.TemporaryDirectory() as d:
            event = Path(d)/'event.json'; event.write_text(json.dumps(self.event))
            summary = Path(d)/'summary.md'
            for consumer, key in routing.PRODUCTION_PREPARE_TARGETS:
                env = dict(self.env, GITHUB_EVENT_PATH=str(event), GITHUB_STEP_SUMMARY=str(summary),
                           SELECT_WORKFLOW_PATH=consumer, SELECT_JOB_KEY=key)
                original = routing.persistent_runner
                def local(*args, **kw): return original(*args, **kw, verify=self.verify)
                for args in (['--probe'], []):
                    out = io.StringIO()
                    with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['selector', *args]), \
                         patch.object(routing, 'persistent_runner', side_effect=local), \
                         patch.object(routing, 'read_statuses') as statuses, \
                         patch.object(routing, 'select_owned_runner') as fallback, \
                         redirect_stdout(out), redirect_stderr(io.StringIO()):
                        self.assertEqual(routing.main(), 0)
                        statuses.assert_not_called(); fallback.assert_not_called()
                    self.assertIn('persistent=true' if args else 'runner='+runner.NAME, out.getvalue())
                self.assertIn('consumer='+consumer+'; job='+key, summary.read_text())
            self.assertIn('readiness is not test qualification', summary.read_text())

    def test_disabled_configuration_keeps_bounded_fallback_not_execution_retry(self):
        self.env['PERSISTENT_MAC_RUNNER'] = ''
        self.assertIsNone(self.select())
        wait = Mock(return_value=dict(path='hosted', reason='offline', runner=None))
        result = routing.select_owned_runner('', self.env, self.event,
            '.github/workflows/runner-release.yml', 'packaged-release-platform',
            verify=self.verify, wait=wait)
        self.assertEqual((result['path'], result['runner']), ('hosted', 'macos-14'))
        wait.assert_called_once()
        wait.side_effect = routing.Refusal('owned preparation cleanup unverified; no hosted fallback')
        with self.assertRaises(routing.Refusal):
            routing.select_owned_runner('', self.env, self.event,
                '.github/workflows/runner-release.yml', 'packaged-release-platform', verify=self.verify, wait=wait)

    def test_real_call_graph_retains_source_guards_and_optional_candidate(self):
        from test_components import job, workflow
        engine = workflow('burrito-release'); packages = workflow('runner-release')
        self.assertIn('prepare_release: ${{ inputs.production_prepare }}', job(engine, 'runners'))
        self.assertIn('publish: false', job(engine, 'runners'))
        self.assertIn('needs: production-source', job(engine, 'qualification'))
        self.assertIn('test "$PACKAGED_HOST_REVISION" = "$GITHUB_SHA"', job(engine, 'production-source'))
        self.assertIn('uses: ./.github/workflows/package-checks.yml', job(packages, 'package-inputs'))
        final = job(packages, 'mac-runner-packaged-release-platform')
        self.assertIn('needs: publication-preflight', final)
        self.assertIn("needs.publication-preflight.result == 'success'", final)
        self.assertIn("needs.qualification.outputs.final_only == 'false'", job(packages, 'mac-runner-packaged-candidate'))
        for flow, target in [('burrito-release', 'build'), ('package-checks', 'native-host-package'),
                             ('runner-release', 'packaged-release-platform'), ('runner-release', 'packaged-candidate')]:
            block = job(workflow(flow), target)
            self.assertIn("matrix.platform == 'macos' && needs.mac-runner", block)
            self.assertIn('|| matrix.runner', block)


class NativeConfig(unittest.TestCase):
    def setUp(self):
        guest = patch.object(runner, 'require_guest', return_value=None)
        guest.start(); self.addCleanup(guest.stop)
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.home=Path(self.folder.name).resolve();self.root=self.home/'runner';self.root.mkdir()
        self.work=self.root/'_work';self.work.mkdir();(self.work/'_temp').mkdir()
        self.workspace=self.work/'symphony/symphony';self.workspace.mkdir(parents=True)
        plist=self.home/'Library/LaunchAgents/actions.runner.symphony.plist';plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps(dict(Label='actions.runner.symphony',ProgramArguments=[str(self.root/'runsvc.sh')],WorkingDirectory=str(self.root))))
        (self.root/'.service').write_text(str(plist))
        (self.root/'.runner').write_text(json.dumps(dict(gitHubUrl=runner.REPOSITORY,agentName=runner.NAME)))
        (self.root/'.env').write_text('TMPDIR='+str(self.work/'_temp')+'\nACTIONS_RUNNER_HOOK_JOB_COMPLETED='+str(self.root.parent/'persistent-mac-job-completed.sh')+'\n')
        self.env=dict(GITHUB_REPOSITORY=REPO,GITHUB_WORKSPACE=str(self.workspace),TMPDIR=str(self.work/'_temp'))
    def service(self):
        with patch.object(Path,'home',return_value=self.home):return runner.service(self.root)
    def test_host_or_wrong_os_cannot_stop_services_or_trigger_fallback_shutdown(self):
        for home,version in ((Path('/Users/jayson'),b'27.0'),(Path('/Users/admin'),b'27.0')):
            calls=[]
            def cmd(argv,**kw):
                calls.append(argv)
                return b'Darwin arm64' if argv[0]=='uname' else version
            with patch.object(Path,'home',return_value=home),patch.object(runner,'command',side_effect=cmd),\
                 patch.object(runner,'require_guest',side_effect=REAL_GUEST_ASSERT),patch.object(runner,'service') as service:
                for function,args in ((runner.clean_job,(self.env,self.root)),(runner.stop_service,(self.root,))):
                    with self.assertRaisesRegex(RuntimeError,'no host service/power effects'):function(*args)
                service.assert_not_called()
            self.assertFalse(any(c[0] in ('sudo','launchctl') for c in calls))

    def test_native_runner_utf8_bom_configuration_is_read_without_changing_scope(self):
        config=self.root/'.runner'
        config.write_bytes(bytes.fromhex('efbbbf')+config.read_bytes())
        self.assertIn('actions.runner.symphony',self.service()[0])

    def test_native_configuration_is_repository_only_not_shared(self):
        self.assertIn('actions.runner.symphony',self.service()[0])
        for config in (dict(gitHubUrl='https://github.com/other/repo',agentName=runner.NAME),
                       dict(gitHubUrl=runner.REPOSITORY,agentName='other-runner')):
            (self.root/'.runner').write_text(json.dumps(config))
            with self.assertRaises(RuntimeError):self.service()
    def test_guest_start_preserves_running_job_starts_loaded_inactive_and_refuses_disabled(self):
        target,plist=self.service();calls=[]
        def cmd(argv,**kw):
            calls.append([str(x) for x in argv])
            if argv[0]=='uname':return b'Darwin arm64\n'
            if argv[0]=='sw_vers':return b'14.8.7\n'
            if argv[0]=='/sbin/mount':return b'APFS on /'
            if argv[1]=='print-disabled':return b'{}'
            return b'state = running\npid = 123\n'
        for active in (True,False):
            calls.clear()
            status=subprocess.CompletedProcess([],0,b'pid = 99' if active else b'state = not running',b'')
            with patch.object(runner,'service',return_value=(target,plist)),patch.object(Path,'home',return_value=Path('/Users/admin')),                 patch.object(runner.sys,'version_info',(3,13)),patch.object(runner,'command',side_effect=cmd),                 patch.object(runner.subprocess,'run',return_value=status),redirect_stdout(io.StringIO()):runner.guest_start(self.root)
            kick=[c for c in calls if c[:2]==['launchctl','kickstart']]
            self.assertEqual(len(kick),0 if active else 1)
            self.assertFalse(any('-k' in c or 'enable' in c for c in calls))
        for representation in (b'true',b'disabled'):
            def disabled(argv,**kw):
                return b'{"actions.runner.symphony" => '+representation+b'}' if argv[:2]==['launchctl','print-disabled'] else cmd(argv,**kw)
            with patch.object(runner,'service',return_value=(target,plist)),patch.object(Path,'home',return_value=Path('/Users/admin')),             patch.object(runner.sys,'version_info',(3,13)),patch.object(runner,'command',side_effect=disabled),             patch.object(runner.subprocess,'run') as print_service,self.assertRaises(RuntimeError):runner.guest_start(self.root)
            print_service.assert_not_called()

    def test_actual_tempfile_dmg_and_checkout_credentials_clean_without_touching_foreign_mount(self):
        with patch.dict(os.environ,{'TMPDIR':str(self.work/'_temp')}),patch.object(tempfile,'tempdir',None):
            with tempfile.TemporaryDirectory() as actual_temp:
                image=Path(actual_temp)/'fixture.dmg';image.write_bytes(b'inert fixture')
                self.assertTrue(image.is_relative_to(self.work/'_temp'))
                images={'images':[{'image-path':str(image),'system-entities':[{'mount-point':'/Volumes/fixture','dev-entry':'/dev/disk9s1'}]},
                                  {'image-path':'/foreign/image.dmg','system-entities':[{'mount-point':'/Volumes/foreign','dev-entry':'/dev/disk8s1'}]}]}
                secret=self.workspace/'.git/config';secret.parent.mkdir();secret.write_text('temporary fixture credential')
                calls=[]
                def cmd(argv,**kw):
                    calls.append(argv)
                    return plistlib.dumps(images) if argv[:2]==['hdiutil','info'] else b''
                with patch.object(runner,'service',return_value=self.service()),patch.object(runner,'command',side_effect=cmd):runner.clean_job(self.env,self.root)
                self.assertIn(['hdiutil','detach','/dev/disk9s1'],calls)
                self.assertNotIn(['hdiutil','detach','/dev/disk8s1'],calls)
                self.assertFalse(self.workspace.exists());self.assertTrue(image.exists())  # runner removes temp after uploads
    def test_two_inert_jobs_reuse_service_with_completed_hook_between_payloads(self):
        target=self.service();trace=[]
        with patch.object(runner,'service',return_value=target),patch.object(runner,'command',return_value=plistlib.dumps({'images':[]})),             patch.object(runner,'stop_service') as stop:
            for number in (1,2):  # model the inspected native one-job queue, not a product scheduler
                if number==2:self.assertFalse(self.workspace.exists())
                self.workspace.mkdir(parents=True,exist_ok=True)
                subprocess.run([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).write_text("job")',str(self.workspace/'payload')],check=True)
                trace.append(number);runner.clean_job(self.env,self.root)
            stop.assert_not_called()
        self.assertEqual(trace,[1,2]);self.assertTrue((self.root/'.runner').exists())
    def test_observed_cleanup_failure_disables_and_stops_accepting_service_before_second_payload(self):
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        calls=[]
        def cmd(argv,**kw):
            calls.append(argv)
            if argv[:2]==['hdiutil','info']:return plistlib.dumps({'images':[]})
            if argv[:2]==['launchctl','bootout']:
                process.terminate();process.wait(timeout=5)
            return b''
        try:
            with patch.object(runner,'service',return_value=self.service()),patch.object(runner,'command',side_effect=cmd),                 patch.object(runner.shutil,'rmtree',side_effect=PermissionError('inert unsafe cleanup')),redirect_stderr(io.StringIO()),self.assertRaises(PermissionError):runner.clean_job(self.env,self.root)
            self.assertLess(next(i for i,c in enumerate(calls) if c[:2]==['launchctl','disable']),next(i for i,c in enumerate(calls) if c[:2]==['launchctl','bootout']))
            self.assertIsNotNone(process.poll())  # no accepting native service in this inert local model
        finally:
            if process.poll() is None:process.terminate()
            process.wait(timeout=5)
    def test_unsafe_temp_workspace_or_dmg_detach_failure_stops_service(self):
        for env in (dict(self.env,TMPDIR='/foreign/tmp'),dict(self.env,GITHUB_WORKSPACE='/foreign/source')):
            with patch.object(runner,'service',return_value=self.service()),patch.object(runner,'stop_service') as stop,self.assertRaises(RuntimeError):runner.clean_job(env,self.root)
            stop.assert_called_once_with(self.root)
        image=self.work/'_temp/leftover.dmg'
        info=plistlib.dumps({'images':[{'image-path':str(image),'system-entities':[{'mount-point':'/Volumes/stale','dev-entry':'/dev/disk9s1'}]}]})
        with patch.object(runner,'service',return_value=self.service()),patch.object(runner,'command',side_effect=[info,subprocess.CalledProcessError(1,'hdiutil')]),             patch.object(runner,'stop_service') as stop,self.assertRaises(subprocess.CalledProcessError):runner.clean_job(self.env,self.root)
        stop.assert_called_once_with(self.root)
    def test_canceled_job_stale_workspace_can_be_cleaned_by_normal_completed_hook(self):
        (self.workspace/'cancelled').write_text('partial job')
        with patch.object(runner,'service',return_value=self.service()),patch.object(runner,'command',return_value=plistlib.dumps({'images':[]})):
            runner.clean_job(self.env,self.root)
        self.assertFalse(self.workspace.exists())

    def test_uncertain_signing_metadata_enters_existing_fail_closed_hook(self):
        private = self.work / '_temp/symphony-signing-interrupted'
        private.mkdir(mode=0o700)
        (private / 'identity.p12').write_bytes(b'INERT P12')
        with patch.object(runner, 'service', return_value=self.service()), \
                patch.object(runner, 'command') as native, patch.object(runner, 'stop_service') as stop, \
                redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'lack recovery'):
            runner.clean_job(self.env, self.root)
        stop.assert_called_once_with(self.root)
        native.assert_not_called()
        self.assertTrue(self.workspace.exists())


class SigningRecovery(unittest.TestCase):
    """Real private files, inert native Security model; no guest activation."""
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.temporary = Path(self.scratch.name).resolve()
        self.directory = self.temporary / 'symphony-signing-fixture'
        self.directory.mkdir(mode=0o700)
        self.record = self.directory / '.keychain-cleanup.json'
        self.keychain = self.directory / 'signing.keychain-db'
        self.p12 = self.directory / 'identity.p12'
        self.baseline = ['/original/login.keychain-db', '/original/with spaces.keychain-db']
        self.write_record()
        self.keychain.write_bytes(b'INERT KEYCHAIN')
        self.p12.write_bytes(b'INERT P12')
        self.observed = [str(self.keychain), *self.baseline]
        self.calls = []

    def write_record(self, value=None):
        self.record.write_text(json.dumps(value or {'schema': 1, 'search_list': self.baseline}))
        self.record.chmod(0o600)

    def security(self, args, **kwargs):
        self.calls.append(args)
        self.assertEqual(args[0], '/usr/bin/security')
        if args[1] == 'delete-keychain':
            self.assertEqual(args[2], self.keychain)
            self.keychain.unlink()
        elif '-s' in args:
            self.observed = args[args.index('-s') + 1:]
        else:
            return ('\n'.join(json.dumps(p) for p in self.observed)).encode()
        return b''

    def clean(self):
        with patch.object(runner, 'command', side_effect=self.security):
            runner.clean_signing_credentials(self.temporary)

    def test_interrupted_import_reconciles_only_owned_objects_and_is_idempotent(self):
        foreign = self.temporary / 'unrelated.keychain-db'
        foreign.write_bytes(b'PRESERVE')
        self.clean()
        self.assertEqual(self.observed, self.baseline)
        self.assertFalse(any(p.exists() for p in (self.record, self.keychain, self.p12)))
        self.assertEqual(foreign.read_bytes(), b'PRESERVE')
        self.calls.clear(); self.clean()
        self.assertEqual(self.calls, [])

    def test_before_create_and_after_native_delete_states_both_reconcile(self):
        self.keychain.unlink(); self.p12.unlink(); self.observed = list(self.baseline)
        self.clean()
        self.assertFalse(self.record.exists())
        self.write_record(); self.observed = [str(self.keychain), *self.baseline]
        self.clean()
        self.assertEqual(self.observed, self.baseline)

    def test_native_delete_may_remove_its_own_search_list_entry(self):
        original = self.security
        def removes_entry(args, **kwargs):
            result = original(args, **kwargs)
            if args[1] == 'delete-keychain':
                self.observed = list(self.baseline)
            return result
        with patch.object(runner, 'command', side_effect=removes_entry):
            runner.clean_signing_credentials(self.temporary)
        self.assertFalse(any('-s' in c for c in self.calls))

    def test_foreign_or_corrupt_metadata_refuses_before_any_native_mutation(self):
        for value in ({'schema': 2, 'search_list': self.baseline},
                      {'schema': 1, 'search_list': self.baseline, 'delete': '/foreign'},
                      {'schema': 1, 'search_list': [str(self.keychain)]},
                      {'schema': 1, 'search_list': ['/duplicate', '/duplicate']},
                      {'schema': 1, 'search_list': ['relative']},
                      {'schema': 1, 'search_list': [None]}):
            with self.subTest(value=value):
                self.write_record(value)
                with self.assertRaises(RuntimeError): self.clean()
                self.assertEqual(self.calls, [])
                self.assertTrue(self.p12.exists())
        self.record.write_text('{broken')
        with self.assertRaises(ValueError): self.clean()
        self.assertEqual(self.calls, [])

    def test_missing_record_symlinks_hardlinks_and_permissions_refuse(self):
        self.record.unlink()
        with self.assertRaisesRegex(RuntimeError, 'lack recovery'): self.clean()
        self.write_record(); self.record.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'unsafe signing recovery record'): self.clean()
        self.record.unlink(); self.record.symlink_to(self.p12)
        with self.assertRaises(RuntimeError): self.clean()
        self.record.unlink(); self.write_record()
        self.keychain.unlink(); self.keychain.symlink_to(self.p12)
        with self.assertRaises(RuntimeError): self.clean()
        self.keychain.unlink(); os.link(self.p12, self.keychain)
        with self.assertRaises(RuntimeError): self.clean()
        self.assertEqual(self.calls, [])

    def test_metadata_cannot_add_unobserved_search_list_entries(self):
        self.write_record({'schema': 1, 'search_list': ['/foreign/added.keychain-db']})
        with self.assertRaisesRegex(RuntimeError, 'search list changed'): self.clean()
        self.assertEqual(len(self.calls), 1)  # Read only; no delete/restore.
        self.assertTrue(self.keychain.exists()); self.assertTrue(self.p12.exists())

    def test_multiple_markers_are_all_validated_before_mutation(self):
        other = self.temporary / 'symphony-signing-other'; other.mkdir(mode=0o700)
        record = other / self.record.name
        record.write_text(self.record.read_text()); record.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'multiple unresolved'): self.clean()
        self.assertEqual(self.calls, [])

    def test_failed_restore_preserves_record_for_idempotent_reconciliation(self):
        original = self.security
        def fail_restore(args, **kwargs):
            if '-s' in args: raise RuntimeError('inert restore failure')
            return original(args, **kwargs)
        with patch.object(runner, 'command', side_effect=fail_restore):
            with self.assertRaisesRegex(RuntimeError, 'inert restore failure'):
                runner.clean_signing_credentials(self.temporary)
        self.assertTrue(self.record.exists()); self.assertFalse(self.keychain.exists())
        self.clean()
        self.assertFalse(self.record.exists()); self.assertEqual(self.observed, self.baseline)


class VMStartup(unittest.TestCase):
    def test_off_starts_once_up_reuses_and_transport_boot_wait_does_not_repeat_guest_start(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d).resolve();(home/'vms'/runner.VM).mkdir(parents=True);ticks=[0]
            for running in (False,True):
                commands=[];probes=[0]
                def cmd(argv,**kw):
                    commands.append(argv)
                    if argv[1]=='get':return json.dumps(dict(MACAddress='aa:bb:cc:dd:ee:ff',Running=running)).encode()
                    if argv[-1]=='/usr/bin/true':
                        probes[0]+=1
                        if probes[0]<3:raise subprocess.CalledProcessError(1,argv)
                    return b''
                with patch.object(runner,'PERSISTENT_HOME',home),patch.object(runner,'command',side_effect=cmd),                     patch.object(runner.subprocess,'Popen') as launch,redirect_stdout(io.StringIO()):
                    runner.start_vm(Path('/inert/tart'),'aa:bb:cc:dd:ee:ff',home,clock=lambda:ticks[0],pause=lambda n:ticks.__setitem__(0,ticks[0]+n))
                self.assertEqual(launch.call_count,0 if running else 1)
                if not running:
                    self.assertIn('--no-clipboard',launch.call_args.args[0]);self.assertNotIn('--dir',launch.call_args.args[0])
                starts=[c for c in commands if c[-1]=='guest-start']
                self.assertEqual(len(starts),1);self.assertIn(str(runner.PYTHON),starts[0])
    def test_foreign_identity_and_guest_refusal_never_reset_or_retry_service(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d).resolve();(home/'vms'/runner.VM).mkdir(parents=True)
            for foreign in (True,False):
                calls=[]
                def cmd(argv,**kw):
                    calls.append(argv)
                    if argv[1]=='get':return json.dumps(dict(MACAddress='11:22:33:44:55:66' if foreign else 'aa:bb:cc:dd:ee:ff',Running=True)).encode()
                    if argv[-1]=='guest-start':raise subprocess.CalledProcessError(1,argv)
                    return b''
                with patch.object(runner,'PERSISTENT_HOME',home),patch.object(runner,'command',side_effect=cmd),                     patch.object(runner.subprocess,'Popen') as launch,self.assertRaises((RuntimeError,subprocess.CalledProcessError)):
                    runner.start_vm(Path('/inert/tart'),'aa:bb:cc:dd:ee:ff',home)
                launch.assert_not_called();self.assertLessEqual(sum(c[-1]=='guest-start' for c in calls),1)


if __name__=='__main__':unittest.main()
