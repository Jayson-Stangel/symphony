"""Exercise production transition selection without image builds or release fallback."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = (ROOT/'scripts/symphony-package.sh').read_text()
FUNCTIONS = SOURCE[SOURCE.index('package_transition_image() {'):SOURCE.index('# A shared manager update')]
IMAGE = 'sha256:' + 'a'*64


class ManagerSelectedEnvironment(unittest.TestCase):
    def execute(self, metadata='', selected='', selection_status=0, agent='codex'):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/'runner.env'
            if metadata: file.write_text(metadata)
            shell = '''set -euo pipefail
package_helper() { test "$1" = owned-file && test -f "$2" && test ! -L "$2"; }
runner_env_file() { printf '%s\\n' "$METADATA"; }
selected_environment_image() { printf '%s' "$SELECTED"; return "$STATUS"; }
package_image() { printf 'release-%s\\n' "$1"; }
die() { echo "$*" >&2; exit 42; }
''' + FUNCTIONS + '\npackage_transition_image /project "$AGENT"\n'
            return subprocess.run(['bash', '-c', shell], text=True, capture_output=True,
                                  env=dict(METADATA=str(file), SELECTED=selected, STATUS=str(selection_status), AGENT=agent))

    def test_explicit_selection_wins_over_old_runner_and_release(self):
        result = self.execute('SYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID=sha256:'+'b'*64+'\n', IMAGE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), IMAGE)

    def test_manager_only_transition_keeps_same_provider_immutable_image(self):
        result = self.execute('SYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID='+IMAGE+'\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), IMAGE)

    def test_missing_or_invalid_selection_never_falls_back(self):
        result = self.execute(selected='', selection_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('release-', result.stdout)
        result = self.execute('SYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID=mutable:tag\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('release-', result.stdout)

    def test_explicit_provider_transition_without_team_selection_uses_matching_release(self):
        result = self.execute('SYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID='+IMAGE+'\n', agent='claude')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'release-claude')
