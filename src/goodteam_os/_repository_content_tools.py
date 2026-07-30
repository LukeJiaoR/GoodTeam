from __future__ import annotations

import base64
import binascii
import json
import multiprocessing
import os
import selectors
import subprocess
import time
from dataclasses import dataclass

from goodteam_os._repository_content_policy import (
    FILE_READ_MAX_BYTES,
    QUERY_LENGTH_MAX,
    SEARCH_MATCHES_MAX,
    TREE_DEPTH_MAX,
    TREE_ENTRIES_MAX,
    hash_bytes,
    redact_observation_text,
    reject_sensitive_path,
)
from goodteam_os.repository_access import (
    AnchoredRepository,
    DEFAULT_IGNORED_DIRECTORIES,
    RepositoryAccessError,
    canonical_relative_path,
)
from goodteam_os.repository_context_provider import ContextProviderError
from goodteam_os.repository_snapshot import RepositorySnapshot


def list_tree(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
) -> dict[str, object]:
    path, depth, max_entries = _validate_list_tree_request(request)
    reject_sensitive_path(path)
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        entries = repository.walk(
            path,
            max_entries=max_entries + 1,
            max_depth=depth,
        )
    observations: list[dict[str, object]] = []
    truncated = len(entries) > max_entries
    returned_chars = 0
    for entry in entries[:max_entries]:
        if _entry_is_sensitive(entry.path):
            truncated = True
            continue
        observation = {
            "kind": "tree_entry",
            "path": entry.path,
            "entry_kind": entry.kind,
        }
        next_chars = returned_chars + sum(
            len(value)
            for value in observation.values()
            if isinstance(value, str)
        )
        if next_chars > result_char_allowance:
            truncated = True
            break
        observations.append(observation)
        returned_chars = next_chars
    return {"observations": observations, "truncated": truncated}


def read_file_excerpt(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
) -> dict[str, object]:
    path, start_line, line_count = _validate_excerpt_request(request)
    reject_sensitive_path(path)
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        text = repository.read_text(path, max_bytes=FILE_READ_MAX_BYTES)

    lines = text.text.splitlines(keepends=True)
    end_line = start_line + line_count - 1
    if end_line > len(lines):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    raw_excerpt = "".join(lines[start_line - 1 : end_line])
    max_text_chars = max(
        0,
        result_char_allowance
        - len(path)
        - len("file_excerpt")
        - len(hash_bytes(b""))
        - 16,
    )
    redacted_text, redaction_applied, text_truncated = redact_observation_text(
        raw_excerpt,
        limit=max_text_chars,
    )
    return {
        "observations": [
            {
                "kind": "file_excerpt",
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "text": redacted_text,
                "content_hash": hash_bytes(raw_excerpt.encode("utf-8")),
                "redaction_applied": redaction_applied,
            }
        ],
        "truncated": text_truncated,
    }


def search_text(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
) -> dict[str, object]:
    path, query, mode, max_matches = _validate_search_request(request)
    reject_sensitive_path(path)
    if mode == "regex":
        return _search_text_regex(
            root=root,
            snapshot=snapshot,
            path=path,
            query=query,
            max_matches=max_matches,
            result_char_allowance=result_char_allowance,
        )
    scan_limits = _literal_search_scan_limits(
        result_char_allowance=result_char_allowance,
        max_matches=max_matches,
    )
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        entries = repository.walk(path, max_entries=scan_limits["walk_max_entries"] + 1)
        observations: list[dict[str, object]] = []
        returned_chars = 0
        truncated = len(entries) > scan_limits["walk_max_entries"]
        scanned_files = 0
        remaining_read_bytes = scan_limits["max_read_bytes"]
        for entry in entries[: scan_limits["walk_max_entries"]]:
            if _entry_is_sensitive(entry.path):
                truncated = True
                continue
            if entry.kind != "file":
                continue
            if scanned_files >= scan_limits["max_files"]:
                truncated = True
                break
            if remaining_read_bytes <= 0:
                truncated = True
                break
            per_read_cap = min(FILE_READ_MAX_BYTES, remaining_read_bytes)
            if per_read_cap <= 0:
                truncated = True
                break
            text = repository.read_text(entry.path, max_bytes=per_read_cap)
            scanned_files += 1
            observed_read_bytes = len(text.text.encode("utf-8"))
            remaining_read_bytes = max(0, remaining_read_bytes - observed_read_bytes)
            if text.truncated:
                truncated = True
            for line_number, line in enumerate(text.text.splitlines(keepends=True), start=1):
                if query not in line:
                    continue
                observation = _build_search_match_observation(
                    path=entry.path,
                    line_number=line_number,
                    raw_line=line,
                )
                next_chars = returned_chars + sum(
                    len(value)
                    for value in observation.values()
                    if isinstance(value, str)
                )
                if next_chars > result_char_allowance:
                    truncated = True
                    return {"observations": observations, "truncated": truncated}
                observations.append(observation)
                returned_chars = next_chars
                if len(observations) >= max_matches:
                    truncated = True
                    return {"observations": observations, "truncated": truncated}
    return {"observations": observations, "truncated": truncated}


