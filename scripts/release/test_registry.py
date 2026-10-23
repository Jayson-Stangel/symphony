#!/usr/bin/env python3
"""Own one disposable native qualification registry and retain cleanup evidence."""
import argparse
import json
from pathlib import Path
import secrets
import re
import subprocess

LABEL = 'com.symphony.native-test-owner'


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE, timeout=120).strip()


def start(path, host_port=5000):
    if type(host_port) is not int or not 1 <= host_port <= 65535:
        raise ValueError('registry host port must be an integer between 1 and 65535')
    token = secrets.token_hex(16)
    state = {'name': 'symphony-native-' + token, 'owner': token, 'container_id': None, 'cleanup': 'pending',
             'host_port': host_port}
    path.write_text(json.dumps(state) + '\n')  # retained before acquisition, including uncertain create completion
    container = docker('create', '--name', state['name'], '--label', LABEL + '=' + token,
                       '-p', f'127.0.0.1:{host_port}:5000', 'registry:3')
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise ValueError("invalid registry acquisition receipt; recover by owned unique name")
    state['container_id'] = container
    path.write_text(json.dumps(state) + '\n')
    docker('start', container)


def anonymous_volumes(value):
    """Identify only implicit Docker-managed mounts of this acquired container."""
    host = value.get('HostConfig') or {}
    explicit = {mount.get('Target') for mount in host.get('Mounts') or [] if mount.get('Source')}
    for binding in host.get('Binds') or []:
        parts = binding.split(':')
        if len(parts) >= 2:explicit.add(parts[1])
    return sorted({mount['Name'] for mount in value.get('Mounts') or []
                   if mount.get('Type') == 'volume' and mount.get('Destination') not in explicit
                   and re.fullmatch('[a-f0-9]{64}', mount.get('Name', ''))})


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
    volumes = anonymous_volumes(value)
    state['owned_anonymous_volumes'] = volumes
    path.write_text(json.dumps(state) + '\n')
    # Docker itself removes anonymous mounts only; explicitly named mounts are
    # preserved even when attached to this acquired container.
    docker('rm', '--force', '--volumes', value['Id'])
    present = docker('container', 'ls', '--all', '--filter', 'name=^/' + state['name'] + '$', '--format', '{{.ID}}')
    if present:
        raise ValueError('owned registry remains after cleanup')
    for volume in volumes:
        try:
            docker('volume', 'inspect', volume)
        except subprocess.CalledProcessError:
            if docker('volume', 'ls', '--filter', 'name=^' + volume + '$', '--format', '{{.Name}}'):
                raise ValueError('owned anonymous volume inspection uncertain')
        else:
            raise ValueError('owned anonymous registry volume remains after cleanup')
    state.update(container_id=value['Id'], cleanup='removed-owned-container')
    path.write_text(json.dumps(state) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('start', 'cleanup'))
    parser.add_argument('state', type=Path)
    parser.add_argument('--host-port', type=int, default=5000,
                        help='explicit loopback host port for start; default 5000')
    args = parser.parse_args()
    try:
        if args.operation == 'start':start(args.state, args.host_port)
        else:cleanup(args.state)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'native registry: ' + type(error).__name__ + '; ownership/cleanup unresolved\n')


if __name__ == '__main__':
    main()
