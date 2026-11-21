"""[INPUT]: 任务助手会话、任务批次与 Worker 尝试。
[OUTPUT]: 生成 Team 工作区视图。
[POS]: 项目会话和批次活动的只读投影。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from __future__ import annotations

from goodteam_os.assistant_workspace import is_assistant_activity
from goodteam_os.conversation_projection import (
    assistant_timeline as _task_assistant_timeline,
)

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any



TEAM_WORKSPACE_SCHEMA_VERSION = "team_workspace.v1"
_WORKER_STATES = {
    "queued": "waiting",
    "ready": "waiting",
    "pending": "waiting",
    "needs_review": "waiting",
    "running": "working",
    "in_progress": "working",
    "completed": "completed",
    "ok": "completed",
    "succeeded": "completed",
    "blocked": "blocked",
    "failed": "blocked",
    "error": "blocked",
    "possibly_stuck": "blocked",
    "aborted": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "skipped": "stopped",
}
_REVIEW_ROLES = {"review", "reviewer", "code_review", "qa", "security"}
_EXECUTION_STATE_PRIORITY = {
    "blocked": 0,
    "reviewing": 1,
    "fixing": 2,
    "working": 3,
    "waiting": 4,
    "up_next": 5,
    "completed": 6,
    "stopped": 7,
}
def empty_team_workspace() -> dict[str, Any]:
    return {
        "schema_version": TEAM_WORKSPACE_SCHEMA_VERSION,
        "mission": None,
        "manager_timeline": [],
        "attention_request": None,
        "execution_actors": [],
        "outcome_tasks": [],
        "capabilities": [],
    }


def build_team_workspace(
    session: Mapping[str, object] | None, *, project_root: Path | None = None,
    project_key: str = "", worker_jobs: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    if session is None:
        return empty_team_workspace()
    timeline = _task_assistant_timeline(session)
    mission, actors = _task_assistant_execution(session, project_root, project_key, worker_jobs)
    return {
        "schema_version": TEAM_WORKSPACE_SCHEMA_VERSION,
        "mission": mission,
        "manager_timeline": timeline,
        "attention_request": session.get("attention_request"),
        "execution_actors": actors,
        "outcome_tasks": [],
        "capabilities": [],
        "suggestions": list(session.get("suggestions", []) or []),
        "proposed_task": session.get("proposed_task"),
        "assistant_requests": session.get("assistant_requests", {}),
        "runtime": session.get("runtime"),
        "pending_team_decision": session.get("pending_team_decision"),
    }


def _task_assistant_execution(session, project_root, project_key, worker_jobs):
    """Read confirmed batch identities and existing Jobs; never advance execution."""
    if project_root is None or not project_key:
        return None, []
    root = str(project_root.resolve())
    batches = [batch for batch in _mapping(session.get("project_task_batches")).values()
               if batch.get("confirmed") is True and batch.get("project_root") == root]
    batches.sort(key=lambda batch: _text(batch.get("created_at")))
    continuation = _mapping(session.get("project_continuation"))
    if _mapping(continuation.get("authority")).get("project_root") != root:
        continuation = {}
    actors = []
    for batch in batches:
        dispatch_ids = {batch.get(key) for key in ("dispatch_run_id", "current_dispatch_run_id") if batch.get(key)}
        dispatch_ids.update(command["dispatch_run_id"]
                            for command in _mapping(batch.get("recovery_commands")).values()
                            if command.get("dispatch_run_id"))
        jobs_by_task = {}
        for job in worker_jobs:
            task_id = _text(job.get("project_task_id"))
            if (job.get("project_key") == project_key and job.get("run_id") in dispatch_ids
                    and task_id in batch.get("task_ids", []) and not is_assistant_activity(job)):
                jobs_by_task.setdefault(task_id, []).append(job)
        outcomes = []
        for task_id, jobs in jobs_by_task.items():
            current = _authoritative_worker_attempt(jobs)
            outcomes.append({"id": task_id, "owner": current.get("role"),
                             "objective": task_id, "status": _worker_attempt_state(current, fallback="waiting"),
                             "updated_at": _updated_at(current)})
        batch_actors = _task_actors(outcomes, jobs_by_task)
        # Keep previous in-flight batches visible after Stop or a newer reservation.
        actors.extend(batch_actors if batch is batches[-1] else
                      [actor for actor in batch_actors if actor["state"] in {"working", "reviewing", "fixing"}])
    if not batches and not continuation:
        return None, []
    latest = batches[-1] if batches else {}
    live_states = {actor["state"] for actor in actors} & {"working", "reviewing", "fixing"}
    if live_states:
        status = next(state for state in ("working", "fixing", "reviewing") if state in live_states)
    elif continuation.get("status") in {"stopped", "completed", "blocked", "exhausted"}:
        status = {"exhausted": "waiting"}.get(continuation["status"], continuation["status"])
    elif latest.get("status") in {"blocked", "review", "delivered", "abandoned"}:
        status = {"review": "reviewing", "delivered": "completed", "abandoned": "stopped"}.get(
            latest["status"], latest["status"])
    else:
        status = "waiting"  # A grant/reservation alone is not a running Worker.
    objective = _text(_mapping(continuation.get("discovery")).get("objective"))
    title = objective or ", ".join(latest.get("task_ids", []))
    return {
        "id": continuation.get("id") or latest.get("run_id"),
        "run_id": latest.get("run_id", ""), "execution_id": latest.get("dispatch_run_id", ""),
        "project_key": project_key, "source": "project_batch", "title": _mission_title(title),
        "objective": objective or title, "status": status, "read_only": False,
        "started_at": continuation.get("created_at") or latest.get("created_at", ""),
        "updated_at": latest.get("updated_at", ""),
    }, actors


def _task_actors(
    outcomes: Sequence[Mapping[str, object]],
    worker_jobs_by_task: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[dict[str, Any]]:
    """Project one current actor per logical task assignment.

    A worker job is an attempt, never the primary row identity. Task ids produce
    stable actor ids; the latest durable attempt only supplies current state and
    attempt metadata. Completed and stopped assignments remain visible as
    lower-priority context, while actionable states sort ahead of them.
    """
    actors: list[dict[str, Any]] = []
    stable_order: dict[str, int] = {}
    outcomes_by_id = {_text(item.get("id")): item for item in outcomes}
    for task_order, task in enumerate(outcomes):
        task_id = _text(task.get("id"))
        task_jobs = list(worker_jobs_by_task.get(task_id, ()))
        current_attempt = _authoritative_worker_attempt(task_jobs)
        role = _text(task.get("owner"))
        if not role and current_attempt is not None:
            role = _text(current_attempt.get("role"))
        if not role:
            continue
        state = _text(task.get("status")) or "up_next"
        actor_id = f"task-assignment:{task_id}"
        stable_order[actor_id] = task_order
        unmet_dependencies = [
            dependency_id
            for dependency_id in (_text(item) for item in _list(task.get("unmet_dependencies")))
            if dependency_id
        ]
        dependency_task_id = unmet_dependencies[0] if len(unmet_dependencies) == 1 else ""
        dependency_task_title = (
            _text(outcomes_by_id[dependency_task_id].get("objective"))
            if dependency_task_id in outcomes_by_id
            else ""
        )
        actors.append(
            {
                "id": actor_id,
                "role": role,
                "state": state,
                "responsibility": _text(task.get("objective")),
                "task_id": task_id,
                "dependency_reason": _text(task.get("status_reason")) if state == "waiting" else "",
                "dependency_task_id": dependency_task_id if state == "waiting" else "",
                "dependency_task_title": dependency_task_title if state == "waiting" else "",
                "status_reason": _text(task.get("status_reason")),
                "attempt": (
                    _text(current_attempt.get("retry_attempt") or current_attempt.get("attempt"))
                    if current_attempt
                    else ""
                ),
                "attempt_count": len(_execution_attempts(task_jobs)),
                "queued_count": sum(_text(job.get("status")) == "queued" for job in task_jobs),
                "current_attempt_id": _text(current_attempt.get("id")) if current_attempt else "",
                "source": "worker_job" if current_attempt else "task_role",
                "updated_at": _text(task.get("updated_at")),
            }
        )

    return _order_execution_actors(actors, stable_order)


def _order_execution_actors(
    actors: Sequence[Mapping[str, object]],
    stable_order: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Order the live-team projection by urgency without refresh jitter.

    State priority is authoritative for presentation. Within a state, durable
    task order wins, followed by timestamp and actor id. Every client
    therefore receives the same deterministic Execution order.
    """
    fallback_order = len(stable_order)
    return [
        dict(actor)
        for actor in sorted(
            actors,
            key=lambda actor: (
                _EXECUTION_STATE_PRIORITY.get(
                    _text(actor.get("state")),
                    len(_EXECUTION_STATE_PRIORITY),
                ),
                stable_order.get(_text(actor.get("id")), fallback_order),
                _text(actor.get("updated_at")),
                _text(actor.get("id")),
            ),
        )
    ]


