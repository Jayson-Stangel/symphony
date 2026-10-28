"""Actual source guards are mocked; these fixtures grant no live readiness."""
from pathlib import Path
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

    def test_reusable_caller_permission_overrides_preserve_status_read(self):
        from test_components import job, workflow
        for caller in ('runner-integration','burrito-release'):
            text=workflow(caller);block=job(text,'runners')
            self.assertIn('uses: ./.github/workflows/runner-release.yml',block)
            self.assertIn('      statuses: read',block)
        for name in ('make-all','package-checks','native-manager-helpers','current-manager-package',
                     'release-native','runner-release','runner-integration','burrito-release'):
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
