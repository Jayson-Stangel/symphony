#!/usr/bin/env python3
"""Actual filesystem-only stock→B→version-only-C update/retry/rollback.

Requires frozen real packages and the exact C source checkout. All images and
original qualification records stay unchanged. No daemon commands execute;
container, Linux and original distribution availability remain separate proof.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import unittest

spec = importlib.util.spec_from_file_location("stock_fixture", Path(__file__).with_name("test-zero-rebuild-stock.py"))
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)


class ManagerPair(stock.NativeCase):
    def setUp(self):
        # Compose the actual approved stock fixture without inheriting/rerunning
        # its unrelated cases. This fixture owns only its temporary home.
        self.fixture = stock.EnvelopeHandoff("test_actual_stock_upgrade_retry_and_rollback_preserve_identity_and_data")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        f = self.fixture
        self.manifest = Path(os.environ["SYMPHONY_NEXT_MANIFEST"]).resolve()
        self.archive = Path(os.environ["SYMPHONY_NEXT_BUNDLE"]).resolve()
        self.source = Path(os.environ["SYMPHONY_NEXT_SOURCE"]).resolve()
        self.value = json.loads(self.manifest.read_text())
        self.revision = self.value["manager_envelope"]["manager_source_revision"]
        self.assertNotEqual(self.revision, f.manager)
        self.assertEqual(f.ok(stock.run("git", "-C", self.source, "rev-parse", "HEAD")), self.revision)
        self.assertEqual(f.ok(stock.run("git", "-C", self.source, "rev-parse", "HEAD^")), f.manager)
        self.assertEqual(f.ok(stock.run("git", "-C", self.source, "diff", "--name-only", "HEAD^", "HEAD")),
                         "scripts/release/manager-version")
        self.assertEqual(f.ok(stock.run("git", "-C", self.source, "status", "--porcelain", "--untracked-files=all")), "")
        self.assertEqual((self.source / "scripts/release/manager-version").read_text().strip(),
                         self.value["manager_envelope"]["base_version"])
        self.assertEqual(stock.digest(self.archive), self.value["bundle"]["sha256"])
        self.folder = f.root / "next"; self.folder.mkdir()
        stock.extract(self.archive, self.folder)
        helper = self.folder / "libexec/symphony-host"
        self.assertEqual(f.ok(stock.run("lipo", "-archs", helper)), "arm64")
        self.assertEqual(f.ok(stock.run(helper, "build-revision")), self.revision)
        f.ok(stock.run(helper, "validate-manager", self.folder / "release.json"))
        f.ok(stock.run(f.helper, "validate-release", self.folder / "release.json"))
        before = json.loads((f.candidate / "manager-core.json").read_text())
        after = json.loads((self.folder / "manager-core.json").read_text())
        self.assertEqual(before["original_components"], after["original_components"])
        self.assertEqual(before["verified_origins"], after["verified_origins"])
        self.assertEqual(before["distribution"], after["distribution"])
        self.assertEqual(before["supported_hosts"], after["supported_hosts"])
        self.assertEqual(before["legacy_source_revision"], after["legacy_source_revision"])

    def assert_identity(self, revision, descriptor):
        value = self.fixture.identity()
        self.assertEqual(value["source_revision"], revision)
        self.assertEqual(value["version"], descriptor["version"])
        self.assertEqual(value["base_version"], descriptor["manager_envelope"]["base_version"])
        self.assertEqual(value["storage_revision"], descriptor["source_revision"])

    def test_actual_two_manager_update_checksum_refusal_retry_and_rollback(self):
        f = self.fixture
        f.ok(f.update())
        self.assert_identity(f.manager, f.target)
        before = f.links()["app/current"]
        f.preserved()
        f.ok(f.update(self.manifest, self.archive))
        self.assert_identity(self.revision, self.value)
        after = f.links()["app/current"]
        self.assertNotEqual(before, after)
        f.preserved()
        f.ok(stock.run(f.command, "update", "--rollback-release", env=f.env))
        self.assert_identity(f.manager, f.target)
        self.assertEqual(f.links()["app/current"], before)
        f.preserved()
        bad = copy.deepcopy(self.value)
        bad["bundle"]["sha256"] = "0" * 64
        bad["bundles"]["macos"]["sha256"] = "0" * 64
        folder = f.root / "corrupt"; folder.mkdir()
        shutil.copyfile(self.folder / "manager-core.json", folder / "manager-core.json")
        manifest = folder / "release.json"; manifest.write_text(json.dumps(bad))
        links = f.links()
        f.refused_update(manifest, self.archive, r"checksum")
        self.assertEqual(f.links(), links)
        self.assert_identity(f.manager, f.target)
        f.preserved()
        f.ok(f.update(self.manifest, self.archive))
        self.assert_identity(self.revision, self.value)
        self.assertEqual(f.links()["app/current"], after)
        f.preserved()
        f.ok(f.update(self.manifest, self.archive))
        self.assertEqual(f.links()["app/current"], after)
        f.ok(stock.run(f.command, "update", "--rollback-release", env=f.env))
        self.assert_identity(f.manager, f.target)
        self.assertEqual(f.links()["app/current"], before)
        f.preserved()


if __name__ == "__main__":unittest.main()
