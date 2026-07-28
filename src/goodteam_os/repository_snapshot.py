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
    RepositoryText,
    canonical_relative_path,
)


_GIT_OUTPUT_LIMIT = 32 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 5.0
_PATH_MANIFEST_SCHEMA = "repository_snapshot.v1.path_manifest"
_PATH_MANIFEST_KEYS = frozenset(
    {"complete", "entries", "excluded", "omitted", "omitted_paths"}
)
_COMPILED_PATH_MANIFEST_KEYS = frozenset({"entries", "excluded", "omitted"})
_EXCLUDED_REASON_KINDS = {
    "binary": frozenset({"file"}),
    "git_ignored": frozenset({"file"}),
    "ignored_directory": frozenset({"directory", "file"}),
    "non_regular": frozenset({"other"}),
    "symlink": frozenset({"file", "symlink"}),
}
_OMITTED_REASONS = frozenset({"max_file_bytes", "max_files", "max_total_bytes"})


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
    path_manifest: dict[str, object]
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
        path_manifest=dict(second["path_manifest"]),
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


def compile_snapshot_path_manifest(
    snapshot: RepositorySnapshot,
) -> dict[str, dict[str, dict[str, object]]]:
    manifest = snapshot.path_manifest
    if not isinstance(manifest, dict) or set(manifest) != _PATH_MANIFEST_KEYS:
        raise _path_manifest_invalid()
    if type(manifest["complete"]) is not bool:
        raise _path_manifest_invalid()
    collections: dict[str, list[object]] = {}
    for state in _COMPILED_PATH_MANIFEST_KEYS:
        value = manifest[state]
        if not isinstance(value, list):
            raise _path_manifest_invalid()
        collections[state] = value
    omitted_paths = manifest["omitted_paths"]
    if not isinstance(omitted_paths, list):
        raise _path_manifest_invalid()

    compiled: dict[str, dict[str, dict[str, object]]] = {
        "entries": {},
        "excluded": {},
        "omitted": {},
    }
    seen_paths: set[str] = set()
    for state in ("entries", "excluded", "omitted"):
        for value in collections[state]:
            record = _validate_path_manifest_record(state, value)
            path = str(record["path"])
            if path in seen_paths:
                raise _path_manifest_invalid(path)
            seen_paths.add(path)
            compiled[state][path] = record

    validated_omitted_paths: list[str] = []
    for value in omitted_paths:
        path = _validate_manifest_path(value)
        if path in validated_omitted_paths:
            raise _path_manifest_invalid(path)
        validated_omitted_paths.append(path)
    expected_omitted_paths = list(compiled["omitted"])
    if validated_omitted_paths != expected_omitted_paths:
        raise _path_manifest_invalid()
    if bool(manifest["complete"]) != (not expected_omitted_paths):
        raise _path_manifest_invalid()
    return compiled


def _validate_path_manifest_record(
    state: str,
    value: object,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _path_manifest_invalid()
    path = _validate_manifest_path(value.get("path"))
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise _path_manifest_invalid(path)

    if state == "entries":
        if kind == "directory":
            if set(value) != {"path", "kind"}:
                raise _path_manifest_invalid(path)
        elif kind == "file":
            _validate_represented_file_record(value, path)
        else:
            raise _path_manifest_invalid(path)
    elif state == "excluded":
        reason = value.get("reason")
        if (
            set(value) != {"path", "kind", "reason"}
            or not isinstance(reason, str)
            or kind not in _EXCLUDED_REASON_KINDS.get(reason, frozenset())
        ):
            raise _path_manifest_invalid(path)
    elif state == "omitted":
        if (
            set(value) != {"path", "kind", "reason"}
            or kind != "file"
            or value.get("reason") not in _OMITTED_REASONS
        ):
            raise _path_manifest_invalid(path)
    else:
        raise _path_manifest_invalid()
    return dict(value)


def _validate_represented_file_record(value: dict[object, object], path: str) -> None:
    identity_kind = value.get("identity_kind")
    if identity_kind in {"worktree", "untracked"}:
        if set(value) != {
            "path",
            "kind",
            "identity_kind",
            "size",
            "content_hash",
            "metadata",
        }:
            raise _path_manifest_invalid(path)
        size = value.get("size")
        content_hash = value.get("content_hash")
        metadata = value.get("metadata")
        if (
            not _is_nonnegative_int(size)
            or not _is_sha256_digest(content_hash)
            or not isinstance(metadata, dict)
            or set(metadata) != {"mode", "mtime_ns", "size"}
            or not _is_nonnegative_int(metadata.get("mode"))
            or not _is_int(metadata.get("mtime_ns"))
            or not _is_nonnegative_int(metadata.get("size"))
            or metadata.get("size") != size
        ):
            raise _path_manifest_invalid(path)
        return
    if identity_kind == "git_index":
        git_identity = value.get("git")
        if (
            set(value) != {"path", "kind", "identity_kind", "git"}
            or not isinstance(git_identity, dict)
            or set(git_identity) != {"mode", "object_id", "stage"}
            or git_identity.get("mode") not in {"100644", "100755", "160000"}
            or not _is_git_object_id(git_identity.get("object_id"))
            or git_identity.get("stage") != 0
        ):
            raise _path_manifest_invalid(path)
        return
    raise _path_manifest_invalid(path)


def _validate_manifest_path(value: object) -> str:
    if not isinstance(value, str):
        raise _path_manifest_invalid()
    try:
        return canonical_relative_path(value)
    except RepositoryAccessError as exc:
        raise _path_manifest_invalid() from exc


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonnegative_int(value: object) -> bool:
    return _is_int(value) and int(value) >= 0


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and _is_lower_hex(value[7:])
    )


