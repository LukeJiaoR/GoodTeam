from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.pr_diff_contract import artifact_json_path


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
    changed_files = payload.get("changed_files")
    return {
        "available": True,
        "reason": "",
        "changed_files": changed_files if isinstance(changed_files, list) else [],
        "artifact_path": str(path),
        "patch_path": _patch_path(project_root, payload),
        "summary": _diff_summary(changed_files),
    }


def _diff_from_artifact(project_root: Path, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed_files = payload.get("changed_files")
    if not isinstance(changed_files, list):
        changed_files = []
    return {
        "available": True,
        "reason": "",
        "changed_files": changed_files,
        "artifact_path": str(path),
        "patch_path": _patch_path(project_root, payload),
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


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
