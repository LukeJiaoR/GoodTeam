from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass

from goodteam_os._repository_content_policy import (
    hash_bytes,
    redact_observation_text,
    reject_sensitive_path,
)
from goodteam_os.repository_access import (
    AnchoredRepository,
    RepositoryAccessError,
    canonical_relative_path,
)
from goodteam_os.repository_context_provider import ContextProviderError
from goodteam_os.repository_snapshot import RepositorySnapshot, verify_repository_snapshot


_GIT_TIMEOUT_SECONDS = 5.0
_GIT_LIMIT_MAX = 50
_REVISION = re.compile(r"^[0-9a-fA-F]{7,40}$")
_ZERO_OID = re.compile(r"^0+$")
_RAW_MODE = re.compile(rb"^[0-7]{6}$")
_RAW_OID = re.compile(rb"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RAW_STATUS = re.compile(rb"^[A-Z]$")


@dataclass(frozen=True)
class _ProcessResult:
    stdout: bytes
    returncode: int


def inspect_git_history(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
    process_factory,
) -> dict[str, object]:
    path, limit = _validate_git_request(request, allowed_keys={"path", "limit"})
    _require_git_snapshot(snapshot)
    target = path or "."
    result = _dispatch_git_operation(
        root=root,
        snapshot=snapshot,
        process_factory=process_factory,
        operation="history",
        path=target,
        limit=limit,
        output_allowance=result_char_allowance,
    )
    observations: list[dict[str, object]] = []
    returned_chars = 0
    truncated = False
    for item in _parse_history(result.stdout):
        observation = {
            "kind": "git_commit",
            "commit_sha": item["commit_sha"],
            "committed_at": item["committed_at"],
            "message": item["message"],
        }
        next_chars = returned_chars + _string_chars(observation)
        if next_chars > result_char_allowance:
            truncated = True
            break
        observations.append(observation)
        returned_chars = next_chars
    return {"observations": observations, "truncated": truncated}


def inspect_recent_changes(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
    process_factory,
) -> dict[str, object]:
    path, limit = _validate_git_request(
        request, allowed_keys={"path", "limit", "since_sha"}
    )
    since_sha = request.get("since_sha")
    if since_sha is not None and (
        not isinstance(since_sha, str) or _REVISION.fullmatch(since_sha) is None
    ):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    _require_git_snapshot(snapshot)
    target = path or "."
    scopes = [("since_revision", since_sha)] if since_sha else [
        ("staged", "cached"),
        ("unstaged", None),
    ]
    changes: list[tuple[str, dict[str, object], str | None]] = []
    for scope, revision in scopes:
        result = _dispatch_git_operation(
            root=root,
            snapshot=snapshot,
            process_factory=process_factory,
            operation="diff_raw",
            path=target,
            revision=revision,
            output_allowance=result_char_allowance,
        )
        changes.extend((scope, item, revision) for item in _parse_raw_diff(result.stdout))
    untracked = _dispatch_git_operation(
        root=root,
        snapshot=snapshot,
        process_factory=process_factory,
        operation="untracked",
        path=target,
        output_allowance=result_char_allowance,
    )
    changes.extend(
        ("untracked", {"path": item, "old_blob": None, "new_blob": None}, None)
        for item in _parse_nul_paths(untracked.stdout)
    )

    eligible_changes: list[tuple[str, dict[str, object], str | None]] = []
    sensitive_paths_omitted = False
    for change in changes:
        try:
            reject_sensitive_path(str(change[1]["path"]))
        except RepositoryAccessError:
            sensitive_paths_omitted = True
            continue
        eligible_changes.append(change)

    observations: list[dict[str, object]] = []
    returned_chars = 0
    truncated = sensitive_paths_omitted or len(eligible_changes) > limit
    for scope, item, revision in eligible_changes[:limit]:
        raw_diff = _change_diff_bytes(
            root=root,
            snapshot=snapshot,
            process_factory=process_factory,
            scope=scope,
            revision=revision,
            path=str(item["path"]),
            output_allowance=max(1, result_char_allowance - returned_chars),
        )
        try:
            diff_text = raw_diff.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            ) from exc
        redacted_diff = redact_observation_text(diff_text)[0]
        observation = {
            "kind": "git_change",
            "path": item["path"],
            "change_scope": scope,
            "old_blob": item.get("old_blob"),
            "new_blob": item.get("new_blob"),
            "diff_hash": hash_bytes(raw_diff),
            "diff": redacted_diff,
        }
        next_chars = returned_chars + _string_chars(observation)
        if next_chars > result_char_allowance:
            truncated = True
            break
        observations.append(observation)
        returned_chars = next_chars
    return {"observations": observations, "truncated": truncated}


