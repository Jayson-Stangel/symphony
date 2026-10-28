"""Synthetic finite capacity handoff tests, never VM/registration/service effects."""
import copy
import datetime
import importlib.util
from pathlib import Path
import sys
import unittest

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import mac_runner_handoff as handoff

NOW = datetime.datetime(2026, 10, 28, 20, tzinfo=datetime.timezone.utc)
REQUEST = dict(repository='Jayson-Stangel/symphony', event_name='pull_request',
    controller_workflow='.github/workflows/package-checks.yml', workflow_sha='a'*40,
    source_sha='a'*40, head_sha='b'*40, base_sha='c'*40, pr_number=156,
    run_id=1234, run_attempt=3, consumer_workflow='.github/workflows/package-checks.yml', job_key='native-host-package')
SESSION = '8f54445f-c9a6-4bb8-8f61-6c08ff574101'

def status(outcome='ready', reason=None, service_id=21):
    payload = dict(REQUEST, schema=1, session_id=SESSION, runner_label='symphony-backup-mac-'+SESSION.replace('-', ''),
        runner_id=49, admission_sha256='d'*64, expires_at='2026-10-28T21:00:00Z', outcome=outcome,
        reason=reason, execution_started=False, cleanup_complete=False)
    if outcome == 'unavailable':
        payload.update(session_id=None, runner_label=None, runner_id=None, cleanup_complete=True)
    return dict(id=service_id, creator={'login':'Jayson-Stangel'}, context=handoff.context(REQUEST),
        url='https://api.github.com/repos/Jayson-Stangel/symphony/statuses/'+'a'*40,
        created_at='2026-10-28T19:59:50Z', state='success' if outcome == 'ready' else 'error',
        target_url=handoff.ready_url(payload)), payload

