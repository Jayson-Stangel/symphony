import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import manager_version
import runner_reuse
from test_component_provenance import record


class ReusePlannerTest(unittest.TestCase):
    """Planner control fixtures; original native evidence is separately required."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, env=env)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "--quiet", "-m", "actual manager source"], check=True, env=env)
        self.baseline = {"runners": {provider: {arch: {k: v for k, v in record(provider, arch).items()
                                                       if k not in ("provider", "architecture")}
                                               for arch in ("arm64", "x86_64")} for provider in ("codex", "claude")}}
        self.calls = []

    def inputs(self, root, provider, architecture):
        return {"input_identity": "d" * 64, "inputs": {"provider": provider, "architecture": architecture}}

    def verify(self, value):
        self.calls.append(value)
        return {"original_artifact_id": value["qualification"]["artifact_id"]}

    def test_matching_components_keep_all_original_identities(self):
        before = copy.deepcopy(self.baseline)
        value = runner_reuse.plan(self.root, self.baseline, self.verify, self.inputs)
        self.assertEqual(self.baseline, before)
        self.assertEqual(len(self.calls), 4)
        for provider, group in value["components"].items():
            for arch, decision in group.items():
                self.assertEqual(decision["action"], "reuse")
                self.assertEqual(decision["runner"], before["runners"][provider][arch])
                self.assertNotEqual(decision["runner"]["source_revision"], value["manager_source_revision"])

    def test_same_source_schema1_bridge_requires_four_actual_current_components(self):
        value=runner_reuse.plan(self.root,self.baseline,self.verify,self.inputs,same_source_bridge=True)
        self.assertTrue(value['has_builds'])
        self.assertEqual(len(value['build_matrix']['include']),4)
        self.assertEqual(value['reuse_matrix']['include'],[])
        self.assertTrue(all(item['source_revision']==value['manager_source_revision'] for item in value['build_matrix']['include']))

    def test_changed_provider_does_not_reuse_or_requalify_other_provider(self):
        def changed(root, provider, architecture):
            result = self.inputs(root, provider, architecture)
            if provider == "claude":
                result["input_identity"] = "e" * 64
            return result
        value = runner_reuse.plan(self.root, self.baseline, self.verify, changed)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(item["action"] == "reuse" for item in value["components"]["codex"].values()))
        self.assertTrue(all(item["action"] == "build" for item in value["components"]["claude"].values()))

    def test_missing_expired_or_incompatible_origin_requests_explicit_rebuild(self):
        def unavailable(value):
            raise ValueError("expired original artifact")
        result = runner_reuse.plan(self.root, self.baseline, unavailable, self.inputs)
        self.assertTrue(all(item["action"] == "build" and "unavailable" in item["reason"]
                            for group in result["components"].values() for item in group.values()))
        empty = runner_reuse.plan(self.root, {"runners": {}}, self.verify, self.inputs)
        self.assertTrue(all(item["action"] == "build" for group in empty["components"].values() for item in group.values()))

    def test_precise_candidate_refusals_do_not_change_fail_closed_selection(self):
        def expired(record):
            raise ValueError('artifact payload has expired')
        value = runner_reuse.plan(self.root, self.baseline, expired, self.inputs)
        for group in value['components'].values():
            for decision in group.values():
                self.assertEqual(decision['candidate_decisions'][0]['outcome'], 'original_evidence_refused')
                self.assertEqual(decision['candidate_decisions'][0]['refusal'], 'expired_or_missing_payload')
                self.assertEqual(decision['action'], 'build')
        value = runner_reuse.plan(self.root, self.baseline, self.verify, self.inputs, reusable=lambda r: False)
        for group in value['components'].values():
            for decision in group.values():
                self.assertEqual(decision['candidate_decisions'][0]['outcome'], 'production_public_reference_unavailable')
                self.assertIn('anonymous canonical', decision['reason'])
                self.assertEqual(decision['action'], 'build')
        self.assertEqual(runner_reuse.evidence_refusal(ValueError('SECRET service URL')), 'incompatible_or_unavailable_evidence')

    def test_mismatched_inputs_have_explicit_unavailable_source_explanation(self):
        changed = lambda *args: dict(self.inputs(*args), input_identity='f'*64)
        value = runner_reuse.plan(self.root, self.baseline, self.verify, changed)
        for group in value['components'].values():
            for decision in group.values():
                audit = decision['candidate_decisions'][0]
                self.assertEqual(audit['outcome'], 'effective_inputs_differ')
                self.assertEqual(audit['input_comparison'], 'original_source_or_recipe_unavailable')
                self.assertEqual(audit['input_identity'], 'd'*64)
                self.assertEqual(decision['input_identity'], 'f'*64)
        self.assertEqual(self.calls, [])

    def test_malformed_untrusted_diagnostic_options_cannot_block_valid_original(self):
        source = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        for dependencies in (None, [], {'build_options': None}, {'build_options': []},
                             {'build_options': {'CODEX_VERSION': []}}):
            broken = record(); broken.update(source_revision=source, input_identity='e'*64,
                                              dependencies=dependencies)
            result = runner_reuse.plan(self.root, self.baseline, self.verify, self.inputs, candidates=[broken])
            decision = result['components']['codex']['arm64']
            self.assertEqual(decision['action'], 'reuse')
            self.assertEqual(decision['candidate_decisions'][0]['input_comparison'],
                             'original_source_or_recipe_unavailable')
            self.assertEqual(decision['record']['source_revision'], self.baseline['runners']['codex']['arm64']['source_revision'])

    def test_docker_snapshots_explain_changed_added_removed_and_control_inputs(self):
        from test_runner_inputs import RunnerInputsTest
        fixture = RunnerInputsTest(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        before = runner_reuse.identity(fixture.root, 'codex', 'arm64')
        source = fixture.git('rev-parse', 'HEAD').decode().strip()
        original = record(); original.update(source_revision=source, input_identity=before['input_identity'])
        original['dependencies']['build_options'] = before['inputs']['build_args']
        fixture.write('elixir/lib/engine.ex', 'real changed runtime')
        fixture.write('elixir/priv/asset', 'real added asset')
        (fixture.root/'elixir/WORKFLOW.md').unlink()
        fixture.commit()
        current = runner_reuse.identity(fixture.root, 'codex', 'arm64')
        reconstructed = runner_reuse.previous_inputs(fixture.root, original)
        self.assertEqual(reconstructed, before)
        delta = runner_reuse.input_delta(reconstructed, current)
        self.assertEqual(delta['changed_effective_paths'], ['elixir/WORKFLOW.md', 'elixir/lib/engine.ex', 'elixir/priv/asset'])
        self.assertIn('directories', delta['changed_input_controls'])

    def test_browser_reuse_requires_matching_root_and_agent_user_original_qualifications(self):
        for group in self.baseline['runners'].values():
            for original in group.values():
                original['browser_input_identity'] = 'd'*64
        result = runner_reuse.plan(self.root, self.baseline, self.verify, self.inputs, browser_inputs=self.inputs)
        self.assertTrue(all(item['action'] == 'reuse' and set(item['qualifications']) == {'codex','claude'}
                            for item in result['browser'].values()))
        self.baseline['runners']['claude']['arm64']['browser_input_identity'] = 'e'*64
        result = runner_reuse.plan(self.root, self.baseline, self.verify, self.inputs, browser_inputs=self.inputs)
        self.assertEqual(result['browser']['arm64']['action'], 'build')
        self.assertEqual(result['browser']['x86_64']['action'], 'reuse')

    def test_unknown_effective_input_closure_cannot_fall_back_to_whole_head_reuse(self):
        def unknown(*args):
            raise ValueError("unknown effective COPY input")
        with self.assertRaisesRegex(ValueError, "unknown effective"):
            runner_reuse.plan(self.root, self.baseline, self.verify, unknown)


class ManagerVersionTest(unittest.TestCase):
    def test_manager_release_version_has_its_own_strict_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scripts/release/manager-version"
            path.parent.mkdir(parents=True)
            path.write_text("1.2.4\n")
            self.assertEqual(manager_version.read(folder), "1.2.4")
            policy=path.with_name('release-schema');policy.write_text('1\n')
            self.assertEqual(manager_version.schema(folder),1)
            policy.write_text('2\n');self.assertEqual(manager_version.schema(folder),2)
            for invalid_policy in ('2','2.0\n','true\n','3\n',' 1\n'):
                policy.write_text(invalid_policy)
                with self.assertRaises(ValueError):manager_version.schema(folder)
            for invalid in ("1.2.4", "1.2.4\nextra\n", "1.2\n", " 1.2.4\n", "01.2.4\n", "1.2.4-a..b\n", "1.2.4-01\n", "1.2.4-.\n"):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    manager_version.read(folder)

    def test_tag_preflight_refuses_completed_or_unavailable_release_before_fanout(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'scripts/release'; directory.mkdir(parents=True)
            (directory / 'manager-version').write_text('0.0.5\n')
            (directory / 'release-schema').write_text('2\n')
            calls = []
            def response(code, value):
                def run(args, **kwargs):
                    calls.append(args)
                    self.assertEqual(kwargs['timeout'], 30)
                    return subprocess.CompletedProcess(args, code, json.dumps(value), 'SENSITIVE ERROR MUST NOT ESCAPE')
                return run
            for code, value in ((1, {'status': '404'}), (0, {'tag_name': 'v0.0.5', 'draft': True})):
                self.assertEqual(manager_version.validate_release_target(folder, 'v0.0.5', response(code, value)), 'v0.0.5')
            for code, value in ((0, {'tag_name': 'v0.0.5', 'draft': False}),
                                (0, {'tag_name': 'v0.0.4', 'draft': True}), (0, {'draft': True}),
                                (0, {'tag_name': 'v0.0.5', 'draft': 'true'}), (0, []),
                                (1, {'status': '403'}), (1, {'status': '500'}), (1, {})):
                with self.subTest(code=code, value=value), self.assertRaises(ValueError) as error:
                    manager_version.validate_release_target(folder, 'v0.0.5', response(code, value))
                self.assertNotIn('SENSITIVE', str(error.exception))
            calls.clear()
            with patch.dict(os.environ, GITHUB_REPOSITORY='foreign/fork'), self.assertRaisesRegex(ValueError, 'supports only'):
                manager_version.validate_release_target(folder, 'v0.0.5', response(1, {'status': '404'}))
            self.assertEqual(calls, [])
            for tag in ('v0.0.4', 'v0.0.5-rc', 'v0.0.5/foreign', 'v0.0.5\n'):
                with self.assertRaises(ValueError):
                    manager_version.validate_release_target(folder, tag, response(1, {'status': '404'}))
            self.assertEqual(calls, [], 'invalid tags must refuse before even a service lookup')
            for stdout in ('not json', ''):
                with self.assertRaisesRegex(ValueError, 'service unavailable'):
                    manager_version.validate_release_target(folder, 'v0.0.5',
                        lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout, 'private service error'))
            for exception in (OSError('private service error'), subprocess.TimeoutExpired('gh', 30)):
                def unavailable(*args, **kwargs):
                    raise exception
                with self.assertRaisesRegex(ValueError, 'service unavailable'):
                    manager_version.validate_release_target(folder, 'v0.0.5', unavailable)


if __name__ == "__main__":
    unittest.main()
