import copy
import hashlib
import io
import zipfile
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import component_join
from test_component_provenance import record


class ComponentJoinTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.plan = {'planning_complete': True, 'manager_source_revision': 'a'*40,
                     'components': {}}
        for provider in ('codex', 'claude'):
            self.plan['components'][provider] = {}
            for architecture in ('arm64', 'x86_64'):
                original = record(provider, architecture)
                self.plan['components'][provider][architecture] = {
                    'action': 'reuse', 'record': original, 'origin':{'exact':'origin'}, 'input_identity': original['input_identity']}
        self.calls = []

    def verify(self, value):
        self.calls.append(copy.deepcopy(value))
        return {"exact":"origin"}

    def test_reused_originals_are_verified_without_reexport_or_new_source_labels(self):
        selected = component_join.join(self.plan, self.root, 'a'*40, self.verify)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(value['source_revision'] == 'b'*40 for value in selected))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_build_must_supply_successful_exact_current_source_input_record(self):
        choice = self.plan['components']['codex']['arm64']
        choice['action'] = 'build'
        path = self.root/'codex-arm64.json'
        path.write_text(json.dumps(choice['record']))
        with self.assertRaisesRegex(ValueError, 'source differs'):
            component_join.join(self.plan, self.root, 'a'*40, self.verify)
        actual = copy.deepcopy(choice['record'])
        actual['source_revision'] = 'a'*40
        path.write_text(json.dumps(actual))
        self.assertEqual(len(component_join.join(self.plan, self.root, 'a'*40, self.verify)), 4)
        actual['input_identity'] = 'f'*64
        path.write_text(json.dumps(actual))
        with self.assertRaisesRegex(ValueError, 'differs from plan'):
            component_join.join(self.plan, self.root, 'a'*40, self.verify)

    def test_reused_native_origin_must_still_equal_the_exact_plan_selection(self):
        self.plan['components']['claude']['x86_64']['origin']={'changed':'origin'}
        with self.assertRaisesRegex(ValueError,'origin changed after planning'):
            component_join.join(self.plan,self.root,'a'*40,self.verify)

    def test_foreign_manager_plan_or_unavailable_origin_cannot_assemble(self):
        with self.assertRaisesRegex(ValueError, 'current manager'):
            component_join.join(self.plan, self.root, 'f'*40, self.verify)
        def expired(value):
            raise ValueError('payload expired')
        with self.assertRaisesRegex(ValueError, 'expired'):
            component_join.join(self.plan, self.root, 'a'*40, expired)


