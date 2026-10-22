#!/usr/bin/env python3
"""Emit the validated retained native-host matrix as an Actions output line."""
import argparse
import json
from pathlib import Path

policy = json.loads(Path(__file__).with_name('host-targets.json').read_text())
expected = {
    ('linux_x86_64', 'ubuntu-24.04', 'Linux', 'x86_64', 'linux_x86_64'),
    ('macos_arm64', 'macos-14', 'Darwin', 'arm64', 'macos'),
}
fields = ('target', 'runner', 'os', 'architecture', 'platform')
assert set(policy) == {'include'} and len(policy['include']) == 2, 'invalid supported host policy'
assert all(set(host) == set(fields) for host in policy['include']), 'unexpected supported host field'
assert {tuple(host[field] for field in fields) for host in policy['include']} == expected, 'unsupported native host target'
parser = argparse.ArgumentParser()
parser.add_argument('--platform', choices=('all', 'macos', 'linux_x86_64'), default='all')
args = parser.parse_args()
if args.platform != 'all':
    policy = {'include': [host for host in policy['include'] if host['platform'] == args.platform]}
    assert len(policy['include']) == 1, 'missing retained native platform'
print('matrix=' + json.dumps(policy, separators=(',', ':')))