def _is_git_object_id(value: object) -> bool:
    return isinstance(value, str) and len(value) in {40, 64} and _is_lower_hex(value)


def _is_lower_hex(value: str) -> bool:
    return bool(value) and all(character in "0123456789abcdef" for character in value)


def _path_manifest_invalid(path: str | None = None) -> RepositorySnapshotError:
    details = {"path": path} if path is not None else {"schema": _PATH_MANIFEST_SCHEMA}
    return RepositorySnapshotError(
        "repository_snapshot_invalid",
        "Repository snapshot path manifest is invalid.",
        details=details,
    )


def validate_snapshot_path_entry(
    snapshot_manifest: dict[str, dict[str, dict[str, object]]],
    entry: RepositoryEntry,
) -> dict[str, object] | None:
    state, record = _compiled_manifest_record(snapshot_manifest, entry.path)
    if state == "entries":
        represented = record
        result = dict(represented)
        result["snapshot_state"] = "represented"
        if str(represented.get("kind")) != entry.kind:
            raise RepositorySnapshotError(
                "repository_snapshot_changed",
                "Repository content no longer matches the captured snapshot.",
                details={"path": entry.path},
            )
        metadata = represented.get("metadata")
        if isinstance(metadata, dict):
            expected_size = metadata.get("size")
            expected_mtime_ns = metadata.get("mtime_ns")
            if (
                isinstance(expected_size, int)
                and entry.kind == "file"
                and expected_size != entry.size
            ) or (
                isinstance(expected_mtime_ns, int)
                and expected_mtime_ns != entry.mtime_ns
            ):
                raise RepositorySnapshotError(
                    "repository_snapshot_changed",
                    "Repository content no longer matches the captured snapshot.",
                    details={"path": entry.path},
                )
        return result
    if state == "excluded":
        result = dict(record)
        result["snapshot_state"] = "excluded"
        return result
    if state == "omitted":
        result = dict(record)
        result["snapshot_state"] = "omitted"
        return result
    raise RepositorySnapshotError(
        "repository_snapshot_changed",
        "Repository content no longer matches the captured snapshot.",
        details={"path": entry.path},
    )


def _compiled_manifest_record(
    snapshot_manifest: object,
    path: str,
) -> tuple[str | None, dict[str, object]]:
    if (
        not isinstance(snapshot_manifest, dict)
        or set(snapshot_manifest) != _COMPILED_PATH_MANIFEST_KEYS
    ):
        raise _path_manifest_invalid()
    matches: list[tuple[str, dict[str, object]]] = []
    for state in ("entries", "excluded", "omitted"):
        bucket = snapshot_manifest[state]
        if not isinstance(bucket, dict):
            raise _path_manifest_invalid()
        if path not in bucket:
            continue
        record = _validate_path_manifest_record(state, bucket[path])
        if record["path"] != path:
            raise _path_manifest_invalid(path)
        matches.append((state, record))
    if len(matches) > 1:
        raise _path_manifest_invalid(path)
    if not matches:
        return None, {}
    return matches[0]


