"""Actual archive/helper with synthetic homes; does not claim fresh-account acceptance."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest


class FirstMacInstallMixin:
    def first_mac_environment(self, home_name="home"):
        env = self.environment()
        for name in list(env):
            if name.startswith('SYMPHONY_') and name != 'SYMPHONY_CONTAINER_RUNTIME':
                env.pop(name)
        home = self.root / home_name
        env['HOME'] = str(home)
        home.mkdir(mode=0o700)
        env.update(SHELL='/bin/zsh', CODEX_HOME=str(home / '.codex'),
                   XDG_CONFIG_HOME=str(home / '.config'))
        env.pop('ZDOTDIR', None)
        return env, home

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_consent_fresh_shell_context_repeat_and_remove(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        profile = home / '.zshrc'
        original = b'# unrelated user text without final newline'
        profile.write_bytes(original); profile.chmod(0o640)
        refused = subprocess.run([bundle / 'install.sh'], env=env, text=True, capture_output=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('installer consent', refused.stderr)
        self.assertFalse((home / '.local').exists())
        self.assertEqual(profile.read_bytes(), original)
        installed = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup', '--no-shell-handoff'],
                                   env=env, text=True, capture_output=True)
        self.assertOK(installed)
        self.assertIn('Open a new Terminal window', installed.stdout)
        self.assertNotIn('source ', installed.stdout)
        content = profile.read_bytes()
        self.assertTrue(content.startswith(original))
        self.assertEqual(profile.stat().st_mode & 0o777, 0o640)
        clean = {k: v for k, v in env.items() if not k.startswith(('SYMPHONY_', 'DOCKER_'))}
        for flags in ('-ic', '-lic'):
            result = subprocess.run(['/bin/zsh', flags,
                'command -v symphony; symphony --version; symphony doctor --json; '
                'print -r -- "$SYMPHONY_APP_HOME" "$SYMPHONY_CONFIG_HOME" "$SYMPHONY_SKILLS_DIR" "${SYMPHONY_RUNNER_HOME:-$SYMPHONY_CONFIG_HOME/runners}" "namespace=$SYMPHONY_RUNNER_NAMESPACE"'],
                env=clean, text=True, capture_output=True)
            output = self.assertOK(result)
            self.assertIn(str(home / '.local/bin/symphony'), output)
            self.assertIn('"installation":"packaged"', output)
            self.assertIn('"ready":true', output)
            self.assertIn('namespace=\n', output + '\n')
            self.assertIn(str(home / '.config/symphony/runners'), output)
        project = self.root / 'new-project'; (project / '.git').mkdir(parents=True)
        (project / '.git/config').write_text('[remote "origin"]\nurl = https://github.com/fixture/first.git\n')
        result = subprocess.run(['/bin/zsh', '-lic', 'symphony init --linear-project fixture'],
                                cwd=project, env=clean, text=True, capture_output=True)
        self.assertIn('Project: initialized', self.assertOK(result))
        doctor = subprocess.run(['/bin/zsh', '-lic', 'symphony doctor'], env=clean, text=True, capture_output=True)
        self.assertIn('Managed zsh setup is installed', self.assertOK(doctor))
        self.assertNotIn('No shell profiles were changed', doctor.stdout)
        mixed = subprocess.run([bundle / 'install.sh', '--stage-only', '--remove-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(mixed.returncode, 0)
        self.assertEqual(profile.read_bytes(), content)
        repeat = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(repeat.returncode, 0)
        self.assertIn('existing Symphony state', repeat.stderr)
        self.assertEqual(profile.read_bytes(), content)
        # Safe removal belongs to the installed managed block, regardless of
        # which shell or inherited startup directory invoked the command.
        removal_env = dict(env, SHELL='/bin/bash', ZDOTDIR=str(home / 'custom-zsh'))
        self.assertOK(subprocess.run([home / '.local/bin/symphony', 'install', '--remove-zsh-setup'],
                                     env=removal_env, text=True, capture_output=True))
        self.assertEqual(profile.read_bytes(), original)
        self.assertEqual(profile.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_default_mac_unsupported_shell_context_retains_explicit_activation(self):
        bundle = self.bundle()
        for name in ('bash', 'zdotdir'):
            with self.subTest(context=name):
                env, home = self.first_mac_environment('home-' + name)
                if name == 'bash':
                    env['SHELL'] = '/bin/bash'
                else:
                    custom = home / 'custom-zsh'; custom.mkdir()
                    env['ZDOTDIR'] = str(custom)
                    (custom / '.zshrc').write_bytes(b'# custom zsh profile\n')
                originals = {home / '.zshrc': b'# unrelated zsh profile\n',
                             home / '.bashrc': b'# unrelated bash profile\n'}
                for profile, original in originals.items():
                    profile.write_bytes(original); profile.chmod(0o640)
                for flag in ('--accept-zsh-setup', '--no-shell-handoff'):
                    refused = subprocess.run([bundle / 'install.sh', flag], env=env,
                                             text=True, capture_output=True)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn('standard /bin/zsh without ZDOTDIR', refused.stderr)
                    self.assertFalse((home / '.local').exists())
                installed = subprocess.run([bundle / 'install.sh'], env=env, text=True, capture_output=True)
                self.assertIn('No shell profiles were changed', self.assertOK(installed))
                self.assertNotIn('zsh setup installed', installed.stdout)
                for profile, original in originals.items():
                    self.assertEqual(profile.read_bytes(), original)
                    self.assertEqual(profile.stat().st_mode & 0o777, 0o640)
                if name == 'zdotdir':
                    self.assertEqual((custom / '.zshrc').read_bytes(), b'# custom zsh profile\n')
                # Exercise the retained JAY-735 activation path and real command
                # from the actual installed archive, without claiming auto setup.
                activation = home / '.local/share/symphony/activate.sh'
                activated = subprocess.run(['/bin/bash', '-c',
                    '. "$1"; command -v symphony; symphony --version; symphony doctor --json',
                    'fixture', str(activation)], env=env, text=True, capture_output=True)
                output = self.assertOK(activated)
                self.assertIn(str(home / '.local/bin/symphony'), output)
                self.assertIn('"ready":true', output)

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_explicit_default_runner_storage_preserves_context(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        runner = home / '.config/symphony/runners'
        env['SYMPHONY_RUNNER_HOME'] = str(runner)
        installed = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup', '--no-shell-handoff'],
                                   env=env, text=True, capture_output=True)
        self.assertIn('zsh setup installed', self.assertOK(installed))
        clean = {k: v for k, v in env.items() if not k.startswith(('SYMPHONY_', 'DOCKER_'))}
        for flags in ('-ic', '-lic'):
            result = subprocess.run(['/bin/zsh', flags,
                'command -v symphony; symphony --version; symphony doctor --json; '
                'print -r -- "explicit=${SYMPHONY_RUNNER_HOME+x}" "$SYMPHONY_RUNNER_HOME" '
                '"$SYMPHONY_CONFIG_HOME" "namespace=$SYMPHONY_RUNNER_NAMESPACE"; '
                'symphony doctor --config-dir "$HOME/selected-config"'],
                env=clean, text=True, capture_output=True)
            output = self.assertOK(result)
            self.assertIn(str(home / '.local/bin/symphony'), output)
            self.assertIn('"ready":true', output)
            self.assertIn('explicit=x\n' + str(runner), output)
            self.assertIn('namespace=\n', output + '\n')
            self.assertIn('Configuration: ' + str(home / 'selected-config'), output)
            self.assertIn('Runner storage: ' + str(runner), output)

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_unsafe_and_existing_targets_preserved(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        outside = self.root / 'outside'; outside.mkdir()
        (home / '.local').symlink_to(outside)
        result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        (home / '.local').unlink()
        profile = home / '.zshrc'; profile.symlink_to(outside / 'profile')
        result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((home / '.local').exists())
        self.assertTrue(profile.is_symlink())
        profile.unlink()
        (home / '.config/symphony').mkdir(parents=True)
        canary = home / '.config/symphony/canary'; canary.write_bytes(b'preserved')
        result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(canary.read_bytes(), b'preserved')
        self.assertFalse(profile.exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_refuses_zsh_alias_and_function(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        profile = home / '.zshrc'
        for text in ('alias symphony="echo existing"\n', 'symphony() { echo existing; }\n', 'if [[ ! -o login ]]; then alias symphony="echo existing"; fi\n'):
            profile.write_text(text)
            result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('conflicting zsh command selection', result.stderr)
            self.assertEqual(profile.read_text(), text)
            self.assertFalse((home / '.local').exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_refuses_redirected_or_disabled_startup(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        startup = home / '.zshenv'
        for text in ('export ZDOTDIR=/nonexistent\n', 'unsetopt RCS\n'):
            startup.write_text(text)
            result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unsupported startup files', result.stderr)
            self.assertEqual(startup.read_text(), text)
            self.assertFalse((home / '.local').exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_early_profile_return_never_claims_ready(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        profile = home / '.zshrc'; profile.write_text('return\n')
        # A warm installer environment must not masquerade as persisted startup.
        env.update(SYMPHONY_APP_HOME=str(home / '.local/share/symphony'),
                   SYMPHONY_BIN_DIR=str(home / '.local/bin'),
                   SYMPHONY_CONFIG_HOME=str(home / '.config/symphony'),
                   SYMPHONY_SKILLS_DIR=str(home / '.codex/skills'))
        env['PATH'] = str(home / '.local/bin') + ':' + env['PATH']
        result = subprocess.run([bundle / 'install.sh', '--accept-zsh-setup'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('managed block was removed', result.stderr)
        self.assertEqual(profile.read_text(), 'return\n')
        self.assertNotIn('zsh setup installed', result.stdout)

    @unittest.skipUnless(sys.platform == 'darwin', 'first install is macOS only')
    def test_first_mac_interactive_handoff_uses_normal_command(self):
        bundle = self.bundle(); env, home = self.first_mac_environment()
        master, slave = os.openpty()
        process = subprocess.Popen([bundle / 'install.sh'], env=env, stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        output = b''; consented = False; invoked = False
        deadline = time.monotonic() + 40
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try: data = os.read(master, 65536)
                    except OSError: break
                    if not data: break
                    output += data
                if b'[y/N]' in output and not consented:
                    os.write(master, b'y\n'); consented = True
                if b'Opening a configured zsh login shell' in output and not invoked:
                    os.write(master, b'symphony --version\nexit\n'); invoked = True
                if process.poll() is not None: break
            process.wait(timeout=3)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            os.close(master)
        self.assertEqual(process.returncode, 0, output.decode(errors='replace'))
        self.assertTrue(invoked, output.decode(errors='replace'))
        self.assertIn(b'"installation":"packaged"', output)
