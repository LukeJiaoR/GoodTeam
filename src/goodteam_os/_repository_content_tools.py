from __future__ import annotations

import json
import subprocess

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
from goodteam_os.repository_access import AnchoredRepository
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
            path=path,
            query=query,
            max_matches=max_matches,
            result_char_allowance=result_char_allowance,
        )
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        entries = repository.walk(path)
        observations: list[dict[str, object]] = []
        returned_chars = 0
        truncated = False
        for entry in entries:
            if entry.kind != "file":
                continue
            text = repository.read_text(entry.path, max_bytes=FILE_READ_MAX_BYTES)
            for line_number, line in enumerate(text.text.splitlines(), start=1):
                if query not in line:
                    continue
                redacted_line, _, _ = redact_observation_text(line)
                observation = {
                    "kind": "search_match",
                    "path": entry.path,
                    "line": line_number,
                    "text": redacted_line,
                    "content_hash": hash_bytes(line.encode("utf-8")),
                }
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
    return path, depth, max_entries


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
    return path, start_line, line_count


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
    return path, query, mode, max_matches


def _search_text_regex(
    *,
    root,
    path: str,
    query: str,
    max_matches: int,
    result_char_allowance: int,
) -> dict[str, object]:
    target = path or "."
    try:
        process = _spawn_rg_process(root=root, query=query, target=target)
    except FileNotFoundError as exc:
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        ) from exc

    try:
        stdout, _stderr = process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=1.0)
        raise ContextProviderError(
            "regex_search_timeout",
            "Repository context regex search timed out.",
        ) from exc

    if process.returncode not in {0, 1}:
        raise ContextProviderError(
            "regex_search_failed",
            "Repository context regex search failed.",
        )

    observations: list[dict[str, object]] = []
    returned_chars = 0
    for raw_line in stdout.splitlines():
        payload = json.loads(raw_line)
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
            continue
        redacted_text, _, _ = redact_observation_text(line_text.rstrip("\n"))
        observation = {
            "kind": "search_match",
            "path": path_value,
            "line": line_number,
            "text": redacted_text,
            "content_hash": hash_bytes(line_text.encode("utf-8")),
        }
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
    return {"observations": observations, "truncated": False}


def _spawn_rg_process(*, root, query: str, target: str):
    return subprocess.Popen(
        [
            "rg",
            "--json",
            "--no-follow",
            "--color",
            "never",
            "--glob",
            "!node_modules/**",
            "--glob",
            "!dist/**",
            "--glob",
            "!build/**",
            "--glob",
            "!coverage/**",
            query,
            target,
        ],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
