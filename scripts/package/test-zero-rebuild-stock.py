#!/usr/bin/env python3
"""Actual stock Mac ARM handoff tests. Never build helpers or invoke Docker.

Run StockContract alone before the new candidate exists. EnvelopeHandoff requires
SYMPHONY_ENVELOPE_MANIFEST and SYMPHONY_ENVELOPE_BUNDLE, produced by the real
packer from one frozen source revision. Missing inputs are errors, not skips.
Runner metadata in the initial installation is a fixture, not image evidence.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import unittest

STOCK_REVISION = "9e1b61e4fdbf8c5a5c4154fba8d4ddecd91a7ecb"
STOCK_SOURCE = Path(os.environ.get("SYMPHONY_STOCK_SOURCE", "/private/tmp/symphony-six-issues/stock-9e1-zero-rebuild-source"))
STOCK_HELPER = Path(os.environ.get("SYMPHONY_STOCK_HELPER", "/private/tmp/symphony-six-issues/stock-9e1-zero-rebuild-arm64-helper"))


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], text=True, capture_output=True, timeout=60, **kwargs)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stock_manifest(version="0.0.5"):
    runners = {p: {a: dict(platform=platform, image="ghcr.io/fixture/" + p + "@sha256:" + "b" * 64,
        source_revision=STOCK_REVISION, tool_versions={t: "fixture" for t in (p, "git", "node", "playwright")})
        for a, platform in (("arm64", "linux/arm64"), ("x86_64", "linux/amd64"))} for p in ("codex", "claude")}
    return dict(schema_version=1, version=version, source_revision=STOCK_REVISION, channel="candidate",
        host_architectures=["arm64", "x86_64"], runners=runners,
        bundle=dict(filename="symphony-" + version + "-macos.tar.gz"))


def extract(archive, destination):
    # The input is local, but reject traversal and link extraction explicitly.
    with tarfile.open(archive, "r:gz") as stream:
        for item in stream.getmembers():
            name = Path(item.name)
            if name.is_absolute() or ".." in name.parts or not (item.isfile() or item.isdir()):
                raise ValueError("unsafe archive member: " + item.name)
        stream.extractall(destination)


class NativeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin" or os.uname().machine != "arm64":
            raise RuntimeError("This suite requires actual macOS ARM64; no architecture replay")
        for command, expected in (((STOCK_HELPER, "build-revision"), STOCK_REVISION),
                                  (("git", "-C", STOCK_SOURCE, "rev-parse", "HEAD"), STOCK_REVISION),
                                  (("lipo", "-archs", STOCK_HELPER), "arm64")):
            result = run(*command)
            if result.returncode or result.stdout.strip() != expected:
                raise RuntimeError("stock fixture identity mismatch: " + result.stdout + result.stderr)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="symphony-stock-envelope-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return path


class StockContract(NativeCase):
    def test_stock_native_package_installs_in_isolated_home(self):
        internal = self.write("stock.json", stock_manifest())
        output = self.root / "output"
        self.ok(run("bash", STOCK_SOURCE / "scripts/release/package.sh", internal, output, STOCK_HELPER))
        bundle = self.root / "bundle"; bundle.mkdir()
        extract(output / stock_manifest()["bundle"]["filename"], bundle)
        self.assertEqual(self.ok(run("lipo", "-archs", bundle / "libexec/symphony-host")), "arm64")
        forbidden = self.root / "forbidden-docker"
        forbidden.write_text('#!/bin/sh\ntouch "$TEST_DOCKER_CALLS"\nexit 97\n')
        forbidden.chmod(0o755)
        env = dict(os.environ, HOME=str(self.root / "home"), SYMPHONY_HOME="",
            SYMPHONY_APP_HOME=str(self.root / "app"), SYMPHONY_CONFIG_HOME=str(self.root / "config"),
            SYMPHONY_BIN_DIR=str(self.root / "bin"), SYMPHONY_SKILLS_DIR=str(self.root / "skills"),
            SYMPHONY_CONTAINER_RUNTIME=str(forbidden), TEST_DOCKER_CALLS=str(self.root / "docker.calls"))
        self.ok(run(bundle / "install.sh", env=env))
        command = self.root / "bin/symphony"
        identity = json.loads(self.ok(run(command, "--version", env=env)))
        self.assertEqual(identity["source_revision"], STOCK_REVISION)
        self.ok(run(bundle / "install.sh", env=env))
        self.assertFalse((self.root / "docker.calls").exists())

    def test_stock_accepts_bound_prerelease_but_refuses_build_metadata(self):
        version = "0.0.5-manager." + "c" * 40 + ".manifest." + "d" * 64
        value = stock_manifest(version)
        self.ok(run(STOCK_HELPER, "validate-manifest", self.write("legal.json", value)))
        value = stock_manifest(version.replace("-manager", "+manager"))
        self.assertNotEqual(run(STOCK_HELPER, "validate-manifest", self.write("illegal.json", value)).returncode, 0)

    def test_stock_requires_common_original_source(self):
        value = stock_manifest()
        value["runners"]["claude"]["arm64"]["source_revision"] = "c" * 40
        self.assertNotEqual(run(STOCK_HELPER, "validate-manifest", self.write("mixed.json", value)).returncode, 0)


class EnvelopeHandoff(NativeCase):
    def setUp(self):
        super().setUp()
        # No automatic build, fake envelope, or silent skip when proof is absent.
        self.external = Path(os.environ["SYMPHONY_ENVELOPE_MANIFEST"]).resolve()
        self.archive = Path(os.environ["SYMPHONY_ENVELOPE_BUNDLE"]).resolve()
        self.target = json.loads(self.external.read_text())
        self.envelope = self.target["manager_envelope"]
        self.manager = self.envelope["manager_source_revision"]
        self.candidate = self.root / "candidate"; self.candidate.mkdir()
        extract(self.archive, self.candidate)
        self.helper = self.candidate / "libexec/symphony-host"
        self.assertEqual(self.ok(run("lipo", "-archs", self.helper)), "arm64")
        self.assertEqual(self.ok(run(self.helper, "build-revision")), self.manager)
        self.ok(run(self.helper, "validate-manager", self.candidate / "release.json"))
        self.assertEqual(digest(self.archive), self.target["bundle"]["sha256"])
        self.install_stock()

    def install_stock(self):
        internal = self.write("stock-internal.json", stock_manifest())
        output = self.root / "stock-output"
        # Explicit supplied native helper avoids stock's universal build default.
        self.ok(run("bash", STOCK_SOURCE / "scripts/release/package.sh", internal, output, STOCK_HELPER))
        installed = self.root / "stock-bundle"; installed.mkdir()
        extract(output / stock_manifest()["bundle"]["filename"], installed)
        tools = self.root / "tools"; tools.mkdir()
        docker = tools / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_CALLS"\nexit 97\n')
        docker.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.root / "home"), PATH=str(tools) + ":/usr/bin:/bin",
            SYMPHONY_HOME="", SYMPHONY_APP_HOME=str(self.root / "app"), SYMPHONY_CONFIG_HOME=str(self.root / "config"),
            SYMPHONY_BIN_DIR=str(self.root / "bin"), SYMPHONY_SKILLS_DIR=str(self.root / "skills"),
            SYMPHONY_CONTAINER_RUNTIME=str(docker), TEST_DOCKER_CALLS=str(self.root / "docker.calls"))
        self.ok(run(installed / "install.sh", env=self.env))
        self.command = self.root / "bin/symphony"
        self.assertEqual(self.identity()["source_revision"], STOCK_REVISION)
        self.canaries = {}
        for name in ("config/project/worktree/dirty", "config/project/state", "config/credentials", "config/WORKFLOW.md"):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("preserve " + name).encode() + b"\x00\n")
            self.canaries[path] = path.read_bytes()

    def identity(self):
        return json.loads(self.ok(run(self.command, "--version", env=self.env)))

    def update(self, manifest=None, archive=None):
        return run(self.command, "update", "--candidate-manifest", manifest or self.external,
                   "--candidate-bundle", archive or self.archive, env=self.env)

    def links(self):
        return {name: os.readlink(self.root / name) if (self.root / name).is_symlink() else None
                for name in ("app/current", "app/previous", "bin/symphony")}

    def preserved(self):
        for path, content in self.canaries.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.root / "docker.calls").exists(), "fixture must make zero Docker calls")
        self.assertFalse((self.root / "app/pending-transition").exists())

    def repack(self, change):
        value = json.loads((self.candidate / "release.json").read_text())
        change(value)
        (self.candidate / "release.json").write_text(json.dumps(value))
        inventory = self.candidate / "files.sha256"
        inventory.write_text("".join(digest(p) + "  ./" + p.relative_to(self.candidate).as_posix() + "\n"
            for p in sorted(self.candidate.rglob("*")) if p.is_file() and p != inventory))
        archive = self.root / "altered.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for path in sorted(self.candidate.iterdir()):
                stream.add(path, arcname="./" + path.name)
        outer = copy.deepcopy(self.target); outer["bundle"]["sha256"] = digest(archive)
        if "bundles" in outer:
            outer["bundles"]["macos"]["sha256"] = digest(archive)
        return self.write("altered-external.json", outer), archive

    def test_actual_stock_upgrade_retry_and_rollback_preserve_identity_and_data(self):
        self.ok(self.update())
        observed = self.identity()
        self.assertEqual(observed["source_revision"], self.manager)
        self.assertEqual(observed["version"], self.target["version"])
        self.assertEqual(observed["base_version"], self.envelope["base_version"])
        self.assertEqual(observed["storage_revision"], self.target["source_revision"])
        self.assertNotEqual(self.manager, self.target["source_revision"])
        active = self.links()["app/current"]
        self.assertTrue(active.endswith(self.target["version"] + "-" + self.target["source_revision"]))
        self.preserved()
        self.ok(self.update())
        self.assertEqual(self.links()["app/current"], active)
        self.ok(run(self.command, "update", "--rollback-release", env=self.env))
        self.assertEqual(self.identity()["source_revision"], STOCK_REVISION)
        self.preserved()
        self.ok(self.update())
        self.assertEqual(self.identity()["source_revision"], self.manager)
        self.preserved()

    def test_outer_b_inner_c_version_refuses_before_activation(self):
        before = self.links()
        def changed_version(value):
            old = value["version"]
            value["version"] = old.replace(self.manager, "c" * 40)
            # Keep the inner descriptor individually valid to the stock helper;
            # its outer/inner comparison must reject the different bound token.
            value["bundle"]["filename"] = value["bundle"]["filename"].replace(old, value["version"])
            for bundle in value.get("bundles", {}).values():
                bundle["filename"] = bundle["filename"].replace(old, value["version"])
        outer, archive = self.repack(changed_version)
        self.ok(run(STOCK_HELPER, "validate-manifest", self.candidate / "release.json"))
        self.assertNotEqual(self.update(outer, archive).returncode, 0)
        self.assertEqual(self.links(), before)
        self.assertEqual(self.identity()["source_revision"], STOCK_REVISION)
        self.preserved()

    def test_lying_inner_manager_c_with_bound_b_token_refuses_before_activation(self):
        before = self.links()
        outer, archive = self.repack(lambda value: value["manager_envelope"].update(manager_source_revision="c" * 40))
        self.assertNotEqual(self.update(outer, archive).returncode, 0)
        self.assertEqual(self.links(), before)
        self.preserved()

    def test_resealed_inventory_cannot_replace_bound_core_before_activation(self):
        before = self.links()
        core = self.candidate / "manager-core.json"
        value = json.loads(core.read_text())
        value["manager_source_revision"] = "c" * 40
        core.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        # Recompute transport/inventory checksums deliberately: the independent
        # core D commitment, rather than incidental archive corruption, must fail.
        outer, archive = self.repack(lambda value: None)
        self.assertNotEqual(self.update(outer, archive).returncode, 0)
        self.assertEqual(self.links(), before)
        self.assertEqual(self.identity()["source_revision"], STOCK_REVISION)
        self.preserved()

    def test_core_wire_limits_refuse_even_with_recomputed_binding(self):
        mutations = {
            "ascii-del": lambda core: core["original_components"]["codex"]["arm64"]["tool_versions"].update(git="\x7f"),
            "negative": lambda core: core["verified_origins"]["codex"]["arm64"].update(run_id=-1),
            "overflow": lambda core: core["verified_origins"]["codex"]["arm64"].update(run_id=9007199254740992),
            "oversize": lambda core: core["original_components"]["codex"]["arm64"]["tool_versions"].update(git="x" * (1024 * 1024)),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                folder = self.root / ("wire-" + name); folder.mkdir()
                core = json.loads((self.candidate / "manager-core.json").read_text())
                mutation(core)
                raw = (json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
                (folder / "manager-core.json").write_bytes(raw)
                value = json.loads((self.candidate / "release.json").read_text())
                old = value["version"]
                sha = hashlib.sha256(raw).hexdigest()
                value["manager_envelope"]["core_sha256"] = sha
                value["version"] = old.replace(self.envelope["core_sha256"], sha)
                for bundle in [value["bundle"], *value.get("bundles", {}).values()]:
                    bundle["filename"] = bundle["filename"].replace(old, value["version"])
                for provider, group in value["runners"].items():
                    for architecture, runner in group.items():
                        runner["tool_versions"] = core["original_components"][provider][architecture]["tool_versions"]
                manifest = folder / "release.json"; manifest.write_text(json.dumps(value))
                self.assertNotEqual(run(self.helper, "validate-release", manifest).returncode, 0)

    def test_different_compiled_manager_refuses_bound_b_before_activation(self):
        # A real source-C helper is supplied by the controller; never synthesize
        # its revision or compile it implicitly inside this adversarial suite.
        other = Path(os.environ["SYMPHONY_ENVELOPE_OTHER_HELPER"])
        other_revision = self.ok(run(other, "build-revision"))
        self.assertNotEqual(other_revision, self.manager)
        self.assertEqual(self.ok(run("lipo", "-archs", other)), "arm64")
        before = self.links()
        shutil.copyfile(other, self.candidate / "libexec/symphony-host")
        outer, archive = self.repack(lambda value: None)
        self.assertNotEqual(self.update(outer, archive).returncode, 0)
        self.assertEqual(self.links(), before)
        self.assertEqual(self.identity()["source_revision"], STOCK_REVISION)
        self.preserved()


if __name__ == "__main__":
    unittest.main()
