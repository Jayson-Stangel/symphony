#!/usr/bin/env python3
"""Bind both actual current native archives without changing runner provenance."""
import json
from pathlib import Path
import sys

import artifacts
from reuse_payload import checksum


def finalize(internal, packages, revision):
    value = json.loads(internal.read_text())
    artifacts.validate(value, require_bundle=False)
    artifacts.require(value['source_revision'] == revision, 'native manager source differs')
    for platform in ('macos', 'linux_x86_64'):
        entry = value['bundles'][platform]
        archive = packages / entry['filename']
        artifacts.require(archive.is_file() and not archive.is_symlink(), 'actual native manager archive missing')
        entry['sha256'] = checksum(archive)
    # JSON decoding does not preserve the internal Python bundle alias.
    value['bundle'] = dict(value['bundles']['macos'])
    artifacts.validate(value)
    (packages / 'release.json').write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    return value


if __name__ == '__main__':
    import os
    finalize(Path(sys.argv[1]), Path(sys.argv[2]), os.environ['GITHUB_SHA'])