def _validate_git_request(
    request: dict[str, object], *, allowed_keys: set[str]
) -> tuple[str, int]:
    if set(request) - allowed_keys:
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    path = request.get("path", "")
    limit = request.get("limit", 20)
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > _GIT_LIMIT_MAX
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a Git-tool limit.",
            details={"limit": "limit"},
        )
    canonical = canonical_relative_path(path, allow_root=True)
    reject_sensitive_path(canonical)
    return canonical, limit


def _require_git_snapshot(snapshot: RepositorySnapshot) -> None:
    if snapshot.source_control.get("kind") != "git":
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        )


def _dispatch_git_operation(
    *,
    root,
    snapshot: RepositorySnapshot,
    process_factory,
    operation: str,
    path: str,
    output_allowance: int,
    limit: int | None = None,
    revision: str | None = None,
) -> _ProcessResult:
    argv = _build_git_argv(
        operation=operation,
        path=path,
        limit=limit,
        revision=revision,
    )
    verify_repository_snapshot(root, snapshot)
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        repository.verify_root()
        _verify_git_path(repository, path)
        process = None
        pending_error: Exception | None = None
        result: _ProcessResult | None = None
        try:
            process = process_factory(
                argv,
                cwd=str(root),
                env=_git_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ContextProviderError(
                "context_tool_unsupported",
                "Repository context tool is unsupported.",
            ) from exc
        try:
            repository.verify_root()
            result = _read_process_bounded(process, output_allowance=max(1, output_allowance))
        except Exception as exc:
            pending_error = exc
        finally:
            _close_process_streams(process)
        repository.verify_root()
    verify_repository_snapshot(root, snapshot)
    if pending_error is not None:
        raise pending_error
    if result is None:
        raise ContextProviderError(
            "context_tool_failed",
            "Repository context tool failed.",
        )
    if result.returncode != 0:
        raise ContextProviderError(
            "context_tool_failed",
            "Repository context tool failed.",
        )
    return result


def _verify_git_path(repository: AnchoredRepository, path: str) -> None:
    if path == ".":
        return
    directory_failure: RepositoryAccessError | None = None
    try:
        repository.list_directory(path, max_entries=0)
        return
    except RepositoryAccessError as directory_error:
        if directory_error.details.get("reason") == "symlink":
            raise
        directory_failure = directory_error
    try:
        repository.read_text(path, max_bytes=0)
    except RepositoryAccessError:
        if directory_failure is not None:
            raise directory_failure from None
        raise


def _build_git_argv(
    *, operation: str, path: str, limit: int | None, revision: str | None
) -> list[str]:
    base = [
        "git",
        "--no-pager",
        "-c",
        "color.ui=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
    ]
    if operation == "history" and limit is not None:
        return [
            *base,
            "log",
            "--no-color",
            "--date=iso-strict",
            "--format=%H%x00%aI%x00%s%x1e",
            f"--max-count={limit}",
            "--",
            path,
        ]
    if operation == "untracked":
        return [
            *base,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            path,
        ]
    if operation in {"diff_raw", "diff_patch"}:
        command = [
            *base,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--no-color",
        ]
        if revision == "cached":
            command.append("--cached")
        elif revision is not None:
            command.append(revision)
        if operation == "diff_raw":
            command.extend(["--raw", "--abbrev=64", "--no-renames", "-z"])
        command.extend(["--", path])
        return command
    raise RuntimeError("unknown internal Git operation")


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
        }
    )
    return environment


