"""Negative origin/attempt/step/integrity cases; fixtures are not CI acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import sys
import unittest
import zipfile

before = sys.path[:]
try:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import validation_evidence as evidence
    import elixir_coverage_profile
    from validation_inputs import digest
finally:
    sys.path[:] = before


class OriginalProof(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        def timestamp(seconds):
            return (now + timedelta(seconds=seconds)).isoformat()
        repo = dict(full_name=evidence.REPOSITORY, id=77)
        self.run = dict(id=101, run_attempt=2, repository=repo, head_repository=repo,
                        event='push', head_branch='dev', head_sha='a' * 40, path=evidence.WORKFLOW,
                        status='completed', conclusion='success', created_at=timestamp(-300))
        stages = [dict(name=name, number=index + 1, status='completed', conclusion='success',
                       started_at=timestamp(-100 + index * 10), completed_at=timestamp(-95 + index * 10))
                  for index, name in enumerate(evidence.STEPS)]
        stages.append(dict(name='Retain upstream full-coverage proof (REUSED)', number=8,
                           status='completed', conclusion='skipped'))
        self.job = dict(id=501, run_id=101, run_attempt=2, head_sha='a' * 40, name=evidence.JOB,
                        status='completed', conclusion='success', labels=['ubuntu-24.04'],
                        runner_id=1000001, runner_group_id=0, runner_group_name='GitHub Actions', steps=stages)
        self.profile = dict(expected_tests=685, expected_linux_skips=10, measured_coverage=100.0)
        self.inputs = dict(identity=dict(execution=dict(audited_oci_fixture=True), audited_full_input_fixture=True, public_inputs=[
            dict(path='/home/runner/work/symphony/symphony/elixir/_build/test', sha256='f'*64, files=1, bytes=1)]))
        self.inputs['sha256'] = digest(self.inputs['identity'])
        self.log = b'Finished in 4.0 seconds\n685 tests, 0 failures, 10 skipped\n| 100.00% | Total |\n'
        self.receipt = dict(schema_version=3, family='full-exunit-coverage', clean_preparation='no-mise-or-compiled-cache-v1',
            producer=dict(repository=evidence.REPOSITORY, repository_id=77, run_id=101, attempt=2,
                          job_id=501, source='a' * 40, workflow=evidence.WORKFLOW),
            inputs_sha256=self.inputs['sha256'], execution=dict(mode='EXECUTED', exit_code=0,
                seconds=4.0, coverage_log_sha256=hashlib.sha256(self.log).hexdigest(),
                summary=dict(tests=685, failures=0, skipped=10, measured_coverage=100.0)))
        self.meta = dict(id=901, name='elixir-full-coverage-101-2', expired=False, size_in_bytes=100,
                         created_at=timestamp(-17), expires_at=timestamp(3600),
                         workflow_run=dict(id=101, head_sha='a' * 40))
        self.pack()

    def pack(self, *, extra=None):
        sidecar = b'inert test-cache fixture, never installed by these provenance tests'
        self.receipt['sidecar'] = {name: dict(sha256=hashlib.sha256(sidecar).hexdigest(), size=len(sidecar),cache=dict(files=1,bytes=1)) for name in ('test','dev','deps','hex')}
        self.receipt.setdefault('checkpoints',dict(test='PRE_COVERAGE',hex='PRE_COVERAGE',dev='POST_DIALYZER',deps='POST_DIALYZER'))
        self.receipt.setdefault('cache_origin',dict(mode='CLEAN'))
        target = io.BytesIO()
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('receipt.json', json.dumps(self.receipt))
            archive.writestr('inputs.json', json.dumps(self.inputs))
            archive.writestr('coverage.log', self.log)
            for name in ('test','dev','deps','hex'): archive.writestr(name+'-cache.tar.gz', sidecar)
            if extra:
                archive.writestr(*extra)
        self.raw = target.getvalue()
        self.meta['size_in_bytes'] = len(self.raw)
        self.meta['digest'] = 'sha256:' + hashlib.sha256(self.raw).hexdigest()

    def verify(self, **arguments):
        return evidence.verify_bundle(self.run, self.job, self.meta, self.raw,
                                      arguments.get('current', self.inputs), self.profile)

    def test_original_identity_is_retained_and_different_candidate_sha_is_not_fabricated(self):
        result = self.verify()
        self.assertEqual(result['mode'], 'REUSED')
        self.assertEqual(result['original'], self.receipt['producer'])
        self.assertEqual(result['artifact_id'], 901)
        self.assertIn('/attempts/2', result['original_run_url'])
        self.assertEqual(result['inputs_sha256'], self.inputs['sha256'])

    def test_hex_checkpoint_timing_and_public_cache_origin_are_required(self):
        for target in ('hex','origin'):
            with self.subTest(target=target):
                saved=json.loads(json.dumps(self.receipt))
                if target=='hex':self.receipt['checkpoints']['hex']='POST_DIALYZER'
                else:self.receipt['cache_origin']['mode']='UNTRUSTED'
                self.pack()
                with self.assertRaises(evidence.Fresh):self.verify()
                self.receipt=saved

    def test_failed_cancelled_incomplete_foreign_manual_pr_stale_producer_never_authorizes(self):
        original = deepcopy(self.run)
        variants = [dict(status='completed', conclusion=value) for value in ('failure', 'cancelled', 'skipped', None)]
        variants += [dict(status='in_progress', conclusion=None), dict(event='pull_request'),
                     dict(event='workflow_dispatch'), dict(head_branch='feature'),
                     dict(created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat()),
                     dict(repository=dict(full_name='foreign/repo', id=78)),
                     dict(head_repository=dict(full_name=evidence.REPOSITORY, id=78)),
                     dict(path='.github/workflows/other.yml')]
        for change in variants:
            with self.subTest(change=change):
                self.run = dict(original, **change)
                with self.assertRaises(evidence.Fresh):
                    self.verify()
        self.run = original

    def test_wrong_job_attempt_source_or_self_hosted_label_refuses(self):
        original = deepcopy(self.job)
        for change in (dict(run_attempt=1), dict(run_id=102), dict(head_sha='b' * 40),
                       dict(labels=['self-hosted', 'ubuntu-24.04']), dict(runner_group_id=1),
                       dict(runner_group_name='Default'), dict(runner_id=0), dict(conclusion='failure')):
            with self.subTest(change=change):
                self.job = dict(original, **change)
                with self.assertRaises(evidence.Fresh):
                    self.verify()
        self.job = original

    def test_every_skipped_failed_cancelled_or_missing_fresh_stage_refuses(self):
        original = deepcopy(self.job)
        for index in range(len(evidence.STEPS)):
            for conclusion in ('skipped', 'failure', 'cancelled', None):
                with self.subTest(stage=index, conclusion=conclusion):
                    self.job = deepcopy(original)
                    self.job['steps'][index]['conclusion'] = conclusion
                    with self.assertRaises(evidence.Fresh):
                        self.verify()
        self.job = deepcopy(original)
        self.job['steps'].pop(3)
        with self.assertRaises(evidence.Fresh):
            self.verify()
        self.job = original

    def test_reused_or_wrapper_only_success_cannot_mint_an_original(self):
        self.job['steps'][-1]['conclusion'] = 'success'
        with self.assertRaises(evidence.Fresh):
            self.verify()
        self.job['steps'][-1]['conclusion'] = 'skipped'
        self.receipt['execution']['mode'] = 'REUSED'; self.pack()
        with self.assertRaises(evidence.Fresh):
            self.verify()

    def test_artifact_must_belong_to_exact_upload_window_source_name_and_integrity(self):
        original = deepcopy(self.meta)
        for change in (dict(name='elixir-full-coverage-101-1'), dict(expired=True),
                       dict(workflow_run=dict(id=101, head_sha='b' * 40)),
                       dict(created_at=self.run['created_at']), dict(digest='sha256:' + 'b' * 64),
                       dict(expires_at=self.run['created_at']), dict(size_in_bytes=0)):
            with self.subTest(change=change):
                self.meta = dict(original, **change)
                with self.assertRaises(evidence.Fresh):
                    self.verify()
        self.meta = original

    def test_changed_inputs_corrupt_output_and_wrong_counts_cannot_be_hidden(self):
        changed = deepcopy(self.inputs); changed['identity']['actual_dependency'] = 'different'
        changed['sha256'] = digest(changed['identity'])
        with self.assertRaises(evidence.Fresh):
            self.verify(current=changed)
        self.receipt['execution']['coverage_log_sha256'] = 'b' * 64; self.pack()
        with self.assertRaises(evidence.Fresh):
            self.verify()
        self.receipt['execution']['coverage_log_sha256'] = hashlib.sha256(self.log).hexdigest()
        self.receipt['execution']['summary']['tests'] = 1; self.pack()
        with self.assertRaises(evidence.Fresh):
            self.verify()

    def test_extra_zip_members_and_duplicate_nonfinite_json_refuse(self):
        self.pack(extra=('../foreign.json', '{}'))
        with self.assertRaises(evidence.Fresh):
            self.verify()
        for raw in (b'{"success":true,"success":false}', b'{"number":NaN}', b'{"number":Infinity}', b'{"number":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(evidence.Fresh):
                evidence.strict_json(raw)


    def merged_service(self, *, change_pr=None, change_run=None, different_merge=False, rerun=False):
        repo = dict(full_name=evidence.REPOSITORY, id=77)
        merged_source, tested_source = 'e'*40, 'c'*40
        parents = [dict(sha='d'*40), dict(sha='a'*40)]
        tree = dict(sha='b'*40, truncated=False, tree=[dict(path=evidence.WORKFLOW, mode='100644', type='blob', sha='f'*40)])
        pr = dict(number=44, merged=True, state='closed', merge_commit_sha=merged_source,
                  head=dict(sha='a'*40, ref='feature', repo=repo), base=dict(sha='d'*40, ref='dev', repo=repo))
        if change_pr: pr.update(change_pr)
        run = dict(self.run, event='pull_request', head_branch='feature')
        if change_run: run.update(change_run)
        self.receipt['producer']['source'] = tested_source
        self.pack()
        job = dict(self.job)
        joins = [dict(id=700+i, name=name, run_id=run['id'], run_attempt=run['run_attempt'],
                      head_sha=run['head_sha'], status='completed', conclusion='success')
                 for i,name in enumerate(('Classify changes / Classify event inputs','Application checks'))]
        requests=[]
        def read(path, **options):
            requests.append(path)
            if path.startswith('git/commits/'):
                source=path.rsplit('/',1)[1]
                return dict(sha=source, tree=dict(sha=('9'*40 if different_merge and source==tested_source else 'b'*40)), parents=parents)
            if path.startswith('git/trees/'):return tree
            if path.startswith('commits/'):return [dict(number=44,merge_commit_sha=merged_source)]
            if path=='pulls/44':return pr
            if path.startswith('actions/workflows/'):return dict(workflow_runs=[run])
            if path=='actions/runs/101':return dict(run,run_attempt=3) if rerun and requests.count(path)>1 else run
            if path=='actions/runs/101/attempts/2/jobs?per_page=100':return dict(total_count=3,jobs=[job,*joins])
            if path=='actions/runs/101/artifacts?per_page=100':return dict(total_count=1,artifacts=[self.meta])
            if path=='actions/artifacts/901':return self.meta
            if path=='actions/artifacts/901/zip':return self.raw
            raise AssertionError('unexpected request: '+path)
        class FakeService:
            pass
        service=FakeService();service.read=read
        return service,evidence.tree_controls(tree),requests

    def test_actual_merged_pr_origin_allows_only_inert_original_bytes_after_api_authentication(self):
        service,controls,requests=self.merged_service()
        current=deepcopy(self.inputs);current['identity']['public_inputs'][0]['sha256']='0'*64
        current['sha256']=digest(current['identity'])
        result=evidence.merged_pr_proof(service,'e'*40,current,self.profile,controls,77,warm=True)
        self.assertEqual(result['mode'],'REUSED')
        self.assertEqual(result['original']['source'],'c'*40)
        self.assertEqual(result['original']['run_id'],101)
        self.assertEqual(set(result['sidecar_bytes']),{'test','dev','deps','hex'})
        self.assertLess(requests.index('pulls/44'),requests.index('actions/artifacts/901/zip'))

    def test_fork_unmerged_failed_new_attempt_and_different_tested_tree_refuse(self):
        for options in (dict(change_pr=dict(merged=False)), dict(change_pr=dict(state='open')),
                dict(change_pr=dict(head=dict(sha='a'*40,repo=dict(full_name='foreign/repo',id=88)))),
                dict(change_run=dict(conclusion='failure')),dict(different_merge=True),dict(rerun=True)):
            with self.subTest(options=options):
                service,controls,_=self.merged_service(**options)
                with self.assertRaises(evidence.Fresh):
                    evidence.merged_pr_proof(service,'e'*40,self.inputs,self.profile,controls,77)

    def test_changed_noncache_runtime_or_missing_test_cache_never_returns_loadable_bytes(self):
        for change in ('runtime','missing'):
            service,controls,_=self.merged_service()
            current=deepcopy(self.inputs)
            if change=='runtime':current['identity']['actual_toolchain']='changed'
            else:current['identity']['public_inputs']=[]
            current['sha256']=digest(current['identity'])
            with self.assertRaises(evidence.Fresh):
                evidence.merged_pr_proof(service,'e'*40,current,self.profile,controls,77)


    def history_service(self, *, newest_failure=False, newest_missing=False, omitted=False):
        original_run = deepcopy(self.run)
        previous = dict(original_run, id=102, head_sha='d' * 40)
        if newest_failure:
            previous['conclusion'] = 'failure'
        source_tree = dict(sha='b' * 40, truncated=False,
                           tree=[dict(path=evidence.WORKFLOW, mode='100644', type='blob', sha='c' * 40)])
        trusted = evidence.tree_controls(source_tree)
        def jobs(run, primary):
            rows = [primary]
            for index, name in enumerate(('Classify changes / Classify event inputs', 'Application checks')):
                rows.append(dict(id=700 + index, name=name, run_id=run['id'], run_attempt=2,
                                 head_sha=run['head_sha'], status='completed', conclusion='success'))
            return dict(total_count=len(rows), jobs=rows)
        requests = []
        def read(path, **options):
            requests.append(path)
            if path.startswith('actions/workflows/'):
                return dict(workflow_runs=[previous, original_run])
            if path == 'actions/runs/102':
                return previous
            if path == 'actions/runs/101':
                return original_run
            if path.startswith('compare/'):
                return dict(status='ahead', merge_base_commit=dict(sha=path.split('/')[1].split('...')[0]))
            if path.startswith('git/commits/'):
                return dict(sha=path.rsplit('/', 1)[1], tree=dict(sha='b' * 40))
            if path.startswith('git/trees/'):
                return source_tree
            if path == 'actions/runs/102/attempts/2/jobs?per_page=100':
                job = dict(self.job, run_id=102, head_sha='d' * 40)
                if omitted:
                    job.update(status='completed', conclusion='skipped', runner_id=0)
                return jobs(previous, job)
            if path == 'actions/runs/101/attempts/2/jobs?per_page=100':
                return jobs(original_run, self.job)
            if path == 'actions/runs/102/artifacts?per_page=100':
                return dict(total_count=0, artifacts=[])
            if path == 'actions/runs/101/artifacts?per_page=100':
                return dict(total_count=1, artifacts=[self.meta])
            if path == 'actions/artifacts/901':
                return self.meta
            if path == 'actions/artifacts/901/zip':
                assert options.get('binary') is True
                return self.raw
            raise AssertionError('unexpected request: ' + path)
        class FakeService:
            pass
        service = FakeService(); service.read = read
        return service, trusted, requests

    def test_newest_failed_or_missing_selected_producer_cannot_fall_back_to_older_pass(self):
        for variant in ('newest_failure', 'newest_missing'):
            service, trusted, requests = self.history_service(**{variant: True})
            with self.subTest(variant=variant), self.assertRaises(evidence.Fresh):
                evidence.latest_proof(service, self.inputs, self.profile, trusted, consumer_repository_id=77, trusted_dev_revision='e' * 40)
            self.assertNotIn('actions/runs/101', requests)

    def test_reused_dev_link_rechecks_latest_attempt_after_original_pr_verification(self):
        for change in (dict(run_attempt=3),dict(conclusion='failure'),dict(status='in_progress'),dict(head_sha='9'*40)):
            with self.subTest(change=change):
                service,controls,requests=self.history_service()
                original=service.read
                def read(path,**options):
                    value=original(path,**options)
                    if path=='actions/runs/102/attempts/2/jobs?per_page=100':
                        value['jobs'][0]['steps']=deepcopy(value['jobs'][0]['steps'])
                        value['jobs'][0]['steps'][-1]['conclusion']='success'
                        executed=next(step for step in value['jobs'][0]['steps'] if step['name']=='Execute full ExUnit coverage (EXECUTED)')
                        executed['conclusion']='skipped'
                    if path=='actions/runs/102' and requests.count(path)>1:return dict(value,**change)
                    return value
                service.read=read
                with patch.object(evidence,'merged_pr_proof',return_value=dict(mode='REUSED',original=dict(run_id=101))):
                    with self.assertRaises(evidence.Fresh):
                        evidence.latest_proof(service,self.inputs,self.profile,controls,consumer_repository_id=77,trusted_dev_revision='e'*40)
                self.assertEqual(requests.count('actions/runs/102'),2)


    def test_only_verified_successful_intentional_omission_can_cross_to_older_original(self):
        service, trusted, requests = self.history_service(omitted=True)
        result = evidence.latest_proof(service, self.inputs, self.profile, trusted, consumer_repository_id=77, trusted_dev_revision='e' * 40)
        self.assertEqual(result['original']['run_id'], 101)
        self.assertIn('actions/runs/102/attempts/2/jobs?per_page=100', requests)
        self.assertEqual(requests.count('actions/runs/101'), 2)


    def test_successful_foreign_history_cannot_authorize_even_with_matching_receipt(self):
        service, trusted, requests = self.history_service(omitted=True)
        original_read = service.read
        def foreign(path, **options):
            if path.startswith('compare/'):
                return dict(status='diverged', merge_base_commit=dict(sha='f' * 40))
            return original_read(path, **options)
        service.read = foreign
        with self.assertRaisesRegex(evidence.Fresh, 'protected dev history'):
            evidence.latest_proof(service, self.inputs, self.profile, trusted,
                                  consumer_repository_id=77, trusted_dev_revision='e' * 40)
        self.assertFalse(any('artifacts/' in path for path in requests))


    def caller_service(self, variant=None):
        run = dict(self.run, event='pull_request', head_branch='dev', head_sha='d' * 40)
        job = dict(self.job, status='in_progress', conclusion=None, head_sha='d' * 40)
        dev = dict(name='dev', protected=True, commit=dict(sha='d' * 40))
        repo = dict(full_name=evidence.REPOSITORY, id=77)
        pr = dict(number=123, state='open', draft=False, merge_commit_sha='e' * 40,
                  head=dict(ref='dev', sha='d' * 40, repo=repo),
                  base=dict(ref='main', sha='b' * 40, repo=repo))
        commit = dict(sha='e' * 40, parents=[dict(sha='b' * 40), dict(sha='d' * 40)])
        if variant == 'feature':
            pr['head']['ref'] = 'codex/feature'
        elif variant == 'fork':
            pr['head']['repo'] = dict(full_name='foreign/fork', id=78)
            run['head_repository'] = pr['head']['repo']
        elif variant == 'draft':
            pr['draft'] = True
        elif variant == 'unprotected':
            dev['protected'] = False
        elif variant == 'stale-merge':
            pr['merge_commit_sha'] = 'f' * 40
        elif variant == 'resolution':
            commit['parents'][0]['sha'] = 'f' * 40
        elif variant == 'malformed-parent':
            commit['parents'].append(None)
        elif variant == 'foreign-run-head':
            run['head_repository'] = dict(full_name='foreign/fork', id=78)
        routes = {'actions/runs/101': run, 'actions/runs/101/attempts/2/jobs?per_page=100': dict(total_count=1,jobs=[job]),
                  'branches/dev': dev, 'pulls/123': pr, 'git/commits/' + 'e' * 40: commit}
        class Service:
            def read(self, path):
                return routes[path]
        return Service()

    def test_current_same_repo_protected_dev_main_caller_binds_actual_tested_merge(self):
        result = evidence.current_context(self.caller_service(), run_id=101, attempt=2,
            source='e' * 40, event='pull_request', ref='refs/pull/123/merge', pr_number=123)
        self.assertTrue(result['eligible_consumer'])
        self.assertFalse(result['eligible_producer'])
        self.assertEqual((result['head'], result['base'], result['source']), ('d' * 40, 'b' * 40, 'e' * 40))

    def test_untrusted_or_stale_actual_caller_never_enters_reuse_route(self):
        for variant in ('feature', 'fork', 'draft', 'unprotected', 'stale-merge', 'resolution',
                        'malformed-parent', 'foreign-run-head'):
            with self.subTest(variant=variant):
                if variant in ('feature', 'fork', 'draft'):
                    result = evidence.current_context(self.caller_service(variant), run_id=101, attempt=2,
                        source='e' * 40, event='pull_request', ref='refs/pull/123/merge', pr_number=123)
                    self.assertFalse(result['eligible_consumer'])
                else:
                    with self.assertRaises(evidence.Fresh):
                        evidence.current_context(self.caller_service(variant), run_id=101, attempt=2,
                            source='e' * 40, event='pull_request', ref='refs/pull/123/merge', pr_number=123)


    def test_actual_protected_main_push_consumes_only_authenticated_dev_promotion(self):
        for invalid in (None,'tree','foreign','parent','unprotected'):
            with self.subTest(invalid=invalid):
                repo=dict(full_name=evidence.REPOSITORY,id=77)
                run=dict(self.run,event='push',head_branch='main',head_sha='e'*40)
                job=dict(self.job,status='in_progress',conclusion=None,head_sha='e'*40)
                commit=dict(sha='e'*40,tree=dict(sha='b'*40),parents=[dict(sha='c'*40),dict(sha='d'*40)])
                tree=dict(sha='b'*40,truncated=False,tree=[])
                promotion=dict(number=44,merged=True,state='closed',merge_commit_sha='e'*40,
                    head=dict(ref='dev',sha='d'*40,repo=repo),base=dict(ref='main',sha='c'*40,repo=repo))
                if invalid=='foreign':promotion['head']['repo']=dict(full_name='foreign/fork',id=78)
                if invalid=='parent':commit['parents'][1]['sha']='9'*40
                routes={'actions/runs/101':run,'actions/runs/101/attempts/2/jobs?per_page=100':dict(total_count=1,jobs=[job]),
                    'branches/dev':dict(name='dev',protected=True,commit=dict(sha='d'*40)),
                    'branches/main':dict(name='main',protected=invalid!='unprotected',commit=dict(sha='e'*40)),
                    'git/commits/'+'e'*40:commit,'git/trees/'+'b'*40+'?recursive=1':tree,
                    'commits/'+'e'*40+'/pulls?per_page=10':[dict(number=44,merge_commit_sha='e'*40)],'pulls/44':promotion,
                    'git/commits/'+'d'*40:dict(sha='d'*40,tree=dict(sha=('8' if invalid=='tree' else 'b')*40)),
                    'git/trees/'+'8'*40+'?recursive=1':dict(sha='8'*40,truncated=False,tree=[]),
                    'compare/'+'d'*40+'...'+'d'*40:dict(status='identical',merge_base_commit=dict(sha='d'*40))}
                class Service:
                    def read(self,path):return routes[path]
                if invalid:
                    with self.assertRaises(evidence.Fresh):evidence.current_context(Service(),run_id=101,attempt=2,source='e'*40,event='push',ref='refs/heads/main')
                else:
                    result=evidence.current_context(Service(),run_id=101,attempt=2,source='e'*40,event='push',ref='refs/heads/main')
                    self.assertTrue(result['eligible_consumer']);self.assertFalse(result['eligible_producer'])
                    self.assertEqual(result['proof_revision'],'d'*40)

    def test_bounded_request_budget_covers_three_history_rows_and_original_link(self):
        import inspect
        import elixir_coverage_bootstrap as bootstrap
        # Main context11 + tested-control2 + listing1 + two intentional omissions6each
        # + reused-dev row6 + original merged-PR verification15 + latest-dev barrier1.
        maximum=11+2+1+2*6+6+15+1
        self.assertEqual(len(bootstrap.FILES)+3,13)
        self.assertLessEqual(maximum,inspect.signature(evidence.Service).parameters['maximum_requests'].default)
        self.assertEqual(inspect.signature(evidence.Service).parameters['seconds'].default,30)


if __name__ == '__main__':
    unittest.main()
