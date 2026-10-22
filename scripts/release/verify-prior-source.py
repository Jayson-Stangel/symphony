#!/usr/bin/env python3
"""Cheap capability check for an explicitly selected source-build prior."""
import argparse
from pathlib import Path
import re
import subprocess

REQUIRED = ('runner/Dockerfile', 'elixir/mix.exs', 'elixir/mix.lock', 'scripts/release/package.sh',
            'scripts/release/build-host-helper.sh', 'scripts/package/host.swift', 'scripts/package/test-package.py',
            'scripts/package/linux/Package.swift', 'scripts/package/linux/Package.resolved',
            'scripts/symphony', 'scripts/symphony-package.sh', 'scripts/symphony-package-update.sh')


def verify(root, revision):
    if not re.fullmatch('[a-f0-9]{40}', revision): raise ValueError('prior source requires a full commit SHA')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--verify', revision+'^{commit}'], text=True).strip()
    if actual != revision: raise ValueError('prior source differs from selected commit')
    for path in REQUIRED:
        kind = subprocess.check_output(['git', '-C', str(root), 'cat-file', '-t', revision+':'+path], text=True).strip()
        if kind != 'blob': raise ValueError('prior source capability is not a file: '+path)
    builder = subprocess.check_output(['git', '-C', str(root), 'show', revision+':scripts/release/build-host-helper.sh'], text=True)
    if '--native' not in builder: raise ValueError('prior Mac helper cannot select the native CPU')
    return revision


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision'); parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    print(verify(args.root, args.revision))
