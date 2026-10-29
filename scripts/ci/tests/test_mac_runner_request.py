"""Actual source guards are mocked; these fixtures grant no live readiness."""
from pathlib import Path
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mac_runner_request as request

FLOW = '.github/workflows/package-checks.yml'
REPO = request.REPOSITORY

class Requests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_REPOSITORY=REPO, GITHUB_ACTOR='Jayson-Stangel',
            GITHUB_TRIGGERING_ACTOR='Jayson-Stangel', GITHUB_EVENT_NAME='pull_request',
            GITHUB_REF='refs/pull/156/merge', GITHUB_SHA='a'*40, GITHUB_WORKFLOW_SHA='a'*40,
            GITHUB_WORKFLOW_REF=REPO+'/'+FLOW+'@refs/pull/156/merge', GITHUB_RUN_ID='12', GITHUB_RUN_ATTEMPT='1')
        self.event = dict(number=156, pull_request=dict(user=dict(login='Jayson-Stangel'),
            head=dict(ref='codex/example',sha='b'*40,repo=dict(full_name=REPO)),
            base=dict(ref='dev',sha='c'*40,repo=dict(full_name=REPO))))
        self.verify = Mock()

    def select(self):
        return request.request_from_event(self.env,self.event,FLOW,'native-host-package',self.verify)

    def test_exact_first_attempt_pr_binds_merge_head_base_and_live_verifier(self):
        value, reason = self.select()
        self.assertEqual((value['source_sha'],value['head_sha'],value['base_sha']),('a'*40,'b'*40,'c'*40))
        self.assertEqual(value['run_attempt'],1)
        self.verify.assert_called_once()
        self.assertNotIn('targets',self.verify.call_args.args[0])
        self.assertNotIn('runner_label',value)

    def test_foreign_fork_tag_and_unsupported_controller_use_visible_ineligibility(self):
        self.env['GITHUB_ACTOR']='foreign'
        self.assertIsNone(self.select()[0]);self.verify.assert_not_called()
        self.env['GITHUB_ACTOR']='Jayson-Stangel'
        self.event['pull_request']['head']['repo']['full_name']='foreign/repo'
        self.assertIsNone(self.select()[0]);self.verify.assert_not_called()
        self.env.update(GITHUB_EVENT_NAME='push',GITHUB_REF='refs/tags/v1')
        self.assertIsNone(self.select()[0]);self.verify.assert_not_called()

    def test_corrupt_eligible_source_and_run_fail_closed_before_live_verification(self):
        for key,value in [('GITHUB_RUN_ID','0'),('GITHUB_RUN_ATTEMPT','1.0'),('GITHUB_SHA','wrong'),
                          ('GITHUB_WORKFLOW_REF',REPO+'/'+FLOW+'@refs/heads/other')]:
            old=self.env[key];self.env[key]=value
            with self.subTest(key=key),self.assertRaises(request.Refusal):self.select()
            self.env[key]=old
        self.verify.assert_not_called()

    def test_actual_live_guard_failure_cannot_become_hosted_unavailability(self):
        self.verify.side_effect=request.Refusal('actual source differs')
        with self.assertRaises(request.Refusal):self.select()

    def test_status_probe_only_gets_exact_source_bounded_pages_and_refuses_overflow(self):
        read=Mock(return_value=[])
        self.assertEqual(request.read_statuses('a'*40,read),[])
        read.assert_called_once_with('repos/'+REPO+'/commits/'+'a'*40+'/statuses?per_page=100&page=1')
        read=Mock(return_value=[{}]*100)
        with self.assertRaises(request.Refusal):request.read_statuses('a'*40,read)
        self.assertEqual(read.call_count,2)

    def test_unrequested_mac_never_waits_or_reads_even_on_linux_only_event(self):
        statuses=Mock();wait=Mock()
        value=request.select_owned_runner('',{}, {},FLOW,'native-host-package',False,
            verify=self.verify,statuses=statuses,wait=wait)
        self.assertEqual(value['runner'],'macos-14')
        self.verify.assert_not_called();statuses.assert_not_called();wait.assert_not_called()

    def test_owned_first_waits_on_first_attempt_without_permit_and_preserves_status_id(self):
        wait=Mock(return_value=dict(path='owned',runner='symphony-backup-mac-'+'d'*32,reason='prepared owned capacity',original_status_id=123))
        value=request.select_owned_runner('',self.env,self.event,FLOW,'native-host-package',
            verify=self.verify,wait=wait)
        self.assertEqual(value['original_status_id'],123)
        self.assertEqual(value['path'],'owned');wait.assert_called_once();self.verify.assert_called_once()

    def test_unavailable_is_hosted_but_failed_owned_test_is_not_a_selection_input(self):
        wait=Mock(return_value=dict(path='hosted',runner=None,reason='busy',original_status_id=123))
        value=request.select_owned_runner('',self.env,self.event,FLOW,'native-host-package',verify=self.verify,wait=wait)
        self.assertEqual((value['path'],value['runner'],value['reason']),('hosted','macos-14','busy'))
        # The selector consumes capacity replies only; no check-result/failure
        # parameter exists that could trigger duplicate hosted execution.

    def test_matching_malformed_permit_refuses_without_masking_as_handoff(self):
        wait=Mock()
        with self.assertRaises(request.Refusal):
            request.select_owned_runner('{"run_id":12}',self.env,self.event,FLOW,'native-host-package',verify=self.verify,wait=wait)
        wait.assert_not_called()

    def test_retired_native_and_smoke_targets_cannot_request_owned_capacity(self):
        for consumer, key in (('.github/workflows/release-native.yml', 'native'),
                              ('.github/workflows/burrito-release.yml', 'smoke')):
            with self.subTest(consumer=consumer, key=key):
                self.verify.reset_mock()
                value, reason = request.request_from_event(self.env, self.event, consumer, key, self.verify)
                self.assertIsNone(value)
                self.assertIn('unsupported', reason)
                self.verify.assert_not_called()

    def test_reusable_caller_permission_overrides_preserve_status_read(self):
        from test_components import job, workflow
        for caller in ('runner-integration','burrito-release'):
            text=workflow(caller);block=job(text,'runners')
            self.assertIn('uses: ./.github/workflows/runner-release.yml',block)
            self.assertIn('      statuses: read',block)
        for name in ('make-all','package-checks','native-manager-helpers','current-manager-package',
                     'runner-release','runner-integration','burrito-release'):
            self.assertIn('  statuses: read',workflow(name))

    def test_only_requested_mac_targets_wait_and_private_selection_budget_has_headroom(self):
        from test_components import job, workflow
        for name,flag in (('make-all','needs.changes.outputs.contracts'),('package-checks','needs.changes.outputs.package_macos')):
            block=job(workflow(name),'mac-runner')
            self.assertIn('SELECT_TARGET_REQUESTED:',block);self.assertIn(flag,block)
            self.assertIn('timeout-minutes: 8',block)
            self.assertIn('scripts/ci/mac_runner_request.py',block)
            self.assertNotIn("if: vars.SYMPHONY_BACKUP_MAC_PERMIT",block)

    def test_malformed_status_api_rows_refuse_instead_of_becoming_capacity_timeout(self):
        for rows in (['broken'],[None],{}):
            with self.assertRaises(request.Refusal):request.read_statuses('a'*40,Mock(return_value=rows))

    def test_real_caller_probe_is_a_separate_visible_step_before_wait(self):
        from test_components import job, workflow
        block=job(workflow('package-checks'),'mac-runner')
        self.assertLess(block.index('id: capacity-probe'),block.index('id: selected'))
        self.assertIn('mac_runner_request.py --probe',block)
        self.assertIn('SELECT_TARGET_REQUESTED:',block)

    def test_package_selector_omission_preserves_the_actual_required_join(self):
        from test_components import classifier, env_for, expression, job, scripts, workflow
        text=workflow('package-checks')
        selector=job(text,'mac-runner')
        condition=re.search(r'^    if: (.+)$',selector,re.M)[1]
        requested=re.search(r'^          SELECT_TARGET_REQUESTED: (.+)$',selector,re.M)[1]
        join='\n'.join(scripts(job(text,'package-qualification'))[:2])
        for path in ('README.md','scripts/release/graph_publication.py',
                     'scripts/package/linux/Package.resolved','scripts/package/host.swift',
                     'scripts/release/engine-smoke.py'):
            route=classifier.decisions([path])
            for ready in (True,False):
                host='success' if route['native'] and ready else 'skipped'
                values={'needs.changes.result':'success','needs.qualification.result':'success',
                    'needs.host-policy.result':host,'needs.changes.outputs.native':str(route['native']).lower(),'needs.changes.outputs.ready':str(ready).lower(),
                    **{'needs.changes.outputs.'+key:str(route[key]).lower() for key in ('package_macos','fixtures')}}
                allowed=expression(condition,values)
                with self.subTest(path=path,ready=ready):
                    self.assertEqual(allowed,route['native'] and ready and (route['package_macos'] or route['fixtures']))
                    env=env_for(tuple(str(route[key]).lower() for key in ('portable','package_macos','package_linux','fixtures')))
                    env.update(CODE='false' if path=='README.md' else 'true',READY=str(ready).lower(),
                               MAC_RUNNER_RESULT='success' if allowed else 'skipped')
                    result=subprocess.run(['bash','-e','-c',join],env=dict(os.environ,**env),capture_output=True)
                    self.assertEqual(result.returncode==0,not route['native'] or ready,result.stderr)
                    if allowed:self.assertTrue(expression(requested,values))
                for key in ('needs.changes.result','needs.qualification.result','needs.host-policy.result'):
                    for failure in ('failure','cancelled',''):
                        failed=dict(values,**{key:failure})
                        self.assertFalse(expression(condition,failed),(path,key,failure))


