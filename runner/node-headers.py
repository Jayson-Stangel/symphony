#!/usr/bin/env python3
"""Image-owned node-gyp headers; missing-only native-HOME cache preparation."""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import subprocess

STORE = Path('/usr/local/share/symphony/node-gyp')
HEADERS = Path('/usr/local/include/node')


def build_store():
    version = subprocess.check_output(
        ['/usr/local/bin/node', '-p', 'process.versions.node'], text=True).strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('unexpected supplied Node version')
    header = (HEADERS / 'node_version.h').read_text()
    actual = '.'.join(re.search(r'^#define NODE_' + part + r'_VERSION (\d+)$',
                               header, re.M)[1] for part in ('MAJOR', 'MINOR', 'PATCH'))
    if actual != version:
        raise RuntimeError('supplied Node executable and headers differ')
    package = json.loads(Path(
        '/usr/local/lib/node_modules/npm/node_modules/node-gyp/package.json').read_text())
    marker = package['installVersion']
    if not isinstance(marker, int) or marker < 1:
        raise RuntimeError('invalid supplied node-gyp installVersion')
    entry = STORE / version
    (entry / 'include').mkdir(parents=True)
    (entry / 'include/node').symlink_to(HEADERS, target_is_directory=True)
    (entry / 'installVersion').write_text(str(marker) + '\n')
    (STORE / 'manifest.json').write_text(json.dumps({
        'version': version, 'installVersion': marker}) + '\n')
    for file in (entry / 'installVersion', STORE / 'manifest.json'):
        file.chmod(0o444)
    for directory in (entry / 'include', entry, STORE):
        directory.chmod(0o555)


def prepare_home(home, store=STORE):
    manifest = json.loads((store / 'manifest.json').read_text())
    version = manifest['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('invalid image header-cache version')
    entry = store / version
    if int((entry / 'installVersion').read_text()) != manifest['installVersion']:
        raise RuntimeError('image header-cache metadata differs')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(home, flags)
    try:
        for part in ('.cache', 'node-gyp'):
            try:
                os.mkdir(part, mode=0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    return 'existing-custom-parent'
                raise
            os.close(descriptor)
            descriptor = child
        try:
            os.symlink(str(entry), version, dir_fd=descriptor)
            return 'prepared'
        except FileExistsError:
            # Preserve existing directories, files, and custom/dangling links.
            return 'existing'
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--build-store', action='store_true')
    mode.add_argument('--prepare-home', type=Path)
    args = parser.parse_args()
    if args.build_store:
        build_store()
    elif not os.environ.get('XDG_CACHE_HOME'):
        prepare_home(args.prepare_home)


if __name__ == '__main__':
    main()
