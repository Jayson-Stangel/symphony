"""One bounded public _build/test sidecar; never an executable trust decision.

The protected consumer authenticates the original run and compares all other
inputs before calling stage(). No archive extraction API or compiler-byte
normalization is used. Unknown file kinds/links/ownership select fresh tests.
"""
import hashlib
from itertools import chain
import gzip
from decimal import Decimal, InvalidOperation
import io
import os
import re
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time

from validation_inputs import UnknownInput, tree_identity

MAX_COMPRESSED = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_ENTRIES = 10_000
LINKS = {
    'phoenix-colocated/symphony_elixir/node_modules': '../../../../assets/node_modules',
    'lib/yamerl/include': '../../../../deps/yamerl/include',
    'lib/yamerl/src': '../../../../deps/yamerl/src',
    'lib/phoenix/priv': '../../../../deps/phoenix/priv',
    'lib/file_system/priv': '../../../../deps/file_system/priv',
    'lib/phoenix_html/priv': '../../../../deps/phoenix_html/priv',
    'lib/symphony_elixir/priv': '../../../../priv',
    'lib/date_time_parser/priv': '../../../../deps/date_time_parser/priv',
    'lib/telemetry/src': '../../../../deps/telemetry/src',
    'lib/phoenix_live_view/priv': '../../../../deps/phoenix_live_view/priv',
}


def require(value):
    if not value:
        raise UnknownInput('test-cache sidecar is outside the audited public archive profile')


def private_hex(root, repository, private_root):
    root, repository, state = Path(root), Path(repository).resolve(), Path(private_root)
    require(state.is_absolute() and state.is_dir() and not state.is_symlink()
            and state.stat().st_uid == os.getuid() and not state.stat().st_mode & 0o077
            and not state.resolve().is_relative_to(repository) and not repository.is_relative_to(state.resolve())
            and root.parent == state and root.name in ('hex','.hex-evidence-v2') and not root.is_symlink())
    return state


def hex_member(name, directory, repository):
    if directory:
        return name in ('.','packages','packages/hexpm')
    if name == 'cache.bin':
        return True
    lock = (Path(repository)/'elixir/mix.lock').read_text()
    rows = re.findall(r'^  "[^"]+": \{:hex, :([a-zA-Z0-9_]+), "([^"]+)"',lock,re.M)
    require(bool(rows))
    return name in {'packages/hexpm/'+package+'-'+version+'.tar' for package,version in rows}


def original_mtime(row):
    try:
        value=Decimal(row.pax_headers.get('mtime',str(row.mtime)))
        require(value.is_finite() and 0<=value<=4102444800)
        return int(value*1000000000)
    except (InvalidOperation,ValueError,OverflowError):
        raise UnknownInput('invalid original public archive timestamp') from None


def precheck(roots):
    entries=total=0;deadline=time.monotonic()+5
    for root in roots:
        root=Path(root)
        for path in chain((root,),root.rglob('*')):
            info=path.lstat();entries+=1
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode))
            if stat.S_ISREG(info.st_mode):total+=info.st_size
            require(entries<=MAX_ENTRIES and total<=MAX_EXPANDED and time.monotonic()<deadline)
    return dict(entries=entries,bytes=total)


def identity(root, repository, *, private_root=None):
    allowed=[repository]
    if private_root is not None:
        allowed.append(private_hex(root,repository,private_root))
    return tree_identity(root, allowed_roots=allowed, deadline=time.monotonic() + 15)[0]


def create(root, repository, *, private_root=None):
    root, repository = Path(root), Path(repository).resolve()
    before = identity(root, repository, private_root=private_root)
    paths = [root, *sorted(root.rglob('*'))]
    require(len(paths) <= MAX_ENTRIES and sum(path.stat().st_size for path in paths if path.is_file() and not path.is_symlink()) <= MAX_EXPANDED)
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:gz', dereference=False, compresslevel=1) as bundle:
        for path in paths:
            name = '.' if path == root else path.relative_to(root).as_posix()
            info = bundle.gettarinfo(str(path), arcname=name)
            require(info.isdir() or info.isfile() or (info.issym() and LINKS.get(name) == info.linkname))
            if private_root is not None:
                require(not info.issym() and hex_member(name,info.isdir(),repository))
            original_mtime(info)
            require(info.uid == os.getuid() and info.gid == os.getgid() and (info.issym() or not info.mode & 0o7022))
            if info.isfile():
                with path.open('rb') as data:
                    bundle.addfile(info, data)
            else:
                bundle.addfile(info)
    raw = target.getvalue()
    require(0 < len(raw) <= MAX_COMPRESSED)
    require(identity(root, repository, private_root=private_root) == before)
    return raw, dict(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), cache=before)


