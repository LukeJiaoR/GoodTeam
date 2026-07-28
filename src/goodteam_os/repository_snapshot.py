from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from goodteam_os.repository_access import (
    DEFAULT_IGNORED_DIRECTORIES,
    AnchoredRepository,
    RepositoryAccessError,
    RepositoryEntry,
    canonical_relative_path,
)


_GIT_OUTPUT_LIMIT = 32 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 5.0


class RepositorySnapshotError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 20_000
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 8 * 1024 * 1024

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RepositorySnapshotError(
                    "repository_snapshot_limits_invalid",
                    "Repository snapshot limits must be positive integers.",
                    details={"limit": name},
                )


@dataclass(frozen=True)
class RepositorySnapshot:
    schema: str
    snapshot_id: str
    project_key: str
    canonical_root: str
    root_identity: dict[str, int]
    source_control: dict[str, object]
    content_scope: dict[str, object]
    captured_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def capture_repository_snapshot(
    root: Path,
    project_key: str,
    expected_identity: dict[str, object] | None = None,
    limits: SnapshotLimits = SnapshotLimits(),
) -> RepositorySnapshot:
    limits.validate()
    root = _absolute_root(root)
    bound_identity = _expected_root_identity(root, expected_identity)
    try:
        first = _capture_once(
            root,
            project_key=project_key,
            expected_identity=bound_identity,
            limits=limits,
        )
        second = _capture_once(
            root,
            project_key=project_key,
            expected_identity=bound_identity,
            limits=limits,
        )
    except RepositoryAccessError as exc:
        code = exc.code
        if code == "context_path_rejected" and exc.details.get("path") == str(root):
            code = "repository_root_changed"
        raise RepositorySnapshotError(code, str(exc), details=exc.details) from exc
    if _canonical_json(first) != _canonical_json(second):
        raise RepositorySnapshotError(
            "repository_snapshot_changed",
            "Repository content changed while GoodTeam captured its snapshot.",
        )
    identity_payload = {"schema": "repository_snapshot.v1", **second}
    snapshot_id = compute_repository_snapshot_id(identity_payload)
    return RepositorySnapshot(
        schema="repository_snapshot.v1",
        snapshot_id=snapshot_id,
        project_key=project_key,
        canonical_root=str(root),
        root_identity=dict(second["root_identity"]),
        source_control=dict(second["source_control"]),
        content_scope=dict(second["content_scope"]),
        captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def verify_repository_snapshot(
    root: Path,
    snapshot: RepositorySnapshot,
    limits: SnapshotLimits = SnapshotLimits(),
) -> None:
    current = capture_repository_snapshot(
        root,
        project_key=snapshot.project_key,
        expected_identity=snapshot.root_identity,
        limits=limits,
    )
    if current.snapshot_id != snapshot.snapshot_id:
        raise RepositorySnapshotError(
            "repository_snapshot_changed",
            "Repository content no longer matches the captured snapshot.",
            details={
                "expected_snapshot_id": snapshot.snapshot_id,
                "actual_snapshot_id": current.snapshot_id,
            },
        )


def snapshot_identity_payload(snapshot: RepositorySnapshot) -> dict[str, object]:
    value = snapshot.as_dict()
    value.pop("snapshot_id", None)
    value.pop("captured_at", None)
    return value


def compute_repository_snapshot_id(
    snapshot: RepositorySnapshot | dict[str, object],
) -> str:
    value = snapshot.as_dict() if isinstance(snapshot, RepositorySnapshot) else dict(snapshot)
    value.pop("snapshot_id", None)
    value.pop("captured_at", None)
    return f"snap_{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _capture_once(
    root: Path,
    *,
    project_key: str,
    expected_identity: dict[str, object] | None,
    limits: SnapshotLimits,
) -> dict[str, object]:
    identity = _expected_root_identity(root, expected_identity)
    with AnchoredRepository(root, expected_identity=identity) as repository:
        source_control, content_scope = _capture_git(repository, limits)
        if source_control is None:
            source_control = {
                "kind": "none",
                "head_sha": None,
                "branch": None,
                "dirty": False,
                "index_digest": None,
                "worktree_digest": None,
                "untracked_digest": None,
                "submodule_digest": None,
            }
            content_scope = _capture_non_git(repository, limits)
        repository.verify_root()
    return {
        "project_key": project_key,
        "canonical_root": str(root),
        "root_identity": identity,
        "source_control": source_control,
        "content_scope": content_scope,
    }


