#!/usr/bin/env python3
"""Own one disposable native qualification registry and retain cleanup evidence."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess

LABEL = 'com.symphony.native-test-owner'


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).strip()


def start(path):
    token = secrets.token_hex(16)
    state = {'name': 'symphony-native-' + token, 'owner': token, 'container_id': None, 'cleanup': 'pending'}
    path.write_text(json.dumps(state) + '\n')  # retained before acquisition, including uncertain create completion
    container = docker('create', '--name', state['name'], '--label', LABEL + '=' + token,
                       '-p', '127.0.0.1:5000:5000', 'registry:3')
    state['container_id'] = container
    path.write_text(json.dumps(state) + '\n')
    docker('start', container)


def cleanup(path):
    if not path.exists():
        path.write_text(json.dumps({'cleanup': 'not-acquired'}) + '\n')
        return
    state = json.loads(path.read_text())
    if 'owner' not in state:
        return
    try:
        value = json.loads(docker('inspect', state['name']))[0]
    except subprocess.CalledProcessError:
        # Distinguish a missing container from unavailable daemon/uncertain state.
        present = docker('container', 'ls', '--all', '--filter', 'name=^/' + state['name'] + '$', '--format', '{{.ID}}')
        if present:
            raise ValueError('registry inspection uncertain; cleanup refused')
        state['cleanup'] = 'absent'
        path.write_text(json.dumps(state) + '\n')
        return
    if (value.get('Name') != '/' + state['name'] or value.get('Config', {}).get('Labels', {}).get(LABEL) != state['owner'] or
            (state['container_id'] is not None and value.get('Id') != state['container_id'])):
        raise ValueError('registry ownership differs; cleanup refused')
    docker('rm', '--force', value['Id'])
    present = docker('container', 'ls', '--all', '--filter', 'name=^/' + state['name'] + '$', '--format', '{{.ID}}')
    if present:
        raise ValueError('owned registry remains after cleanup')
    state.update(container_id=value['Id'], cleanup='removed-owned-container')
    path.write_text(json.dumps(state) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('start', 'cleanup'))
    parser.add_argument('state', type=Path)
    args = parser.parse_args()
    try:
        (start if args.operation == 'start' else cleanup)(args.state)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'native registry: ' + type(error).__name__ + '; ownership/cleanup unresolved\n')


if __name__ == '__main__':
    main()