class SelectorAllocation(unittest.TestCase):
    def test_actual_application_selector_and_join_require_only_requested_mac_work_even_in_drafts(self):
        from test_components import expression,job,scripts,workflow
        text=workflow('make-all');condition=re.search(r'^    if: (.+)$',job(text,'mac-runner'),re.M)[1]
        join=scripts(job(text,'make-all'))[0]
        for npm,contracts,ready in ((a,b,c) for a in ('true','false') for b in ('true','false') for c in ('true','false')):
            selected=npm=='true' or contracts=='true'
            values={'needs.changes.result':'success','needs.changes.outputs.npm':npm,'needs.changes.outputs.contracts':contracts}
            self.assertEqual(expression(condition,values),selected)
            base=dict(RESULT='success',CODE='false',CLAUDE='false',ELIXIR_RESULT='skipped',ELIXIR_MODE='',ELIXIR_ORIGIN='',CLAUDE_RESULT='skipped',POLICY='true',READY=ready,NPM=npm,READINESS='false',CONFIG='false',CONTRACTS=contracts,PROCESS_RESULT='success' if selected else 'skipped',GITHUB_STEP_SUMMARY=os.devnull)
            for observed in ('success','skipped','failure','cancelled',''):
                actual=subprocess.run(['bash','-e','-c',join],env=dict(os.environ,**base,MAC_RUNNER_RESULT=observed),capture_output=True)
                self.assertEqual(actual.returncode==0,observed==('success' if selected else 'skipped'),(npm,contracts,ready,observed))
            for failed in ('failure','cancelled','skipped',''):
                self.assertFalse(expression(condition,dict(values,**{'needs.changes.result':failed})))

    def test_actual_package_selector_native_guard_and_join_preserve_linux_only_and_strict_draft_failures(self):
        from test_components import env_for,expression,job,scripts,workflow
        text=workflow('package-checks');selector=re.search(r'^    if: (.+)$',job(text,'mac-runner'),re.M)[1]
        native=re.search(r'^    if: (.+)$',job(text,'native-host-package'),re.M)[1]
        join=scripts(job(text,'package-qualification'))[0]
        for flags in (('false','false','false','false'),('true','false','false','false'),('true','false','true','false'),('true','true','false','false'),('true','false','false','true')):
            initial=env_for(flags);mac=flags[1]=='true' or flags[3]=='true'
            for ready in ('true','false'):
                selected=initial['NATIVE_REQUIRED']=='true' and mac and ready=='true'
                values={'needs.changes.result':'success','needs.qualification.result':'success','needs.host-policy.result':'success' if initial['NATIVE_REQUIRED']=='true' and ready=='true' else 'skipped','needs.changes.outputs.native':initial['NATIVE_REQUIRED'],'needs.changes.outputs.ready':ready,'needs.changes.outputs.package_macos':flags[1],'needs.changes.outputs.fixtures':flags[3]}
                self.assertEqual(expression(selector,values),selected)
                for observed in ('success','skipped','failure','cancelled',''):
                    with self.subTest(flags=flags,ready=ready,observed=observed):
                        actual_native=expression(native,dict(values,**{'needs.mac-runner.result':observed}))
                        expected_native=initial['NATIVE_REQUIRED']=='true' and ready=='true' and observed==('success' if mac else 'skipped')
                        self.assertEqual(actual_native,expected_native)
                        env=dict(os.environ,**initial);env.update(READY=ready,MAC_RUNNER_RESULT=observed)
                        result=subprocess.run(['bash','-e','-c',join],env=env,capture_output=True)
                        self.assertEqual(result.returncode==0,(initial['NATIVE_REQUIRED']=='false' or ready=='true') and observed==('success' if selected else 'skipped'))
                for key in ('needs.changes.result','needs.qualification.result','needs.host-policy.result'):
                    for failed in ('failure','cancelled','skipped',''):
                        self.assertFalse(expression(selector,dict(values,**{key:failed})))
