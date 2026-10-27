"""Routing fixtures do not demonstrate service admission or an isolated host."""
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/ci'))
import select_mac_runner as selector
sys.path.pop(0)
from test_components import job, scripts, workflow, env_for

HEAD, SOURCE, BASE = 'a' * 40, 'b' * 40, 'c' * 40
REPO = selector.REPOSITORY
NOW = datetime.datetime(2026, 10, 27, 18, 0, tzinfo=datetime.timezone.utc)
LABEL = 'symphony-backup-mac-' + 'd' * 32


class Selection(unittest.TestCase):
    def setUp(self):
        self.workflow = '.github/workflows/make-all.yml'
        self.context = {
            'GITHUB_RUN_ID': '12', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_EVENT_NAME': 'pull_request',
            'GITHUB_REF': 'refs/pull/34/merge', 'GITHUB_SHA': SOURCE, 'GITHUB_WORKFLOW_SHA': SOURCE, 'GITHUB_REPOSITORY': REPO,
            'GITHUB_WORKFLOW_REF': REPO + '/' + self.workflow + '@refs/pull/34/merge',
            'GITHUB_ACTOR': 'Jayson-Stangel', 'GITHUB_TRIGGERING_ACTOR': 'Jayson-Stangel',
        }
        self.event = {'number': 34, 'pull_request': {
            'user': {'login': 'Jayson-Stangel'},
            'head': {'ref': 'codex/feature', 'sha': HEAD, 'repo': {'full_name': REPO}},
            'base': {'sha': BASE, 'ref': 'dev', 'repo': {'full_name': REPO}},
        }}
        self.permit = {
            'schema': 1, 'repository': REPO, 'event_name': 'pull_request',
            'controller_workflow': self.workflow, 'workflow_ref': self.context['GITHUB_WORKFLOW_REF'],
            'workflow_sha': SOURCE, 'source_sha': SOURCE, 'pr_head': HEAD, 'base_sha': BASE,
            'pr_number': 34, 'run_id': 12, 'run_attempt': 2,
            'targets': [{'consumer_workflow': self.workflow, 'job_key': 'platform-contracts', 'runner_label': LABEL}],
            'expires_at': '2026-10-27T18:20:00Z', 'actor': 'Jayson-Stangel',
            'triggering_actor': 'Jayson-Stangel', 'pr_author': 'Jayson-Stangel',
        }
        self.inspect = Mock(return_value=None)

    def select(self, raw=None):
        return selector.select_runner(json.dumps(self.permit) if raw is None else raw,
                                      self.context, self.event, self.workflow, selector.TARGETS[self.workflow].copy().pop(), NOW, self.inspect)

    def test_hosted_default_other_run_and_reusable_callers_do_not_read_live_metadata(self):
        self.assertEqual(self.select(''), 'macos-14')
        self.permit['run_id'] = 13
        self.assertEqual(self.select(), 'macos-14')
        self.permit['run_id'] = 12
        self.permit['targets'] = [{'consumer_workflow': '.github/workflows/package-checks.yml', 'job_key': 'native-host-package', 'runner_label': LABEL}]
        self.assertEqual(self.select(), 'macos-14')
        self.inspect.reset_mock()
        self.inspect.assert_not_called()

    def test_local_import_retains_intel_default_and_exact_live_main_permit(self):
        self.workflow = '.github/workflows/macos-local-signing-import.yml'
        self.context.update(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                            GITHUB_WORKFLOW_REF=REPO + '/' + self.workflow + '@refs/heads/main')
        self.event = {}
        self.permit.update(event_name='workflow_dispatch', controller_workflow=self.workflow,
                           workflow_ref=self.context['GITHUB_WORKFLOW_REF'], pr_number=None,
                           pr_head=None, base_sha=None, pr_author=None,
                           targets=[{'consumer_workflow': self.workflow, 'job_key': 'verify', 'runner_label': LABEL}])
        self.assertEqual(self.select(''), 'macos-15-intel')
        self.permit['run_id'] = 13
        self.assertEqual(self.select(), 'macos-15-intel')
        self.inspect.assert_not_called()
        self.permit['run_id'] = 12
        root = 'repos/' + REPO
        docs = {root + '/actions/runs/12/attempts/2': {
            'id': 12, 'run_attempt': 2, 'repository': {'full_name': REPO},
            'event': 'workflow_dispatch', 'path': self.workflow, 'head_sha': SOURCE,
            'status': 'in_progress', 'actor': {'login': 'Jayson-Stangel'},
            'triggering_actor': {'login': 'Jayson-Stangel'}},
            root + '/commits/' + SOURCE: {'sha': SOURCE}}
        self.inspect = lambda permit: selector.verify_live(permit, read=docs.__getitem__)
        self.assertEqual(self.select(), LABEL)
        self.permit['source_sha'] = HEAD
        with self.assertRaises(selector.Refusal):
            self.select()

    def test_private_ingress_bypasses_mac_selector_and_native_gates_stay_exact(self):
        text = workflow('macos-local-signing-import')
        ingress = job(text, 'staging-ingress')
        native = job(text, 'verify')
        route = job(text, 'mac-runner-local-import')
        self.assertIn('runs-on: ubuntu-24.04', ingress)
        self.assertNotIn('needs:', ingress)
        self.assertNotIn('SYMPHONY_BACKUP_MAC_PERMIT', ingress)
        for block in (native, route, ingress):
            self.assertIn("github.ref == 'refs/heads/main'", block)
            self.assertIn('inputs.approved_revision == github.sha', block)
        self.assertIn('needs: [mac-runner-local-import, staging-ingress]', native)
        self.assertIn('runs-on: ${{ needs.mac-runner-local-import.outputs.runner }}', native)
        self.assertIn('environment: macos-production-signing', native)
        self.assertIn('name: Verify locally signed distribution', native)
        self.assertIn('runner=macos-15-intel', route)
        self.assertIn('SELECT_JOB_KEY: verify', route)
        self.assertIn('test "$(uname -m)" = arm64', native)
        self.assertIn('14.*)', native)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('.github/workflows/macos-production-signing.yml', selector.TARGETS)

    def test_exact_owner_pr_permit_selects_one_label_after_live_source_check(self):
        self.assertEqual(self.select(), LABEL)
        self.inspect.assert_called_once_with(self.permit, expected_pr_base_ref='dev', expected_pr_head_ref='codex/feature')
        self.workflow = '.github/workflows/package-checks.yml'
        self.permit['targets'] = [{'consumer_workflow': self.workflow, 'job_key': 'native-host-package', 'runner_label': LABEL}]
        self.permit.update(controller_workflow=self.workflow,
                           workflow_ref=REPO + '/' + self.workflow + '@refs/pull/34/merge')
        self.context['GITHUB_WORKFLOW_REF'] = self.permit['workflow_ref']
        self.assertEqual(self.select(), LABEL)

    def test_matching_run_refuses_stale_untrusted_and_overscoped_permits(self):
        fields = {
            'repository': 'untrusted/repo', 'event_name': 'pull_request_target',
            'controller_workflow': '.github/workflows/macos-signing.yml', 'workflow_sha': 'e' * 40,
            'source_sha': HEAD, 'pr_head': SOURCE, 'base_sha': HEAD,
            'pr_number': 35, 'run_attempt': 1, 'targets': [{'consumer_workflow': self.workflow, 'job_key': 'make-all', 'runner_label': LABEL}], 'expires_at': '2026-10-27T17:59:59Z',
            'actor': 'other', 'triggering_actor': 'other', 'pr_author': 'other',
            'schema': True,
        }
        original = copy.deepcopy(self.permit)
        for key, value in fields.items():
            with self.subTest(key=key):
                self.permit = dict(original, **{key: value})
                with self.assertRaises(selector.Refusal):
                    self.select()
        self.permit = original
        for raw in ('{', '[]', json.dumps(dict(original, unexpected=True)), 'x' * 16385):
            with self.subTest(raw=raw[:50]), self.assertRaises(selector.Refusal):
                self.select(raw)

    def test_context_actor_fork_or_advanced_source_refuses(self):
        for key in ('GITHUB_ACTOR', 'GITHUB_TRIGGERING_ACTOR', 'GITHUB_WORKFLOW_REF', 'GITHUB_WORKFLOW_SHA', 'GITHUB_SHA'):
            original = self.context[key]
            self.context[key] = 'other'
            with self.subTest(key=key), self.assertRaises(selector.Refusal):
                self.select()
            self.context[key] = original
        for branch, key, value in (('head', 'sha', SOURCE), ('base', 'ref', 'main')):
            original = self.event['pull_request'][branch][key]
            self.event['pull_request'][branch][key] = value
            with self.assertRaises(selector.Refusal):
                self.select()
            self.event['pull_request'][branch][key] = original
        self.event['pull_request']['head']['repo']['full_name'] = 'untrusted/fork'
        with self.assertRaises(selector.Refusal):
            self.select()

    def test_live_refusal_unavailability_and_bad_base_do_not_output_label(self):
        self.inspect.side_effect = selector.Refusal('source advanced')
        with self.assertRaises(selector.Refusal):
            self.select()
        self.inspect.side_effect = None

    def test_expiry_is_bounded_and_requires_explicit_utc(self):
        for expiry in ('2026-10-27T20:01:00Z', '2026-10-27T18:20:00', '2026-10-27T18:00:00Z', None):
            self.permit['expires_at'] = expiry
            with self.subTest(expiry=expiry), self.assertRaises(selector.Refusal):
                self.select()

    def test_every_credential_free_consumer_and_distinct_targets(self):
        for consumer, keys in selector.TARGETS.items():
            for key in keys:
                permit = copy.deepcopy(self.permit)
                permit['targets'] = [{'consumer_workflow': consumer, 'job_key': key, 'runner_label': LABEL}]
                with self.subTest(consumer=consumer, key=key):
                    self.assertEqual(selector.select_runner(json.dumps(permit), self.context, self.event,
                                     consumer, key, NOW, self.inspect), LABEL)
        for targets in ([], self.permit['targets'] * 2, self.permit['targets'] * 9,
                        [dict(self.permit['targets'][0], consumer_workflow='.github/workflows/macos-signing.yml')],
                        [dict(self.permit['targets'][0], consumer_workflow=[])],
                        [dict(self.permit['targets'][0], runner_label='macos-14')]):
            self.permit['targets'] = targets
            with self.subTest(targets=targets), self.assertRaises(selector.Refusal):
                self.select()

    def test_manual_and_push_source_binding_and_tag_refusal(self):
        for event in ('workflow_dispatch', 'push'):
            self.context.update(GITHUB_EVENT_NAME=event, GITHUB_REF='refs/heads/dev',
                                GITHUB_WORKFLOW_REF=REPO + '/' + self.workflow + '@refs/heads/dev')
            self.permit.update(event_name=event, workflow_ref=self.context['GITHUB_WORKFLOW_REF'],
                               pr_number=None, pr_head=None, base_sha=None, pr_author=None)
            self.assertEqual(self.select(), LABEL)
            self.context['GITHUB_REF'] = 'refs/tags/v0.0.6'
            self.context['GITHUB_WORKFLOW_REF'] = REPO + '/' + self.workflow + '@refs/tags/v0.0.6'
            self.permit['workflow_ref'] = self.context['GITHUB_WORKFLOW_REF']
            with self.assertRaises(selector.Refusal):
                self.select()

    def test_non_pr_live_service_identity_refuses_old_success_and_actor_changes(self):
        permit = dict(self.permit, event_name='workflow_dispatch')
        root = 'repos/' + REPO
        run = {'id': 12, 'run_attempt': 2, 'repository': {'full_name': REPO},
               'event': 'workflow_dispatch', 'path': self.workflow, 'head_sha': SOURCE,
               'status': 'in_progress', 'actor': {'login': 'Jayson-Stangel'},
               'triggering_actor': {'login': 'Jayson-Stangel'}}
        docs = {root + '/actions/runs/12/attempts/2': run, root + '/commits/' + SOURCE: {'sha': SOURCE}}
        selector.verify_live(permit, read=docs.__getitem__)
        for key, value in (('status', 'completed'), ('head_sha', HEAD), ('run_attempt', 1),
                           ('actor', {'login': 'untrusted'}), ('path', '.github/workflows/macos-signing.yml')):
            original = run[key]; run[key] = value
            with self.subTest(key=key), self.assertRaises(selector.Refusal):
                selector.verify_live(permit, read=docs.__getitem__)
            run[key] = original

    def test_reusable_permissions_do_not_exceed_existing_read_only_callers(self):
        for consumer in selector.TARGETS:
            text = (ROOT / consumer).read_text()
            header = text.split('\njobs:', 1)[0]
            self.assertNotIn('pull-requests:', header, consumer)
            self.assertNotIn(': write', header, consumer)
            self.assertIn('  contents: read', header, consumer)
            self.assertIn('  actions: read', header, consumer)

    def promotion_documents(self):
        root = 'repos/' + REPO
        self.event['pull_request']['head']['ref'] = 'dev'
        self.event['pull_request']['base']['ref'] = 'main'
        documents = {
            root: {'full_name': REPO, 'owner': {'type': 'User'}, 'visibility': 'public'},
            root + '/actions/runs/12/attempts/2': {
                'id': 12, 'run_attempt': 2, 'repository': {'full_name': REPO},
                'event': 'pull_request', 'path': self.workflow, 'head_sha': HEAD,
                'status': 'in_progress', 'pull_requests': [{'number': 34}],
                'actor': {'login': 'Jayson-Stangel'}, 'triggering_actor': {'login': 'Jayson-Stangel'},
            },
            root + '/pulls/34': dict(copy.deepcopy(self.event['pull_request']),
                                     number=34, state='open', merge_commit_sha=SOURCE),
            root + '/commits/' + SOURCE: {'sha': SOURCE, 'parents': [{'sha': BASE}, {'sha': HEAD}]},
            root + '/git/ref/heads/main': {'object': {'sha': BASE}},
            root + '/git/ref/heads/dev': {'object': {'sha': HEAD}},
        }
        return documents

    def select_using_real_live_verifier(self, documents):
        # Only GitHub transport is replaced; exercise select -> verify_live ->
        # inspect_run and every real source/route/actor check together.
        return selector.select_runner(json.dumps(self.permit), self.context, self.event,
            self.workflow, selector.TARGETS[self.workflow].copy().pop(), NOW,
            verify=lambda permit, **kwargs: selector.verify_live(permit, read=documents.__getitem__, **kwargs))

    def test_genuine_dev_to_main_ci_uses_actual_live_inspector_for_both_required_workflows(self):
        for workflow_path, key in (('.github/workflows/make-all.yml', 'platform-contracts'),
                                   ('.github/workflows/package-checks.yml', 'native-host-package')):
            self.workflow = workflow_path
            self.context['GITHUB_WORKFLOW_REF'] = REPO + '/' + workflow_path + '@refs/pull/34/merge'
            self.permit.update(controller_workflow=workflow_path, workflow_ref=self.context['GITHUB_WORKFLOW_REF'],
                targets=[{'consumer_workflow': workflow_path, 'job_key': key, 'runner_label': LABEL}])
            documents = self.promotion_documents()
            with self.subTest(workflow=workflow_path):
                self.assertEqual(self.select_using_real_live_verifier(documents), LABEL)

    def test_promotion_ci_refuses_feature_to_main_fork_and_nonowner_event(self):
        for branch, key, value in (('head', 'ref', 'codex/feature'),
                                    ('head', 'repo', {'full_name': 'untrusted/fork'}),
                                    ('user', 'login', 'untrusted')):
            documents = self.promotion_documents()
            self.event['pull_request'][branch][key] = value
            with self.subTest(branch=branch, key=key), self.assertRaises(selector.Refusal):
                self.select_using_real_live_verifier(documents)
            # Reset fixture for the next case, including user/repository identity.
            self.event['pull_request']['user']['login'] = 'Jayson-Stangel'
            self.event['pull_request']['head']['repo']['full_name'] = REPO

    def test_promotion_ci_refuses_stale_refs_route_actors_and_source_via_actual_inspector(self):
        root = 'repos/' + REPO
        original = self.promotion_documents()
        cases = [
            (root + '/git/ref/heads/main', ('object', 'sha'), 'd' * 40),
            (root + '/git/ref/heads/dev', ('object', 'sha'), 'd' * 40),
            (root + '/pulls/34', ('base', 'sha'), 'd' * 40),
            (root + '/pulls/34', ('head', 'sha'), 'd' * 40),
            (root + '/pulls/34', ('head', 'ref'), 'codex/feature'),
            (root + '/pulls/34', ('head', 'repo', 'full_name'), 'untrusted/fork'),
            (root + '/pulls/34', ('base', 'ref'), 'dev'),
            (root + '/pulls/34', ('user', 'login'), 'untrusted'),
            (root + '/actions/runs/12/attempts/2', ('actor', 'login'), 'untrusted'),
            (root + '/actions/runs/12/attempts/2', ('triggering_actor', 'login'), 'untrusted'),
            (root + '/actions/runs/12/attempts/2', ('run_attempt',), 1),
            (root + '/actions/runs/12/attempts/2', ('path',), '.github/workflows/package-checks.yml'),
            (root + '/commits/' + SOURCE, ('parents',), [{'sha': 'd' * 40}, {'sha': HEAD}]),
        ]
        for endpoint, keys, value in cases:
            documents = copy.deepcopy(original)
            target = documents[endpoint]
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(endpoint=endpoint, keys=keys), self.assertRaises(selector.Refusal):
                self.select_using_real_live_verifier(documents)

    def test_required_wrappers_reject_failed_selector_even_when_mac_work_is_omitted(self):
        app = scripts(job(workflow('make-all'), 'make-all'))[0]
        package = '\n'.join(scripts(job(workflow('package-checks'), 'package-qualification'))[:2])
        app_env = dict(RESULT='success', CODE='false', POLICY='false', READY='true', NPM='false',
                       READINESS='false', CONFIG='false', CONTRACTS='false', PROCESS_RESULT='skipped')
        package_env = env_for(('false',) * 4)
        for command, env in ((app, app_env), (package, package_env)):
            for status in ('success', 'failure', 'cancelled', 'skipped', ''):
                result = subprocess.run(['bash', '-e', '-c', command],
                    env=dict(os.environ, **dict(env, MAC_RUNNER_RESULT=status, GITHUB_STEP_SUMMARY=os.devnull)),
                    capture_output=True)
                self.assertEqual(result.returncode == 0, status == 'success', status)


if __name__ == '__main__':
    unittest.main()
