from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path, PurePosixPath


DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        ".next",
    }
)


class RepositoryAccessError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class RepositoryEntry:
    path: str
    kind: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RepositoryText:
    path: str
    text: str
    content_hash: str
    size: int
    start_line: int
    end_line: int
    truncated: bool


def canonical_relative_path(value: str, allow_root: bool = False) -> str:
    raw_path = str(value)
    if raw_path == "" and allow_root:
        return ""
    candidate = PurePosixPath(raw_path)
    components = raw_path.split("/")
    if (
        not raw_path
        or "\x00" in raw_path
        or candidate.is_absolute()
        or candidate.as_posix() != raw_path
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise _path_rejected(raw_path)
    return raw_path


class AnchoredRepository:
    def __init__(
        self,
        root: Path,
        *,
        expected_identity: dict[str, int] | None = None,
    ):
        self.root = Path(root)
        self._root_fd = -1
        self._root_parent_fd = -1
        self._root_name: str | None = None
        try:
            (
                self._root_fd,
                self._root_parent_fd,
                self._root_name,
            ) = _open_anchored_root(self.root)
            opened = os.fstat(self._root_fd)
            self._expected_identity = {
                "device": (
                    opened.st_dev
                    if expected_identity is None
                    else int(expected_identity["device"])
                ),
                "inode": (
                    opened.st_ino
                    if expected_identity is None
                    else int(expected_identity["inode"])
                ),
            }
            self.verify_root()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> AnchoredRepository:
        self.verify_root()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1
        if self._root_parent_fd >= 0:
            os.close(self._root_parent_fd)
            self._root_parent_fd = -1

    def verify_root(self) -> None:
        if self._root_fd < 0:
            raise RepositoryAccessError(
                "repository_root_changed",
                "Repository root descriptor is closed.",
            )
        opened = os.fstat(self._root_fd)
        if not self._matches_expected_root(opened):
            raise self._root_changed(opened)
        if self._root_name is None:
            return
        try:
            current = os.stat(
                self._root_name,
                dir_fd=self._root_parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise self._root_changed(opened) from exc
        if not self._matches_expected_root(current):
            raise self._root_changed(current)

    def list_directory(
        self,
        path: str,
        *,
        max_entries: int | None = None,
    ) -> list[RepositoryEntry]:
        canonical = canonical_relative_path(path, allow_root=True)
        if max_entries is not None and max_entries < 0:
            raise ValueError("max_entries must be non-negative")
        descriptor = self._open_directory(canonical)
        try:
            with os.scandir(descriptor) as iterator:
                selected = iterator if max_entries is None else islice(iterator, max_entries)
                entries = [
                    _repository_entry(
                        canonical,
                        entry.name,
                        entry.stat(follow_symlinks=False),
                    )
                    for entry in selected
                ]
            entries.sort(key=lambda entry: entry.path)
        finally:
            os.close(descriptor)
        self.verify_root()
        return entries

    def walk(
        self,
        path: str = "",
        *,
        ignored_directories: frozenset[str] = DEFAULT_IGNORED_DIRECTORIES,
        max_entries: int | None = None,
        max_depth: int | None = None,
    ) -> list[RepositoryEntry]:
        canonical = canonical_relative_path(path, allow_root=True)
        if max_entries is not None and max_entries < 0:
            raise ValueError("max_entries must be non-negative")
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        results: list[RepositoryEntry] = []
        pending = [(canonical, 0)]
        while pending:
            directory, depth = pending.pop()
            if max_depth is not None and depth >= max_depth:
                continue
            remaining = (
                None if max_entries is None else max_entries - len(results)
            )
            children = self.list_directory(directory, max_entries=remaining)
            child_directories: list[str] = []
            for entry in children:
                if entry.kind == "directory" and PurePosixPath(entry.path).name in ignored_directories:
                    continue
                if max_entries is not None and len(results) >= max_entries:
                    self.verify_root()
                    return results
                results.append(entry)
                if entry.kind == "directory":
                    child_directories.append(entry.path)
            pending.extend((child, depth + 1) for child in reversed(child_directories))
        self.verify_root()
        return results

    def read_text(self, path: str, *, max_bytes: int) -> RepositoryText:
        canonical = canonical_relative_path(path)
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        parent_path, name = _split_parent(canonical)
        parent_fd = self._open_directory(parent_path)
        descriptor = -1
        try:
            before_entry = _lstat_at(parent_fd, name)
            if stat.S_ISLNK(before_entry.st_mode):
                raise _symlink_rejected(canonical)
            descriptor = _open_file_at(parent_fd, name, canonical)
            before_descriptor = os.fstat(descriptor)
            if not stat.S_ISREG(before_descriptor.st_mode):
                raise RepositoryAccessError(
                    "context_path_rejected",
                    f"Repository path is not a regular file: {canonical}",
                    details={"path": canonical},
                )
            _require_same_entry(canonical, before_entry, before_descriptor)
            observed = _read_bounded(descriptor, max_bytes + 1)
            after_descriptor = os.fstat(descriptor)
            try:
                after_entry = _lstat_at(parent_fd, name)
            except OSError as exc:
                raise _entry_changed(canonical) from exc
            _require_unchanged_entry(
                canonical,
                before_descriptor,
                after_descriptor,
                after_entry,
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)
        self.verify_root()
        returned, truncated = _bounded_text_bytes(
            canonical,
            observed,
            max_bytes=max_bytes,
        )
        text = _decode_text(canonical, returned, allow_incomplete_suffix=False)
        result = RepositoryText(
            path=canonical,
            text=text,
            content_hash=f"sha256:{hashlib.sha256(returned).hexdigest()}",
            size=before_descriptor.st_size,
            start_line=1,
            end_line=_end_line(text),
            truncated=truncated,
        )
        self.verify_root()
        return result

    def _matches_expected_root(self, value: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(value.st_mode)
            and value.st_dev == self._expected_identity["device"]
            and value.st_ino == self._expected_identity["inode"]
        )

    def _root_changed(self, value: os.stat_result) -> RepositoryAccessError:
        return RepositoryAccessError(
            "repository_root_changed",
            "Repository root identity changed.",
            details={
                "expected": dict(self._expected_identity),
                "actual": {"device": value.st_dev, "inode": value.st_ino},
            },
        )

    def _open_directory(self, path: str) -> int:
        self.verify_root()
        descriptor = os.dup(self._root_fd)
        try:
            if path:
                for component in PurePosixPath(path).parts:
                    child = _open_directory_at(descriptor, component, path)
                    os.close(descriptor)
                    descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise


def _open_anchored_root(root: Path) -> tuple[int, int, str | None]:
    absolute = root if root.is_absolute() else Path.cwd() / root
    components = absolute.parts
    descriptor = os.open(absolute.anchor, _directory_flags())
    if len(components) == 1:
        return descriptor, -1, None
    try:
        for component in components[1:-1]:
            child = _open_directory_at(descriptor, component, str(root))
            os.close(descriptor)
            descriptor = child
        root_name = components[-1]
        root_descriptor = _open_directory_at(descriptor, root_name, str(root))
        return root_descriptor, descriptor, root_name
    except BaseException:
        os.close(descriptor)
        raise


def _open_directory_at(parent_fd: int, name: str, path: str) -> int:
    try:
        before = _lstat_at(parent_fd, name)
    except FileNotFoundError:
        raise RepositoryAccessError(
            "context_path_rejected",
            f"Repository path does not exist: {path}",
            details={"path": path},
        ) from None
    if stat.S_ISLNK(before.st_mode):
        raise _symlink_rejected(path)
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _symlink_rejected(path) from exc
        raise
    try:
        _require_same_entry(path, before, os.fstat(descriptor))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_file_at(parent_fd: int, name: str, path: str) -> int:
    try:
        return os.open(name, _file_flags(), dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _symlink_rejected(path) from exc
        raise


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _lstat_at(directory_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _metadata(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _require_same_entry(
    path: str,
    listed: os.stat_result,
    opened: os.stat_result,
) -> None:
    if _metadata(listed) != _metadata(opened):
        raise _entry_changed(path)


def _require_unchanged_entry(
    path: str,
    before: os.stat_result,
    after: os.stat_result,
    current_entry: os.stat_result,
) -> None:
    expected = _metadata(before)
    if _metadata(after) != expected or _metadata(current_entry) != expected:
        raise _entry_changed(path)


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _bounded_text_bytes(path: str, observed: bytes, *, max_bytes: int) -> tuple[bytes, bool]:
    if b"\x00" in observed:
        raise RepositoryAccessError(
            "repository_binary_file",
            f"Repository file contains NUL bytes: {path}",
            details={"path": path},
        )
    truncated = len(observed) > max_bytes
    _decode_text(path, observed, allow_incomplete_suffix=truncated)
    if not truncated:
        return observed, False
    limited = observed[:max_bytes]
    newline = limited.rfind(b"\n")
    if newline >= 0:
        return limited[: newline + 1], True
    try:
        limited.decode("utf-8")
        return limited, True
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data" and exc.end == len(limited):
            return limited[: exc.start], True
        raise _non_text_file(path) from exc


def _decode_text(path: str, value: bytes, *, allow_incomplete_suffix: bool) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        if (
            allow_incomplete_suffix
            and exc.reason == "unexpected end of data"
            and exc.end == len(value)
        ):
            return value[: exc.start].decode("utf-8")
        raise _non_text_file(path) from exc


def _end_line(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _repository_entry(
    parent: str,
    name: str,
    metadata: os.stat_result,
) -> RepositoryEntry:
    if stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
    else:
        kind = "other"
    path = f"{parent}/{name}" if parent else name
    return RepositoryEntry(
        path=path,
        kind=kind,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
    )


def _split_parent(path: str) -> tuple[str, str]:
    value = PurePosixPath(path)
    parent = "" if value.parent == PurePosixPath(".") else value.parent.as_posix()
    return parent, value.name


def _path_rejected(path: str) -> RepositoryAccessError:
    return RepositoryAccessError(
        "context_path_rejected",
        f"Path must be a canonical repository-relative path: {path}",
        details={"path": path},
    )


def _symlink_rejected(path: str) -> RepositoryAccessError:
    return RepositoryAccessError(
        "context_path_rejected",
        f"Repository path crosses a symbolic link: {path}",
        details={"path": path},
    )


def _entry_changed(path: str) -> RepositoryAccessError:
    return RepositoryAccessError(
        "repository_entry_changed",
        f"Repository entry changed during reading: {path}",
        details={"path": path},
    )


def _non_text_file(path: str) -> RepositoryAccessError:
    return RepositoryAccessError(
        "repository_non_text_file",
        f"Repository file is not valid UTF-8 text: {path}",
        details={"path": path},
    )