def _capture_git(
    repository: AnchoredRepository,
    limits: SnapshotLimits,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    repository.verify_root()
    probe = _run_git(repository.root, ["rev-parse", "--is-inside-work-tree"])
    if probe.returncode != 0:
        if b"not a git repository" in probe.stderr.lower():
            return None, None
        raise _git_failed(probe)
    if probe.stdout.strip() != b"true":
        raise RepositorySnapshotError(
            "repository_snapshot_failed",
            "Repository root must be a Git worktree or a non-repository directory.",
            details={"operation": "rev-parse"},
        )

    top_level = _require_git(repository.root, ["rev-parse", "--show-toplevel"])
    if os.path.abspath(os.fsdecode(top_level.strip())) != str(repository.root):
        raise RepositorySnapshotError(
            "repository_snapshot_failed",
            "Repository root must match the Git worktree root.",
            details={"operation": "rev-parse"},
        )

    head = _run_git(repository.root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode not in {0, 128}:
        raise _git_failed(head)
    head_sha = os.fsdecode(head.stdout.strip()) if head.returncode == 0 else None
    branch_result = _run_git(
        repository.root,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
    )
    if branch_result.returncode not in {0, 1}:
        raise _git_failed(branch_result)
    branch = (
        os.fsdecode(branch_result.stdout.strip())
        if branch_result.returncode == 0
        else None
    )
    status = _require_git(
        repository.root,
        [
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ],
    )
    index = _require_git(repository.root, ["ls-files", "--stage", "-z"])
    submodules = _parse_submodule_records(
        _require_git(repository.root, ["submodule", "status", "--recursive"])
    )

    parsed_status = _parse_status(status)
    policy_exclusions: dict[str, str] = {}
    all_eligible_status = []
    for item in parsed_status:
        reason = _status_exclusion_reason(item)
        if reason is not None:
            _record_policy_exclusion(policy_exclusions, str(item["path"]), reason)
            continue
        all_eligible_status.append(item)
    eligible_status = list(all_eligible_status)
    all_index_records = _parse_index_records(index)
    index_paths: set[str] = set()
    for item in all_index_records:
        reason = _index_exclusion_reason(item)
        if reason is not None:
            _record_policy_exclusion(policy_exclusions, str(item["path"]), reason)
            continue
        index_paths.add(str(item["path"]))
    status_records_by_path = {
        str(item["path"]): str(item["record"]) for item in eligible_status
    }
    eligible_paths = sorted(
        index_paths | set(status_records_by_path)
    )
    included_paths = eligible_paths[: limits.max_files]
    omitted_paths = eligible_paths[limits.max_files :]
    selected_entries = [
        {
            "path": path,
            "bucket": _content_bucket_for_path(path, eligible_status),
            "status_record": status_records_by_path.get(path),
        }
        for path in included_paths
    ]
    worktree_records, untracked_records, content_scope_state = _hash_git_content_paths(
        repository,
        selected_entries,
        limits=limits,
    )
    omitted_paths = sorted(set(omitted_paths) | set(content_scope_state["omitted_paths"]))
    exclusion_reasons = _merge_counts(
        _count_exclusion_reasons(policy_exclusions),
        content_scope_state["exclusion_reasons"],
    )
    max_files_omitted = len(set(eligible_paths[limits.max_files :]))
    if max_files_omitted:
        exclusion_reasons["max_files"] = (
            max_files_omitted + exclusion_reasons.get("max_files", 0)
        )
    omitted = len(omitted_paths)
    complete = omitted == 0
    represented_paths = [path for path in included_paths if path not in omitted_paths]
    canonical_status = _status_identity_records(
        eligible_status,
        excluded_paths=content_scope_state["excluded_paths"],
    )
    canonical_index = _index_identity_records(
        [
            item
            for item in all_index_records
            if _index_exclusion_reason(item) is None
        ]
    )
    source_control = {
        "kind": "git",
        "head_sha": head_sha,
        "branch": branch,
        "dirty": any(
            str(item["path"]) not in content_scope_state["excluded_paths"]
            for item in eligible_status
        ),
        "status_digest": _digest_json(canonical_status),
        "index_digest": _digest_json(canonical_index),
        "worktree_digest": _digest_json(worktree_records),
        "untracked_digest": _digest_json(untracked_records),
        "submodule_digest": _digest_json(submodules),
    }
    content_scope = {
        "complete": complete,
        "eligible_files": len(represented_paths),
        "excluded_files": sum(exclusion_reasons.values()) - omitted,
        "omitted_files": omitted,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "content_digest": _digest_json(
            {
                "head": head_sha,
                "index": source_control["index_digest"],
                "worktree": worktree_records,
                "untracked": untracked_records,
                "submodules": source_control["submodule_digest"],
            }
        ),
    }
    repository.verify_root()
    return source_control, content_scope


def _capture_non_git(
    repository: AnchoredRepository,
    limits: SnapshotLimits,
) -> dict[str, object]:
    pending = [""]
    eligible_seen = 0
    records: list[dict[str, object]] = []
    exclusion_reasons: dict[str, int] = {}
    excluded_files = 0
    omitted_files = 0
    total_bytes = 0
    complete = True
    while pending:
        directory = pending.pop()
        entries = repository.list_directory(directory)
        for entry in entries:
            if entry.kind == "directory":
                if PurePosixPath(entry.path).name in DEFAULT_IGNORED_DIRECTORIES:
                    excluded_files += 1
                    _increment(exclusion_reasons, "ignored_directory")
                else:
                    pending.append(entry.path)
                continue
            if entry.kind == "symlink":
                excluded_files += 1
                _increment(exclusion_reasons, "symlink")
                continue
            if entry.kind != "file":
                excluded_files += 1
                _increment(exclusion_reasons, "non_regular")
                continue
            if eligible_seen >= limits.max_files:
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_files")
                continue
            if entry.size > limits.max_file_bytes:
                eligible_seen += 1
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_file_bytes")
                continue
            if total_bytes + entry.size > limits.max_total_bytes:
                eligible_seen += 1
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_total_bytes")
                continue
            record = _hash_entry(repository, entry)
            total_bytes += entry.size
            if record is None:
                excluded_files += 1
                _increment(exclusion_reasons, "binary")
                continue
            eligible_seen += 1
            records.append(record)
    records.sort(key=lambda item: str(item["path"]))
    return {
        "complete": complete,
        "eligible_files": len(records),
        "excluded_files": excluded_files,
        "omitted_files": omitted_files,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "content_digest": _digest_json(records),
    }


def _content_bucket_for_path(
    path: str,
    status_items: list[dict[str, object]],
) -> str | None:
    for item in status_items:
        if str(item["path"]) != path:
            continue
        if item["kind"] == "tracked" and item["worktree_changed"]:
            return "worktree"
        if item["kind"] == "untracked":
            return "untracked"
    return None


def _hash_git_content_paths(
    repository: AnchoredRepository,
    entries: list[dict[str, object]],
    *,
    limits: SnapshotLimits,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    worktree_records: list[dict[str, object]] = []
    untracked_records: list[dict[str, object]] = []
    exclusions: dict[str, int] = {}
    excluded_paths: set[str] = set()
    omitted_paths: set[str] = set()
    total_bytes = 0
    for entry in entries:
        path = str(entry["path"])
        bucket = entry["bucket"]
        if bucket is None:
            continue
        metadata = _anchored_file_metadata(repository, path)
        if metadata is None:
            continue
        size = int(metadata["size"])
        if size > limits.max_file_bytes:
            omitted_paths.add(path)
            _increment(exclusions, "max_file_bytes")
            continue
        if total_bytes + size > limits.max_total_bytes:
            omitted_paths.add(path)
            _increment(exclusions, "max_total_bytes")
            continue
        try:
            value = repository.read_text(path, max_bytes=size)
        except RepositoryAccessError as exc:
            total_bytes += size
            if exc.code in {"repository_binary_file", "repository_non_text_file"}:
                _increment(exclusions, "binary")
                excluded_paths.add(path)
                continue
            if exc.code == "context_path_rejected":
                _increment(exclusions, "symlink")
                excluded_paths.add(path)
                continue
            raise
        total_bytes += size
        if value.truncated:
            raise RepositoryAccessError(
                "repository_entry_changed",
                f"Repository entry changed while it was being read: {path}",
                details={"path": path},
            )
        record = {
            "path": path,
            "size": value.size,
            "content_hash": value.content_hash,
            "metadata": metadata,
        }
        if bucket == "worktree":
            worktree_records.append(record)
        else:
            untracked_records.append(record)
    return worktree_records, untracked_records, {
        "complete": not omitted_paths,
        "omitted_paths": omitted_paths,
        "excluded_paths": excluded_paths,
        "exclusion_reasons": exclusions,
    }


def _anchored_file_metadata(
    repository: AnchoredRepository,
    path: str,
) -> dict[str, int] | None:
    candidate = PurePosixPath(path)
    parent = "" if str(candidate.parent) == "." else str(candidate.parent)
    descriptor = repository._open_directory(parent)
    try:
        try:
            value = os.stat(candidate.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(descriptor)
    if stat.S_ISLNK(value.st_mode):
        raise RepositoryAccessError(
            "context_path_rejected",
            f"Repository path is a symbolic link: {path}",
            details={"path": path},
        )
    if not stat.S_ISREG(value.st_mode):
        raise RepositoryAccessError(
            "context_path_rejected",
            f"Repository path is not a regular file: {path}",
            details={"path": path},
        )
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
    }


def _hash_entry(
    repository: AnchoredRepository,
    entry: RepositoryEntry,
) -> dict[str, object] | None:
    metadata = _anchored_file_metadata(repository, entry.path)
    if (
        metadata is None
        or metadata["size"] != entry.size
        or metadata["mtime_ns"] != entry.mtime_ns
    ):
        raise RepositoryAccessError(
            "repository_entry_changed",
            f"Repository entry changed while it was being read: {entry.path}",
            details={"path": entry.path},
        )
    try:
        value = repository.read_text(entry.path, max_bytes=entry.size)
    except RepositoryAccessError as exc:
        if exc.code in {"repository_binary_file", "repository_non_text_file"}:
            return None
        raise
    if value.truncated or value.size != entry.size:
        raise RepositoryAccessError(
            "repository_entry_changed",
            f"Repository entry changed while it was being read: {entry.path}",
            details={"path": entry.path},
        )
    return {
        "path": entry.path,
        "size": entry.size,
        "content_hash": value.content_hash,
        "metadata": metadata,
    }


@dataclass(frozen=True)
class _ProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


def _run_git(root: Path, arguments: list[str]) -> _ProcessResult:
    command = ["git", "--no-pager", *arguments]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
        )
        deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise RepositorySnapshotError(
                    "repository_snapshot_failed",
                    "Read-only Git inspection timed out.",
                )
            if (
                os.fstat(stdout.fileno()).st_size > _GIT_OUTPUT_LIMIT
                or os.fstat(stderr.fileno()).st_size > _GIT_OUTPUT_LIMIT
            ):
                process.kill()
                process.wait()
                raise RepositorySnapshotError(
                    "repository_snapshot_failed",
                    "Read-only Git inspection exceeded its output limit.",
                )
            time.sleep(0.01)
        if (
            os.fstat(stdout.fileno()).st_size > _GIT_OUTPUT_LIMIT
            or os.fstat(stderr.fileno()).st_size > _GIT_OUTPUT_LIMIT
        ):
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git inspection exceeded its output limit.",
            )
        stdout.seek(0)
        stderr.seek(0)
        return _ProcessResult(
            args=tuple(command),
            returncode=process.returncode,
            stdout=stdout.read(_GIT_OUTPUT_LIMIT + 1),
            stderr=stderr.read(_GIT_OUTPUT_LIMIT + 1),
        )


def _require_git(root: Path, arguments: list[str]) -> bytes:
    result = _run_git(root, arguments)
    if result.returncode != 0:
        raise _git_failed(result)
    return result.stdout


def _git_failed(result: _ProcessResult) -> RepositorySnapshotError:
    return RepositorySnapshotError(
        "repository_snapshot_failed",
        "Read-only Git inspection failed.",
        details={"operation": result.args[2] if len(result.args) > 2 else "unknown"},
    )


def _parse_status(value: bytes) -> list[dict[str, object]]:
    fields = value.split(b"\0")
    records: list[dict[str, object]] = []
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw:
            continue
        if raw.startswith(b"1 "):
            parts = raw.split(b" ", 8)
            if len(parts) != 9:
                raise _status_invalid()
            xy = parts[1]
            path = _canonical_git_path(parts[8])
            records.append(
                _status_record(
                    raw,
                    path,
                    xy,
                    {
                        "entry_kind": "ordinary",
                        "xy": os.fsdecode(parts[1]),
                        "submodule": os.fsdecode(parts[2]),
                        "head_mode": os.fsdecode(parts[3]),
                        "index_mode": os.fsdecode(parts[4]),
                        "worktree_mode": os.fsdecode(parts[5]),
                        "head_oid": os.fsdecode(parts[6]),
                        "index_oid": os.fsdecode(parts[7]),
                        "path": path,
                    },
                )
            )
        elif raw.startswith(b"2 "):
            parts = raw.split(b" ", 9)
            if len(parts) != 10 or index >= len(fields):
                raise _status_invalid()
            xy = parts[1]
            path = _canonical_git_path(parts[9])
            original = fields[index]
            index += 1
            original_path = _canonical_git_path(original)
            item = _status_record(
                raw + b"\0" + original,
                path,
                xy,
                {
                    "entry_kind": "renamed",
                    "xy": os.fsdecode(parts[1]),
                    "submodule": os.fsdecode(parts[2]),
                    "head_mode": os.fsdecode(parts[3]),
                    "index_mode": os.fsdecode(parts[4]),
                    "worktree_mode": os.fsdecode(parts[5]),
                    "head_oid": os.fsdecode(parts[6]),
                    "index_oid": os.fsdecode(parts[7]),
                    "score": os.fsdecode(parts[8]),
                    "path": path,
                    "original_path": original_path,
                },
            )
            item["original_path"] = original_path
            records.append(item)
        elif raw.startswith(b"u "):
            parts = raw.split(b" ", 10)
            if len(parts) != 11:
                raise _status_invalid()
            xy = parts[1]
            path = _canonical_git_path(parts[10])
            records.append(
                _status_record(
                    raw,
                    path,
                    xy,
                    {
                        "entry_kind": "unmerged",
                        "xy": os.fsdecode(parts[1]),
                        "submodule": os.fsdecode(parts[2]),
                        "stage1_mode": os.fsdecode(parts[3]),
                        "stage2_mode": os.fsdecode(parts[4]),
                        "stage3_mode": os.fsdecode(parts[5]),
                        "worktree_mode": os.fsdecode(parts[6]),
                        "stage1_oid": os.fsdecode(parts[7]),
                        "stage2_oid": os.fsdecode(parts[8]),
                        "stage3_oid": os.fsdecode(parts[9]),
                        "path": path,
                    },
                )
            )
        elif raw.startswith(b"? "):
            path = _canonical_git_path(raw[2:])
            records.append(
                {
                    "kind": "untracked",
                    "path": path,
                    "worktree_changed": True,
                    "record": os.fsdecode(raw),
                    "identity": {"entry_kind": "untracked", "path": path},
                }
            )
        elif raw.startswith(b"! "):
            path = _canonical_git_path(raw[2:])
            records.append(
                {
                    "kind": "ignored",
                    "path": path,
                    "worktree_changed": False,
                    "record": os.fsdecode(raw),
                    "identity": {"entry_kind": "ignored", "path": path},
                }
            )
        else:
            raise _status_invalid()
    return records


def _status_record(
    raw: bytes,
    path: str,
    xy: bytes,
    identity: dict[str, object],
) -> dict[str, object]:
    return {
        "kind": "tracked",
        "path": path,
        "worktree_changed": len(xy) == 2 and xy[1:2] not in {b".", b" "},
        "record": os.fsdecode(raw),
        "identity": identity,
    }


def _status_invalid() -> RepositorySnapshotError:
    return RepositorySnapshotError(
        "repository_snapshot_failed",
        "Read-only Git status output was invalid.",
    )


def _canonical_git_path(value: bytes) -> str:
    decoded = os.fsdecode(value)
    candidate = decoded[:-1] if decoded.endswith("/") else decoded
    try:
        return canonical_relative_path(candidate)
    except RepositoryAccessError as exc:
        raise RepositorySnapshotError(
            "repository_snapshot_failed",
            "Read-only Git output contained an invalid repository path.",
            details={"path": decoded},
        ) from exc


def _parse_submodule_records(value: bytes) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for record in value.splitlines():
        if not record:
            continue
        if (
            len(record) < 42
            or record[:1] not in {b" ", b"+", b"-", b"U"}
            or record[41:42] != b" "
        ):
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git submodule output was invalid.",
            )
        raw_path = record[42:]
        description: str | None = None
        if raw_path.endswith(b")") and b" (" in raw_path:
            raw_path, raw_description = raw_path.rsplit(b" (", 1)
            description = os.fsdecode(b"(" + raw_description)
        records.append(
            {
                "status": os.fsdecode(record[:1]),
                "commit": os.fsdecode(record[1:41]),
                "path": _canonical_git_path(raw_path),
                "description": description,
            }
        )
    records.sort(key=_canonical_json)
    return records


