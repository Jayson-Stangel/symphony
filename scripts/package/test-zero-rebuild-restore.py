#!/usr/bin/env python3
"""Adversarial archive/ownership tests; no Docker daemon commands executed."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("restore", Path(__file__).with_name("restore-zero-rebuild-images.py"))
restore = importlib.util.module_from_spec(spec); spec.loader.exec_module(restore)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "archive.tar.gz"

    def archive(self, tags=None, bad_config=False):
        files = {}; manifest = []
        for role in ("candidate", "previous"):
            value = {"architecture":"arm64", "os":"linux", "config":{"Labels":{
                "com.symphony.agent":"codex", "com.symphony.architecture":"arm64",
                "org.opencontainers.image.revision":("a" if role == "candidate" else "b") * 40}}}
            raw = json.dumps(value).encode()
            name = hashlib.sha256(raw).hexdigest() + ".json"
            files[name] = raw + (b"corruption" if bad_config else b"")
            manifest.append({"Config":name,"RepoTags":tags or ["localhost:5000/symphony-codex:" + role],"Layers":[]})
        files["manifest.json"] = json.dumps(manifest).encode()
        with tarfile.open(self.path, "w:gz") as archive:
            for name, raw in files.items():
                item = tarfile.TarInfo(name); item.size = len(raw); archive.addfile(item, io.BytesIO(raw))
        return restore.archive_inventory(self.path, "codex")

    def test_every_transported_config_and_tag_including_previous_is_enumerated(self):
        inventory = self.archive()
        self.assertEqual(len(inventory), 2)
        self.assertEqual([r["tags"][0].split(":")[-1] for r in inventory], ["candidate", "previous"])
        self.assertTrue(all(r["transport_only"] for r in inventory))

    def test_remote_context_and_other_socket_are_not_authorized(self):
        restore.require_local_daemon('unix:///Users/jayson/.docker/run/docker.sock')
        for value in ('tcp://127.0.0.1:2375','ssh://host','default','unix:///tmp/foreign.sock','unix:///var/run/docker.sock'):
            with self.assertRaisesRegex(ValueError,'approved local Docker socket'):
                restore.require_local_daemon(value)

    def test_config_corruption_and_foreign_tags_refuse(self):
        with self.assertRaisesRegex(ValueError, "config SHA"):
            self.archive(bad_config=True)
        with self.assertRaisesRegex(ValueError, "foreign transported tag"):
            self.archive(tags=["user/private:latest"])

    def test_late_tag_collision_refuses_before_any_load(self):
        calls = []
        def docker(*values, **kwargs):
            calls.append(values)
            if values[-1].endswith(":previous"):
                return subprocess.CompletedProcess([], 0, '[{"Id":"foreign"}]', "")
            return subprocess.CompletedProcess([], 1, "", "Error: No such image")
        with self.assertRaisesRegex(ValueError, "load refused"):
            restore.preflight(self.archive(), docker)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(v[:2] == ("image", "inspect") for v in calls))

    def test_unknown_inspection_error_is_not_absence(self):
        def docker(*values, **kwargs):return subprocess.CompletedProcess([], 1, "", "daemon unavailable")
        with self.assertRaisesRegex(ValueError, "load refused"):
            restore.preflight(self.archive(), docker)

    def test_cleanup_preserves_replaced_foreign_tag(self):
        calls = []
        def docker(*values, **kwargs):
            calls.append(values)
            return subprocess.CompletedProcess([], 0, '[{"Id":"foreign"}]', "")
        with self.assertRaisesRegex(ValueError, "foreign replacement"):
            restore.cleanup({"owned_tags":{"owned:tag":"expected"},"owned_configs":[]}, docker)
        self.assertEqual(calls, [("image", "inspect", "owned:tag")])

    def test_cleanup_refuses_new_foreign_reference_to_owned_config(self):
        calls = []
        def docker(*values, **kwargs):
            calls.append(values)
            return subprocess.CompletedProcess([], 0, json.dumps([{"Id":"expected","RepoTags":["user:new"],"RepoDigests":[]}]), "")
        with self.assertRaisesRegex(ValueError, "foreign image references"):
            restore.cleanup({"owned_tags":{},"owned_configs":["expected"]}, docker)
        self.assertEqual(calls, [("image", "inspect", "expected")])

    def test_cleanup_requires_positive_final_absence_after_successful_rm(self):
        calls = []
        def docker(*values, **kwargs):
            calls.append(values)
            if values[:2] == ('image','rm'):return subprocess.CompletedProcess([],0,'removed','')
            return subprocess.CompletedProcess([],0,'[{"Id":"expected"}]','')
        state = {"owned_tags":{"owned:tag":"expected"},"owned_configs":[]}
        with self.assertRaisesRegex(ValueError, 'final absence uncertain'):
            restore.cleanup(state,docker)
        self.assertNotEqual(state.get('cleanup'), 'removed-owned-references')
        self.assertEqual(calls[-1], ('image','inspect','owned:tag'))

    def test_cleanup_only_granted_acquisitions_not_unattempted_planned_configs(self):
        def docker(*values, **kwargs):
            self.fail('unattempted plan grants no cleanup authority')
        state = {'owned_tags':{},'owned_configs':[], 'planned_configs':['foreign-now']}
        restore.cleanup(state,docker)
        self.assertEqual(state['cleanup'],'removed-owned-references')


if __name__ == "__main__":unittest.main()
