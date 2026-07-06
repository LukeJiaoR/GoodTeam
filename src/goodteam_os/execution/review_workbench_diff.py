from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.pr_diff_contract import artifact_json_path


_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$")
_HUNK_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")


def diff_surface(project_root: Path, execution_id: str, events: list[LedgerEvent]) -> dict[str, Any]:
    path = artifact_json_path(project_root, execution_id)
    if path.is_file():
        return _diff_from_artifact(project_root, path)

    artifact = _diff_artifact(project_root, events)
    if artifact is None:
        return {
            "available": False,
            "reason": "diff_artifact_missing",
            "changed_files": [],
            "artifact_path": "",
            "summary": "",
        }
    path, payload = artifact
    return _diff_from_payload(project_root, path, payload)


def _diff_from_artifact(project_root: Path, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _diff_from_payload(project_root, path, payload if isinstance(payload, dict) else {})


def _diff_from_payload(project_root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    changed_files = _changed_files(payload.get("changed_files"))
    patch_path = _patch_path(project_root, payload)
    patch_state, parsed_files = _patch_projection(Path(patch_path) if patch_path else None)
    return {
        "available": True,
        "reason": "",
        "changed_files": _merge_changed_files(changed_files, parsed_files),
        "artifact_path": str(path),
        "patch_path": patch_path,
        "patch_state": patch_state,
        "summary": _diff_summary(changed_files),
    }


def _diff_artifact(
    project_root: Path,
    events: list[LedgerEvent],
) -> tuple[Path, dict[str, Any]] | None:
    for event in reversed(events):
        payload = event.payload
        for key in ("artifact_path", "diff_artifact_path", "diff_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                path = _resolve_path(project_root, value)
                if path.is_file():
                    return path, _artifact_payload(path, payload)
    return None


def _artifact_payload(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(fallback)
    return dict(payload) if isinstance(payload, dict) else dict(fallback)


def _patch_path(project_root: Path, payload: dict[str, Any]) -> str:
    value = payload.get("patch_path")
    if not isinstance(value, str) or not value.strip():
        return ""
    path = _resolve_path(project_root, value)
    return str(path) if path.is_file() else ""


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root / path


def _diff_summary(changed_files: Any) -> str:
    if not isinstance(changed_files, list):
        return "0 files changed (+0/-0)"
    files = [file for file in changed_files if isinstance(file, dict)]
    additions = sum(_int(file.get("additions")) for file in files)
    deletions = sum(_int(file.get("deletions")) for file in files)
    return f"{len(changed_files)} files changed (+{additions}/-{deletions})"


def parse_unified_diff(patch: str) -> list[dict[str, Any]]:
    files, malformed = _parse_unified_diff_result(patch)
    return [] if malformed else files


def _parse_unified_diff_result(patch: str) -> tuple[list[dict[str, Any]], bool]:
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None
    old_line = 0
    new_line = 0
    malformed = False

    for raw_line in patch.splitlines():
        diff_match = _DIFF_GIT_RE.match(raw_line)
        if diff_match:
            current_file = _parsed_file(diff_match.group(1), diff_match.group(2))
            files.append(current_file)
            current_hunk = None
            continue
        if current_file is None:
            malformed = malformed or raw_line.strip().startswith(("@@", "+", "-"))
            continue
        if raw_line.startswith("--- "):
            current_file["old_path"] = _header_path(raw_line[4:])
            continue
        if raw_line.startswith("+++ "):
            current_file["new_path"] = _header_path(raw_line[4:])
            current_file["path"] = current_file["new_path"] or current_file["old_path"]
            continue
        hunk_match = _HUNK_RE.match(raw_line)
        if raw_line.startswith("@@"):
            if hunk_match is None:
                malformed = True
                current_hunk = None
                continue
            old_line = int(hunk_match.group("old"))
            new_line = int(hunk_match.group("new"))
            current_hunk = {
                "header": raw_line,
                "old_start": old_line,
                "new_start": new_line,
                "lines": [],
            }
            current_file["hunks"].append(current_hunk)
            continue
        if current_hunk is None:
            continue
        line, old_line, new_line = _parsed_hunk_line(raw_line, old_line, new_line)
        current_hunk["lines"].append(line)

    return files, malformed or not files


def _parsed_file(old_path: str, new_path: str) -> dict[str, Any]:
    path = new_path if new_path != "/dev/null" else old_path
    return {
        "path": path,
        "old_path": old_path,
        "new_path": new_path,
        "hunks": [],
    }


def _header_path(value: str) -> str:
    path = value.strip().split("\t", 1)[0]
    if path == "/dev/null":
        return ""
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[2:]
    return path


def _parsed_hunk_line(raw_line: str, old_line: int, new_line: int) -> tuple[dict[str, Any], int, int]:
    if raw_line.startswith("+"):
        return _line("add", None, new_line, raw_line), old_line, new_line + 1
    if raw_line.startswith("-"):
        return _line("delete", old_line, None, raw_line), old_line + 1, new_line
    if raw_line.startswith(" "):
        line = _line("context", old_line, new_line, raw_line)
        return line, old_line + 1, new_line + 1
    return _line("metadata", None, None, raw_line), old_line, new_line


def _line(kind: str, old_line: int | None, new_line: int | None, text: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "old_line": old_line,
        "new_line": new_line,
        "text": text,
    }


def _patch_projection(patch_path: Path | None) -> tuple[str, list[dict[str, Any]]]:
    if patch_path is None or not patch_path.is_file():
        return "missing", []
    files, malformed = _parse_unified_diff_result(patch_path.read_text(encoding="utf-8"))
    if malformed:
        return "parse_failed", []
    return "available", files


def _merge_changed_files(
    changed_files: list[dict[str, Any]],
    parsed_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parsed_by_path = {str(file.get("path", "")): file for file in parsed_files}
    merged = []
    for file in changed_files:
        path = str(file.get("path", ""))
        parsed = parsed_by_path.get(path, {})
        merged.append(
            {
                **file,
                "anchor": _file_anchor(path),
                "hunks": list(parsed.get("hunks", [])) if isinstance(parsed, dict) else [],
                "rationale_refs": [],
                "rationale_status": "not_mapped",
            }
        )
    return merged


def _changed_files(value: Any) -> list[dict[str, Any]]:
    return [dict(file) for file in value if isinstance(file, dict)] if isinstance(value, list) else []


def _file_anchor(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return f"file-{slug or 'changed-file'}"


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
