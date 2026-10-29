"""Audited full-ExUnit input identity; unknown state never authorizes reuse.

This module collects public code/configuration only. It never reads credential
stores or serializes the ambient environment. The caller still authenticates
producer and consumer identities independently.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import stat
import subprocess
import time


class UnknownInput(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def command(arguments, *, cwd=None, env=None, timeout=20):
    try:
        result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise UnknownInput('public input probe unavailable') from None
    if result.returncode or len(result.stdout) > 2_000_000:
        raise UnknownInput('public input probe failed or exceeded its bound')
    return result.stdout


def stable_stat(info):
    # Reading can update atime; labels/inodes guard races but are not evidence.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid)


def file_identity(path):
    """Hash bytes only after pinning the actual regular-file identity."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise UnknownInput('regular public input required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise UnknownInput('public input changed before reading')
        value = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                value.update(chunk)
        after = path.lstat()
        if stable_stat(before) != stable_stat(after):
            raise UnknownInput('public input changed while reading')
        return dict(sha256=value.hexdigest(), size=before.st_size,
                    mode=stat.S_IMODE(before.st_mode), uid=before.st_uid, gid=before.st_gid)
    finally:
        os.close(fd)


def tree_identity(root, *, allowed_roots, deadline, maximum_files=100_000):
    """Include raw cache metadata, NIFs, modes and symlink destinations.

    No compiler manifest is normalized away. Cold/force compilation can produce
    different executable bytes and must then select fresh coverage.
    """
    root = Path(root).absolute()
    allowed = [Path(item).resolve() for item in allowed_roots]
    rows, elfs, total = [], [], 0
    active = set()

    def inside(path):
        return any(path == parent or path.is_relative_to(parent) for parent in allowed)

    def visit(path, label):
        nonlocal total
        if time.monotonic() > deadline or len(rows) >= maximum_files:
            raise UnknownInput('public input manifest exceeded its budget')
        try:
            info = path.lstat()
        except FileNotFoundError:
            rows.append([label, 'absent'])
            return
        if stat.S_ISLNK(info.st_mode):
            try:
                target = path.resolve(strict=False)
            except RuntimeError:
                raise UnknownInput('public input symlink cycle') from None
            if not inside(target):
                raise UnknownInput('public input symlink escapes the audited roots')
            rows.append([label, 'symlink', os.readlink(path), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid])
            if target in active:
                raise UnknownInput('public input symlink cycle')
            visit(target, label + '/@resolved')
            if stable_stat(path.lstat()) != stable_stat(info):
                raise UnknownInput('public input symlink changed while reading')
        elif stat.S_ISDIR(info.st_mode):
            resolved = path.resolve()
            if resolved in active:
                raise UnknownInput('public input directory cycle')
            active.add(resolved)
            rows.append([label, 'directory', stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid])
            for child in sorted(path.iterdir()):
                visit(child, label + '/' + child.name)
            active.remove(resolved)
            after = path.lstat()
            if stable_stat(info) != stable_stat(after):
                raise UnknownInput('public input directory changed while reading')
        elif stat.S_ISREG(info.st_mode):
            value = file_identity(path)
            rows.append([label, 'file', value])
            total += value['size']
            with path.open('rb') as source:
                if source.read(4) == b'\x7fELF':
                    elfs.append(str(path.resolve()))
        else:
            raise UnknownInput('unsupported public input file kind')

    if not inside(root.resolve()):
        raise UnknownInput('public input root escapes the audited roots')
    try:
        visit(root, '.')
        if time.monotonic() > deadline:
            raise UnknownInput('public input manifest exceeded its budget')
    except OSError:
        raise UnknownInput('public input inventory unavailable') from None
    return dict(path=str(root), sha256=digest(rows), files=len(rows), bytes=total), sorted(set(elfs))


