import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("artifacts", Path(__file__).parents[1] / "artifacts.py")
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


def manifest():
    revision = "a" * 40
    return {"schema_version": 1, "version": "0.0.3", "source_revision": revision,
            "channel": "candidate", "host_architectures": ["arm64", "x86_64"],
            "bundle": {"filename": "symphony-0.0.3-macos.tar.gz", "sha256": "b" * 64},
            "runners": {provider: {arch: {"platform": platform, "image": f"localhost:5000/{provider}@sha256:" + "c" * 64,
                "source_revision": revision, "tool_versions": {tool: "1.2.3" for tool in (provider, "git", "node", "playwright")}}
                for arch, platform in artifacts.ARCHITECTURES.items()} for provider in artifacts.PROVIDERS}}


class ArtifactsTest(unittest.TestCase):
    def test_complete_release_and_internal_metadata(self):
        value = manifest()
        artifacts.validate(value)
        del value["bundle"]["sha256"]
        artifacts.validate(value, require_bundle=False)
        with self.assertRaisesRegex(ValueError, "checksum"):
            artifacts.validate(value)

    def test_reject_incomplete_or_mismatched_release(self):
        cases = [lambda x: x.update(schema_version=True), lambda x: x.update(runners=[]),
                 lambda x: x["runners"]["codex"].update(arm64=[]), lambda x: x.update(schema_version=2), lambda x: x.update(channel="preview"),
                 lambda x: x["runners"].pop("claude"), lambda x: x["runners"]["codex"].pop("x86_64"),
                 lambda x: x["runners"]["claude"]["arm64"].update(source_revision="d" * 40),
                 lambda x: x["runners"]["codex"]["arm64"].update(platform="linux/amd64"),
                 lambda x: x["runners"]["codex"]["arm64"].update(image="codex:latest"),
                 lambda x: x["runners"]["codex"]["arm64"]["tool_versions"].pop("codex"),
                 lambda x: x["bundle"].update(filename="../unsafe.tar.gz")]
        for mutate in cases:
            value = copy.deepcopy(manifest())
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifacts.validate(value)

    def test_additive_platform_manifest_retains_legacy_mac_contract(self):
        value = manifest()
        legacy = copy.deepcopy(value)
        value["bundles"] = {"macos": copy.deepcopy(value["bundle"]), "linux_x86_64": {
            "filename": "symphony-0.0.3-linux_x86_64.tar.gz", "sha256": "e" * 64}}
        artifacts.validate(value)
        self.assertEqual(value["schema_version"], legacy["schema_version"])
        self.assertEqual(value["host_architectures"], legacy["host_architectures"])
        self.assertEqual(value["bundle"], legacy["bundle"])
        mutations = [lambda v: v["bundles"].update(linux_arm64={}),
                     lambda v: v["bundles"]["linux_x86_64"].update(filename="../linux.tar.gz"),
                     lambda v: v["bundles"]["linux_x86_64"].update(sha256="invalid"),
                     lambda v: v["bundles"]["macos"].update(sha256="f" * 64)]
        for mutate in mutations:
            changed = copy.deepcopy(value); mutate(changed)
            with self.assertRaises(ValueError): artifacts.validate(changed)

    def test_duplicate_records_and_bundle_checksum(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for provider, architectures in manifest()["runners"].items():
                for architecture, runner in architectures.items():
                    path = root / f"{provider}-{architecture}.json"
                    path.write_text(json.dumps(dict(runner, provider=provider, architecture=architecture)))
                    records.append(path)
            bundle = root / "symphony-0.0.3-macos.tar.gz"
            bundle.write_bytes(b"candidate archive")
            args = SimpleNamespace(records=records, version="0.0.3", revision="a" * 40,
                                   channel="candidate", bundle=bundle, output=root / "release.json")
            artifacts.assemble(args)
            actual = json.loads(args.output.read_text())
            self.assertEqual(actual["bundle"]["sha256"], artifacts.hashlib.sha256(bundle.read_bytes()).hexdigest())
            args.records = records + records[:1]
            with self.assertRaisesRegex(ValueError, "duplicate"):
                artifacts.assemble(args)


if __name__ == "__main__":
    unittest.main()