def _parse_index_records(value: bytes) -> list[dict[str, object]]:
    records = []
    for record in value.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git index output was invalid.",
            )
        parts = metadata.split(b" ")
        if len(parts) != 3:
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git index output was invalid.",
            )
        try:
            stage = int(os.fsdecode(parts[2]))
        except ValueError as exc:
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git index output was invalid.",
            ) from exc
        records.append(
            {
                "mode": os.fsdecode(parts[0]),
                "oid": os.fsdecode(parts[1]),
                "stage": stage,
                "path": _canonical_git_path(raw_path),
                "record": record,
                "identity": {
                    "mode": os.fsdecode(parts[0]),
                    "oid": os.fsdecode(parts[1]),
                    "stage": stage,
                    "path": _canonical_git_path(raw_path),
                },
            }
        )
    records.sort(key=lambda item: _canonical_json(item["identity"]))
    return records


def _status_identity_records(
    items: list[dict[str, object]],
    *,
    excluded_paths: set[str] | None = None,
) -> list[dict[str, object]]:
    excluded_paths = excluded_paths or set()
    records = [
        dict(item["identity"])
        for item in items
        if str(item["path"]) not in excluded_paths
    ]
    records.sort(key=_canonical_json)
    return records


def _index_identity_records(items: list[dict[str, object]]) -> list[dict[str, object]]:
    records = [dict(item["identity"]) for item in items]
    records.sort(key=_canonical_json)
    return records


