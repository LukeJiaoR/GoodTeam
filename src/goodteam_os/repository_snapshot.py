from __future__ import annotations

import hashlib
import json
import os
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
)


_GIT_OUTPUT_LIMIT = 32 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 5.0


class RepositorySnapshotError(ValueError):
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
    *,
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
        return None, None

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
    submodules = _require_git(repository.root, ["submodule", "status", "--recursive"])

    parsed_status = _parse_status(status)
    policy_exclusions: dict[str, str] = {}
    all_eligible_status = []
    for item in parsed_status:
        reason = _status_exclusion_reason(item)
        if reason is not None:
            _record_policy_exclusion(policy_exclusions, str(item["path"]), reason)
            continue
        all_eligible_status.append(item)
    status_omitted = max(0, len(all_eligible_status) - limits.max_files)
    eligible_status = all_eligible_status[: limits.max_files]
    all_index_records = _parse_index_records(index)
    eligible_index_records: list[bytes] = []
    for item in all_index_records:
        reason = _index_exclusion_reason(item)
        if reason is not None:
            _record_policy_exclusion(policy_exclusions, str(item["path"]), reason)
            continue
        eligible_index_records.append(bytes(item["record"]))
    index_omitted = max(0, len(eligible_index_records) - limits.max_files)
    index_records = eligible_index_records[: limits.max_files]
    worktree_paths = sorted(
        {
            str(item["path"])
            for item in eligible_status
            if item["kind"] == "tracked" and item["worktree_changed"]
        }
    )
    untracked_paths = sorted(
        {str(item["path"]) for item in eligible_status if item["kind"] == "untracked"}
    )
    worktree_records, worktree_scope = _hash_paths(
        repository,
        worktree_paths,
        limits=limits,
    )
    untracked_records, untracked_scope = _hash_paths(
        repository,
        untracked_paths,
        limits=limits,
    )
    exclusion_reasons = _merge_counts(
        _count_exclusion_reasons(policy_exclusions),
        worktree_scope["exclusion_reasons"],
        untracked_scope["exclusion_reasons"],
    )
    ceiling_omitted = status_omitted + index_omitted
    if ceiling_omitted:
        exclusion_reasons["max_files"] = (
            exclusion_reasons.get("max_files", 0) + ceiling_omitted
        )
    omitted = (
        int(worktree_scope["omitted_files"])
        + int(untracked_scope["omitted_files"])
        + ceiling_omitted
    )
    complete = bool(
        worktree_scope["complete"]
        and untracked_scope["complete"]
        and ceiling_omitted == 0
    )
    excluded_status_paths = set(worktree_scope["excluded_paths"]) | set(
        untracked_scope["excluded_paths"]
    )
    canonical_status = [
        item["record"]
        for item in eligible_status
        if str(item["path"]) not in excluded_status_paths
    ]
    source_control = {
        "kind": "git",
        "head_sha": head_sha,
        "branch": branch,
        "dirty": bool(canonical_status),
        "status_digest": _digest_json(canonical_status),
        "index_digest": _digest_bytes(index_records),
        "worktree_digest": _digest_json(worktree_records),
        "untracked_digest": _digest_json(untracked_records),
        "submodule_digest": _digest_bytes(submodules),
    }
    content_scope = {
        "complete": complete,
        "eligible_files": len(index_records) + len(worktree_records) + len(untracked_records),
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
    observed_entries = 0
    records: list[dict[str, object]] = []
    exclusion_reasons: dict[str, int] = {}
    excluded_files = 0
    omitted_files = 0
    total_bytes = 0
    complete = True
    while pending and observed_entries < limits.max_files:
        directory = pending.pop()
        remaining = limits.max_files - observed_entries
        entries = repository.list_directory(directory, max_entries=remaining + 1)
        if len(entries) > remaining:
            entries = entries[:remaining]
            complete = False
            omitted_files += 1
            _increment(exclusion_reasons, "max_files")
        for entry in entries:
            observed_entries += 1
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
            if entry.size > limits.max_file_bytes:
                excluded_files += 1
                _increment(exclusion_reasons, "oversized")
                continue
            if total_bytes + entry.size > limits.max_total_bytes:
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_total_bytes")
                continue
            record = _hash_entry(repository, entry)
            if record is None:
                excluded_files += 1
                _increment(exclusion_reasons, "binary")
                continue
            records.append(record)
            total_bytes += entry.size
    if pending:
        complete = False
        omitted_files += 1
        _increment(exclusion_reasons, "max_files")
    records.sort(key=lambda item: str(item["path"]))
    return {
        "complete": complete,
        "eligible_files": len(records),
        "excluded_files": excluded_files,
        "omitted_files": omitted_files,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "content_digest": _digest_json(records),
    }


def _hash_paths(
    repository: AnchoredRepository,
    paths: list[str],
    *,
    limits: SnapshotLimits,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: list[dict[str, object]] = []
    exclusions: dict[str, int] = {}
    excluded_paths: set[str] = set()
    omitted = 0
    total_bytes = 0
    for index, path in enumerate(paths):
        if index >= limits.max_files:
            omitted += 1
            _increment(exclusions, "max_files")
            continue
        try:
            value = repository.read_text(path, max_bytes=limits.max_file_bytes)
        except RepositoryAccessError as exc:
            if exc.code in {"repository_binary_file", "repository_non_text_file"}:
                _increment(exclusions, "binary")
                excluded_paths.add(path)
                continue
            if exc.code == "context_path_rejected":
                _increment(exclusions, "symlink")
                excluded_paths.add(path)
                continue
            raise
        if value.truncated:
            _increment(exclusions, "oversized")
            excluded_paths.add(path)
            continue
        if total_bytes + value.size > limits.max_total_bytes:
            omitted += 1
            _increment(exclusions, "max_total_bytes")
            continue
        records.append(
            {"path": path, "size": value.size, "content_hash": value.content_hash}
        )
        total_bytes += value.size
    return records, {
        "complete": omitted == 0,
        "omitted_files": omitted,
        "excluded_paths": excluded_paths,
        "exclusion_reasons": exclusions,
    }


def _hash_entry(
    repository: AnchoredRepository,
    entry: RepositoryEntry,
) -> dict[str, object] | None:
    try:
        value = repository.read_text(entry.path, max_bytes=entry.size)
    except RepositoryAccessError as exc:
        if exc.code in {"repository_binary_file", "repository_non_text_file"}:
            return None
        raise
    return {"path": entry.path, "size": entry.size, "content_hash": value.content_hash}


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
            path = os.fsdecode(parts[8])
            records.append(_status_record(raw, path, xy))
        elif raw.startswith(b"2 "):
            parts = raw.split(b" ", 9)
            if len(parts) != 10 or index >= len(fields):
                raise _status_invalid()
            xy = parts[1]
            path = os.fsdecode(parts[9])
            original = fields[index]
            index += 1
            records.append(_status_record(raw + b"\0" + original, path, xy))
        elif raw.startswith(b"? "):
            path = os.fsdecode(raw[2:])
            records.append(
                {
                    "kind": "untracked",
                    "path": path,
                    "worktree_changed": True,
                    "record": os.fsdecode(raw),
                }
            )
        elif raw.startswith(b"! "):
            records.append(
                {
                    "kind": "ignored",
                    "path": os.fsdecode(raw[2:]),
                    "worktree_changed": False,
                    "record": os.fsdecode(raw),
                }
            )
        else:
            raise _status_invalid()
    return records


def _status_record(raw: bytes, path: str, xy: bytes) -> dict[str, object]:
    return {
        "kind": "tracked",
        "path": path,
        "worktree_changed": len(xy) == 2 and xy[1:2] not in {b".", b" "},
        "record": os.fsdecode(raw),
    }


def _status_invalid() -> RepositorySnapshotError:
    return RepositorySnapshotError(
        "repository_snapshot_failed",
        "Read-only Git status output was invalid.",
    )


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
        records.append(
            {
                "mode": os.fsdecode(parts[0]),
                "path": os.fsdecode(raw_path),
                "record": record,
            }
        )
    records.sort(key=lambda item: bytes(item["record"]))
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
