# Shell activation process fixtures; no Docker daemon, accounts or host edits.
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CommandActivationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symphony-command-activation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        # Exercise shell quoting, including shell metacharacters, not just spaces.
        self.app = self.root / "app '$() with spaces"
        self.app.mkdir()
        self.bin = self.root / "candidate bin"
        self.bin.mkdir()
        self.stable = self.root / "stable bin"
        self.stable.mkdir()
        for directory, label in [(self.bin, "candidate"), (self.stable, "stable")]:
            command = directory / "symphony"
            command.write_text('#!/bin/sh\nprintf "%s\\n" ' + label + '\n')
            command.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{self.stable}:{self.bin}:/usr/bin:/bin",
                        TEST_APP=str(self.app), TEST_BIN=str(self.bin), TEST_ROOT=str(self.root))
        for key in list(self.env):
            if key.startswith(("SYMPHONY_", "DOCKER_")):
                self.env.pop(key)
        self.functions = self.root / "functions.sh"
        source = (ROOT / "scripts/symphony-package.sh").read_text()
        functions = source.split("package_shell_activation_preflight() {", 1)[1].split("package_preflight_links() {", 1)[0]
        self.functions.write_text("package_shell_activation_preflight() {" + functions)
        self.setup = r'''
set -eu
. "$TEST_ROOT/functions.sh"
package_app_home=$TEST_APP
bin_dir=$TEST_BIN
config_home="$TEST_ROOT/private config"
skills_dir="$TEST_ROOT/skills"
runner_home="$TEST_ROOT/separate runners"
runner_namespace=candidate-test
symphony_home="$TEST_APP/releases/fixture"
die() { printf '%s\n' "$*" >&2; exit 1; }
container_runtime() { printf '/no-docker\n'; }
# The ownership peer refuses links/directories; native package tests separately
# exercise the same preflight against the actual host helper.
package_helper() {
  [ "$1" = owned-file ] && [ -f "$2" ] && [ ! -L "$2" ]
}
'''

    def bash(self, body, env=None):
        return subprocess.run(["/bin/bash", "-c", self.setup + body], env=env or self.env,
                              text=True, capture_output=True)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def generate(self, env=None):
        self.assert_ok(self.bash("package_shell_activation_write", env))
        return self.app / "activate.sh"

    def test_activation_restores_context_and_clears_cached_command_in_bash_and_zsh(self):
        activation = self.generate()
        self.assertEqual(activation.stat().st_mode & 0o777, 0o600)
        for shell in ["/bin/bash", shutil.which("zsh")]:
            if not shell:
                continue
            with self.subTest(shell=shell):
                env = dict(self.env, SYMPHONY_HOME="/wrong/source", SYMPHONY_RUNNER_NAMESPACE="wrong",
                           SYMPHONY_CONFIG_HOME="/wrong/config", DOCKER_CONTEXT="wrong")
                script = r'''
symphony
hash symphony
. "$TEST_APP/activate.sh"
symphony
printf '%s\n' "$SYMPHONY_APP_HOME" "$SYMPHONY_BIN_DIR/symphony" "$SYMPHONY_CONFIG_HOME" "$SYMPHONY_SKILLS_DIR" "$SYMPHONY_RUNNER_HOME" "$SYMPHONY_RUNNER_NAMESPACE"
[ -z "${SYMPHONY_HOME+x}" ] && [ -z "${DOCKER_CONTEXT+x}" ]
'''
                result = subprocess.run([shell, "-c", script], env=env, text=True, capture_output=True)
                out = self.assert_ok(result).splitlines()
                self.assertEqual(out, ["stable", "candidate", str(self.app), str(self.bin / "symphony"),
                                      str(self.root / "private config"), str(self.root / "skills"),
                                      str(self.root / "separate runners"), "candidate-test"])
        self.assertEqual((self.stable / "symphony").read_text(), '#!/bin/sh\nprintf "%s\\n" stable\n')

    def test_fresh_interactive_shell_uses_explicit_profile_opt_in(self):
        self.generate()
        # The installer never writes these. Simulate the user's explicit opt-in.
        self.assertFalse((self.home / ".bashrc").exists())
        self.assertFalse((self.home / ".zshrc").exists())
        line = '. "$TEST_APP/activate.sh"\n'
        (self.home / ".bashrc").write_text(line)
        result = subprocess.run(["/bin/bash", "--rcfile", str(self.home / ".bashrc"), "-ic", "symphony"],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(self.assert_ok(result).strip(), "candidate")
        zsh = shutil.which("zsh")
        if zsh:
            (self.home / ".zshrc").write_text(line)
            result = subprocess.run([zsh, "-ic", "symphony"], env=dict(self.env, ZDOTDIR=str(self.home)),
                                    text=True, capture_output=True)
            self.assertEqual(self.assert_ok(result).strip(), "candidate")

    def test_snapshot_retains_only_explicit_install_and_engine_context(self):
        env = dict(self.env, DOCKER_CONTEXT="test-context", DOCKER_CONFIG=str(self.root / "docker config"),
                   SYMPHONY_CONTAINER_RUNTIME="/opt/docker", GH_TOKEN="do-not-save-this-token",
                   LINEAR_API_KEY="do-not-save-this-key")
        activation = self.generate(env)
        text = activation.read_text()
        self.assertNotIn("do-not-save", text)
        result = subprocess.run(["/bin/bash", "-c", '. "$TEST_APP/activate.sh"; printf "%s\\n" "$DOCKER_CONTEXT" "$DOCKER_CONFIG" "$SYMPHONY_CONTAINER_RUNTIME"'],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(self.assert_ok(result).splitlines(), ["test-context", str(self.root / "docker config"), "/opt/docker"])

    def test_conflicting_and_missing_path_are_reported_without_executing_foreign_command(self):
        output = self.assert_ok(self.bash("package_shell_context; package_shell_activation_instructions"))
        self.assertIn("PATH command (fresh lookup): " + str(self.stable / "symphony"), output)
        self.assertIn("Installed command: " + str(self.bin / "symphony"), output)
        self.assertIn("Command activation required", output)
        self.assertNotIn("\nstable\n", output)
        missing = dict(self.env, PATH="/usr/bin:/bin")
        self.assertIn("PATH command (fresh lookup): not found", self.assert_ok(self.bash("package_shell_context", missing)))
        selected = dict(self.env, PATH=f"{self.bin}:/usr/bin:/bin")
        self.assertNotIn("Command activation required", self.assert_ok(self.bash("package_shell_context", selected)))

    def test_engine_context_report_does_not_expose_host_value(self):
        env = dict(self.env, DOCKER_HOST="tcp://private-engine:2376")
        out = self.assert_ok(self.bash("package_shell_context", env))
        self.assertIn("DOCKER_HOST override (value omitted)", out)
        self.assertNotIn("private-engine", out)
        env["DOCKER_CONTEXT"] = "selected-context"
        self.assertIn("Docker context: selected-context", self.assert_ok(self.bash("package_shell_context", env)))

    def test_existing_unmanaged_activation_file_and_symlink_are_preserved(self):
        activation = self.app / "activate.sh"
        activation.write_text("user-owned-data\n")
        refused = self.bash("package_shell_activation_write")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing to replace existing shell activation file", refused.stderr)
        self.assertEqual(activation.read_text(), "user-owned-data\n")
        activation.unlink()
        activation.symlink_to(self.stable / "symphony")
        self.assertNotEqual(self.bash("package_shell_activation_write").returncode, 0)
        self.assertTrue(activation.is_symlink())

    def test_repeat_install_refreshes_owned_activation_without_profile_edits(self):
        activation = self.generate()
        self.assert_ok(self.bash('runner_namespace=changed; package_shell_activation_write'))
        self.assertIn("SYMPHONY_RUNNER_NAMESPACE=changed", activation.read_text())
        self.assertEqual(list(self.home.iterdir()), [])
        out = self.assert_ok(self.bash("package_shell_activation_instructions"))
        self.assertIn("source ", out)
        self.assertIn("No shell profiles were changed", out)


class PackageCommandActivationMixin:
    """Native helper + real archive/installer integration (in test-package.py)."""

    def test_native_install_activation_default_and_custom_paths(self):
        bundle = self.bundle()
        original = self.environment()
        for defaults in (True, False):
            with self.subTest(defaults=defaults):
                env = dict(original)
                env.pop("SYMPHONY_RUNNER_HOME", None)
                if defaults:
                    for name in ("SYMPHONY_APP_HOME", "SYMPHONY_BIN_DIR", "SYMPHONY_CONFIG_HOME", "SYMPHONY_SKILLS_DIR"):
                        env.pop(name, None)
                    home = Path(env["HOME"])
                    app = home / ".local/share/symphony-package-fixture"
                    bin_dir = home / ".local/bin"
                else:
                    app = Path(env["SYMPHONY_APP_HOME"])
                    bin_dir = Path(env["SYMPHONY_BIN_DIR"])
                foreign = self.root / ("source-command-default" if defaults else "source-command-custom")
                foreign.mkdir()
                command = foreign / "symphony"
                command.write_text("#!/bin/sh\necho foreign-command\n")
                command.chmod(0o755)
                env["PATH"] = str(foreign) + ":" + env["PATH"]
                output = self.assertOK(subprocess.run([str(bundle / "install.sh")], env=env, text=True, capture_output=True))
                self.assertIn("Command activation required", output)
                self.assertIn("PATH command (fresh lookup): " + str(command), output)
                self.assertIn("Installed command: " + str(bin_dir / "symphony"), output)
                self.assertIn("source ", output)
                self.assertTrue((app / "activate.sh").is_file())
                activated_env = dict(env, TEST_ACTIVATION=str(app / "activate.sh"), SYMPHONY_HOME="/wrong-source")
                result = subprocess.run(["/bin/bash", "-c", '. "$TEST_ACTIVATION"; symphony --version; symphony doctor'],
                                        env=activated_env, text=True, capture_output=True)
                out = self.assertOK(result)
                self.assertIn('"installation":"packaged"', out)
                self.assertIn("PATH command (fresh lookup): " + str(bin_dir / "symphony"), out)
                self.assertIn("Namespace: package-fixture", out)
                self.assertNotIn("Command activation required", out)
                self.assertEqual(command.read_text(), "#!/bin/sh\necho foreign-command\n")
                self.assertFalse((Path(env["HOME"]) / ".zshrc").exists())
                self.assertFalse((Path(env["HOME"]) / ".bashrc").exists())
                self.assertOK(subprocess.run([str(bundle / "install.sh")], env=env, text=True, capture_output=True))

    def test_native_installer_preserves_foreign_activation_before_activation(self):
        bundle = self.bundle()
        env = self.environment()
        app = Path(env["SYMPHONY_APP_HOME"])
        app.mkdir()
        activation = app / "activate.sh"
        activation.write_text("user-owned file\n")
        result = subprocess.run([str(bundle / "install.sh")], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace existing shell activation file", result.stderr)
        self.assertEqual(activation.read_text(), "user-owned file\n")
        self.assertFalse((app / "current").exists())
        self.assertFalse((Path(env["SYMPHONY_BIN_DIR"]) / "symphony").exists())


if __name__ == "__main__":
    unittest.main()
