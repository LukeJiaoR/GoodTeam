from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.state import AgileDevState


TEAM_WORKSPACE_SCHEMA_VERSION = "team_workspace.v1"

_TASK_STATES = {
    "todo": "up_next",
    "in_progress": "working",
    "review": "reviewing",
    "blocked": "blocked",
    "done": "completed",
    "canceled": "stopped",
}
_WORKER_STATES = {
    "queued": "waiting",
    "ready": "waiting",
    "running": "working",
    "completed": "completed",
    "ok": "completed",
    "failed": "blocked",
    "error": "blocked",
    "possibly_stuck": "blocked",
    "aborted": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "skipped": "stopped",
}
_OPERATOR_RUN_STATES = {
    "queued": "working",
    "running": "working",
    "succeeded": "completed",
    "failed": "blocked",
    "cancelled": "stopped",
    "canceled": "stopped",
}
_OPERATOR_PHASE_STATES = {
    "hydrating": "working",
    "preflight": "working",
    "executing": "working",
    "pr_ready": "working",
    "observing_ci": "reviewing",
    "review_ready": "reviewing",
    "fixing": "fixing",
    "waiting_telegram": "waiting",
    "approved": "working",
    "changes_requested": "fixing",
    "merging": "working",
    "paused": "blocked",
}
_OPERATOR_PHASE_ROLES = {
    "hydrating": "orchestrator",
    "preflight": "orchestrator",
    "executing": "implementation",
    "pr_ready": "release",
    "observing_ci": "qa",
    "review_ready": "qa",
    "fixing": "implementation",
    "waiting_telegram": "qa",
    "approved": "release",
    "changes_requested": "implementation",
    "merging": "release",
    "paused": "orchestrator",
}
_REVIEW_ROLES = {"review", "reviewer", "code_review", "qa", "security"}


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
    *,
    state: AgileDevState | None,
    workflow_active: bool,
    project_root: Path | None,
    project_key: str = "",
    operator_state_dir: Path | None = None,
    tasks: Sequence[Mapping[str, object]] = (),
    worker_jobs: Sequence[Mapping[str, object]] = (),
    approval_reviews: Sequence[Mapping[str, object]] = (),
    action_items: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    """Project durable records into the Manager-centered Work contract.

    The returned object is presentation data only. This function never writes.
    Workflow state owns workflow missions; the durable Operator queue owns
    Operator mission lifecycle, with the execution ledger joined as detail.
    """

    if state is not None and workflow_active:
        return _build_workflow_workspace(
            state=state,
            tasks=tasks,
            worker_jobs=worker_jobs,
            approval_reviews=approval_reviews,
            action_items=action_items,
        )
    if operator_state_dir is not None and project_key:
        queue_item = _active_operator_item(operator_state_dir, project_key)
        if queue_item is not None:
            task, run, events = _operator_execution_details(
                project_root,
                _text(queue_item.get("execution_id")),
            )
            return _build_operator_workspace(
                queue_item=queue_item,
                task=task,
                run=run,
                events=events,
            )
    return empty_team_workspace()


