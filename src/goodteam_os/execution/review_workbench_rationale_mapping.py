from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXCERPT_LIMIT = 800


def map_changed_files_to_rationale(
    project_root: Path,
    diff: dict[str, Any],
    rationale: dict[str, Any],
) -> dict[str, Any]:
    files = diff.get("changed_files")
    if not isinstance(files, list):
        return dict(diff)

    sources = _rationale_sources(Path(project_root), rationale)
    rationale_available = bool(rationale.get("available"))
    mapped_files = []
    for file in files:
        if not isinstance(file, dict):
            continue
        mapped = dict(file)
        path = str(mapped.get("path", "")).strip()
        refs = _refs_for_path(path, sources) if path and rationale_available else []
        mapped["rationale_refs"] = refs
        mapped["rationale_status"] = _status(refs, rationale_available)
        mapped_files.append(mapped)

    return {**diff, "changed_files": mapped_files}


def _rationale_sources(project_root: Path, rationale: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    worker_result = _dict(rationale.get("worker_result"))
    worker_text = _source_text(worker_result)
    if worker_text:
        sources.append(
            {
                "kind": "worker_result",
                "title": "Worker result",
                "path": _display_path(project_root, worker_result.get("path")),
                "text": worker_text,
            }
        )

    for snapshot in _dict_items(rationale.get("prompt_snapshots")):
        name = str(snapshot.get("name", "")).strip()
        text = _source_text(snapshot)
        if not text:
            continue
        sources.append(
            {
                "kind": "prompt_snapshot",
                "name": name,
                "title": f"Prompt snapshot: {name or 'snapshot'}",
                "path": _display_path(project_root, snapshot.get("path")),
                "text": text,
            }
        )

    task_text = _json_text(rationale.get("task"))
    if task_text:
        sources.append(
            {
                "kind": "task_context",
                "title": "Task context",
                "path": "review_workbench.rationale.task",
                "text": task_text,
            }
        )
    run_text = _json_text(rationale.get("run"))
    if run_text:
        sources.append(
            {
                "kind": "run_context",
                "title": "Run context",
                "path": "review_workbench.rationale.run",
                "text": run_text,
            }
        )
    return sources


def _refs_for_path(path: str, sources: list[dict[str, str]]) -> list[dict[str, str]]:
    refs = []
    for source in sources:
        excerpt = _excerpt_for_path(source["text"], path)
        if not excerpt:
            continue
        ref = {
            "kind": source["kind"],
            "title": source["title"],
            "path": source["path"],
            "excerpt": excerpt,
            "confidence": "direct",
        }
        name = source.get("name", "")
        if name:
            ref["name"] = name
        refs.append(ref)
    return refs


def _status(refs: list[dict[str, str]], rationale_available: bool) -> str:
    if refs:
        return "mapped"
    if not rationale_available:
        return "rationale_artifact_missing"
    return "not_mapped"


def _source_text(source: dict[str, Any]) -> str:
    path = str(source.get("path", "")).strip()
    if path:
        try:
            candidate = Path(path)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
    return str(source.get("excerpt") or source.get("summary") or "")


def _json_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _excerpt_for_path(text: str, path: str) -> str:
    normalized = " ".join(str(text).split())
    index = normalized.find(path)
    if index < 0:
        return ""
    start = max(0, index - 160)
    end = min(len(normalized), start + EXCERPT_LIMIT)
    if end - start < EXCERPT_LIMIT:
        start = max(0, end - EXCERPT_LIMIT)
    return normalized[start:end]


def _display_path(project_root: Path, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return text


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