def tracked_source(root, revision, excluded):
    """Compare actual checkout bytes/modes to the immutable candidate tree."""
    root = Path(root)
    if not re.fullmatch('[0-9a-f]{40}', revision or ''):
        raise UnknownInput('invalid source revision')
    actual = command(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip()
    if actual != revision:
        raise UnknownInput('checkout is not the actual validation candidate')
    rows = []
    for raw in command(['git', 'ls-tree', '-rz', revision], cwd=root).split(b'\0'):
        if not raw:
            continue
        metadata, name = raw.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        path = name.decode()
        if excluded(path):
            continue
        if kind != 'blob' or mode not in {'100644', '100755', '120000'}:
            raise UnknownInput('unsupported tracked validation input')
        expected = command(['git', 'cat-file', 'blob', oid], cwd=root)
        current = root / path
        if mode == '120000':
            if not current.is_symlink() or os.fsencode(os.readlink(current)) != expected:
                raise UnknownInput('tracked symlink differs from candidate')
            if not current.resolve().is_relative_to(root.resolve()):
                raise UnknownInput('tracked source symlink escapes checkout')
        else:
            identity = file_identity(current)
            if identity['sha256'] != hashlib.sha256(expected).hexdigest() or identity['size'] != len(expected):
                raise UnknownInput('tracked input differs from candidate')
            executable = bool(identity['mode'] & 0o111)
            if executable != (mode == '100755'):
                raise UnknownInput('tracked input executable mode differs')
        info = current.lstat()
        rows.append([path, mode, hashlib.sha256(expected).hexdigest(),
                     stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid])
    return dict(sha256=digest(rows), files=len(rows)), rows


def untracked_inputs(root, generated):
    """Generated trees must be separately hashed; no hidden cwd inputs vanish.

    `generated` is a finite trusted profile, never a candidate-provided ignore
    pattern. Both ignored and ordinary untracked files are inspected.
    """
    for arguments in (['--others', '--exclude-standard'],
                      ['--others', '--ignored', '--exclude-standard']):
        rows = command(['git', 'ls-files', '-z', *arguments], cwd=root).split(b'\0')
        for raw in rows:
            if not raw:
                continue
            name = raw.decode()
            if not any(name == prefix or name.startswith(prefix.rstrip('/') + '/')
                       for prefix in generated):
                raise UnknownInput('untracked or ignored validation input outside audited generated trees')


def controlled_environment(root, state, parent):
    """An explicit test environment; never copy provider or Actions credentials."""
    root, supplied = Path(root).resolve(), Path(state)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise UnknownInput('private validation state must be an absolute ordinary path')
    state = supplied.resolve()
    if state == root or root.is_relative_to(state) or state.is_relative_to(root):
        raise UnknownInput('private validation state overlaps source')
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        raise UnknownInput('private validation state already exists') from None
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise UnknownInput('private validation state is unsafe')
    for name in ('home', 'tmp', 'hex'):
        path = state / name
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise UnknownInput('private validation directory is unsafe')
    original_home = Path(parent['HOME'])
    mix_home = Path(parent.get('MIX_HOME', original_home / '.mix')).resolve()
    if any((mix_home / name).exists() for name in ('config.exs', 'config', '.env', 'auth.json')):
        raise UnknownInput('unreviewed global Mix configuration')
    path = parent.get('PATH', '')
    if not path or any(not item.startswith('/') for item in path.split(':')):
        raise UnknownInput('noncanonical executable search path')
    return dict(PATH=path, HOME=str(state / 'home'), TMPDIR=str(state / 'tmp'),
                HEX_HOME=str(state / 'hex'), HEX_OFFLINE='1', MIX_HOME=str(mix_home),
                MIX_ENV='test', LANG='C.UTF-8', LC_ALL='C.UTF-8', TZ='UTC',
                CI='true', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                GIT_CONFIG_COUNT='0', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')


def mount_semantics(text):
    """Device/mount IDs are labels; type, roots, flags and propagation matter."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        try:
            separator = parts.index('-')
            if separator < 6 or len(parts) < separator + 4:
                raise ValueError()
            optional = sorted(field.split(':', 1)[0] for field in parts[6:separator])
            rows.append([parts[3], parts[4], sorted(parts[5].split(',')), optional,
                         parts[separator + 1], sorted(parts[separator + 3].split(','))])
        except ValueError:
            raise UnknownInput('malformed actual mount inventory') from None
    if not rows:
        raise UnknownInput('actual mount inventory unavailable')
    return rows


def linux_platform(parent):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.getuid() == 0:
        raise UnknownInput('only ordinary nonroot Linux x86_64 runners are audited')
    if Path('/.dockerenv').exists() or Path('/opt/symphony').exists():
        raise UnknownInput('container or installed native environment is not audited')
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release or 'VERSION_ID="24.04"' not in release:
        raise UnknownInput('only Ubuntu24.04 is audited')
    image = parent.get('ImageVersion', '')
    if parent.get('ImageOS') != 'ubuntu24' or not re.fullmatch(r'[0-9]{8}\.[0-9]+\.[0-9]+', image):
        raise UnknownInput('immutable hosted image identity unavailable')
    status = Path('/proc/self/status').read_text()
    fields = {}
    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs', 'Seccomp', 'Seccomp_filters'):
        match = re.search('^' + key + r':\s+(\S+)$', status, re.M)
        if not match:
            raise UnknownInput('process security settings unavailable')
        fields[key] = match[1]
    limits = {str(key): list(resource.getrlimit(value)) for key, value in vars(resource).items()
              if key.startswith('RLIMIT_') and isinstance(value, int)}
    current_umask = os.umask(0o022)
    os.umask(current_umask)
    return dict(image_os=parent['ImageOS'], image_version=image, os_release_sha256=hashlib.sha256(release.encode()).hexdigest(),
                kernel=[platform.release(), platform.version()], architecture=platform.machine(),
                uid=os.getuid(), gid=os.getgid(), groups=sorted(os.getgroups()), umask=current_umask,
                cpu_affinity=sorted(os.sched_getaffinity(0)), security=fields, resource_limits=limits,
                mounts=mount_semantics(Path('/proc/self/mountinfo').read_text()))
