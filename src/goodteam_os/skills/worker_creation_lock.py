"""Serialize one logical Worker key across legacy and Skill creation paths.

This preserves legacy create semantics while preventing a simultaneous legacy
and Skill request from both winning a previously unused key. The lock is only a
creation concurrency boundary; protected Skill registries remain authoritative.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import os
from pathlib import Path
import stat
import threading

from .filesystem import directory
from .models import SkillError

_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_HELD = threading.local()


@contextmanager
def _creation_lock(root: Path, key: str):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    location = root.expanduser().resolve() / ".goodteam" / "workers" / "creation-locks"
    identity = str(location / digest)
    with _GUARD:
        lock = _LOCKS.setdefault(identity, threading.RLock())
    with lock:
        held = getattr(_HELD, "keys", set())
        if identity in held:
            yield
            return
        with directory(location, create=True) as fd:
            descriptor = os.open(digest + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SkillError("unsafe_file", "Worker creation lock is unsafe.")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                _HELD.keys = held | {identity}
                try:
                    yield
                finally:
                    _HELD.keys = held
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def serialized_worker_creation(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        key = str(kwargs.get("idempotency_key", "") or "").strip()
        if not key:
            return function(self, *args, **kwargs)
        with _creation_lock(Path(self.project_root), key):
            return function(self, *args, **kwargs)
    return wrapped
