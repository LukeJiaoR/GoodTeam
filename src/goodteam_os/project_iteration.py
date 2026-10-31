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


def normalize_write_domains(value: object) -> list[str] | None:
    """Validate declared project-relative write scopes; never infer safe scopes.

    Paths are advisory planning metadata, not an OS sandbox. Real dispatch
    still needs its own worktree isolation, file collision and fan-in checks.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        return None
    domains: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item or item != item.strip() or len(item) > 160:
            return None
        if item.startswith("/") or "\\" in item or ":" in item or any(ord(ch) < 32 for ch in item):
            return None
        if any(character in item for character in "*?[]"):
            return None
        segments = item.removesuffix("/").split("/")
        if not segments or any(segment in {"", ".", ".."} for segment in segments):
            return None
        normalized = "/".join(segments)
        if normalized.casefold() in seen:
            return None
        seen.add(normalized.casefold())
        domains.append(normalized)
    return domains


def _domains_overlap(left: str, right: str) -> bool:
    a, b = left.casefold(), right.casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _validate_parallel_group(tasks: list[dict[str, Any]]) -> None:
    """Fail closed unless every selected writer has explicit disjoint scopes."""
    owners: list[tuple[str, str]] = []
    for task in tasks:
        if task["execution_policy"] == "read_only":
            continue
        if task["write_domain_status"] != "declared":
            raise ValueError("Parallel writing needs explicit write domains for each selected task.")
        for domain in task["write_domains"]:
            for other_task, other_domain in owners:
                if _domains_overlap(domain, other_domain):
                    raise ValueError(
                        f"Parallel write-domain collision between {other_task} and {task['task_id']}."
                    )
            owners.append((task["task_id"], domain))


def rank_project_backlog(tasks: list[dict[str, Any]], project_key: str, *, limit: int | None = 8) -> dict[str, Any]:
    """Rank real backlog entries without inventing work or authorizing execution.

    Eligible means *candidate for planning*, not ready to run. The Manager still
    needs to inspect source evidence and resolve missing acceptance criteria.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Backlog candidate view limit must be a positive integer or None.")
    if not isinstance(tasks, list):
        raise ValueError("Project backlog must be a task list.")
    relevant = [task for task in tasks if isinstance(task, dict) and not _internal(task)]
    known = {str(task.get("id", "")): task for task in tasks if isinstance(task, dict)}
    # Workflow-stage and Goal-owned cards are not project backlog decisions.
    # Only include their *status* when a genuine project task depends on them.
    dependency_ids = {dep for task in relevant for dep in _items(task.get("dependencies"), 64, 120)}
    fingerprint = {
        "tasks": [
            {key: task.get(key) for key in (
                "id", "title", "description", "status", "priority", "role",
                "dependencies", "acceptance_criteria", "external_refs",
                "artifact_refs", "source_evidence_refs", "review_gaps", "updated_at",
                "write_domains", "execution_policy",
            )}
            for task in sorted(relevant, key=lambda row: str(row.get("id", "")))
        ],
        "dependencies": {dep: known.get(dep, {}).get("status") for dep in sorted(dependency_ids)},
    }
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
        domains = normalize_write_domains(task.get("write_domains"))
        policy = task.get("execution_policy")
        policy = "read_only" if policy == "read_only" else "workspace_write"
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
            "source_refs": _items(task.get("external_refs"), 12, 256),
            "artifact_refs": _items(task.get("artifact_refs"), 12, 256),
            "review_gaps": _items(task.get("review_gaps"), 6, 300),
            "execution_policy": policy,
            "write_domains": domains or [],
            "write_domain_status": "declared" if domains else "unknown",
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
        "candidates": candidates if limit is None else candidates[:limit],
        "candidate_count": len(candidates),
        "truncated": limit is not None and len(candidates) > limit,
        "source": "local_task_ledger",
        "execution_authorized": False,
    }


def project_iteration_backlog(project_root: Path | None, project_key: str, *, limit: int | None = 8) -> dict[str, Any]:
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


def validate_iteration_choice(snapshot: dict[str, Any], response: object) -> dict[str, Any]:
    """Bind a model's advisory choice to the exact eligible candidate inventory.

    A task description and a model response are untrusted data. This result
    carries no authority to create tasks, dispatch Workers or change the Goal.
    """
    if snapshot.get("schema_version") != "project_iteration_backlog.v1" or snapshot.get("status") != "candidate_ready":
        raise ValueError("No valid project backlog is available for planning.")
    if not isinstance(response, dict):
        raise ValueError("Manager choice must be a JSON object.")
    selected = response.get("selected_task_id")
    selected_ids = response.get("selected_task_ids")
    if selected_ids is None:
        selected_ids = [selected]
    if (not isinstance(selected_ids, list) or not selected_ids
            or any(not isinstance(item, str) or not item or len(item) > 120 for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)):
        raise ValueError("Manager must select distinct eligible candidate IDs.")
    if selected is not None and selected != selected_ids[0]:
        raise ValueError("Manager primary task ID differs from the selected batch.")
    selected = selected_ids[0]
    reason = response.get("rationale")
    compared = response.get("compared_task_ids")
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 1600:
        raise ValueError("Manager must explain the project-level priority choice.")
    if (not isinstance(compared, list) or not compared
            or any(not isinstance(item, str) for item in compared)
            or len(compared) != len(set(compared))
            or any(item not in compared for item in selected_ids)):
        raise ValueError("Manager must name the candidate IDs it compared.")
    candidates = {item["task_id"]: item for item in snapshot.get("candidates", [])}
    if len(selected_ids) > len(candidates) or len(compared) > len(candidates):
        raise ValueError("Manager selection exceeds the supplied project candidate inventory.")
    if any(item not in candidates or not candidates[item]["eligible_to_plan"] for item in selected_ids):
        raise ValueError("Manager selected a missing, stale or blocked task.")
    if any(item not in candidates or not candidates[item]["eligible_to_plan"] for item in compared):
        raise ValueError("Manager compared an unavailable or blocked candidate.")
    if len(selected_ids) > 1:
        _validate_parallel_group([candidates[task_id] for task_id in selected_ids])
    return {
        "schema_version": "project_iteration_choice.v1",
        "project_key": snapshot["project_key"],
        "backlog_version": snapshot["version"],
        "selected_task_id": selected,
        "selected_title": candidates[selected]["title"],
        "selected_task_ids": list(selected_ids),
        "selected_tasks": [{"task_id": item, "title": candidates[item]["title"]} for item in selected_ids],
        "execution_mode": "parallel" if len(selected_ids) > 1 else "single",
        "parallel_execution_authorized": False,
        "requires_worktree_isolation": len(selected_ids) > 1,
        "fan_in_required": len(selected_ids) > 1,
        "compared_task_ids": list(compared),
        "rationale": reason.strip(),
        "status": "recommended",
        "execution_authorized": False,
        "source": "manager_on_backlog_snapshot",
    }


def latest_iteration_choice(session: dict[str, Any] | None, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Show a persisted choice only while its task-ledger revision is current."""
    for event in reversed((session or {}).get("timeline", [])):
        if not isinstance(event, dict) or event.get("event") != "project_iteration_choice_recorded":
            continue
        choice = (event.get("details") or {}).get("choice")
        if not isinstance(choice, dict) or choice.get("schema_version") != "project_iteration_choice.v1":
            continue
        if choice.get("backlog_version") != snapshot.get("version"):
            return {**choice, "status": "stale", "execution_authorized": False}
        return {**choice, "execution_authorized": False}
    return None
