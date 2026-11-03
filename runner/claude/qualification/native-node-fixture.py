#!/usr/bin/env python3
"""Local fixture for native Bash qualification; this never calls a model.

In a disposable Claude profile/container, trusted setup runs --prepare before
worker startup. The actual native tool runs --run once from the same workspace.
Docker-exec execution of --run is toolchain evidence only, not SDK-tool proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

# Reuse the maintained dependency tarball/addon rather than another native stub.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_native_node import NativeNodeTests

STATE = '.native-node-qualification.json'
HEADER = Path('/usr/local/include/node/node.h')
CANARY = '# synthetic-native-node-qualification\n'


def context():
    home, workspace = Path.home(), Path.cwd()
    if os.getuid() != 11001 or home != Path('/home/claude'):
        raise RuntimeError('requires disposable Claude native profile')
    if workspace != Path('/workspaces/native-node-qualification'):
        raise RuntimeError('requires isolated qualification workspace')
    return home, workspace


def prepare():
    home, workspace = context()
    if any(workspace.iterdir()):
        raise RuntimeError('qualification workspace must start empty')
    canaries = [home / '.npmrc', home / '.claude/symphony-native-node-canary']
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    native = os.open(home, flags)
    try:
        try:
            os.mkdir('.claude', mode=0o700, dir_fd=native)
        except FileExistsError:
            pass
        # Validate parents before helper/canary writes and retain their handles.
        auth = os.open('.claude', flags, dir_fd=native)
        try:
            subprocess.run(['python3', '/usr/local/lib/symphony/node-headers.py',
                            '--prepare-home', str(home)], check=True)
            for parent, name in ((native, '.npmrc'),
                                 (auth, 'symphony-native-node-canary')):
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(CANARY)
        finally:
            os.close(auth)
    finally:
        os.close(native)
    (workspace / STATE).write_text(json.dumps({
        'canaries': [str(path) for path in canaries],
        'header_sha256': hashlib.sha256(HEADER.read_bytes()).hexdigest(),
    }))
    print('Native Node fixture prepared; model requests: 0')


def run():
    home, workspace = context()
    state = json.loads((workspace / STATE).read_text())
    expected = [str(home / '.npmrc'), str(home / '.claude/symphony-native-node-canary')]
    if state['canaries'] != expected:
        raise RuntimeError('unexpected synthetic canary paths')

    def verify():
        for path in expected:
            if Path(path).read_text() != CANARY:
                raise RuntimeError('synthetic auth/config canary changed')
        if hashlib.sha256(HEADER.read_bytes()).hexdigest() != state['header_sha256']:
            raise RuntimeError('image header changed')

    verify()
    # mkdir, without exist_ok, makes a second run fail rather than reuse proof.
    projects = workspace / 'projects'
    projects.mkdir()
    for private in (False, True):
        project = projects / ('private' if private else 'supplied')
        project.mkdir()
        tarball, command = project / 'fixture.tgz', 'node-gyp'
        if private:
            modules = project / 'private/node_modules'
            shutil.copytree('/usr/local/lib/node_modules/npm/node_modules', modules)
            command = 'node ' + shlex.quote(str(modules / 'node-gyp/bin/node-gyp.js'))
        NativeNodeTests.native_tarball(SimpleNamespace(tarball=tarball), command)
        (project / 'package.json').write_text(json.dumps({
            'name': 'native-node-qualification', 'version': '1.0.0', 'private': True,
            'dependencies': {'cold-fixture': 'file:' + str(tarball)},
            'allowScripts': {'file:' + str(tarball): True},
        }))
        env = {**os.environ, 'npm_config_foreground_scripts': 'true',
               'npm_config_strict_allow_scripts': 'true'}
        load = ['node', '-e', 'if(require("cold-fixture")!==741)process.exit(1);'
                'console.log("Native addon loaded: 741")']
        subprocess.run(['npm', 'install', '--offline', '--no-audit', '--no-fund'],
                       cwd=project, env=env, check=True)
        subprocess.run(load, cwd=project, check=True)
        shutil.rmtree(project / 'node_modules/cold-fixture/build')
        subprocess.run(['npm', 'rebuild', 'cold-fixture', '--offline',
                        '--foreground-scripts'], cwd=project, env=env, check=True)
        subprocess.run(load, cwd=project, check=True)
    verify()
    receipt = {'fixture': 'native-node', 'uid': os.getuid(), 'arch': os.uname().machine,
               'supplied_and_private': 'compile/load/delete/rebuild/load passed',
               'synthetic_auth_config': 'unchanged'}
    (workspace / 'native-node-result.json').write_text(json.dumps(receipt) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', action='store_true')
    args = parser.parse_args()
    prepare() if args.prepare else run()
