"""The source and native package predicates enforce the same mount ownership."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]


class WorkflowMountTest(unittest.TestCase):
    def check_backend(self, command):
        root = "/projects/project with spaces"
        for platform in ("Linux", "Darwin"):
            for destination, source in (("/etc/symphony", root),
                                        ("/etc/symphony/WORKFLOW.md", root + "/WORKFLOW.md")):
                for projection in ("", "/host_mnt"):
                    mount = dict(Type="bind", Source=projection + source, Destination=destination, RW=False)
                    for layout in ("owned", "directory"):
                        valid = (not projection or platform == "Darwin") and (layout == "owned" or destination == "/etc/symphony")
                        self.assert_mount(command, [mount], root, platform, layout, valid)
                        for field, value in (("Type", "volume"), ("RW", True), ("Source", source + ".foreign"),
                                             ("Destination", "/foreign")):
                            self.assert_mount(command, [{**mount, field: value}], root, platform, layout, False)
        good = dict(Type="bind", Source=root, Destination="/etc/symphony", RW=False)
        for destination in ("/etc", "/", "/etc/symphony", "/etc/symphony/WORKFLOW.md", "/etc/symphony/hooks"):
            self.assert_mount(command, [good, {**good, "Destination": destination}], root, "Linux", "owned", False)
        self.assert_mount(command, [], root, "Linux", "owned", False)
        self.assert_mount(command, [good, dict(Type="volume", Destination="/workspaces")], root, "Linux", "directory", True)

    def assert_mount(self, command, mounts, root, platform, layout, expected):
        result = subprocess.run(command + [root, platform, layout], input=json.dumps(mounts),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, expected, (command, mounts, platform, layout, result.stderr))

    def test_source(self):
        self.check_backend(["bash", "-c", 'source "$1" --help >/dev/null; host_json workflow-mount "$2" "$3" "$4"',
                            "mount-test", str(ROOT / "scripts/symphony")])

    def test_native(self):
        helper = os.environ.get("SYMPHONY_TEST_HOST_HELPER")
        if not helper:
            self.skipTest("native helper requires SYMPHONY_TEST_HOST_HELPER")
        self.check_backend([helper, "json", "workflow-mount"])


if __name__ == "__main__":
    unittest.main()