def validate_snapshot_text_content(
    repository: AnchoredRepository,
    snapshot_manifest: dict[str, dict[str, dict[str, object]]],
    source_control: dict[str, object],
    entry: RepositoryEntry,
    *,
    max_bytes: int,
) -> RepositoryText | None:
    state = validate_snapshot_path_entry(snapshot_manifest, entry)
    if state is None or state.get("snapshot_state") != "represented":
        return None
    if entry.kind != "file":
        raise RepositorySnapshotError(
            "repository_snapshot_changed",
            "Repository content no longer matches the captured snapshot.",
            details={"path": entry.path},
        )
    try:
        result = repository.read_text(entry.path, max_bytes=max_bytes)
    except RepositoryAccessError as exc:
        if exc.code in {"repository_binary_file", "repository_non_text_file"}:
            if state.get("identity_kind") in {"worktree", "untracked"}:
                raise RepositorySnapshotError(
                    "repository_snapshot_changed",
                    "Repository content no longer matches the captured snapshot.",
                    details={"path": entry.path},
                ) from exc
            return None
        raise
    if result.truncated:
        raise RepositorySnapshotError(
            "repository_snapshot_changed",
            "Repository content no longer matches the captured snapshot.",
            details={"path": entry.path},
        )
    identity_kind = str(state.get("identity_kind"))
    if identity_kind in {"worktree", "untracked"}:
        if result.content_hash != state.get("content_hash"):
            raise RepositorySnapshotError(
                "repository_snapshot_changed",
                "Repository content no longer matches the captured snapshot.",
                details={"path": entry.path},
            )
    elif identity_kind == "git_index":
        git_identity = state.get("git")
        if not isinstance(git_identity, dict):
            raise RepositorySnapshotError(
                "repository_snapshot_changed",
                "Repository content no longer matches the captured snapshot.",
                details={"path": entry.path},
            )
        expected_oid = git_identity.get("object_id")
        object_format = str(source_control.get("object_format") or "sha1")
        if _git_object_oid(result.text.encode("utf-8"), object_format) != expected_oid:
            raise RepositorySnapshotError(
                "repository_snapshot_changed",
                "Repository content no longer matches the captured snapshot.",
                details={"path": entry.path},
            )
    else:
        raise RepositorySnapshotError(
            "repository_snapshot_changed",
            "Repository content no longer matches the captured snapshot.",
            details={"path": entry.path},
        )
    return result


def _capture_once(
    root: Path,
    *,
    project_key: str,
    expected_identity: dict[str, object] | None,
    limits: SnapshotLimits,
) -> dict[str, object]:
    identity = _expected_root_identity(root, expected_identity)
    with AnchoredRepository(root, expected_identity=identity) as repository:
        source_control, content_scope, path_manifest = _capture_git(repository, limits)
        if source_control is None:
            source_control = {
                "kind": "none",
                "head_sha": None,
                "branch": None,
                "dirty": False,
                "object_format": None,
                "index_digest": None,
                "worktree_digest": None,
                "untracked_digest": None,
                "submodule_digest": None,
            }
            content_scope, path_manifest = _capture_non_git(repository, limits)
        repository.verify_root()
    return {
        "project_key": project_key,
        "canonical_root": str(root),
        "root_identity": identity,
        "source_control": source_control,
        "content_scope": content_scope,
        "path_manifest": path_manifest,
    }