def _build_workflow_workspace(
    *,
    state: AgileDevState,
    tasks: Sequence[Mapping[str, object]],
    worker_jobs: Sequence[Mapping[str, object]],
    approval_reviews: Sequence[Mapping[str, object]],
    action_items: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    run_actions = [item for item in action_items if _text(item.get("run_id")) in {"", state.run_id}]
    run_jobs = [item for item in worker_jobs if _text(item.get("run_id")) in {"", state.run_id}]
    ordered_actions = sorted(run_actions, key=lambda item: _text(item.get("timestamp")))
    started_at = next(
        (_text(item.get("timestamp")) for item in ordered_actions if item.get("action") == "workflow_started"),
        "",
    )
    outcomes = _workflow_outcomes(tasks, run_jobs, approval_reviews)
    updated_at = _latest_timestamp(ordered_actions, run_jobs, outcomes)
    actors = _workflow_actors(outcomes, run_jobs)
    attention = _workflow_attention(state, outcomes, run_jobs, approval_reviews)
    mission_status = "waiting" if attention else "working"

    timeline = [
        {
            "id": f"{state.run_id}:intent",
            "actor": "user",
            "kind": "intent",
            "event": "mission_requested",
            "timestamp": started_at,
            "content": state.idea,
            "details": {},
            "refs": [],
        },
        {
            "id": f"{state.run_id}:manager-start",
            "actor": "manager",
            "kind": "progress",
            "event": "mission_organized",
            "timestamp": started_at,
            "content": "",
            "details": {"task_count": len(outcomes)},
            "refs": [item["id"] for item in outcomes],
        },
    ]
    timeline.extend(_workflow_timeline_items(state.run_id, ordered_actions))

    if attention:
        mission_status = "blocked" if attention["kind"] == "blocker" else "waiting"
        timeline.append(
            {
                "id": f"{state.run_id}:attention:{attention['id']}",
                "actor": "manager",
                "kind": "attention",
                "event": "attention_required",
                "timestamp": updated_at,
                "content": "",
                "details": {"attention_id": attention["id"], "attention_kind": attention["kind"]},
                "refs": list(attention.get("refs", [])),
            }
        )

    return {
        "schema_version": TEAM_WORKSPACE_SCHEMA_VERSION,
        "mission": {
            "id": state.run_id,
            "run_id": state.run_id,
            "execution_id": "",
            "project_key": state.project_key,
            "source": "workflow",
            "title": _mission_title(state.idea),
            "objective": state.idea,
            "status": mission_status,
            "started_at": started_at,
            "updated_at": updated_at,
        },
        "manager_timeline": _dedupe_timeline(timeline),
        "attention_request": attention,
        "execution_actors": actors,
        "outcome_tasks": outcomes,
        "capabilities": _workflow_capabilities(state, attention),
    }


def _workflow_outcomes(
    tasks: Sequence[Mapping[str, object]],
    worker_jobs: Sequence[Mapping[str, object]],
    approval_reviews: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    task_rows = [dict(task) for task in tasks]
    status_by_id = {_text(task.get("id")): _text(task.get("status")) for task in task_rows}
    outcomes: list[dict[str, Any]] = []
    for task in task_rows:
        task_id = _text(task.get("id"))
        role = _text(task.get("role"))
        raw_status = _text(task.get("status"))
        dependencies = [_text(item) for item in _list(task.get("dependencies")) if _text(item)]
        unmet = [item for item in dependencies if status_by_id.get(item) != "done"]
        status = _TASK_STATES.get(raw_status, "up_next")
        if status == "up_next" and unmet:
            status = "waiting"
        matching_jobs = [job for job in worker_jobs if _text(job.get("role")) == role]
        latest_job = _latest_item(matching_jobs)
        if latest_job and raw_status not in {"done", "canceled"}:
            status = _actor_state(latest_job, fallback=status)
        review = _matching_review(task, approval_reviews)
        if review and bool(review.get("is_actionable")):
            status = "reviewing"
        refs = [_text(item) for item in _list(task.get("artifact_refs")) if _text(item)]
        primary_outcome = _primary_artifact(refs)
        outcomes.append(
            {
                "id": task_id,
                "objective": _text(task.get("title")) or task_id,
                "description": _text(task.get("description")),
                "owner": role,
                "status": status,
                "status_reason": _task_status_reason(status, unmet),
                "dependencies": dependencies,
                "acceptance_criteria": [_text(item) for item in _list(task.get("acceptance_criteria"))],
                "attempt_count": len(matching_jobs),
                "review_state": _text(review.get("decision")) if review else "",
                "primary_outcome": primary_outcome,
                "supporting_refs": refs[1:] if primary_outcome else refs,
                "updated_at": _text(task.get("updated_at")),
            }
        )
    return outcomes


def _workflow_actors(
    outcomes: Sequence[Mapping[str, object]],
    worker_jobs: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    jobs_by_role: dict[str, list[Mapping[str, object]]] = {}
    for job in worker_jobs:
        jobs_by_role.setdefault(_text(job.get("role")) or "worker", []).append(job)

    for role, role_jobs in sorted(jobs_by_role.items()):
        for job in sorted(role_jobs, key=_updated_at):
            task = _latest_outcome_for_role(outcomes, role)
            state = _actor_state(job, fallback=_text(task.get("status")) if task else "waiting")
            if state == "working" and _is_review_role(role):
                state = "reviewing"
            if state == "working" and _text(job.get("retry_attempt")):
                state = "fixing"
            actors.append(
                {
                    "id": _text(job.get("id")) or f"role:{role}",
                    "role": role,
                    "state": state,
                    "responsibility": (
                        _text(task.get("objective")) if task else _text(job.get("summary"))
                    ),
                    "task_id": _text(task.get("id")) if task else "",
                    "dependency_reason": _text(task.get("status_reason")) if task and state == "waiting" else "",
                    "attempt": _text(job.get("retry_attempt")) or _text(job.get("attempt")),
                    "source": "worker_job",
                    "updated_at": _text(job.get("updated_at")),
                }
            )

    roles_with_jobs = set(jobs_by_role)
    for task in outcomes:
        role = _text(task.get("owner"))
        if not role or role in roles_with_jobs:
            continue
        actors.append(
            {
                "id": f"task-role:{task.get('id')}",
                "role": role,
                "state": _text(task.get("status")) or "up_next",
                "responsibility": _text(task.get("objective")),
                "task_id": _text(task.get("id")),
                "dependency_reason": _text(task.get("status_reason")) if task.get("status") == "waiting" else "",
                "attempt": "",
                "source": "task_role",
                "updated_at": _text(task.get("updated_at")),
            }
        )
    return actors


def _workflow_attention(
    state: AgileDevState,
    outcomes: Sequence[Mapping[str, object]],
    worker_jobs: Sequence[Mapping[str, object]],
    approval_reviews: Sequence[Mapping[str, object]],
) -> dict[str, Any] | None:
    actionable = next((item for item in approval_reviews if bool(item.get("is_actionable"))), None)
    if actionable is not None:
        gate = _text(actionable.get("approval"))
        return {
            "id": f"approval:{gate}",
            "kind": "approval",
            "target_id": gate,
            "reason": "approval_required",
            "refs": [gate],
            "actions": ["approve", "request_changes", "review"],
        }
    recovery = next(
        (item for item in approval_reviews if isinstance(item.get("recovery"), Mapping) and item["recovery"].get("available")),
        None,
    )
    if recovery is not None:
        gate = _text(recovery.get("approval")) or "takeover"
        return {
            "id": f"recovery:{gate}",
            "kind": "blocker",
            "target_id": gate,
            "reason": "review_recovery_required",
            "refs": [gate],
            "actions": ["review", "cancel_mission"],
        }
    blocked_tasks = [item for item in outcomes if item.get("status") == "blocked"]
    blocked_jobs = [item for item in worker_jobs if _actor_state(item) == "blocked"]
    if state.blockers or blocked_tasks or blocked_jobs:
        refs = [_text(item.get("id")) for item in blocked_tasks]
        refs.extend(_text(item.get("id")) for item in blocked_jobs)
        return {
            "id": "workflow-blocked",
            "kind": "blocker",
            "target_id": state.run_id,
            "reason": _text(state.blockers[0]) if state.blockers else "execution_blocked",
            "refs": [item for item in refs if item],
            "actions": ["retry", "cancel_mission"],
        }
    return None


def _workflow_capabilities(
    state: AgileDevState,
    attention: Mapping[str, object] | None,
) -> list[dict[str, str]]:
    capabilities = [
        {"action": "inspect_workflow", "target_id": state.run_id},
        {"action": "inspect_activity", "target_id": state.run_id},
        {"action": "inspect_delivery", "target_id": state.run_id},
        {"action": "cancel_mission", "target_id": state.run_id},
    ]
    for action in _list(attention.get("actions")) if attention else []:
        value = _text(action)
        if value in {item["action"] for item in capabilities}:
            continue
        capabilities.append({"action": value, "target_id": _text(attention.get("target_id"))})
    return capabilities


def _workflow_timeline_items(run_id: str, actions: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        action_name = _text(action.get("action"))
        if action_name == "workflow_started":
            continue
        event = _workflow_event_name(action_name)
        if not event:
            continue
        is_human = action_name == "approval_decided" or _text(action.get("actor")) == "human"
        kind = "decision" if action_name == "approval_decided" else "outcome" if action_name == "run_terminated" else "progress"
        items.append(
            {
                "id": _text(action.get("id")) or f"{run_id}:action:{index}:{action_name}",
                "actor": "user" if is_human else "manager",
                "kind": kind,
                "event": event,
                "timestamp": _text(action.get("timestamp")),
                "content": _text(action.get("note")),
                "details": {
                    "action": action_name,
                    "artifact": _text(action.get("artifact")),
                    "gate": _text(action.get("gate")),
                    "decision": _text(action.get("decision")),
                    "after_node": _text(action.get("after_node")),
                },
                "refs": [
                    value
                    for value in (_text(action.get("artifact_path")), _text(action.get("actor_ref")))
                    if value
                ],
            }
        )
    return items


def _workflow_event_name(action: str) -> str:
    if action == "approval_decided":
        return "approval_decided"
    if action == "run_terminated":
        return "mission_stopped"
    if action.endswith("_generated") or action.endswith("_regenerated"):
        return "deliverable_ready"
    if "retry" in action:
        return "work_retried"
    if any(token in action for token in ("failed", "blocked", "triage")):
        return "work_blocked"
    if any(token in action for token in ("completed", "dispatched", "worker_attempt")):
        return "work_progressed"
    return ""


def _active_operator_item(operator_state_dir: Path, project_key: str) -> dict[str, Any] | None:
    item = DurableOperatorQueue(operator_state_dir).active_item_snapshot()
    if item is None or _text(item.get("project_key")).upper() != project_key.strip().upper():
        return None
    return item


def _operator_execution_details(
    project_root: Path | None,
    execution_id: str,
) -> tuple[ExecutionTask | None, ExecutionRun | None, list[LedgerEvent]]:
    if project_root is None or not _safe_path_segment(execution_id):
        return None, None, []
    ledger = ExecutionLedger(project_root)
    try:
        task = ledger.read_task(execution_id)
    except (OSError, TypeError, ValueError):
        task = None
    try:
        run = ledger.read_run(execution_id)
    except (OSError, TypeError, ValueError):
        run = None
    try:
        events = ledger.read_events(execution_id)
    except (OSError, TypeError, ValueError):
        events = []
    return task, run, events


def _build_operator_workspace(
    *,
    queue_item: Mapping[str, object],
    task: ExecutionTask | None,
    run: ExecutionRun | None,
    events: Sequence[LedgerEvent],
) -> dict[str, Any]:
    metadata = _mapping(queue_item.get("metadata"))
    phase = _text(queue_item.get("phase")) or "executing"
    status = _OPERATOR_PHASE_STATES.get(phase, "working")
    execution_id = _text(queue_item.get("execution_id"))
    mission_id = _text(queue_item.get("id"))
    task_contract = _operator_task_contract(task, metadata)
    title = _operator_title(task, queue_item, metadata)
    objective = _operator_objective(task, task_contract, title)
    event_rows = sorted(events, key=lambda item: item.created_at)
    pr_event = next((event for event in reversed(event_rows) if event.payload.get("pr_number")), None)
    result_event = next(
        (event for event in reversed(event_rows) if event.payload.get("result_path") or event.payload.get("artifact_path")),
        None,
    )
    review_event = next((event for event in reversed(event_rows) if str(event.event_type) == "review_recorded"), None)
    primary = _operator_primary_outcome(queue_item, task, run, pr_event, result_event)
    attention = _operator_attention(queue_item)
    timeline = [
        {
            "id": f"{mission_id}:intent",
            "actor": "user",
            "kind": "intent",
            "event": "mission_requested",
            "timestamp": _text(queue_item.get("created_at")),
            "content": objective,
            "details": {
                "source_type": _text(queue_item.get("source_type")),
                "issue_number": metadata.get("issue_number"),
            },
            "refs": [_text(queue_item.get("source_url"))],
        },
        {
            "id": f"{mission_id}:manager-start",
            "actor": "manager",
            "kind": "progress",
            "event": "mission_organized",
            "timestamp": _text(queue_item.get("created_at")),
            "content": "",
            "details": {"task_count": 1},
            "refs": [task.task_id if task else mission_id],
        }
    ]
    timeline.extend(_operator_timeline(event_rows))
    timeline.append(_operator_phase_timeline_item(queue_item, primary))
    if attention:
        timeline.append(
            {
                "id": f"{mission_id}:attention:{attention['id']}",
                "actor": "manager",
                "kind": "attention",
                "event": "attention_required",
                "timestamp": _text(queue_item.get("updated_at")),
                "content": "",
                "details": {"attention_id": attention["id"], "attention_kind": attention["kind"]},
                "refs": list(attention.get("refs", [])),
            }
        )

    task_id = task.task_id if task else mission_id
    branch_name = run.branch_name if run else _text(metadata.get("branch_name"))
    return {
        "schema_version": TEAM_WORKSPACE_SCHEMA_VERSION,
        "mission": {
            "id": mission_id,
            "run_id": "",
            "execution_id": execution_id,
            "project_key": _text(queue_item.get("project_key")),
            "source": "operator_queue",
            "title": title,
            "objective": objective,
            "status": status,
            "phase": phase,
            "started_at": _text(queue_item.get("created_at")),
            "updated_at": _text(queue_item.get("updated_at")),
        },
        "manager_timeline": _dedupe_timeline(timeline),
        "attention_request": attention,
        "execution_actors": _operator_actors(queue_item, run, title, task_id),
        "outcome_tasks": [
            {
                "id": task_id,
                "objective": title,
                "description": task.body if task else _text(task_contract.get("instructions")),
                "owner": "implementation",
                "status": status,
                "status_reason": f"operator_{phase}",
                "dependencies": [],
                "acceptance_criteria": [
                    _text(item)
                    for item in _list(task_contract.get("acceptance_criteria"))
                    if _text(item)
                ],
                "attempt_count": max(1, _integer(queue_item.get("attempt"))),
                "review_state": _operator_review_state(metadata, review_event),
                "primary_outcome": primary,
                "supporting_refs": [value for value in (branch_name, _text(metadata.get("head_sha"))) if value],
                "updated_at": _text(queue_item.get("updated_at")),
            }
        ],
        "capabilities": [
            {"action": "inspect_execution", "target_id": execution_id or mission_id},
            {"action": "inspect_delivery", "target_id": execution_id or mission_id},
        ],
    }


def _operator_title(
    task: ExecutionTask | None,
    queue_item: Mapping[str, object],
    metadata: Mapping[str, object],
) -> str:
    if task and task.title.strip():
        return task.title.strip()
    title = _text(metadata.get("issue_title"))
    if title:
        return title
    source = _text(queue_item.get("source_url"))
    return f"Issue #{metadata.get('issue_number')}" if metadata.get("issue_number") else source or "Operator mission"


def _operator_task_contract(
    task: ExecutionTask | None,
    metadata: Mapping[str, object],
) -> dict[str, Any]:
    if task and task.task_contract:
        return task.task_contract.to_dict()
    return dict(_mapping(metadata.get("task_contract")))


def _operator_objective(
    task: ExecutionTask | None,
    task_contract: Mapping[str, object],
    title: str,
) -> str:
    if task and task.goal.strip():
        return task.goal.strip()
    return _text(task_contract.get("summary")) or _text(task_contract.get("instructions")) or title


def _operator_attention(queue_item: Mapping[str, object]) -> dict[str, Any] | None:
    mission_id = _text(queue_item.get("id"))
    execution_id = _text(queue_item.get("execution_id"))
    phase = _text(queue_item.get("phase"))
    if _text(queue_item.get("status")) == "paused" or phase == "paused":
        return {
            "id": f"operator-paused:{mission_id}",
            "kind": "blocker",
            "target_id": execution_id or mission_id,
            "reason": "operator_paused",
            "refs": [value for value in (execution_id, _text(queue_item.get("last_failure_class"))) if value],
            "actions": ["inspect_execution"],
        }
    if phase == "waiting_telegram":
        return {
            "id": f"operator-telegram:{mission_id}",
            "kind": "decision",
            "target_id": execution_id or mission_id,
            "reason": "operator_waiting_telegram",
            "refs": [value for value in (execution_id, _text(queue_item.get("source_url"))) if value],
            "actions": [],
        }
    return None


def _operator_actors(
    queue_item: Mapping[str, object],
    run: ExecutionRun | None,
    responsibility: str,
    task_id: str,
) -> list[dict[str, Any]]:
    phase = _text(queue_item.get("phase")) or "executing"
    mission_id = _text(queue_item.get("id"))
    updated_at = _text(queue_item.get("updated_at"))
    actors: list[dict[str, Any]] = []
    if run is not None:
        run_state = _OPERATOR_RUN_STATES.get(str(run.status), "working")
        if phase == "executing" and run_state != "blocked":
            run_state = "working"
        actors.append(
            {
                "id": run.worker_job_id or f"{mission_id}:builder",
                "role": "implementation",
                "state": run_state,
                "responsibility": responsibility,
                "task_id": task_id,
                "dependency_reason": "",
                "attempt": "1",
                "source": "execution_ledger",
                "updated_at": run.updated_at,
            }
        )

    role = _OPERATOR_PHASE_ROLES.get(phase, "orchestrator")
    phase_state = _OPERATOR_PHASE_STATES.get(phase, "working")
    builder_is_current = run is not None and phase == "executing" and role == "implementation"
    if not builder_is_current:
        actors.append(
            {
                "id": f"{mission_id}:phase:{phase}",
                "role": role,
                "state": phase_state,
                "responsibility": responsibility,
                "task_id": task_id,
                "dependency_reason": f"operator_{phase}" if phase_state in {"waiting", "blocked"} else "",
                "attempt": _text(queue_item.get("attempt")),
                "source": "operator_queue",
                "updated_at": updated_at,
            }
        )
    return actors


def _operator_phase_timeline_item(
    queue_item: Mapping[str, object],
    primary: Mapping[str, object] | None,
) -> dict[str, Any]:
    phase = _text(queue_item.get("phase")) or "executing"
    event = {
        "executing": "work_dispatched",
        "pr_ready": "implementation_completed",
        "observing_ci": "verification_updated",
        "review_ready": "review_updated",
        "fixing": "work_retried",
        "waiting_telegram": "attention_required",
        "paused": "work_blocked",
    }.get(phase, "work_progressed")
    return {
        "id": f"{_text(queue_item.get('id'))}:phase:{phase}:{_text(queue_item.get('updated_at'))}",
        "actor": "manager",
        "kind": "attention" if phase in {"waiting_telegram", "paused"} else "progress",
        "event": event,
        "timestamp": _text(queue_item.get("updated_at")),
        "content": "",
        "details": {
            "phase": phase,
            "artifact": _text(primary.get("label")) if primary else "",
        },
        "refs": [_text(queue_item.get("execution_id"))] if queue_item.get("execution_id") else [],
    }


def _operator_timeline(events: Sequence[LedgerEvent]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        transition = _text(event.payload.get("transition"))
        semantic = {
            "intake_created": "mission_organized",
            "worker_job_created": "work_dispatched",
            "execution_completed": "implementation_completed",
            "execution_failed": "work_blocked",
            "pr_opened": "deliverable_ready",
            "ci_observed": "verification_updated",
        }.get(transition)
        if not semantic and str(event.event_type) == "review_recorded":
            semantic = "review_updated"
        if not semantic:
            continue
        items.append(
            {
                "id": event.event_id,
                "actor": "manager",
                "kind": "outcome" if semantic in {"deliverable_ready", "implementation_completed"} else "progress",
                "event": semantic,
                "timestamp": event.created_at,
                "content": _text(event.payload.get("summary")) if isinstance(event.payload.get("summary"), str) else "",
                "details": {
                    "transition": transition,
                    "pr_number": event.payload.get("pr_number"),
                    "ci_state": _text(event.payload.get("state")),
                    "decision": _text(event.payload.get("decision")),
                },
                "refs": [
                    value
                    for value in (
                        _text(event.payload.get("result_path")),
                        _text(event.payload.get("artifact_path")),
                    )
                    if value
                ],
            }
        )
    return items


def _operator_primary_outcome(
    queue_item: Mapping[str, object],
    task: ExecutionTask | None,
    run: ExecutionRun | None,
    pr_event: LedgerEvent | None,
    result_event: LedgerEvent | None,
) -> dict[str, str] | None:
    metadata = _mapping(queue_item.get("metadata"))
    pr_number = _integer(
        metadata.get("pr_number")
        or (run.pr_number if run else None)
        or (pr_event.payload.get("pr_number") if pr_event else None)
    )
    if pr_number:
        href = _text(metadata.get("pr_url"))
        repository = _text(queue_item.get("repository_identity")) or (task.repo if task else "")
        if not href and repository:
            href = f"https://github.com/{repository}/pull/{pr_number}"
        return {
            "kind": "pull_request",
            "label": f"PR #{pr_number}",
            "href": href,
            "path": "",
            "status": "ready",
        }
    if result_event:
        path = _text(result_event.payload.get("result_path")) or _text(result_event.payload.get("artifact_path"))
        if path:
            return {
                "kind": "artifact",
                "label": Path(path).name or "Result",
                "href": "",
                "path": path,
                "status": "ready",
            }
    return None


def _operator_review_state(metadata: Mapping[str, object], event: LedgerEvent | None) -> str:
    review = _mapping(metadata.get("review"))
    decision = _mapping(metadata.get("decision"))
    projected = (
        _text(decision.get("decision"))
        or _text(review.get("recommendation"))
        or _text(review.get("verdict"))
        or _text(review.get("status"))
    )
    if projected:
        return projected
    if event is None:
        return ""
    return _text(event.payload.get("decision")) or _text(event.payload.get("state")) or "recorded"


def _matching_review(
    task: Mapping[str, object],
    reviews: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    refs = {_text(item).removeprefix("stage:") for item in _list(task.get("external_refs"))}
    role = _text(task.get("role"))
    aliases = {
        "product_requirement": {"prd"},
        "architect": {"rfc", "security"},
        "qa": {"staging", "qa"},
        "release": {"release"},
    }
    wanted = refs | aliases.get(role, {role})
    return next((review for review in reviews if _text(review.get("approval")) in wanted), None)


def _primary_artifact(refs: Sequence[str]) -> dict[str, str] | None:
    if not refs:
        return None
    path = refs[0]
    return {
        "kind": "document",
        "label": Path(path).name or path,
        "href": "",
        "path": path,
        "status": "ready",
    }


def _actor_state(job: Mapping[str, object], fallback: str = "waiting") -> str:
    return _WORKER_STATES.get(_text(job.get("status")).lower(), fallback)


def _is_review_role(role: str) -> bool:
    lowered = role.lower()
    return lowered in _REVIEW_ROLES or "review" in lowered


def _latest_outcome_for_role(
    outcomes: Sequence[Mapping[str, object]],
    role: str,
) -> Mapping[str, object] | None:
    matches = [item for item in outcomes if _text(item.get("owner")) == role]
    if not matches:
        return None
    active = [item for item in matches if item.get("status") not in {"completed", "stopped"}]
    return max(active or matches, key=lambda item: _text(item.get("updated_at")))


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


def _dedupe_timeline(items: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        identifier = _text(item.get("id"))
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append(item)
    return sorted(result, key=lambda item: (_text(item.get("timestamp")), _text(item.get("id"))))


def _latest_timestamp(*collections: Sequence[Mapping[str, object]]) -> str:
    values = [
        _updated_at(item)
        for collection in collections
        for item in collection
        if _updated_at(item)
    ]
    return max(values, default="")


def _latest_item(items: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    return max(items, key=_updated_at) if items else None


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


def _safe_path_segment(value: str) -> bool:
    return bool(value and value not in {".", ".."} and Path(value).name == value)


def _text(value: object) -> str:
    return str(value or "").strip()