def _literal_search_scan_limits(
    *,
    result_char_allowance: int,
    max_matches: int,
) -> dict[str, int]:
    allowance = max(1, result_char_allowance)
    walk_cap = min(
        TREE_ENTRIES_MAX,
        max_matches * 4,
        max(1, allowance // 24),
    )
    file_cap = min(walk_cap, max_matches * 2, max(1, allowance // 40))
    read_cap = min(
        FILE_READ_MAX_BYTES,
        max(1, allowance * 4),
    )
    return {
        "walk_max_entries": max(1, walk_cap),
        "max_files": max(1, file_cap),
        "max_read_bytes": max(1, read_cap),
    }


def _entry_is_sensitive(path: str) -> bool:
    try:
        reject_sensitive_path(path)
    except RepositoryAccessError:
        return True
    return False


def _validate_list_tree_request(
    request: dict[str, object],
) -> tuple[str, int, int]:
    if set(request) - {"path", "depth", "max_entries"}:
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    path = request.get("path", "")
    depth = request.get("depth", 1)
    max_entries = request.get("max_entries", 10)
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    for name, value, ceiling in (
        ("depth", depth, TREE_DEPTH_MAX),
        ("max_entries", max_entries, TREE_ENTRIES_MAX),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > ceiling
        ):
            raise ContextProviderError(
                "context_tool_limit_exceeded",
                "Repository context request exceeded a content-tool limit.",
                details={"limit": name},
            )
    canonical = canonical_relative_path(path, allow_root=True)
    return canonical, depth, max_entries


def _validate_excerpt_request(
    request: dict[str, object],
) -> tuple[str, int, int]:
    if set(request) != {"path", "start_line", "line_count"}:
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    path = request.get("path")
    start_line = request.get("start_line")
    line_count = request.get("line_count")
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    if (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or start_line <= 0
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    if (
        isinstance(line_count, bool)
        or not isinstance(line_count, int)
        or line_count <= 0
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    canonical = canonical_relative_path(path)
    return canonical, start_line, line_count


def _validate_search_request(
    request: dict[str, object],
) -> tuple[str, str, str, int]:
    if set(request) != {"path", "query", "mode", "max_matches"}:
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    path = request.get("path")
    query = request.get("query")
    mode = request.get("mode")
    max_matches = request.get("max_matches")
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
        )
    if (
        not isinstance(query, str)
        or not query
        or len(query) > QUERY_LENGTH_MAX
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
            details={"limit": "query"},
        )
    if mode not in {"literal", "regex"}:
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
            details={"limit": "mode"},
        )
    if (
        isinstance(max_matches, bool)
        or not isinstance(max_matches, int)
        or max_matches <= 0
        or max_matches > SEARCH_MATCHES_MAX
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a content-tool limit.",
            details={"limit": "max_matches"},
        )
    canonical = canonical_relative_path(path, allow_root=True)
    return canonical, query, mode, max_matches


def _search_text_regex(
    *,
    root,
    snapshot: RepositorySnapshot,
    path: str,
    query: str,
    max_matches: int,
    result_char_allowance: int,
) -> dict[str, object]:
    target = path or "."
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        repository.verify_root()
    try:
        process = _spawn_rg_process(
            root=root,
            snapshot=snapshot,
            query=query,
            target=target,
            result_char_allowance=result_char_allowance,
            max_matches=max_matches,
        )
    except FileNotFoundError as exc:
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        ) from exc
    try:
        result = _stream_regex_matches(
            process=process,
            result_char_allowance=result_char_allowance,
            max_matches=max_matches,
        )
    finally:
        _reap_process(process)
        with AnchoredRepository(
            root,
            expected_identity=snapshot.root_identity,
        ) as repository:
            repository.verify_root()
    return result


def _spawn_rg_process(
    *,
    root,
    snapshot: RepositorySnapshot,
    query: str,
    target: str,
    result_char_allowance: int,
    max_matches: int,
):
    scan_limits = _literal_search_scan_limits(
        result_char_allowance=result_char_allowance,
        max_matches=max_matches,
    )
    path = "" if target == "." else target
    lines: list[tuple[str, int, str]] = []
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        entries = repository.walk(path, max_entries=scan_limits["walk_max_entries"] + 1)
        scanned_files = 0
        remaining_read_bytes = scan_limits["max_read_bytes"]
        for entry in entries[: scan_limits["walk_max_entries"]]:
            if _entry_is_sensitive(entry.path) or entry.kind != "file":
                continue
            if scanned_files >= scan_limits["max_files"] or remaining_read_bytes <= 0:
                break
            text = repository.read_text(
                entry.path,
                max_bytes=min(FILE_READ_MAX_BYTES, remaining_read_bytes),
            )
            scanned_files += 1
            remaining_read_bytes -= len(text.text.encode("utf-8"))
            lines.extend(
                (entry.path, line_number, line)
                for line_number, line in enumerate(
                    text.text.splitlines(keepends=True),
                    start=1,
                )
            )
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    process = multiprocessing.get_context("fork").Process(
        target=_regex_worker,
        args=(query, lines, stdout_write, stderr_write),
        daemon=True,
    )
    try:
        process.start()
    except Exception:
        os.close(stdout_read)
        os.close(stdout_write)
        os.close(stderr_read)
        os.close(stderr_write)
        raise
    os.close(stdout_write)
    os.close(stderr_write)
    return _RegexWorkerProcess(
        process=process,
        stdout=os.fdopen(stdout_read, "rb", buffering=0),
        stderr=os.fdopen(stderr_read, "rb", buffering=0),
    )


def _regex_worker(
    query: str,
    lines: list[tuple[str, int, str]],
    stdout_fd: int,
    stderr_fd: int,
) -> None:
    import re
    try:
        pattern = re.compile(query)
    except re.error:
        os.write(stderr_fd, b"invalid regex")
        raise SystemExit(2)
    try:
        for path, line_number, line in lines:
            if pattern.search(line) is None:
                continue
            payload = {
                "type": "match",
                "data": {
                    "path": {"text": path},
                    "line_number": line_number,
                    "lines": {"text": line},
                },
            }
            os.write(
                stdout_fd,
                json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n",
            )
    finally:
        os.close(stdout_fd)
        os.close(stderr_fd)


@dataclass
class _RegexWorkerProcess:
    process: object
    stdout: object
    stderr: object

    @property
    def returncode(self):
        return self.process.exitcode

    def poll(self):
        return self.process.exitcode

    def wait(self, timeout=None):
        self.process.join(timeout)
        if self.process.is_alive():
            raise subprocess.TimeoutExpired("regex-worker", timeout)
        return self.process.exitcode

    def terminate(self):
        if self.process.is_alive():
            self.process.terminate()

    def kill(self):
        if self.process.is_alive():
            self.process.kill()


def _stream_regex_matches(
    *,
    process,
    result_char_allowance: int,
    max_matches: int,
) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    observations: list[dict[str, object]] = []
    returned_chars = 0
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
    except (AttributeError, OSError, ValueError) as exc:
        _terminate_process(process)
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        ) from exc
    selector = None
    try:
        selector = selectors.DefaultSelector()
        selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
        selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
        os.set_blocking(stdout_fd, False)
        os.set_blocking(stderr_fd, False)
    except Exception as exc:
        _terminate_process(process)
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        ) from exc
    try:
        stdout_buffer = bytearray()
        stderr_bytes = 0
        stdout_bytes = 0
        output_byte_limit = max(FILE_READ_MAX_BYTES, result_char_allowance * 4)
        open_streams = {"stdout", "stderr"}
        while open_streams:
            now = time.monotonic()
            if now >= deadline:
                _terminate_process(process)
                raise ContextProviderError(
                    "context_tool_timeout",
                    "Repository context regex search timed out.",
                )
            ready = selector.select(deadline - now)
            if not ready:
                _terminate_process(process)
                raise ContextProviderError(
                    "context_tool_timeout",
                    "Repository context regex search timed out.",
                )
            for key, _events in ready:
                stream_name = key.data
                try:
                    chunk = os.read(key.fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    try:
                        selector.unregister(key.fd)
                    except Exception:
                        pass
                    open_streams.discard(stream_name)
                    continue
                if stream_name == "stderr":
                    stderr_bytes += len(chunk)
                    if stderr_bytes > output_byte_limit:
                        _terminate_process(process)
                        raise ContextProviderError(
                            "context_tool_limit_exceeded",
                            "Repository context request exceeded a content-tool limit.",
                        )
                    continue
                stdout_bytes += len(chunk)
                if stdout_bytes > output_byte_limit:
                    _terminate_process(process)
                    raise ContextProviderError(
                        "context_tool_limit_exceeded",
                        "Repository context request exceeded a content-tool limit.",
                    )
                stdout_buffer.extend(chunk)
                while True:
                    newline_index = stdout_buffer.find(b"\n")
                    if newline_index < 0:
                        break
                    raw_line_bytes = bytes(stdout_buffer[: newline_index + 1])
                    del stdout_buffer[: newline_index + 1]
                    try:
                        raw_line = raw_line_bytes.decode("utf-8")
                        payload = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        _terminate_process(process)
                        raise ContextProviderError(
                            "context_tool_invalid_output",
                            "Repository context tool produced invalid output.",
                        ) from exc
                    if payload.get("type") != "match":
                        continue
                    data = payload.get("data", {})
                    path_value = _extract_rg_field_text(
                        data.get("path"),
                        output_byte_limit=output_byte_limit,
                    )
                    line_number = data.get("line_number")
                    line_text = _extract_rg_field_text(
                        data.get("lines"),
                        output_byte_limit=output_byte_limit,
                    )
                    if (
                        not isinstance(path_value, str)
                        or not isinstance(line_number, int)
                        or not isinstance(line_text, str)
                    ):
                        _terminate_process(process)
                        raise ContextProviderError(
                            "context_tool_invalid_output",
                            "Repository context tool produced invalid output.",
                        )
                    validated_path = _validate_search_result_path(path_value)
                    observation = _build_search_match_observation(
                        path=validated_path,
                        line_number=line_number,
                        raw_line=line_text,
                    )
                    next_chars = returned_chars + sum(
                        len(value)
                        for value in observation.values()
                        if isinstance(value, str)
                    )
                    if next_chars > result_char_allowance:
                        _terminate_process(process)
                        return {"observations": observations, "truncated": True}
                    observations.append(observation)
                    returned_chars = next_chars
                    if len(observations) >= max_matches:
                        _terminate_process(process)
                        return {"observations": observations, "truncated": True}
        if stdout_buffer:
            _terminate_process(process)
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            )
        returncode = process.wait(timeout=1.0)
        if returncode not in {0, 1}:
            raise ContextProviderError(
                "context_tool_failed",
                "Repository context tool failed.",
            )
        return {"observations": observations, "truncated": False}
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass


