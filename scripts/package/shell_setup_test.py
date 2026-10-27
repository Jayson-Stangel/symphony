"""Native helper proof of managed zsh setup using only synthetic home directories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
START = b"# >>> Symphony managed zsh setup v1 >>>"
END = b"# <<< Symphony managed zsh setup v1 <<<"


class ShellSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="symphony-shell-helper-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.helper = os.environ.get("SYMPHONY_TEST_HOST_HELPER")
        if not cls.helper:
            cls.helper = str(Path(cls.build.name).resolve() / "symphony-host")
            subprocess.run(["bash", str(ROOT / "scripts/release/build-host-helper.sh"), cls.helper, "--native"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symphony-shell-home-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.profile = self.home / ".zshrc"
        self.activation = self.home / "app ' $() with spaces" / "activate.sh"
        self.activation.parent.mkdir()
        self.activation.write_text("export SYMPHONY_SHELL_TEST=ready\n")

    def call(self, action, home=None, activation=None):
        return subprocess.run([self.helper, "zsh-setup-" + action, str(home or self.home),
                               str(activation or self.activation)], capture_output=True, timeout=10)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"zsh shell setup", result.stderr)

    def test_check_is_read_only_and_missing_profile_install_remove(self):
        self.ok(self.call("check"))
        self.assertFalse(self.profile.exists())
        self.ok(self.call("install"))
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.profile.stat().st_nlink, 1)
        self.assertEqual(self.profile.read_bytes().count(START), 1)
        self.ok(self.call("remove"))
        self.assertEqual(self.profile.read_bytes(), b"")
        self.assertEqual(list(self.home.glob(".symphony-zsh-*")), [])

    def test_preserve_arbitrary_profile_bytes_mode_and_final_line(self):
        for original in (b"", b"# unrelated\nexport HELLO=world\n", b"# no final newline", b"# bytes \xff\n"):
            with self.subTest(original=original):
                self.profile.write_bytes(original)
                self.profile.chmod(0o640)
                self.ok(self.call("check"))
                self.assertEqual(self.profile.read_bytes(), original)
                self.ok(self.call("install"))
                self.assertTrue(self.profile.read_bytes().startswith(original))
                self.assertEqual(self.profile.stat().st_mode & 0o777, 0o640)
                self.ok(self.call("remove"))
                self.assertEqual(self.profile.read_bytes(), original)
                self.assertEqual(self.profile.stat().st_mode & 0o777, 0o640)

    def test_repeat_and_changed_managed_block_are_preserved_and_refused(self):
        self.ok(self.call("install"))
        original = self.profile.read_bytes()
        self.refused(self.call("check"))
        self.refused(self.call("install"))
        self.assertEqual(self.profile.read_bytes(), original)
        self.refused(self.call("remove", activation=self.home / "other-activation"))
        self.assertEqual(self.profile.read_bytes(), original)
        changed = original.replace(b"[ ! -r", b"# changed [ ! -r")
        self.profile.write_bytes(changed)
        self.refused(self.call("remove"))
        self.assertEqual(self.profile.read_bytes(), changed)

    def test_managed_markers_in_unowned_text_are_not_replaced(self):
        for data in (START + b"\n", END + b"\n", START + b"\ncustom text\n" + END + b"\n"):
            with self.subTest(data=data):
                self.profile.write_bytes(data)
                self.refused(self.call("check"))
                self.refused(self.call("install"))
                self.refused(self.call("remove"))
                self.assertEqual(self.profile.read_bytes(), data)

    def test_unsafe_profile_symlink_hardlink_directory_and_writable_modes(self):
        canary = self.home / "canary"
        canary.write_bytes(b"preserve")
        for variant in ("symlink", "hardlink", "directory", "writable", "setid", "oversize"):
            with self.subTest(variant=variant):
                if variant == "symlink": self.profile.symlink_to(canary)
                elif variant == "hardlink": os.link(canary, self.profile)
                elif variant == "directory": self.profile.mkdir()
                else:
                    self.profile.write_bytes(b"preserve" if variant != "oversize" else b"x" * (1024 * 1024 + 1))
                    self.profile.chmod({"writable": 0o622, "setid": 0o4600, "oversize": 0o600}[variant])
                if variant == "setid" and self.profile.stat().st_mode & 0o7000 == 0:
                    self.profile.unlink()
                    continue  # Some macOS temporary volumes strip special mode bits.
                self.refused(self.call("check"))
                self.refused(self.call("install"))
                self.assertEqual(canary.read_bytes(), b"preserve")
                if self.profile.is_dir(): self.profile.rmdir()
                else: self.profile.unlink()
        self.assertEqual(list(self.home.glob(".symphony-zsh-*")), [])

    def test_symlinked_home_ancestor_and_writable_home_refuse(self):
        alias = self.root / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.refused(self.call("install", home=alias))
        self.home.chmod(0o777)
        self.refused(self.call("install"))
        self.assertFalse(self.profile.exists())
        self.home.chmod(0o700)
        self.root.chmod(0o777)
        self.refused(self.call("install"))
        self.assertFalse(self.profile.exists())
        self.root.chmod(0o700)

    def test_first_install_paths_refuse_symlink_and_unsafe_ancestors(self):
        def check(path):
            return subprocess.run([self.helper, "first-install-path-check", str(self.home), str(path)],
                                  capture_output=True, timeout=10)
        self.ok(check(self.home / ".local/share/symphony"))
        local = self.home / ".local"
        local.mkdir(mode=0o755)
        self.ok(check(local / "bin"))
        local.chmod(0o777)
        self.assertNotEqual(check(local / "bin").returncode, 0)
        local.chmod(0o755)
        local.rmdir()
        local.symlink_to(self.activation.parent, target_is_directory=True)
        self.assertNotEqual(check(local / "bin").returncode, 0)
        self.assertNotEqual(check(self.root / "outside").returncode, 0)
        self.assertNotEqual(check(self.home / "../outside").returncode, 0)
        self.assertTrue(local.is_symlink())

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics")
    def test_safe_acl_preserved_and_write_acl_refused(self):
        self.profile.write_bytes(b"# preserve ACL\n")
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(self.profile)], check=True)
        def acl():
            listing = subprocess.run(["/bin/ls", "-le", str(self.profile)], check=True, capture_output=True).stdout
            return b"\n".join(listing.splitlines()[1:])
        self.addCleanup(subprocess.run, ["/bin/chmod", "-N", str(self.profile)], check=False, capture_output=True)
        before = acl()
        self.ok(self.call("install"))
        self.assertEqual(acl(), before)
        self.ok(self.call("remove"))
        self.assertEqual(acl(), before)
        self.assertEqual(self.profile.read_bytes(), b"# preserve ACL\n")
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(self.profile)], check=True)
        self.refused(self.call("check"))
        self.refused(self.call("install"))
        self.assertEqual(self.profile.read_bytes(), b"# preserve ACL\n")
        subprocess.run(["/bin/chmod", "-N", str(self.profile)], check=True)

    @unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
    def test_real_zsh_sources_quoted_activation_without_user_commands(self):
        self.ok(self.call("install"))
        for flags in ("-ic", "-lic"):
            with self.subTest(flags=flags):
                env = dict(os.environ, HOME=str(self.home), ZDOTDIR=str(self.home))
                env.pop("SYMPHONY_SHELL_TEST", None)
                result = subprocess.run([shutil.which("zsh"), flags, 'printf "%s\\n" "$SYMPHONY_SHELL_TEST"'],
                                        env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b"ready\n")
        self.assertFalse((self.home / "with").exists())


if __name__ == "__main__":
    unittest.main()
