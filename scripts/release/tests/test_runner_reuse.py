import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
            for invalid in ("1.2.4", "1.2.4\nextra\n", "1.2\n", " 1.2.4\n"):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    manager_version.read(folder)


if __name__ == "__main__":
    unittest.main()
