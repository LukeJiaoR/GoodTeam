"""Anchored POSIX filesystem primitives for explicit package/store roots."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Iterator

from .models import SkillError, resource_path


@contextmanager
def directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk every path component without following links; caller authorizes root.

    Callers using platform aliases such as macOS /tmp must explicitly authorize
    their canonical root before calling. This module never discovers permissions.
    """
    if os.name != "posix":
        raise SkillError("unsupported_platform", "Anchored Skill storage currently requires POSIX.")
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise SkillError("unsafe_path", "Explicit roots may not contain parent traversal.")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        raise SkillError("filesystem_unavailable", "The authorized Skill directory is unavailable or unsafe.") from exc
    finally:
        os.close(fd)


@contextmanager
def subdirectory(root_fd: int, relative: str, *, create: bool = False) -> Iterator[int]:
    resource_path(relative)
    fd = os.dup(root_fd)
    try:
        for part in relative.split("/"):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def regular_file(fd: int, name: str, *, maximum: int) -> tuple[bytes, os.stat_result]:
    if "/" in resource_path(name):
        raise SkillError("unsafe_path", "Expected one file path component.")
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SkillError("unsafe_file", "Package resources must be singly linked regular files.")
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise SkillError("unsafe_file", "Special permission bits are not supported.")
        if before.st_size > maximum:
            raise SkillError("package_limit", "A Skill resource exceeds its size limit.")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        def identity(item):
            return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(data) > maximum or len(data) != before.st_size or identity(before) != identity(after):
            raise SkillError("source_changed", "A Skill resource changed while being captured.")
        return data, before


def write_new(fd: int, name: str, data: bytes, *, mode: int = 0o600) -> None:
    if "/" in resource_path(name):
        raise SkillError("unsafe_path", "Expected one file path component.")
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    with os.fdopen(target, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