def _worker_attempt_state(job: Mapping[str, object], *, fallback: str) -> str:
    state = _actor_state(job, fallback=fallback)
    if state == "working" and _is_retry_attempt(job):
        return "fixing"
    if state == "working" and _is_review_role(_text(job.get("role"))):
        return "reviewing"
    return state


def _is_retry_attempt(job: Mapping[str, object]) -> bool:
    return bool(
        _text(job.get("retry_attempt"))
        or _text(job.get("original_failed_job_id"))
        or _text(job.get("retry_of_idempotency_key"))
        or _integer(job.get("attempt")) > 1
    )


def _execution_attempts(jobs):
    """Exclude queued selections from logical Worker attempts, not provider requests."""
    return [job for job in jobs if _text(job.get("status")) not in {"queued", "ready", "pending"}]


def _authoritative_worker_attempt(
    worker_jobs: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    if not worker_jobs:
        return None
    return max(
        worker_jobs,
        key=lambda job: (
            _updated_at(job),
            _integer(job.get("retry_attempt") or job.get("attempt")),
            _text(job.get("id")),
        ),
    )


def _actor_state(job: Mapping[str, object], fallback: str = "waiting") -> str:
    return _WORKER_STATES.get(_text(job.get("status")).lower(), fallback)


def _is_review_role(role: str) -> bool:
    lowered = role.lower()
    return lowered in _REVIEW_ROLES or "review" in lowered


def _task_status_reason(status: str, unmet_dependencies: Sequence[str]) -> str:
    if status == "waiting" and unmet_dependencies:
        return "waiting_for_dependencies"
    return {
        "up_next": "not_started",
        "working": "worker_running",
        "reviewing": "awaiting_review",
        "fixing": "fix_in_progress",
        "blocked": "execution_blocked",
        "completed": "delivered",
        "stopped": "stopped",
    }.get(status, "not_started")


def _mission_title(objective: str) -> str:
    first = next((line.strip() for line in objective.splitlines() if line.strip()), "")
    return first[:120]


def _updated_at(item: Mapping[str, object]) -> str:
    return _text(item.get("updated_at")) or _text(item.get("timestamp")) or _text(item.get("created_at"))


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: object) -> str:
    return str(value or "").strip()