def stage(raw, metadata, directory, repository, *, private_root=None):
    """Validate every inert member before writing a fresh private staging tree."""
    directory, repository = Path(directory), Path(repository).resolve()
    require(type(raw) is bytes and 0 < len(raw) <= MAX_COMPRESSED
            and type(metadata) is dict and metadata.get('size') == len(raw)
            and metadata.get('sha256') == hashlib.sha256(raw).hexdigest())
    require(not directory.exists() and not directory.is_symlink())
    if private_root is None:
        require(directory.parent.resolve().is_relative_to(repository))
    else:
        private_hex(directory,repository,private_root)
    try:
        limit = MAX_EXPANDED + MAX_ENTRIES * 2048
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded = compressed.read(limit + 1)
        require(0 < len(expanded) <= limit)
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as bundle:
            rows, total = [], 0
            for row in bundle:
                rows.append(row); total += row.size
                require(len(rows) <= MAX_ENTRIES and 0 <= row.size <= MAX_EXPANDED and total <= MAX_EXPANDED)
            require(bool(rows))
            names = set()
            for row in rows:
                name = PurePosixPath(row.name)
                require(row.name == '.' or (not name.is_absolute() and '..' not in name.parts
                        and str(name) == row.name and row.name not in ('', './')))
                require(row.name not in names and row.uid == os.getuid() and row.gid == os.getgid()
                        and (row.issym() or not row.mode & 0o7022) and row.size >= 0)
                require(row.isdir() or row.isfile() or (row.issym() and LINKS.get(row.name) == row.linkname))
                if private_root is not None:
                    require(not row.issym() and hex_member(row.name,row.isdir(),repository))
                original_mtime(row)
                names.add(row.name)
            by_name = {row.name: row for row in rows}
            require('.' in names and by_name['.'].isdir())
            for row in rows:
                if row.name != '.':
                    parent = str(PurePosixPath(row.name).parent)
                    require(parent in names and by_name[parent].isdir())
            directory.mkdir(mode=0o700)
            for row in sorted(rows, key=lambda item: (len(PurePosixPath(item.name).parts), item.name)):
                path = directory if row.name == '.' else directory / row.name
                if row.isdir():
                    if path != directory:
                        path.mkdir(mode=0o700)
                elif row.isfile():
                    source = bundle.extractfile(row)
                    require(source is not None)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as output:
                        remaining = row.size
                        while remaining:
                            block = source.read(min(1024 * 1024, remaining))
                            require(bool(block)); output.write(block); remaining -= len(block)
                    os.chmod(path, row.mode)
                    stamp=original_mtime(row);os.utime(path,ns=(stamp,stamp),follow_symlinks=False)
            # Only the finite audited links are created, after regular data.
            for row in rows:
                if row.issym():
                    path = directory / row.name
                    require((path.parent / row.linkname).resolve().is_relative_to(repository))
                    path.symlink_to(row.linkname)
            for row in rows:
                if row.isdir():
                    os.chmod(directory if row.name == '.' else directory / row.name, row.mode)
            # Directory timestamps are last, after every child write/link.
            for row in sorted(rows,key=lambda item:len(PurePosixPath(item.name).parts),reverse=True):
                if row.isdir() or row.issym():
                    path=directory if row.name=='.' else directory/row.name
                    stamp=original_mtime(row);os.utime(path,ns=(stamp,stamp),follow_symlinks=False)
        observed = identity(directory, repository, private_root=private_root)
        expected = metadata.get('cache')
        require(type(expected) is dict and all(observed[key] == expected.get(key) for key in ('sha256', 'files', 'bytes')))
        return observed
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise UnknownInput('test-cache sidecar could not be validated/materialized') from None
