"""Pure archive binding shared by packaging and independent signed admission.

No workflow/service/publication/controller imports: assembly uses these checks
before notarization upload, and ordinary native package tests consume them.
"""
import hashlib
from pathlib import PurePosixPath
import re
import stat
import tarfile

MAX_BYTES = 512 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bundle_files(archive_path, with_directories=False):
    result = {}
    total = 0; names = set(); directories = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(not archive.pax_headers, 'extended archive metadata refused')
        for member in archive:
            name = member.name.removeprefix('./')
            path = PurePosixPath(name)
            require((member.isfile() or member.isdir()) and not path.is_absolute() and '..' not in path.parts
                    and '\\' not in name and name == str(path) and name not in names
                    and (member.isdir() or name != '.') and not member.pax_headers and member.mode & 0o7000 == 0,
                    'unsafe or duplicate package member')
            names.add(name)
            if member.isdir():
                directories[name] = stat.S_IMODE(member.mode)
                continue
            total += member.size
            require(total <= MAX_BYTES and len(result) < 10000, 'package payload exceeds bound')
            result[name] = (archive.extractfile(member).read(), stat.S_IMODE(member.mode))
    return (result, directories) if with_directories else result


def compare_package_payload(signed_archive, qualified_archive):
    """The same exact qualified payload comparison before upload and on ingress."""
    current, directories = bundle_files(signed_archive, with_directories=True)
    original, original_directories = bundle_files(qualified_archive, with_directories=True)
    require(directories == original_directories, 'signed payload directory inventory/modes differ')
    require('distribution-status.txt' in original and 'signing.json' not in original,
            'original qualification is not the expected unsigned package')
    require(set(current) == (set(original) - {'distribution-status.txt'}) | {'signing.json'},
            'signed payload file inventory differs from qualification')
    for name in set(original) - {'libexec/symphony-host', 'files.sha256', 'distribution-status.txt'}:
        require(current[name] == original[name], 'signed package source bytes or permissions differ from qualification')
    require(current['libexec/symphony-host'][1] == original['libexec/symphony-host'][1], 'signed helper permissions differ')
    require(current['files.sha256'][1] == original['files.sha256'][1], 'signed inventory permissions differ')
    require(current['signing.json'][1] == 0o644, 'signed receipt permissions differ')
    lines = current['files.sha256'][0].decode().splitlines()
    expected = {name: hashlib.sha256(raw).hexdigest() for name, (raw, _) in current.items() if name != 'files.sha256'}
    observed = {}
    for line in lines:
        require(re.fullmatch(r'[a-f0-9]{64}  \./[^\r\n]+', line), 'invalid signed payload inventory')
        digest, name = line.split('  ./', 1)
        require(name not in observed, 'duplicate signed payload inventory')
        observed[name] = digest
    require(observed == expected, 'signed payload inventory does not match actual files')
    return current


def helper_hash(archive):
    with tarfile.open(archive, 'r:gz') as bundle:
        entries = [e for e in bundle if e.name in ('./libexec/symphony-host', 'libexec/symphony-host')]
        require(len(entries) == 1 and entries[0].isfile() and entries[0].size <= MAX_BYTES,
                'production archive helper missing')
        return hashlib.sha256(bundle.extractfile(entries[0]).read()).hexdigest()
