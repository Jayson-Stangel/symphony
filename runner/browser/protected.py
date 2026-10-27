#!/usr/bin/env python3
"""Credential-free guarded-launch + command/exec proof in a disposable image.

Requires /native-profile/config.toml to be an empty read-only file mount. This
uses the image's actual launch guard and official native chain; it supplies no
browser environment override and no model/account. It is supporting task-path
proof, separate from the final marketing suite and native platform acceptance.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, '/qualification')
from test_npm_cache import NativeCommands

SOURCE = Path('/opt/symphony/elixir/priv')
BOUNDARY = SOURCE / 'codex_config_boundary.py'
spec = importlib.util.spec_from_file_location('boundary', BOUNDARY)
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)


def main():
    if 'CHROME_CONFIG_HOME' in os.environ or 'XDG_CONFIG_HOME' in os.environ:
        raise RuntimeError('default qualification requires no browser configuration override')
    home = Path('/native-profile')
    config = home / 'config.toml'
    if config.read_bytes() != b'':
        raise RuntimeError('qualification needs an empty synthetic native config')
    (home / 'auth.json').write_text('{"symphony_browser_canary":true}')
    root = Path(tempfile.mkdtemp(prefix='browser-proof-', dir='/workspaces')).resolve()
    project = root / 'task with spaces'
    project.mkdir()
    (project / '.git').mkdir()
    (project / 'package.json').write_text(json.dumps({'private': True, 'scripts': {
        'browser:test': 'node /opt/symphony/browser/smoke.cjs',
        'browser:minimal': 'node minimal.cjs'}}))
    (project / 'minimal.cjs').write_text("""const assert = require('node:assert/strict');
const { chromium } = require('/opt/symphony/browser/node_modules/@playwright/test');
(async () => {
  const browser = await chromium.launch({channel: 'chromium', headless: true});
  try {
    const page = await browser.newPage();
    await page.goto('data:text/html,<title>Minimal browser</title>');
    assert.equal(await page.title(), 'Minimal browser');
    console.log('minimal full Chromium passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
    executable = str(Path(shutil.which('codex')).resolve())
    chain = boundary.image_chain(executable)
    node, _ = boundary._fingerprint_binary(Path(shutil.which('node')).resolve())
    chain['node'] = node
    policy = {'type': 'workspaceWrite', 'writableRoots': [str(project), str(project / '.git')],
              'networkAccess': True, 'excludeTmpdirEnvVar': False, 'excludeSlashTmp': False}
    value = {'operation': 'exec', 'snapshot_profile': 'trust-ro-v2',
             'expected_snapshot_uid': config.stat().st_uid,
             'expected_snapshot_sha256': hashlib.sha256(b'').hexdigest(),
             'expected_home': str(home), 'expected_codex_home': str(home),
             'expected_executable': executable,
             'expected_executable_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
             'expected_native_environment': {
                 key: hashlib.sha256(value.encode()).hexdigest() for key, value in os.environ.items()
                 if key != 'CODEX_HOME' and key.startswith(boundary.NATIVE_ENV_PREFIXES)},
             'workspace': str(project), 'managed_workspace': str(root), 'launch_root': str(project),
             'browser_policy': policy, 'expected_launch_chain': chain,
             'expected_image_chain': {key: value for key, value in chain.items() if key != 'node'}}
    command = (sys.executable, '-I', '-c', BOUNDARY.read_text() + '\n' + (SOURCE / 'codex_launch.py').read_text(),
               json.dumps(value), 'codex', '--config', 'shell_environment_policy.inherit=all', 'app-server')
    native = None
    try:
        for phase in ('cold', 'restart', 'pruned'):
            state = project / '.symphony-browser-config'
            if phase == 'pruned':
                shutil.rmtree(state)
            native = NativeCommands(home, project, command=command)
            native.start()
            for login in (False, True):
                script = ('test "$CHROME_CONFIG_HOME" = "$PWD/.symphony-browser-config" && '
                          'npm run browser:test && npm run browser:minimal')
                result = native.request('command/exec', {
                    'command': ['/bin/bash', '-lceu' if login else '-ceu', script],
                    'cwd': str(project), 'timeoutMs': 40000, 'sandboxPolicy': policy})
                print(json.dumps({'phase': phase, 'login': login, 'result': result}), flush=True)
                if result['exitCode'] != 0:
                    raise RuntimeError('ordinary protected browser command failed')
            denied = native.request('command/exec', {
                'command': ['/bin/bash', '-ceu', 'printf forbidden > "$HOME/auth.json"'],
                'cwd': str(project), 'timeoutMs': 10000, 'sandboxPolicy': policy})
            if denied['exitCode'] == 0 or (home / 'auth.json').read_text() != '{"symphony_browser_canary":true}':
                raise RuntimeError('native HOME/auth write boundary failed')
            native.close()
            native = None
        print(json.dumps({'protected_browser': 'passed', 'native_chain': chain,
                          'policy': policy, 'browser_state_lifetime': 'workspace'}), flush=True)
    finally:
        if native is not None:
            native.close()
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
