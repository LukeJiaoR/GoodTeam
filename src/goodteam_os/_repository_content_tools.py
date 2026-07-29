from __future__ import annotations

import json
import time
import subprocess

from goodteam_os._repository_content_policy import (
    FILE_READ_MAX_BYTES,
    QUERY_LENGTH_MAX,
    SEARCH_MATCHES_MAX,
    SENSITIVE_PATH_PATTERNS,
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
        truncated = False
        scanned_files = 0
        read_bytes = 0
        for entry in entries:
            if scanned_files >= scan_limits["walk_max_entries"]:
                truncated = True
                break
            if entry.kind != "file":
                continue
            if scanned_files >= scan_limits["max_files"]:
                truncated = True
                break
            if read_bytes >= scan_limits["max_read_bytes"]:
                truncated = True
                break
            text = repository.read_text(entry.path, max_bytes=FILE_READ_MAX_BYTES)
            scanned_files += 1
            read_bytes += len(text.text.encode("utf-8"))
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
        max(FILE_READ_MAX_BYTES // 32, allowance * 4),
    )
    return {
        "walk_max_entries": max(1, walk_cap),
        "max_files": max(1, file_cap),
        "max_read_bytes": max(1, read_cap),
    }


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
            process = _spawn_rg_process(root=root, query=query, target=target)
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
            repository.verify_root()
    return result


def _spawn_rg_process(*, root, query: str, target: str):
    globs: list[str] = []
    for ignored in sorted(DEFAULT_IGNORED_DIRECTORIES):
        globs.extend(["--glob", f"!{ignored}/**"])
    for sensitive in SENSITIVE_PATH_PATTERNS:
        globs.extend(["--glob", f"!{sensitive}"])
    return subprocess.Popen(
        [
            "rg",
            "--json",
            "--no-follow",
            "--color",
            "never",
            *globs,
            query,
            target,
        ],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stream_regex_matches(
    *,
    process,
    result_char_allowance: int,
    max_matches: int,
) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    observations: list[dict[str, object]] = []
    returned_chars = 0
    output_bytes = 0
    output_byte_limit = max(FILE_READ_MAX_BYTES, result_char_allowance * 4)
    stdout = getattr(process, "stdout", None)
    if stdout is None or not hasattr(stdout, "readline"):
        return _collect_regex_matches_fallback(
            process=process,
            result_char_allowance=result_char_allowance,
            max_matches=max_matches,
        )
    while True:
        if time.monotonic() >= deadline:
            _terminate_process(process)
            raise ContextProviderError(
                "context_tool_timeout",
                "Repository context regex search timed out.",
            )
        raw_line = stdout.readline()
        if raw_line == "":
            break
        output_bytes += len(raw_line.encode("utf-8"))
        if output_bytes > output_byte_limit:
            _terminate_process(process)
            raise ContextProviderError(
                "context_tool_limit_exceeded",
                "Repository context request exceeded a content-tool limit.",
            )
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _terminate_process(process)
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            ) from exc
        if payload.get("type") != "match":
            continue
        data = payload.get("data", {})
        path_value = data.get("path", {}).get("text")
        line_number = data.get("line_number")
        line_text = data.get("lines", {}).get("text")
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
    returncode = process.wait(timeout=1.0)
    if returncode not in {0, 1}:
        raise ContextProviderError(
            "context_tool_failed",
            "Repository context tool failed.",
        )
    return {"observations": observations, "truncated": False}


def _collect_regex_matches_fallback(
    *,
    process,
    result_char_allowance: int,
    max_matches: int,
) -> dict[str, object]:
    try:
        stdout, _stderr = process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired as exc:
        if hasattr(process, "kill"):
            process.kill()
        if hasattr(process, "wait"):
            process.wait(timeout=1.0)
        raise ContextProviderError(
            "context_tool_timeout",
            "Repository context regex search timed out.",
        ) from exc
    observations: list[dict[str, object]] = []
    returned_chars = 0
    output_bytes = 0
    output_byte_limit = max(FILE_READ_MAX_BYTES, result_char_allowance * 4)
    for raw_line in stdout.splitlines():
        output_bytes += len(raw_line.encode("utf-8"))
        if output_bytes > output_byte_limit:
            raise ContextProviderError(
                "context_tool_limit_exceeded",
                "Repository context request exceeded a content-tool limit.",
            )
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ContextProviderError(
                "context_tool_invalid_output",
                "Repository context tool produced invalid output.",
            ) from exc
        if payload.get("type") != "match":
            continue
        data = payload.get("data", {})
        path_value = data.get("path", {}).get("text")
        line_number = data.get("line_number")
        line_text = data.get("lines", {}).get("text")
        if (
            not isinstance(path_value, str)
            or not isinstance(line_number, int)
            or not isinstance(line_text, str)
        ):
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
            return {"observations": observations, "truncated": True}
        observations.append(observation)
        returned_chars = next_chars
        if len(observations) >= max_matches:
            return {"observations": observations, "truncated": True}
    if getattr(process, "returncode", 0) not in {0, 1}:
        raise ContextProviderError(
            "context_tool_failed",
            "Repository context tool failed.",
        )
    return {"observations": observations, "truncated": False}


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


def _validate_search_result_path(path: str) -> str:
    try:
        canonical = canonical_relative_path(path)
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
