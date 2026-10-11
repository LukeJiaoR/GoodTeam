"""Bounded POSIX file-store primitives; no memory/retrieval policy lives here.

One stable lock inode serializes each role across threads and processes. WAL
replacement is the commit point; derived files are individually atomic, not a
multi-file transaction. A separately retained checkpoint permits index repair
without discarding the last known append-only history.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import threading
import uuid
from weakref import WeakValueDictionary

try:
    import fcntl
except ImportError:  # Explicitly fail closed on unsupported platforms.
    fcntl = None

from .skills.filesystem import directory, open_lock_file, regular_file, write_new
from .skills.models import SkillError

MAX_WAL_BYTES = 32 * 1024 * 1024
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_GUARD = threading.Lock()
_LOCKS: WeakValueDictionary = WeakValueDictionary()
_HELD = threading.local()
_OPEN_LOCKS: set[int] = set()


def _after_fork() -> None:
    # A child must not inherit a parent's reentrant ownership or locked Python
    # mutexes. The stable file lock still mediates the two processes.
    global _GUARD, _LOCKS, _HELD, _OPEN_LOCKS
    for descriptor in _OPEN_LOCKS:
        os.close(descriptor)
    _OPEN_LOCKS = set()
    _GUARD = threading.Lock()
    _LOCKS = WeakValueDictionary()
    _HELD = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class MemoryStorageError(ValueError):
    """Stable safe failure; callers must not fall back to stale projections."""


@contextmanager
def role_lock(root: Path, *, create: bool = True):
    if fcntl is None or os.name != "posix":
        raise MemoryStorageError("memory_storage_unsupported")
    identity = str(root.absolute())
    with _GUARD:
        lock = _LOCKS.setdefault(identity, threading.RLock())
    with lock:
        held = getattr(_HELD, "keys", set())
        if identity in held:
            yield True
            return
        entered = False
        try:
            with directory(root, create=create) as fd:
                entered = True
                descriptor = open_lock_file(fd, ".store.lock")
                _OPEN_LOCKS.add(descriptor)
                owner_pid = os.getpid()
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    _HELD.keys = held | {identity}
                    try:
                        yield True
                    finally:
                        if os.getpid() == owner_pid:
                            _HELD.keys = held
                finally:
                    if os.getpid() == owner_pid:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        finally:
                            _OPEN_LOCKS.discard(descriptor)
                            os.close(descriptor)
        except SkillError as exc:
            if not entered and not create and isinstance(exc.__cause__, FileNotFoundError):
                # This absence observation is the read's linearization point.
                # The caller must return an empty view, not read files unlocked
                # if another process initializes the role immediately after it.
                yield False
                return
            raise MemoryStorageError("memory_storage_unavailable") from exc


def serialized_role(function=None, *, create: bool = True, missing: str = ""):
    if function is None:
        return lambda method: serialized_role(method, create=create, missing=missing)

    @wraps(function)
    def wrapped(self, role_key, *args, **kwargs):
        with role_lock(self.role_root(role_key), create=create) as present:
            if not present:
                return getattr(self, missing)(role_key, *args, **kwargs)
            return function(self, role_key, *args, **kwargs)
    return wrapped


def read_bytes(path: Path, *, maximum: int = MAX_PROJECTION_BYTES) -> bytes | None:
    """Missing leaf is distinct from an empty file; never follow symlinks."""
    try:
        with directory(path.parent) as fd:
            try:
                return regular_file(fd, path.name, maximum=maximum)[0]
            except FileNotFoundError:
                return None
    except SkillError as exc:
        raise MemoryStorageError("memory_storage_unreadable") from exc


def atomic_write(path: Path, data: bytes, *, maximum: int = MAX_PROJECTION_BYTES) -> None:
    if len(data) > maximum:
        raise MemoryStorageError("memory_storage_size_limit")
    temporary = ".pending-" + uuid.uuid4().hex
    try:
        with directory(path.parent, create=True) as fd:
            # Validate existing leaves before replacing them, including links.
            try:
                _, info = regular_file(fd, path.name, maximum=maximum)
                mode = info.st_mode & 0o777
            except FileNotFoundError:
                mode = 0o600
            try:
                write_new(fd, temporary, data, mode=mode)
                os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
    except SkillError as exc:
        raise MemoryStorageError("memory_storage_unavailable") from exc