class ComponentJoinEntrypointTest(unittest.TestCase):
    """Actual CLI, selection and native verifiers; only GitHub HTTP is synthetic."""
    def test_manual_integration_current_build_and_selected_original_reuse(self):
        from runner_inputs import identity
        import component_provenance as provenance
        import manager_version
        source = Path(__file__).resolve().parents[3]
        real_output = subprocess.check_output
        with tempfile.TemporaryDirectory(prefix='component-join-cli-') as folder:
            root = Path(folder) / 'checkout'
            subprocess.run(['git', 'clone', '--shared', '--quiet', str(source), str(root)], check=True)
            revision = real_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            records = root / 'native-records'; records.mkdir()
            plan = {'planning_complete': True, 'manager_source_revision': revision, 'components': {}}
            originals, responses, metadata_by_pair, jobs_by_pair = {}, {}, {}, {}
            run = {'id': 123, 'head_sha': revision, 'event': 'workflow_dispatch',
                   'repository': {'full_name': provenance.REPOSITORY},
                   'head_repository': {'full_name': provenance.REPOSITORY},
                   'path': '.github/workflows/runner-integration.yml', 'conclusion': 'failure'}
            jobs = {'total_count': 4, 'jobs': []}; inventory = {'total_count': 4, 'artifacts': []}
            responses['actions/runs/123'] = run
            responses['actions/runs/123/attempts/1/jobs?per_page=100'] = jobs
            responses['actions/runs/123/artifacts?per_page=100'] = inventory
            for provider in ('codex', 'claude'):
                plan['components'][provider] = {}
                for architecture in ('arm64', 'x86_64'):
                    index = len(originals); payload_id, metadata_id = 1000 + index, 2000 + index
                    original = record(provider, architecture)
                    original['source_revision'] = revision
                    original['input_identity'] = identity(root, provider, architecture)['input_identity']
                    original['qualification'].update(source_revision=revision, controller_revision=revision,
                        input_identity=original['input_identity'], artifact_id=payload_id)
                    originals[provider, architecture] = original
                    (records / f'{provider}-{architecture}.json').write_text(json.dumps(original))
                    plan['components'][provider][architecture] = {'action': 'build', 'input_identity': original['input_identity']}
                    payload = {'id': payload_id, 'name': original['qualification']['artifact_name'],
                               'digest': original['qualification']['artifact_digest'], 'expired': False,
                               'expires_at': '2099-01-01T00:00:00Z', 'size_in_bytes': 1234,
                               'workflow_run': {'id': 123, 'head_sha': revision}}
                    archive = io.BytesIO()
                    with zipfile.ZipFile(archive, 'w') as bundle:
                        bundle.writestr(f'{provider}-{architecture}.json', json.dumps(original))
                    raw = archive.getvalue()
                    metadata = dict(payload, id=metadata_id, name=f'runner-records-{provider}-{architecture}',
                                    size_in_bytes=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest())
                    metadata_by_pair[provider, architecture] = metadata
                    inventory['artifacts'].append(metadata)
                    responses[f'actions/artifacts/{payload_id}'] = payload
                    responses[f'actions/artifacts/{metadata_id}/zip'] = raw
                    job = {'name': original['qualification']['job_name'], 'status': 'completed',
                           'conclusion': 'success', 'labels': ['ubuntu-24.04-arm' if architecture == 'arm64' else 'ubuntu-24.04']}
                    jobs_by_pair[provider, architecture] = job
                    jobs['jobs'].append(job)
            responses['actions/runs/456'] = dict(run, id=456)
            responses['actions/runs/456/attempts/1/jobs?per_page=100'] = {'total_count': 0, 'jobs': []}
            responses['actions/runs/456/artifacts?per_page=100'] = {'total_count': 0, 'artifacts': []}
            def assign_run(provider, architecture, run_id):
                pair = provider, architecture
                original = originals[pair]; original['qualification']['run_id'] = run_id
                payload_id = original['qualification']['artifact_id']; metadata = metadata_by_pair[pair]
                responses[f'actions/artifacts/{payload_id}']['workflow_run'] = {'id': run_id, 'head_sha': revision}
                metadata['workflow_run'] = {'id': run_id, 'head_sha': revision}
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(f'{provider}-{architecture}.json', json.dumps(original))
                raw = archive.getvalue()
                responses[f"actions/artifacts/{metadata['id']}/zip"] = raw
                metadata.update(size_in_bytes=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest())
                (records / f'{provider}-{architecture}.json').write_text(json.dumps(original))
                for selected_run in (123, 456):
                    pairs = [key for key, value in originals.items() if value['qualification']['run_id'] == selected_run]
                    responses[f'actions/runs/{selected_run}/attempts/1/jobs?per_page=100'].update(
                        total_count=len(pairs), jobs=[jobs_by_pair[key] for key in pairs])
                    responses[f'actions/runs/{selected_run}/artifacts?per_page=100'].update(
                        total_count=len(pairs), artifacts=[metadata_by_pair[key] for key in pairs])
            http_reads = []
            def fetch(path, binary=False):
                http_reads.append(path)
                return copy.deepcopy(responses[path])
            def http_transport(argv, *args, **kwargs):
                if argv[:3] == ['gh', 'api', '--help']:
                    return '--allow-escape-sequences' if kwargs.get('text') else b'--allow-escape-sequences'
                if argv[:2] != ['gh', 'api']:
                    return real_output(argv, *args, **kwargs)
                prefix = 'repos/' + provenance.REPOSITORY + '/'
                self.assertTrue(argv[-1].startswith(prefix))
                value = fetch(argv[-1][len(prefix):])
                return value if isinstance(value, bytes) else json.dumps(value).encode()
            previous = Path.cwd()
            try:
                os.chdir(root)
                for mode in ('current-build', 'mixed-current-original', 'selected-historical-reuse'):
                    with self.subTest(mode=mode):
                        if mode != 'current-build':
                            for (provider, architecture), original in originals.items():
                                if mode == 'selected-historical-reuse' or provider == 'codex':
                                    assign_run(provider, architecture, 456)
                                    plan['components'][provider][architecture].update(action='reuse', record=original,
                                        origin=provenance.verify_origin(original, fetch=fetch, candidate_run=456))
                        plan_path = root / 'selected-plan.json'; plan_path.write_text(json.dumps(plan))
                        output = root / mode
                        argv = ['component_join.py', '--plan', str(plan_path), '--records', str(records),
                                '--output', str(output), '--schema', '2']
                        if mode != 'current-build': argv += ['--baseline-run', '456']
                        http_reads.clear()
                        with patch.object(sys, 'argv', argv), patch.dict(os.environ,
                                GITHUB_SHA=revision, GITHUB_RUN_ID='789' if mode == 'selected-historical-reuse' else '123',
                                GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY=provenance.REPOSITORY), \
                                patch.object(subprocess, 'check_output', side_effect=http_transport):
                            component_join.main()
                        self.assertEqual(sum(path.endswith('/zip') for path in http_reads), 8,
                                         'join and assembly must both verify all four original records')
                        manifest = json.loads((output / 'release.json').read_text())
                        self.assertEqual(manifest['schema_version'], 2)
                        self.assertEqual(manifest['version'], manager_version.read(root))
                        self.assertEqual(manifest['source_revision'], revision)
                        for (provider, architecture), original in originals.items():
                            self.assertEqual(json.loads((output / f'{provider}-{architecture}.json').read_text()), original)
                            self.assertEqual(manifest['runners'][provider][architecture],
                                             {k: v for k, v in original.items() if k not in ('provider', 'architecture')})
                # Run refusal controls through the same CLI and real verifier.
                for provider in plan['components']:
                    for architecture, decision in plan['components'][provider].items():
                        assign_run(provider, architecture, 123)
                        plan['components'][provider][architecture] = {'action': 'build', 'input_identity': decision['input_identity']}
                plan_path.write_text(json.dumps(plan))
                for field, value in [('event', 'push'), ('path', '.github/workflows/untrusted.yml'),
                                     ('repository', {'full_name': 'foreign/repository'}),
                                     ('head_repository', {'full_name': 'foreign/fork'}), ('head_sha', 'f' * 40)]:
                    original = run[field]; run[field] = value
                    try:
                        self.refuses(argv[:argv.index('--baseline-run')], root, revision, http_transport)
                    finally: run[field] = original
                for target, field, value in [(jobs['jobs'][0], 'conclusion', 'failure'),
                        (responses['actions/artifacts/1000'], 'expired', True),
                        (responses['actions/artifacts/1000'], 'digest', 'sha256:' + '0' * 64)]:
                    original = target[field]; target[field] = value
                    try: self.refuses(argv[:argv.index('--baseline-run')], root, revision, http_transport)
                    finally: target[field] = original
                original = responses['actions/artifacts/2000/zip']; responses['actions/artifacts/2000/zip'] += b'changed'
                try: self.refuses(argv[:argv.index('--baseline-run')], root, revision, http_transport)
                finally: responses['actions/artifacts/2000/zip'] = original
            finally:
                os.chdir(previous)

    def refuses(self, argv, root, revision, http_transport):
        output = root / 'refused'
        argv = list(argv); argv[argv.index('--output') + 1] = str(output)
        with patch.object(sys, 'argv', argv), patch.dict(os.environ, GITHUB_SHA=revision,
                GITHUB_RUN_ID='123', GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='Jayson-Stangel/symphony'), \
                patch.object(subprocess, 'check_output', side_effect=http_transport), self.assertRaises(SystemExit):
            component_join.main()
        self.assertFalse(output.exists(), 'refused evidence must not publish any candidate metadata')
