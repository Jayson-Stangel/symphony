#!/usr/bin/env python3
"""Real npm effects; --protected runs command/exec in a disposable Codex image.

Local runs qualify configuration semantics only, never native sandbox security.
No registry, account, model, host home or existing project is used.
"""
# Hosted routing probe: this comment changes no npm test or sandbox behavior.
# Draft update verifies cumulative consumer routing without changing assertions.
import io
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

PROTECTED = '--protected' in sys.argv
if PROTECTED:
    sys.argv.remove('--protected')
CONFIG = Path(__file__).with_name('npm-cache.npmrc')


def live_group_members(group):
    # Inspect identities/state only, never unrelated command lines or environment.
    # Orphan zombies cannot write and may await the container's PID 1 reaper.
    rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,stat='],
                                   text=True, timeout=5)
    return {int(pid) for pid, pgid, state in (line.split() for line in rows.splitlines())
            if int(pgid) == group and not state.startswith(('Z', 'X'))}


class NativeCommands:
    def __init__(self, home, cwd, command=('codex', 'app-server')):
        self.home, self.cwd, self.command = home, cwd, command
        self.child = self.stderr = None
        self.closed = False

    def start(self):
        if self.child is not None or self.closed:
            raise RuntimeError('native command owner cannot be started twice')
        # Assign this owner before starting so initialization failures still have
        # a reachable cleanup owner; never leave a TemporaryDirectory finalizer
        # to remove HOME under a process that could not be stopped.
        try:
            self.stderr = tempfile.TemporaryFile()
            self.child = subprocess.Popen(self.command, cwd=self.cwd,
                                         env={**os.environ, 'HOME': str(self.home), 'CODEX_HOME': str(self.home)},
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self.stderr, bufsize=0, start_new_session=True)
            self.sequence = 0
            self.buffer = bytearray()
            self.request('initialize', {'clientInfo': {'name': 'npm-cache-proof', 'version': '1'},
                                        'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.child.stdin.write((json.dumps(value) + '\n').encode())
        self.child.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        self.send({'id': self.sequence, 'method': method, 'params': params})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer[:] = rest
                message = json.loads(line)
                if message.get('id') == self.sequence:
                    if 'error' in message:
                        raise RuntimeError(message['error'])
                    return message['result']
            ready, _, _ = select.select([self.child.stdout], [], [], max(0, deadline-time.monotonic()))
            if not ready:
                break
            data = os.read(self.child.stdout.fileno(), 65536)
            if not data:
                break
            self.buffer.extend(data)
        raise RuntimeError('native command response unavailable')

    def close(self):
        if self.closed:
            return
        if self.child is not None:
            if not self.child.stdin.closed:
                self.child.stdin.close()
            # The frontend can exit while startup Git children remain. Own the
            # entire session's group even when its leader has already exited.
            def signal_owned_group(value):
                if not live_group_members(self.child.pid):
                    return
                try:
                    os.killpg(self.child.pid, value)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # A group can disappear between inspection and signaling.
                    # Never suppress a refusal while live members remain.
                    if live_group_members(self.child.pid):
                        raise

            signal_owned_group(signal.SIGTERM)
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                signal_owned_group(signal.SIGKILL)
                self.child.wait(timeout=2)
            if live_group_members(self.child.pid):
                signal_owned_group(signal.SIGKILL)
            deadline = time.monotonic() + 2
            while live_group_members(self.child.pid):
                if time.monotonic() >= deadline:
                    raise RuntimeError('owned native command group survived cleanup; retain fixture directories')
                select.select([], [], [], 0.01)
            self.child.stdout.close()
        if self.stderr is not None:
            self.stderr.close()
        self.closed = True


class NpmCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='npm-cache-', dir='/workspaces' if PROTECTED else None)).resolve()
        self.home = self.native = None
        self.addCleanup(self.cleanup_resources)
        self.project = self.root / 'task with spaces'
        self.project.mkdir()
        # Native home deliberately outside every sandbox write root.
        self.home = Path(tempfile.mkdtemp(prefix='npm-home-', dir='/var/lib/symphony' if PROTECTED else None)).resolve()
        (self.home / '.codex').mkdir()
        (self.home / '.codex/auth.json').write_text('synthetic-auth-canary')
        self.env = {k: v for k, v in os.environ.items() if not k.lower().startswith('npm_config_')}
        self.env.update(HOME=str(self.home), CODEX_HOME=str(self.home / '.codex'))
        if not PROTECTED:
            self.env['NPM_CONFIG_GLOBALCONFIG'] = str(CONFIG)
        else:
            self.native = NativeCommands(self.home, self.project)
            self.native.start()
        self.tarball = self.root / 'fixture.tgz'
        with tarfile.open(self.tarball, 'w:gz') as archive:
            for name, data in {'package.json': json.dumps({'name': 'cold-fixture', 'version': '1.0.0', 'main': 'index.js'}),
                               'index.js': 'module.exports = 741;\n'}.items():
                payload = data.encode()
                info = tarfile.TarInfo('package/' + name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        self.package(self.project)

    def cleanup_resources(self):
        # One ordered cleanup: unittest runs remaining callbacks after failures.
        # A failed close must therefore stop deletion, not merely fail a prior
        # callback while another callback removes directories beneath a writer.
        if self.native is not None:
            self.native.close()
        if self.home is not None:
            shutil.rmtree(self.home)
        shutil.rmtree(self.root)

    def package(self, directory):
        (directory / 'package.json').write_text(json.dumps({
            'name': 'cache-proof', 'version': '1.0.0',
            'dependencies': {'cold-fixture': 'file:' + str(self.tarball)},
            'scripts': {'build': 'node -e "if(require(\'cold-fixture\')!==741)process.exit(1)"',
                        'child': 'npm config get cache',
                        'nested': 'git clone -q ../seed lifecycle-nested && cd lifecycle-nested && '
                                  'npm config get cache && npm install --offline --no-audit --no-fund && npm run build',
                        'install': 'npm run build'}}))

    def run_command(self, script, cwd=None, env=None, login=False, success=True):
        cwd = cwd or self.project
        environment = {**self.env, **(env or {})}
        if PROTECTED:
            # Supply only synthetic fixture environment changes; no host/native secrets.
            assignments = {k: environment[k] for k in ('HOME', 'CODEX_HOME')}
            assignments.update(env or {})
            command = ['env', *[f'{k}={v}' for k, v in assignments.items()], 'bash', '-lceu' if login else '-ceu', script]
            result = self.native.request('command/exec', {
                'command': command, 'cwd': str(cwd), 'timeoutMs': 40000,
                'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [str(self.root), str(cwd / '.git')],
                                  'networkAccess': False, 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True}})
            code, stdout, stderr = result['exitCode'], result['stdout'], result['stderr']
            print(json.dumps({'cwd': str(cwd), 'command': command, 'exit': code,
                              'stdout': stdout, 'stderr': stderr}), flush=True)
        else:
            result = subprocess.run(['bash', '-lceu' if login else '-ceu', script], cwd=cwd,
                                    env=environment, capture_output=True, text=True, timeout=40)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        if success:
            self.assertEqual(code, 0, f'{script}\nstdout={stdout}\nstderr={stderr}')
        return code, stdout, stderr

    def cache(self, **kwargs):
        return self.run_command('npm config get cache', **kwargs)[1].strip()

    def test_cold_install_lifecycle_login_nested_clone_and_pruned_recovery(self):
        expected = self.project / '.npm-cache'
        self.assertFalse(expected.exists())
        self.assertFalse((self.project / 'node_modules').exists())
        self.assertEqual(self.cache(), str(expected))
        self.assertEqual(self.cache(login=True), str(expected))
        self.run_command('npm install --offline --no-audit --no-fund')
        self.assertTrue(any((expected / '_cacache').rglob('*')))
        self.assertIn(str(expected), self.run_command('npm run child')[1])
        self.run_command('npm cache verify')
        # Restart native frontend with retained workspace/cache.
        if PROTECTED:
            self.native.close()
            self.native = NativeCommands(self.home, self.project)
            self.native.start()
        self.run_command('npm run build')
        shutil.rmtree(expected)
        shutil.rmtree(self.project / 'node_modules')
        self.run_command('npm ci --offline --no-audit --no-fund', login=True)
        self.assertTrue((expected / '_cacache').is_dir())
        seed = self.root / 'seed'
        seed.mkdir()
        self.package(seed)
        (seed / '.git').mkdir()  # Grant an existing exact owned metadata root.
        self.run_command('git init -q; git -c user.name=Fixture -c user.email=fixture@example.invalid add package.json; '
                         'git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm fixture', cwd=seed)
        shutil.rmtree(expected)
        self.assertIn(str(expected), self.run_command('npm run nested')[1])
        self.assertTrue((expected / '_cacache').is_dir())
        self.assertFalse((self.project / 'lifecycle-nested/.npm-cache').exists())
        self.run_command('git clone -q ../seed nested; cd nested; '
                         'test ! -e node_modules; test ! -e .npm-cache; '
                         'npm install --offline --no-audit --no-fund; npm run build')
        self.assertTrue((self.project / 'nested/.npm-cache/_cacache').is_dir())
        self.assertEqual((self.home / '.codex/auth.json').read_text(), 'synthetic-auth-canary')
        self.assertFalse((self.home / '.npm').exists())

    def test_explicit_precedence_and_lowercase_lifecycle(self):
        user = self.root / 'user-cache'
        project = self.root / 'project-cache'
        (self.home / '.npmrc').write_text('cache=' + str(user) + '\n')
        self.assertEqual(self.cache(), str(user))
        (self.project / '.npmrc').write_text('cache=' + str(project) + '\n')
        self.assertEqual(self.cache(), str(project))
        for name in ('NPM_CONFIG_CACHE', 'npm_config_cache'):
            chosen = str(self.root / name)
            self.assertEqual(self.cache(env={name: chosen}), chosen)
            self.assertIn(chosen, self.run_command('npm run child', env={name: chosen})[1])
        lower = str(self.root / 'lower')
        upper = str(self.root / 'upper')
        mixed = {'NPM_CONFIG_CACHE': upper, 'npm_config_cache': lower}
        # npm versions/environment order differ for duplicate-case keys. Preserve
        # the installed npm's own behavior, including inside lifecycle children.
        baseline = self.run_command('npm --globalconfig=/dev/null config get cache', env=mixed)[1].strip()
        self.assertIn(baseline, (lower, upper))
        self.assertEqual(self.cache(env=mixed), baseline)
        child_baseline = self.run_command('npm --globalconfig=/dev/null run child', env=mixed)[1].splitlines()[-1]
        self.assertEqual(self.run_command('npm run child', env=mixed)[1].splitlines()[-1], child_baseline)
        cli = str(self.root / 'cli')
        self.assertEqual(self.run_command('npm --cache ' + shlex.quote(cli) + ' config get cache',
                                         env={'npm_config_cache': lower})[1].strip(), cli)
        # An inherited /root/.npm is an explicit env choice only when configured;
        # the ordinary native default is replaced by the global image setting.
        self.assertEqual(self.cache(env={'npm_config_cache': '/root/.npm'}), '/root/.npm')

    def test_custom_global_config_is_respected(self):
        custom = self.root / 'team.npmrc'
        chosen = self.root / 'team-cache'
        custom.write_text('cache=' + str(chosen) + '\n')
        self.assertEqual(self.cache(env={'NPM_CONFIG_GLOBALCONFIG': str(custom)}), str(chosen))
        self.run_command('npm install --offline --no-audit --no-fund',
                         env={'NPM_CONFIG_GLOBALCONFIG': str(custom)})
        self.assertTrue((chosen / '_cacache').is_dir())
        self.assertFalse((self.project / '.npm-cache').exists())

    def test_inaccessible_explicit_destination_is_not_redirected(self):
        target = self.root / 'file-cache'
        target.write_text('unrelated data')
        code, stdout, stderr = self.run_command('npm install --offline --no-audit --no-fund',
                                               env={'npm_config_cache': str(target)}, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn(str(target), stdout + stderr)
        self.assertEqual(target.read_text(), 'unrelated data')
        self.assertFalse((self.project / '.npm-cache').exists())

    @unittest.skipUnless(PROTECTED, 'native sandbox denial requires --protected in disposable image')
    def test_native_home_and_symlink_escape_remain_denied(self):
        # Without the new global default, native home remains unusable as cache.
        code, stdout, stderr = self.run_command(
            'npm --globalconfig=/dev/null install --offline --no-audit --no-fund', success=False)
        self.assertNotEqual(code, 0)
        self.assertIn(str(self.home / '.npm'), stdout + stderr)
        self.run_command('npm install --offline --no-audit --no-fund')
        code, _, _ = self.run_command('printf forbidden > "$HOME/.codex/auth.json"', success=False)
        self.assertNotEqual(code, 0)
        escape = self.project / 'escape'
        escape.symlink_to(self.home)
        shutil.rmtree(self.project / 'node_modules')
        code, stdout, stderr = self.run_command('npm install --offline --no-audit --no-fund',
                                               env={'npm_config_cache': str(escape / 'cache')}, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn('cache', stdout + stderr)
        self.assertFalse((self.home / 'cache').exists())
        shutil.rmtree(self.project / '.npm-cache')
        (self.project / '.npm-cache').symlink_to(self.home / 'default-escape')
        code, _, _ = self.run_command('npm install --offline --no-audit --no-fund', success=False)
        self.assertNotEqual(code, 0)
        self.assertFalse((self.home / 'default-escape').exists())
        self.assertEqual((self.home / '.codex/auth.json').read_text(), 'synthetic-auth-canary')


if __name__ == '__main__':
    unittest.main(verbosity=2)
