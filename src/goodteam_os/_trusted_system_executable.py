from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


SYSTEM_EXECUTION_PATH = "/usr/bin:/bin"
SYSTEM_GIT_CANDIDATES = (
    ("/usr/bin/git", "/usr"),
    ("/bin/git", "/"),
)


class TrustedExecutableError(Exception):
    pass


@dataclass(frozen=True)
class ExecutableIdentity:
    device: int
    inode: int
    mode: int


def resolve_trusted_system_git(
    root: Path,
    *,
    candidates: tuple[tuple[str, str], ...] = SYSTEM_GIT_CANDIDATES,
) -> tuple[str, str, ExecutableIdentity]:
    root_value = os.path.realpath(root)
    for candidate, trusted_root in candidates:
        executable = os.path.realpath(candidate)
        canonical_trusted_root = os.path.realpath(trusted_root)
        if (
            not path_is_within(executable, canonical_trusted_root)
            or path_is_within(executable, root_value)
            or not trusted_system_path(executable, canonical_trusted_root)
        ):
            continue
        try:
            descriptor = os.open(
                executable,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            continue
        try:
            value = os.fstat(descriptor)
            if not stat.S_ISREG(value.st_mode) or not value.st_mode & 0o111:
                continue
            identity = ExecutableIdentity(value.st_dev, value.st_ino, value.st_mode)
        finally:
            os.close(descriptor)
        return executable, SYSTEM_EXECUTION_PATH, identity
    raise TrustedExecutableError("Trusted Git executable was not available.")


def open_verified_executable(
    executable: str,
    expected: ExecutableIdentity,
) -> int:
    try:
        descriptor = os.open(
            executable,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise TrustedExecutableError(
            "Trusted Git executable changed before use."
        ) from exc
    value = os.fstat(descriptor)
    actual = ExecutableIdentity(value.st_dev, value.st_ino, value.st_mode)
    if actual != expected or not stat.S_ISREG(value.st_mode):
        os.close(descriptor)
        raise TrustedExecutableError("Trusted Git executable changed before use.")
    return descriptor


def trusted_system_path(executable: str, trusted_root: str) -> bool:
    current = trusted_root
    paths = [current]
    relative = os.path.relpath(os.path.dirname(executable), trusted_root)
    if relative != ".":
        for component in relative.split(os.sep):
            current = os.path.join(current, component)
            paths.append(current)
    paths.append(executable)
    for path in paths:
        try:
            value = os.stat(path, follow_symlinks=False)
        except OSError:
            return False
        if value.st_uid != 0 or value.st_mode & 0o022:
            return False
    try:
        executable_stat = os.stat(executable, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(executable_stat.st_mode) and bool(executable_stat.st_mode & 0o111)


def path_is_within(candidate: str, root: str) -> bool:
    try:
        return os.path.commonpath((candidate, root)) == root
    except ValueError:
        return False