def _read_process_bounded(process, *, output_allowance: int) -> _ProcessResult:
    stdout = getattr(process, "stdout", None)
    stderr = getattr(process, "stderr", None)
    if stdout is None or stderr is None:
        _terminate_process(process)
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        )
    try:
        stdout_fd = stdout.fileno()
        stderr_fd = stderr.fileno()
        os.set_blocking(stdout_fd, False)
        os.set_blocking(stderr_fd, False)
    except (AttributeError, OSError, ValueError) as exc:
        _terminate_process(process)
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        ) from exc
    selector = selectors.DefaultSelector()
    stdout_buffer = bytearray()
    stderr_bytes = 0
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    try:
        selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
        selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
        open_streams = {"stdout", "stderr"}
        while open_streams:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                _terminate_process(process)
                raise ContextProviderError(
                    "context_tool_timeout",
                    "Repository context Git inspection timed out.",
                )
            ready = selector.select(remaining_time)
            if not ready:
                _terminate_process(process)
                raise ContextProviderError(
                    "context_tool_timeout",
                    "Repository context Git inspection timed out.",
                )
            for key, _events in ready:
                stream_name = key.data
                consumed = (
                    len(stdout_buffer) if stream_name == "stdout" else stderr_bytes
                )
                read_limit = output_allowance + 1 - consumed
                if read_limit <= 0:
                    _terminate_process(process)
                    raise ContextProviderError(
                        "context_tool_limit_exceeded",
                        "Repository context request exceeded a Git-tool limit.",
                    )
                try:
                    chunk = os.read(key.fd, min(4096, read_limit))
                except BlockingIOError:
                    continue
                if not chunk:
                    try:
                        selector.unregister(key.fd)
                    except Exception:
                        pass
                    open_streams.discard(stream_name)
                    continue
                if stream_name == "stdout":
                    stdout_buffer.extend(chunk)
                    exceeded = len(stdout_buffer) > output_allowance
                else:
                    stderr_bytes += len(chunk)
                    exceeded = stderr_bytes > output_allowance
                if exceeded:
                    _terminate_process(process)
                    raise ContextProviderError(
                        "context_tool_limit_exceeded",
                        "Repository context request exceeded a Git-tool limit.",
                    )
        remaining_time = max(0.0, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining_time)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            raise ContextProviderError(
                "context_tool_timeout",
                "Repository context Git inspection timed out.",
            ) from None
        return _ProcessResult(stdout=bytes(stdout_buffer), returncode=int(returncode))
    finally:
        selector.close()


def _terminate_process(process) -> None:
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1.0)
    except Exception:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1.0)
        except Exception:
            pass


def _close_process_streams(process) -> None:
    if process.poll() is None:
        _terminate_process(process)
    for name in ("stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _parse_history(value: bytes) -> list[dict[str, str]]:
    observations: list[dict[str, str]] = []
    for raw_record in value.split(b"\x1e"):
        raw_record = raw_record.strip(b"\r\n")
        if not raw_record:
            continue
        fields = raw_record.split(b"\0")
        if len(fields) != 3:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            )
        try:
            commit_sha, committed_at, message = (
                field.decode("utf-8", errors="strict") for field in fields
            )
        except UnicodeDecodeError as exc:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            ) from exc
        if re.fullmatch(r"[0-9a-f]{40,64}", commit_sha) is None:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            )
        observations.append(
            {
                "commit_sha": commit_sha,
                "committed_at": committed_at,
                "message": message,
            }
        )
    return observations


def _parse_raw_diff(value: bytes) -> list[dict[str, object]]:
    fields = value.split(b"\0")
    observations: list[dict[str, object]] = []
    index = 0
    while index < len(fields):
        metadata = fields[index]
        index += 1
        if not metadata:
            continue
        if index >= len(fields):
            raise _invalid_output()
        raw_path = fields[index]
        index += 1
        parts = metadata.split()
        if (
            len(parts) != 5
            or not parts[0].startswith(b":")
            or _RAW_MODE.fullmatch(parts[0][1:]) is None
            or _RAW_MODE.fullmatch(parts[1]) is None
            or _RAW_OID.fullmatch(parts[2]) is None
            or _RAW_OID.fullmatch(parts[3]) is None
            or _RAW_STATUS.fullmatch(parts[4]) is None
        ):
            raise _invalid_output()
        try:
            old_blob = parts[2].decode("ascii")
            new_blob = parts[3].decode("ascii")
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid_output() from exc
        observations.append(
            {
                "path": canonical_relative_path(path),
                "old_blob": None if _ZERO_OID.fullmatch(old_blob) else old_blob,
                "new_blob": None if _ZERO_OID.fullmatch(new_blob) else new_blob,
            }
        )
    return observations


def _parse_nul_paths(value: bytes) -> list[str]:
    paths: list[str] = []
    for raw_path in value.split(b"\0"):
        if not raw_path:
            continue
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid_output() from exc
        paths.append(canonical_relative_path(path))
    return paths


def _change_diff_bytes(
    *,
    root,
    snapshot: RepositorySnapshot,
    process_factory,
    scope: str,
    revision: str | None,
    path: str,
    output_allowance: int,
) -> bytes:
    if scope == "untracked":
        with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
            text = repository.read_text(path, max_bytes=output_allowance)
        return text.text.encode("utf-8")
    return _dispatch_git_operation(
        root=root,
        snapshot=snapshot,
        process_factory=process_factory,
        operation="diff_patch",
        path=path,
        revision=revision,
        output_allowance=output_allowance,
    ).stdout


def _invalid_output() -> ContextProviderError:
    return ContextProviderError(
        "context_tool_invalid_output",
        "Repository context tool produced invalid output.",
    )


def _string_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_string_chars(item) for item in value)
    if type(value) is dict:
        return sum(_string_chars(item) for item in value.values())
    return 0
