"""One host-local foreground session; no daemon, provisioning or authority."""
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

from mac_runner_preflight import Refusal


class CapacityBusy(Refusal):
    """Another healthy foreground owner holds the slot; do not alter it."""


class LocalSessionLease:
    def __init__(self, directory, session_id):
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Refusal('local capacity session UUID is invalid') from None
        self.session_id = session_id
        self.directory = Path(directory)
        if not self.directory.is_absolute() or self.directory.resolve() != self.directory:
            raise Refusal('local capacity state directory is redirected')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refusal('local capacity state directory must be private and owned')
        self.path = self.directory / 'foreground-session.lock'
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            self._identity()
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CapacityBusy('owned capacity busy; healthy session preserved') from None
            raw = os.read(self.fd, 8193)
            if raw:
                try:
                    previous = json.loads(raw)
                except ValueError:
                    raise Refusal('previous capacity state is uncertain; fresh execution refused') from None
                if (not isinstance(previous, dict) or set(previous) != {'schema', 'session_id', 'cleanup_complete'}
                        or previous['schema'] != 1 or previous['cleanup_complete'] is not True):
                    raise Refusal('previous session cleanup is unverified; fresh execution refused')
            self._save(False)
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise

    def _identity(self):
        opened, named = os.fstat(self.fd), self.path.lstat()
        if ((opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or opened.st_mode & 0o077 or opened.st_nlink != 1):
            raise Refusal('local capacity lock identity changed or is unsafe')

    def _save(self, complete):
        self._identity()
        value = json.dumps(dict(schema=1, session_id=self.session_id, cleanup_complete=complete)).encode()
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        remaining = memoryview(value)
        while remaining:
            written = os.write(self.fd, remaining)
            if written <= 0:
                raise Refusal('capacity record write did not complete')
            remaining = remaining[written:]
        os.fsync(self.fd)

    def complete(self, proof):
        # Caller supplies the actual pinned watchdog and independent cleanup
        # readbacks. This mutex does not manufacture those observations.
        fields = ('cleanup_complete', 'vm_stopped', 'vm_deleted', 'runner_deregistered',
                  'foreground_reaped', 'permit_removed', 'settings_restored')
        if proof.get('session_id') != self.session_id or any(proof.get(key) is not True for key in fields):
            raise Refusal('capacity release requires complete matching owned cleanup')
        self._save(True)

    def close(self):
        if self.fd is not None:
            # Closing an unclean session unlocks its inode but retains false.
            # A crashed controller cannot silently admit the next session.
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