def _ignored_path(path: object) -> bool:
    return any(part in DEFAULT_IGNORED_DIRECTORIES for part in PurePosixPath(str(path)).parts)


def _status_exclusion_reason(
    item: dict[str, object],
) -> str | None:
    path = str(item["path"])
    ignored_reason = _ignored_path_reason(path)
    if ignored_reason is not None:
        return ignored_reason
    if item["kind"] == "ignored":
        return "git_ignored"
    return None


def _index_exclusion_reason(
    item: dict[str, object],
) -> str | None:
    path = str(item["path"])
    ignored_reason = _ignored_path_reason(path)
    if ignored_reason is not None:
        return ignored_reason
    if item["mode"] == "120000":
        return "symlink"
    return None


def _ignored_path_reason(path: str) -> str | None:
    return "ignored_directory" if _ignored_path(path.rstrip("/")) else None


def _record_policy_exclusion(
    exclusions: dict[str, str],
    path: str,
    reason: str,
) -> None:
    normalized = path.rstrip("/") or path
    exclusions.setdefault(normalized, reason)


def _count_exclusion_reasons(exclusions: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in exclusions.values():
        _increment(counts, reason)
    return counts


def _expected_root_identity(
    root: Path,
    expected: dict[str, object] | None,
) -> dict[str, int]:
    if expected is not None:
        try:
            return {"device": int(expected["device"]), "inode": int(expected["inode"])}
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositorySnapshotError(
                "repository_root_changed",
                "Expected repository root identity is invalid.",
            ) from exc
    try:
        value = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise RepositorySnapshotError(
            "repository_root_changed",
            "Repository root is unavailable.",
        ) from exc
    return {"device": value.st_dev, "inode": value.st_ino}


def _absolute_root(root: Path) -> Path:
    value = Path(root).expanduser()
    return value if value.is_absolute() else Path.cwd() / value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _digest_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _digest_bytes(value: bytes | list[bytes]) -> str:
    digest = hashlib.sha256()
    if isinstance(value, bytes):
        digest.update(value)
    else:
        for item in value:
            digest.update(len(item).to_bytes(8, "big"))
            digest.update(item)
    return f"sha256:{digest.hexdigest()}"


def _increment(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _merge_counts(*values: object) -> dict[str, int]:
    merged: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            if isinstance(key, str) and isinstance(count, int):
                merged[key] = merged.get(key, 0) + count
    return merged