def _build_search_match_observation(
    *,
    path: str,
    line_number: int,
    raw_line: str,
) -> dict[str, object]:
    redacted_text, redaction_applied, _ = redact_observation_text(raw_line.rstrip("\n"))
    return {
        "kind": "search_match",
        "path": path,
        "line": line_number,
        "start_line": line_number,
        "end_line": line_number,
        "text": redacted_text,
        "content_hash": hash_bytes(raw_line.encode("utf-8")),
        "redaction_applied": redaction_applied,
    }


def _extract_rg_field_text(field: object, *, output_byte_limit: int) -> str | None:
    if type(field) is not dict:
        return None
    text_value = field.get("text")
    if isinstance(text_value, str):
        return text_value
    bytes_value = field.get("bytes")
    if not isinstance(bytes_value, str):
        return None
    decoded = _decode_rg_base64(bytes_value, output_byte_limit=output_byte_limit)
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextProviderError(
            "context_tool_invalid_output",
            "Repository context tool produced invalid output.",
        ) from exc


def _decode_rg_base64(value: str, *, output_byte_limit: int) -> bytes:
    max_encoded_length = ((output_byte_limit + 2) // 3) * 4
    if len(value) > max_encoded_length:
        raise ContextProviderError(
            "context_tool_invalid_output",
            "Repository context tool produced invalid output.",
        )
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContextProviderError(
            "context_tool_invalid_output",
            "Repository context tool produced invalid output.",
        ) from exc
    if len(decoded) > output_byte_limit:
        raise ContextProviderError(
            "context_tool_invalid_output",
            "Repository context tool produced invalid output.",
        )
    return decoded


def _validate_search_result_path(path: str) -> str:
    try:
        normalized = path[2:] if path.startswith("./") else path
        canonical = canonical_relative_path(normalized)
        reject_sensitive_path(canonical)
    except RepositoryAccessError as exc:
        raise ContextProviderError(
            "context_tool_invalid_output",
            "Repository context tool produced invalid output.",
        ) from exc
    for part in canonical.split("/"):
        if part in DEFAULT_IGNORED_DIRECTORIES:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            )
    return canonical


def _terminate_process(process) -> None:
    if hasattr(process, "terminate"):
        process.terminate()


def _reap_process(process) -> None:
    if getattr(process, "poll", None) is not None:
        try:
            returncode = process.poll()
        except Exception:
            returncode = None
        if returncode is None:
            _terminate_process(process)
    if hasattr(process, "wait"):
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if hasattr(process, "kill"):
                process.kill()
                process.wait(timeout=1.0)
