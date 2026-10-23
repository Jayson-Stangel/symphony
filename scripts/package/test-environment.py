#!/usr/bin/env python3
"""Selection transaction tests, with container operations recorded explicitly."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:" + "a" * 64
REVISION = "b" * 40


class EnvironmentSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symphony-environment-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runners = self.root / "runners"
        self.runners.mkdir(mode=0o700)
        self.log = self.root / "operations"
        self.helper = self.root / "helper"
        self.helper.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
cmd=sys.argv[1]
if cmd in ("owned-directory","validate-environment-selection"):
 p=Path(sys.argv[2]); assert not p.is_symlink()
 assert (p.is_dir() if cmd=="owned-directory" else p.is_file())
 if cmd=="validate-environment-selection":
  v=json.loads(p.read_text()); assert v["schema_version"]==1
elif cmd=="get":
 print(json.loads(Path(sys.argv[2]).read_text())[sys.argv[3]])
''')
        self.helper.chmod(0o700)
        self.runtime = self.root / "runtime"
        self.runtime.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ["OPERATIONS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")
a=sys.argv[1:]
if a[:2]==["image","inspect"]:
 if os.environ.get("MISSING"): sys.exit(1)
 if "--format" in a:
  fmt=a[a.index("--format")+1]
  print({"{{.Id}}":os.environ["IMAGE_ID"], '{{ index .Config.Labels "org.opencontainers.image.revision" }}':os.environ["REVISION"],"{{.Os}}/{{.Architecture}}":"linux/arm64"}[fmt])
 else: print("[]")
elif a and a[0]=="run":
 sys.exit(1 if os.environ.get("PROBE_FAIL") else 0)
else: sys.exit(99)
''')
        self.runtime.chmod(0o700)
        self.env = dict(os.environ, OPERATIONS=str(self.log), IMAGE_ID=IMAGE, REVISION=REVISION)
        self.prefix = f'''
set -euo pipefail
packaged_install=true
runner_home={str(self.runners)!r}
symphony_home={str(self.root)!r}
package_revision={REVISION}
package_helper() {{ {str(self.helper)!r} "$@"; }}
die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
project_id() {{ printf 'project-one\\n'; }}
validate_agent() {{ case "$1" in codex|claude) ;; *) return 1 ;; esac; }}
runner_project_lock() {{ :; }}
package_update_lock() {{ :; }}
package_current_home() {{ printf '%s\\n' "$symphony_home"; }}
package_pending_transition() {{ :; }}
container_runtime() {{ printf '%s\\n' {str(self.runtime)!r}; }}
package_selected_image_compatible() {{ [ -z "${{INCOMPATIBLE:-}}" ] || die incompatible; "$1" image inspect "$2" >/dev/null || die missing-selected-image; }}
. {str(ROOT / "scripts/symphony-environment.sh")!r}
'''

    def run_shell(self, body, **changes):
        return subprocess.run(["bash", "-c", self.prefix + body], env=dict(self.env, **changes), text=True, capture_output=True)

    def select(self, **changes):
        return self.run_shell('environment_select /project codex team:chosen', **changes)

    @property
    def selection(self):
        return self.runners / "environments/project-one.json"

    def test_selection_pins_identity_and_subsequent_use_never_runs_probe(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.selection.read_text())
        self.assertEqual(record["image_id"], IMAGE)
        self.assertEqual(record["runtime_revision"], REVISION)
        self.log.write_text("")
        result = self.run_shell('environment_use_project /project codex; printf "%s" "$SYMPHONY_RUNNER_IMAGE"', IMAGE_ID="sha256:" + "c" * 64)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, IMAGE)
        self.assertEqual(self.log.read_text(), "")

    def test_missing_image_never_builds_pulls_or_changes_selection(self):
        result = self.select(MISSING="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unavailable locally", result.stderr)
        self.assertFalse(self.selection.exists())
        self.assertTrue(all(json.loads(line)[:2] == ["image", "inspect"] for line in self.log.read_text().splitlines()))

    def test_refusal_and_failed_probe_preserve_previous_selection(self):
        self.assertEqual(self.select().returncode, 0)
        original = self.selection.read_bytes()
        for changes in ({"INCOMPATIBLE": "1"}, {"PROBE_FAIL": "1"}, {"REVISION": "unknown"}):
            result = self.select(**changes)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.selection.read_bytes(), original)

    def test_wrong_provider_refuses_without_substitution(self):
        self.assertEqual(self.select().returncode, 0)
        result = self.run_shell('selected_environment_image /project claude')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses codex", result.stderr)

    def test_unsafe_selection_and_directory_refuse(self):
        directory = self.runners / "environments"
        directory.symlink_to(self.root)
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(directory.is_symlink())
        self.assertFalse((self.root / "project-one.json").exists())

    def test_all_selections_are_validated_before_activation(self):
        self.assertEqual(self.select().returncode, 0)
        result = self.run_shell('selected_environment_validate_all "$(container_runtime)"', INCOMPATIBLE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incompatible", result.stderr)

    def test_show_reports_real_runtime_separately(self):
        self.assertEqual(self.select().returncode, 0)
        result = self.run_shell('package_revision=' + "c" * 40 + '; environment_show /project false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Embedded runtime: " + REVISION, result.stdout)
        self.assertIn("Manager: " + "c" * 40, result.stdout)


if __name__ == "__main__":
    unittest.main()