def _capture_git(
    repository: AnchoredRepository,
    limits: SnapshotLimits,
) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, object] | None]:
    repository.verify_root()
    probe = _run_git(repository.root, ["rev-parse", "--is-inside-work-tree"])
    if probe.returncode != 0:
        if b"not a git repository" in probe.stderr.lower():
            return None, None, None
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
    object_format = os.fsdecode(
        _require_git(repository.root, ["rev-parse", "--show-object-format"]).strip()
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
        "object_format": object_format,
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
    path_manifest = _git_path_manifest(
        included_paths=included_paths,
        omitted_paths=omitted_paths,
        index_records=all_index_records,
        policy_exclusions=policy_exclusions,
        worktree_records=worktree_records,
        untracked_records=untracked_records,
        content_scope_state=content_scope_state,
    )
    repository.verify_root()
    return source_control, content_scope, path_manifest


def _capture_non_git(
    repository: AnchoredRepository,
    limits: SnapshotLimits,
) -> tuple[dict[str, object], dict[str, object]]:
    pending = [""]
    eligible_seen = 0
    records: list[dict[str, object]] = []
    manifest_entries: list[dict[str, object]] = []
    excluded_entries: list[dict[str, object]] = []
    omitted_entries: list[dict[str, object]] = []
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
                    excluded_entries.append(
                        {"path": entry.path, "kind": "directory", "reason": "ignored_directory"}
                    )
                else:
                    manifest_entries.append({"path": entry.path, "kind": "directory"})
                    pending.append(entry.path)
                continue
            if entry.kind == "symlink":
                excluded_files += 1
                _increment(exclusion_reasons, "symlink")
                excluded_entries.append(
                    {"path": entry.path, "kind": "symlink", "reason": "symlink"}
                )
                continue
            if entry.kind != "file":
                excluded_files += 1
                _increment(exclusion_reasons, "non_regular")
                excluded_entries.append(
                    {"path": entry.path, "kind": entry.kind, "reason": "non_regular"}
                )
                continue
            if eligible_seen >= limits.max_files:
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_files")
                omitted_entries.append(
                    {"path": entry.path, "kind": "file", "reason": "max_files"}
                )
                continue
            if entry.size > limits.max_file_bytes:
                eligible_seen += 1
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_file_bytes")
                omitted_entries.append(
                    {"path": entry.path, "kind": "file", "reason": "max_file_bytes"}
                )
                continue
            if total_bytes + entry.size > limits.max_total_bytes:
                eligible_seen += 1
                complete = False
                omitted_files += 1
                _increment(exclusion_reasons, "max_total_bytes")
                omitted_entries.append(
                    {"path": entry.path, "kind": "file", "reason": "max_total_bytes"}
                )
                continue
            record = _hash_entry(repository, entry)
            total_bytes += entry.size
            if record is None:
                excluded_files += 1
                _increment(exclusion_reasons, "binary")
                excluded_entries.append(
                    {"path": entry.path, "kind": "file", "reason": "binary"}
                )
                continue
            eligible_seen += 1
            records.append(record)
            manifest_entries.append(
                {
                    "path": entry.path,
                    "kind": "file",
                    "identity_kind": "worktree",
                    "size": int(record["size"]),
                    "content_hash": str(record["content_hash"]),
                    "metadata": _manifest_file_metadata(record["metadata"]),
                }
            )
    records.sort(key=lambda item: str(item["path"]))
    manifest_entries.sort(key=_canonical_json)
    excluded_entries.sort(key=_canonical_json)
    omitted_entries.sort(key=_canonical_json)
    return {
        "complete": complete,
        "eligible_files": len(records),
        "excluded_files": excluded_files,
        "omitted_files": omitted_files,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "content_digest": _digest_json(records),
    }, {
        "complete": complete,
        "entries": manifest_entries,
        "excluded": excluded_entries,
        "omitted": omitted_entries,
        "omitted_paths": [str(item["path"]) for item in omitted_entries],
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
    omitted_path_reasons: dict[str, str] = {}
    excluded_path_reasons: dict[str, str] = {}
    total_bytes = 0
    for entry in entries:
        path = str(entry["path"])
        bucket = entry["bucket"]
        if bucket is None:
            continue
        metadata = _anchored_file_metadata(repository, path)
        if metadata is None:
            raise RepositoryAccessError(
                "repository_entry_changed",
                f"Repository entry changed while it was being read: {path}",
                details={"path": path},
            )
        size = int(metadata["size"])
        if size > limits.max_file_bytes:
            omitted_paths.add(path)
            omitted_path_reasons[path] = "max_file_bytes"
            _increment(exclusions, "max_file_bytes")
            continue
        if total_bytes + size > limits.max_total_bytes:
            omitted_paths.add(path)
            omitted_path_reasons[path] = "max_total_bytes"
            _increment(exclusions, "max_total_bytes")
            continue
        try:
            value = repository.read_text(path, max_bytes=size)
        except RepositoryAccessError as exc:
            total_bytes += size
            if exc.code in {"repository_binary_file", "repository_non_text_file"}:
                _increment(exclusions, "binary")
                excluded_paths.add(path)
                excluded_path_reasons[path] = "binary"
                continue
            if exc.code == "context_path_rejected":
                _increment(exclusions, "symlink")
                excluded_paths.add(path)
                excluded_path_reasons[path] = "symlink"
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
        "omitted_path_reasons": omitted_path_reasons,
        "excluded_paths": excluded_paths,
        "excluded_path_reasons": excluded_path_reasons,
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


def _manifest_file_metadata(metadata: dict[str, object]) -> dict[str, int]:
    return {
        "mode": int(metadata["mode"]),
        "mtime_ns": int(metadata["mtime_ns"]),
        "size": int(metadata["size"]),
    }


def _git_path_manifest(
    *,
    included_paths: list[str],
    omitted_paths: list[str],
    index_records: list[dict[str, object]],
    policy_exclusions: dict[str, str],
    worktree_records: list[dict[str, object]],
    untracked_records: list[dict[str, object]],
    content_scope_state: dict[str, object],
) -> dict[str, object]:
    worktree_by_path = {str(item["path"]): item for item in worktree_records}
    untracked_by_path = {str(item["path"]): item for item in untracked_records}
    index_by_path: dict[str, list[dict[str, object]]] = {}
    for item in index_records:
        if _index_exclusion_reason(item) is not None:
            continue
        index_by_path.setdefault(str(item["path"]), []).append(item)

    entries: list[dict[str, object]] = []
    for path in included_paths:
        if path in content_scope_state["omitted_paths"] or path in content_scope_state["excluded_paths"]:
            continue
        if path in worktree_by_path:
            record = worktree_by_path[path]
            entries.append(
                {
                    "path": path,
                    "kind": "file",
                    "identity_kind": "worktree",
                    "size": int(record["size"]),
                    "content_hash": str(record["content_hash"]),
                    "metadata": _manifest_file_metadata(record["metadata"]),
                }
            )
            continue
        if path in untracked_by_path:
            record = untracked_by_path[path]
            entries.append(
                {
                    "path": path,
                    "kind": "file",
                    "identity_kind": "untracked",
                    "size": int(record["size"]),
                    "content_hash": str(record["content_hash"]),
                    "metadata": _manifest_file_metadata(record["metadata"]),
                }
            )
            continue
        candidates = index_by_path.get(path, [])
        if not candidates:
            continue
        if len(candidates) != 1:
            raise RepositorySnapshotError(
                "repository_snapshot_failed",
                "Read-only Git index output was ambiguous for a tracked path.",
                details={"path": path},
            )
        candidate = candidates[0]
        entries.append(
            {
                "path": path,
                "kind": "file",
                "identity_kind": "git_index",
                "git": {
                    "mode": str(candidate["mode"]),
                    "object_id": str(candidate["oid"]),
                    "stage": int(candidate["stage"]),
                },
            }
        )

    excluded: list[dict[str, object]] = [
        {"path": path, "kind": "file", "reason": reason}
        for path, reason in sorted(policy_exclusions.items())
    ]
    excluded.extend(
        {
            "path": path,
            "kind": "file",
            "reason": reason,
        }
        for path, reason in sorted(content_scope_state["excluded_path_reasons"].items())
    )
    omitted: list[dict[str, object]] = [
        {"path": path, "kind": "file", "reason": "max_files"}
        for path in sorted(set(omitted_paths) - set(content_scope_state["omitted_paths"]))
    ]
    omitted.extend(
        {
            "path": path,
            "kind": "file",
            "reason": reason,
        }
        for path, reason in sorted(content_scope_state["omitted_path_reasons"].items())
    )

    directory_paths = {
        str(parent)
        for group in (entries, excluded, omitted)
        for item in group
        for parent in PurePosixPath(str(item["path"])).parents
        if str(parent) != "."
    }
    directory_entries = [{"path": path, "kind": "directory"} for path in sorted(directory_paths)]
    manifest_entries = directory_entries + sorted(entries, key=_canonical_json)
    return {
        "complete": not omitted,
        "entries": manifest_entries,
        "excluded": sorted(excluded, key=_canonical_json),
        "omitted": sorted(omitted, key=_canonical_json),
        "omitted_paths": [str(item["path"]) for item in sorted(omitted, key=_canonical_json)],
    }


def _git_object_oid(content: bytes, object_format: str) -> str:
    if object_format not in {"sha1", "sha256"}:
        raise RepositorySnapshotError(
            "repository_snapshot_failed",
            "Read-only Git inspection returned an unsupported object format.",
            details={"object_format": object_format},
        )
    header = f"blob {len(content)}".encode("ascii") + b"\0"
    digest = hashlib.new(object_format)
    digest.update(header)
    digest.update(content)
    return digest.hexdigest()


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
