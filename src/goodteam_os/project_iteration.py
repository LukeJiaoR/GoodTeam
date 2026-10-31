"""Read-only, evidence-grounded starting point for project iteration planning.

This is a projection of the existing project task ledger, not a new scheduler.
Workflow stages and work units managed by a confirmed Goal are deliberately
excluded: they are execution records, not independently prioritizable ideas.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_PRIORITY = {"critical": 0, "urgent": 0, "p0": 0, "high": 1, "p1": 1,
             "medium": 2, "normal": 2, "p2": 2, "low": 3, "p3": 3}
_INTERNAL_REFS = ("stage:", "goal:")
_ACTIVE_STATUSES = {"todo", "blocked", "review", "in_progress"}


def _text(value: object, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _items(value: object, maximum: int, width: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item, width) for item in value[:maximum] if _text(item, width)]


def _internal(task: dict[str, Any]) -> bool:
    refs = task.get("external_refs", [])
    return isinstance(refs, list) and any(
        isinstance(ref, str) and ref.startswith(_INTERNAL_REFS) for ref in refs
    )


def rank_project_backlog(tasks: list[dict[str, Any]], project_key: str, *, limit: int = 8) -> dict[str, Any]:
    """Rank real backlog entries without inventing work or authorizing execution.

    Eligible means *candidate for planning*, not ready to run. The Manager still
    needs to inspect source evidence and resolve missing acceptance criteria.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Backlog candidate limit must be between 1 and 20.")
    if not isinstance(tasks, list):
        raise ValueError("Project backlog must be a task list.")
    relevant = [task for task in tasks if isinstance(task, dict) and not _internal(task)]
    known = {str(task.get("id", "")): task for task in tasks if isinstance(task, dict)}
    fingerprint = [
        {key: task.get(key) for key in (
            "id", "title", "description", "status", "priority", "role",
            "dependencies", "acceptance_criteria", "external_refs",
        )}
        for task in sorted((row for row in tasks if isinstance(row, dict)), key=lambda row: str(row.get("id", "")))
    ]
    version = "sha256:" + hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    candidates: list[dict[str, Any]] = []
    for task in relevant:
        status = str(task.get("status", ""))
        if status not in _ACTIVE_STATUSES:
            continue
        dependencies = _items(task.get("dependencies"), 64, 120)
        blockers = [dep for dep in dependencies if known.get(dep, {}).get("status") != "done"]
        eligible = status == "todo" and not blockers
        reason = ("ready_to_plan" if eligible else "blocked_by_dependencies" if blockers
                  else "blocked" if status == "blocked" else "already_active" if status == "in_progress"
                  else "awaiting_review")
        candidates.append({
            "task_id": _text(task.get("id"), 120),
            "title": _text(task.get("title"), 180),
            "description": _text(task.get("description"), 1200),
            "role": _text(task.get("role"), 100),
            "priority": _text(task.get("priority"), 32) or "medium",
            "status": status,
            "dependencies": dependencies,
            "blocked_by": blockers,
            "acceptance_criteria": _items(task.get("acceptance_criteria"), 8, 400),
            "eligible_to_plan": eligible,
            "reason": reason,
        })
    candidates.sort(key=lambda item: (
        not item["eligible_to_plan"],
        _PRIORITY.get(item["priority"].lower(), 4),
        item["task_id"],
    ))
    ready = next((item for item in candidates if item["eligible_to_plan"]), None)
    return {
        "schema_version": "project_iteration_backlog.v1",
        "project_key": project_key,
        "version": version,
        "status": "candidate_ready" if ready else "blocked" if candidates else "empty",
        "next_candidate": ready,
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "truncated": len(candidates) > limit,
        "source": "local_task_ledger",
        "execution_authorized": False,
    }


def project_iteration_backlog(project_root: Path | None, project_key: str, *, limit: int = 8) -> dict[str, Any]:
    """Use the existing durable ledger; never hide corruption as an empty plan."""
    if project_root is None:
        return {"schema_version": "project_iteration_backlog.v1", "project_key": project_key,
                "status": "unavailable", "reason": "project_root_unavailable",
                "next_candidate": None, "candidates": [], "execution_authorized": False}
    from goodteam_os.tasks import LocalTaskStore, TaskStoreCorruptError

    try:
        tasks = LocalTaskStore(Path(project_root), project_key).list_tasks()
    except (TaskStoreCorruptError, OSError):
        return {"schema_version": "project_iteration_backlog.v1", "project_key": project_key,
                "status": "unavailable", "reason": "task_ledger_unreadable",
                "next_candidate": None, "candidates": [], "execution_authorized": False}
    return rank_project_backlog(tasks, project_key, limit=limit)