class Handoff(unittest.TestCase):
    def test_ready_preserves_original_service_identity_and_prepared_label(self):
        record, payload = status()
        decision = handoff.response([record], REQUEST, NOW)
        self.assertEqual(decision['path'], 'owned')
        self.assertEqual(decision['runner'], payload['runner_label'])
        self.assertEqual(decision['original_status_id'], record['id'])

    def test_offline_busy_and_clean_provision_failure_have_no_resources_for_this_request(self):
        for reason in handoff.UNAVAILABLE_REASONS:
            record, payload = status('unavailable', reason)
            decision = handoff.response([record], REQUEST, NOW)
            self.assertEqual((decision['path'], decision['reason']), ('hosted', reason))
            self.assertIsNone(decision['runner'])
        # No mutation of any other healthy busy session is possible here.

    def test_expired_wrong_source_foreign_forged_and_executed_never_become_fallback(self):
        changes = dict(expires_at='2026-10-28T19:59:59Z', source_sha='e'*40, run_attempt=2,
                       execution_started=True, runner_id=0, runner_label='self-hosted', schema=True)
        for key, value in changes.items():
            record, payload = status(); payload[key] = value; record['target_url'] = handoff.ready_url(payload)
            with self.subTest(key=key), self.assertRaises(handoff.Refusal):
                handoff.response([record], REQUEST, NOW)
        for key, value in (('creator', {'login':'foreign'}), ('url','https://foreign.invalid/status'),
                           ('target_url','http://github.com/foreign'), ('state','failure')):
            record, _ = status(); record[key]=value
            with self.subTest(key=key), self.assertRaises(handoff.Refusal):
                handoff.response([record], REQUEST, NOW)

    def test_newer_bad_matching_receipt_cannot_reveal_older_ready(self):
        old, _ = status(service_id=21); new, _ = status(service_id=22)
        new.update(target_url='malformed', created_at='2026-10-28T19:59:51Z')
        with self.assertRaises(handoff.Refusal): handoff.response([old,new],REQUEST,NOW)
        # Prior-attempt contexts are ignored, never treated as a current READY.
        old['context']=old['context'].replace('/3/', '/2/')
        self.assertIsNone(handoff.response([old],REQUEST,NOW))

    def test_unavailable_with_uncertain_cleanup_or_assigned_resources_refuses(self):
        for key,value in (('cleanup_complete',False),('runner_id',49),('session_id',SESSION),('execution_started',True)):
            record,payload=status('unavailable','busy');payload[key]=value;record['target_url']=handoff.ready_url(payload)
            with self.subTest(key=key), self.assertRaises(handoff.Refusal):handoff.response([record],REQUEST,NOW)

    def test_bounded_no_response_fallback_allocates_no_private_label(self):
        clock=[0];reads=[]
        def read(source, **_):reads.append(source);return []
        def pause(seconds):clock[0]+=seconds
        decision=handoff.await_response(REQUEST,read,clock=lambda:clock[0],pause=pause,utcnow=lambda:NOW)
        self.assertEqual(len(reads),15);self.assertEqual(clock[0],300)
        self.assertEqual(decision['path'],'hosted');self.assertIsNone(decision['runner'])

    def test_api_failure_and_matching_bad_reply_do_not_mask_as_unavailable(self):
        def failed(_, **kwargs):raise OSError('synthetic read unavailable')
        with self.assertRaises(handoff.Refusal):handoff.await_response(REQUEST,failed)
        record,_=status();record['target_url']='bad'
        with self.assertRaises(handoff.Refusal):handoff.await_response(REQUEST,lambda _, **kwargs:[record],utcnow=lambda:NOW)

    def test_immediate_ready_does_not_poll_or_wait_again(self):
        record,_=status();reads=[]
        def read(_, **kwargs):reads.append(1);return [record]
        decision=handoff.await_response(REQUEST,read,pause=lambda _:self.fail('unnecessary wait'),utcnow=lambda:NOW)
        self.assertEqual(decision['path'],'owned');self.assertEqual(len(reads),1)

    def test_canonical_source_url_and_scope_bounds(self):
        record,payload=status()
        for url in (record['target_url'].replace('github.com/','github.com:443/'),
                    record['target_url'].replace('/1234/','/9999/'), record['target_url']+'=', 'x'*2049):
            with self.assertRaises(handoff.Refusal):handoff.decode_url(url,REQUEST)
        request=dict(REQUEST,source_sha='../foreign')
        with self.assertRaises(handoff.Refusal):handoff.context(request)
        request=dict(REQUEST,pr_number=True)
        with self.assertRaises(handoff.Refusal):handoff.context(request)


    def test_slow_ready_after_deadline_never_emits_owned_label(self):
        clock=[0];record,_=status();budgets=[]
        def read(source, *, timeout):
            budgets.append(timeout);clock[0]=301;return [record]
        with self.assertRaisesRegex(handoff.Refusal,'after deadline'):
            handoff.await_response(REQUEST,read,clock=lambda:clock[0],
                pause=lambda _:self.fail('no wait after deadline'),utcnow=lambda:NOW)
        self.assertEqual(budgets,[300])

    def test_malformed_inventory_rows_and_creator_refuse_instead_of_unavailable(self):
        for rows in (['broken'],[None],42):
            with self.assertRaises(handoff.Refusal):handoff.response(rows,REQUEST,NOW)
        record,_=status();record['creator']='broken'
        with self.assertRaises(handoff.Refusal):handoff.response([record],REQUEST,NOW)

    def test_preparing_crash_timeout_never_falls_back_to_hosted(self):
        record,payload=status();payload.update(outcome='preparing',runner_id=None)
        record.update(state='pending',target_url=handoff.ready_url(payload))
        clock=[0]
        with self.assertRaisesRegex(handoff.Refusal,'cleanup unverified'):
            handoff.await_response(REQUEST,lambda _,**kwargs:[record],clock=lambda:clock[0],
                pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds),utcnow=lambda:NOW)

    def test_preparing_transitions_only_to_same_session_ready_or_verified_clean(self):
        pending,payload=status();payload.update(outcome='preparing',runner_id=None)
        pending.update(state='pending',target_url=handoff.ready_url(payload))
        ready,_=status(service_id=22)
        clean,clean_payload=status('unavailable','provisioning-failed-clean',22)
        clean_payload['session_id']=SESSION;clean['target_url']=handoff.ready_url(clean_payload)
        for terminal,expected in ((ready,'owned'),(clean,'hosted')):
            rows=iter(([pending],[pending,terminal]));clock=[0]
            result=handoff.await_response(REQUEST,lambda _,**kwargs:next(rows),clock=lambda:clock[0],
                pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds),utcnow=lambda:NOW)
            self.assertEqual(result['path'],expected);self.assertEqual(result['original_status_id'],22)
        foreign,payload=status(service_id=22);payload['admission_sha256']='e'*64
        foreign['target_url']=handoff.ready_url(payload);rows=iter(([pending],[pending,foreign]))
        with self.assertRaisesRegex(handoff.Refusal,'terminal identity'):
            handoff.await_response(REQUEST,lambda _,**kwargs:next(rows),pause=lambda _:None,utcnow=lambda:NOW)

    def test_uncertain_terminal_preparation_is_a_refusal(self):
        record,payload=status();payload.update(outcome='refused',reason='cleanup-unverified',runner_id=None)
        record.update(state='error',target_url=handoff.ready_url(payload))
        with self.assertRaisesRegex(handoff.Refusal,'cleanup unverified'):handoff.response([record],REQUEST,NOW)

    def test_slow_empty_snapshot_cannot_hide_preparation_after_claim_cutoff(self):
        clock=[0]
        def read(source, **kwargs):clock[0]=301;return []
        with self.assertRaisesRegex(handoff.Refusal,'post-claim-window'):
            handoff.await_response(REQUEST,read,clock=lambda:clock[0],pause=lambda _:None,utcnow=lambda:NOW)
