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
_TERMINAL_TASK_STATUSES = {"done", "canceled"}
_WORKFLOW_STAGE_BY_GATE = {
    "prd": "product_requirement",
    "rfc": "architect",
    "security": "architect",
    "fix_plan": "implementation",
    "code_review": "implementation",
    "staging": "qa",
    "qa": "qa",
    "release": "release",
}
_WORKFLOW_STAGE_BY_ARTIFACT = {
    "prd": "product_requirement",
    "prd_proposed": "product_requirement",
    "rfc": "architect",
    "task_breakdown": "implementation",
    "ui_spec": "implementation",
    "implementation_handoff": "implementation",
    "review_report": "implementation",
    "code_review": "implementation",
    "qa_report": "qa",
    "staging_report": "qa",
    "release_notes": "release",
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
    # Worker jobs always persist a run id. Blank or foreign ids belong in the
    # attempt inspector, not in an active workflow's current-team projection.
    run_jobs = [item for item in worker_jobs if _text(item.get("run_id")) == state.run_id]
    ordered_actions = sorted(run_actions, key=lambda item: _text(item.get("timestamp")))
    started_at = next(
        (_text(item.get("timestamp")) for item in ordered_actions if item.get("action") == "workflow_started"),
        "",
    )
    jobs_by_task, unbound_jobs_by_role = _associate_workflow_jobs(tasks, run_jobs)
    outcomes = _workflow_outcomes(tasks, jobs_by_task, approval_reviews)
    updated_at = _latest_timestamp(ordered_actions, run_jobs, outcomes)
    actors = _workflow_actors(outcomes, jobs_by_task, unbound_jobs_by_role)
    attention = _workflow_attention(state, outcomes, actors, approval_reviews)
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
    worker_jobs_by_task: Mapping[str, Sequence[Mapping[str, object]]],
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
        matching_jobs = list(worker_jobs_by_task.get(task_id, ()))
        current_attempt = _authoritative_worker_attempt(matching_jobs)
        status = _workflow_assignment_state(task, current_attempt, fallback=status)
        review = _matching_review(task, approval_reviews)
        if (
            review
            and bool(review.get("is_actionable"))
            and raw_status not in _TERMINAL_TASK_STATUSES
            and status not in {"working", "fixing", "blocked"}
        ):
            status = "reviewing"
        refs = [_text(item) for item in _list(task.get("artifact_refs")) if _text(item)]
        primary_outcome = _primary_artifact(refs)
        current_attempt_id = _text(current_attempt.get("id")) if current_attempt else ""
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
                "current_attempt_id": current_attempt_id,
                "review_state": _text(review.get("decision")) if review else "",
                "primary_outcome": primary_outcome,
                "supporting_refs": refs[1:] if primary_outcome else refs,
                "updated_at": max(_text(task.get("updated_at")), _updated_at(current_attempt or {})),
            }
        )
    return outcomes


def _workflow_actors(
    outcomes: Sequence[Mapping[str, object]],
    worker_jobs_by_task: Mapping[str, Sequence[Mapping[str, object]]],
    unbound_jobs_by_role: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[dict[str, Any]]:
    """Project one current actor per logical task assignment.

    A worker job is an attempt, never the primary row identity. Task ids produce
    stable actor ids; the latest durable attempt only supplies current state and
    attempt metadata. Jobs that cannot be associated without guessing are
    compressed into one unbound actor per role instead of being attached to an
    arbitrary same-role task.
    """
    actors: list[dict[str, Any]] = []
    for task in outcomes:
        task_id = _text(task.get("id"))
        task_jobs = list(worker_jobs_by_task.get(task_id, ()))
        current_attempt = _authoritative_worker_attempt(task_jobs)
        role = _text(task.get("owner"))
        if not role and current_attempt is not None:
            role = _text(current_attempt.get("role"))
        if not role:
            continue
        state = _text(task.get("status")) or "up_next"
        actors.append(
            {
                "id": f"task-assignment:{task_id}",
                "role": role,
                "state": state,
                "responsibility": _text(task.get("objective")),
                "task_id": task_id,
                "dependency_reason": _text(task.get("status_reason")) if state == "waiting" else "",
                "attempt": (
                    _text(current_attempt.get("retry_attempt") or current_attempt.get("attempt"))
                    if current_attempt
                    else ""
                ),
                "attempt_count": len(task_jobs),
                "current_attempt_id": _text(current_attempt.get("id")) if current_attempt else "",
                "source": "worker_job" if current_attempt else "task_role",
                "updated_at": _text(task.get("updated_at")),
            }
        )

    for role, role_jobs in sorted(unbound_jobs_by_role.items()):
        current_attempt = _authoritative_worker_attempt(role_jobs)
        if current_attempt is None:
            continue
        state = _worker_attempt_state(current_attempt, fallback="waiting")
        actors.append(
            {
                "id": f"unbound-role:{role}",
                "role": role,
                "state": state,
                "responsibility": _text(current_attempt.get("summary")),
                "task_id": "",
                "dependency_reason": "",
                "attempt": _text(current_attempt.get("retry_attempt") or current_attempt.get("attempt")),
                "attempt_count": len(role_jobs),
                "current_attempt_id": _text(current_attempt.get("id")),
                "source": "unbound_worker_job",
                "updated_at": _updated_at(current_attempt),
            }
        )
    return actors


def _associate_workflow_jobs(
    tasks: Sequence[Mapping[str, object]],
    worker_jobs: Sequence[Mapping[str, object]],
) -> tuple[dict[str, list[Mapping[str, object]]], dict[str, list[Mapping[str, object]]]]:
    """Associate attempts to tasks without inferring identity from display text.

    The deterministic priority is: an explicit task/assignment id; exact durable
    references (task source, external ref, execution id, or artifact path);
    workflow gate/phase/artifact identity; retry or dispatch lineage; and finally
    a compatibility role match only when that role owns exactly one task. Any
    remaining attempts are grouped once by role as unbound current work.
    """
    task_rows = [task for task in tasks if _text(task.get("id"))]
    tasks_by_id = {_text(task.get("id")): task for task in task_rows}
    jobs_by_task: dict[str, list[Mapping[str, object]]] = {task_id: [] for task_id in tasks_by_id}
    assigned_jobs: list[tuple[Mapping[str, object], str]] = []
    pending_jobs: list[tuple[Mapping[str, object], bool]] = []

    def assign(job: Mapping[str, object], task_id: str) -> None:
        jobs_by_task[task_id].append(job)
        assigned_jobs.append((job, task_id))

    for job in worker_jobs:
        task_id, protected = _explicit_task_match(job, task_rows, tasks_by_id)
        if task_id:
            assign(job, task_id)
        else:
            pending_jobs.append((job, protected))

    # Retry and dispatch identities can connect a compatibility record to a job
    # that already carries an explicit task reference. Resolve transitively.
    while True:
        next_pending: list[tuple[Mapping[str, object], bool]] = []
        resolved = False
        for job, protected in pending_jobs:
            if protected:
                next_pending.append((job, protected))
                continue
            lineage_matches = {
                task_id
                for assigned_job, task_id in assigned_jobs
                if _worker_jobs_share_lineage(job, assigned_job)
            }
            if len(lineage_matches) == 1:
                assign(job, lineage_matches.pop())
                resolved = True
            else:
                next_pending.append((job, protected or len(lineage_matches) > 1))
        pending_jobs = next_pending
        if not resolved:
            break

    tasks_by_role: dict[str, list[str]] = {}
    for task_id, task in tasks_by_id.items():
        role = _text(task.get("role"))
        if role:
            tasks_by_role.setdefault(role, []).append(task_id)

    unbound_jobs_by_role: dict[str, list[Mapping[str, object]]] = {}
    for job, protected in pending_jobs:
        role = _text(job.get("role")) or "worker"
        compatible_tasks = tasks_by_role.get(role, [])
        if not protected and len(compatible_tasks) == 1:
            assign(job, compatible_tasks[0])
            continue
        unbound_jobs_by_role.setdefault(role, []).append(job)
    return jobs_by_task, unbound_jobs_by_role


def _explicit_task_match(
    job: Mapping[str, object],
    tasks: Sequence[Mapping[str, object]],
    tasks_by_id: Mapping[str, Mapping[str, object]],
) -> tuple[str, bool]:
    direct_task_id = next(
        (
            _text(job.get(key))
            for key in ("task_id", "assignment_id", "logical_task_id")
            if _text(job.get(key))
        ),
        "",
    )
    if direct_task_id:
        return (direct_task_id, True) if direct_task_id in tasks_by_id else ("", True)

    exact_references = [
        *[_text(item) for item in _list(job.get("external_refs")) if _text(item)],
        _text(job.get("task_source")),
        _text(job.get("execution_id")),
        _text(job.get("artifact_path")),
        _text(job.get("idempotency_key")),
    ]
    reference_matches = {
        task_id
        for reference in exact_references
        if reference and reference != "inline"
        for task_id in _task_ids_for_reference(tasks, reference)
    }
    if len(reference_matches) == 1:
        return reference_matches.pop(), True
    if len(reference_matches) > 1:
        return "", True

    execution_id = _text(job.get("execution_id"))
    if execution_id:
        return "", True
    task_source = _text(job.get("task_source"))
    if task_source.startswith(("task:", "task_id:", "assignment:", "logical_task:")):
        return "", True

    gate = _text(job.get("gate") or job.get("approval"))
    stage = _WORKFLOW_STAGE_BY_GATE.get(gate)
    if stage:
        stage_matches = _task_ids_for_stage(tasks, stage)
        if len(stage_matches) == 1:
            return stage_matches[0], True
        if len(stage_matches) > 1:
            return "", True

    artifact_stage = _WORKFLOW_STAGE_BY_ARTIFACT.get(_text(job.get("artifact_key")))
    if artifact_stage:
        stage_matches = _task_ids_for_stage(tasks, artifact_stage)
        if len(stage_matches) == 1:
            return stage_matches[0], True
        if len(stage_matches) > 1:
            return "", True

    phase = _text(job.get("phase"))
    if phase in {"product_requirement", "architect", "implementation", "qa", "release"}:
        stage_matches = _task_ids_for_stage(tasks, phase)
        if len(stage_matches) == 1:
            return stage_matches[0], True
        if len(stage_matches) > 1:
            return "", True
    return "", False


def _task_ids_for_reference(tasks: Sequence[Mapping[str, object]], reference: str) -> list[str]:
    references = {reference}
    for prefix in ("task:", "task_id:", "assignment:", "logical_task:"):
        if reference.startswith(prefix):
            references.add(reference.removeprefix(prefix))
    matches: list[str] = []
    for task in tasks:
        task_references = {
            _text(task.get("id")),
            *[_text(item) for item in _list(task.get("external_refs")) if _text(item)],
            *[_text(item) for item in _list(task.get("artifact_refs")) if _text(item)],
        }
        if references & task_references:
            matches.append(_text(task.get("id")))
    return matches


def _task_ids_for_stage(tasks: Sequence[Mapping[str, object]], stage: str) -> list[str]:
    stage_ref = f"stage:{stage}"
    explicit = [
        _text(task.get("id"))
        for task in tasks
        if stage_ref in {_text(item) for item in _list(task.get("external_refs"))}
    ]
    if explicit:
        return explicit
    return [_text(task.get("id")) for task in tasks if _text(task.get("role")) == stage]


def _worker_jobs_share_lineage(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> bool:
    left_id = _text(left.get("id"))
    right_id = _text(right.get("id"))
    if left_id and left_id == _text(right.get("original_failed_job_id")):
        return True
    if right_id and right_id == _text(left.get("original_failed_job_id")):
        return True
    left_key = _text(left.get("idempotency_key"))
    right_key = _text(right.get("idempotency_key"))
    if left_key and left_key == _text(right.get("retry_of_idempotency_key")):
        return True
    if right_key and right_key == _text(left.get("retry_of_idempotency_key")):
        return True
    left_group = _text(left.get("dispatch_group_id"))
    right_group = _text(right.get("dispatch_group_id"))
    return bool(left_group and left_group == right_group)


def _workflow_assignment_state(
    task: Mapping[str, object],
    current_attempt: Mapping[str, object] | None,
    *,
    fallback: str,
) -> str:
    """Derive current assignment state with terminal task state as authority.

    Otherwise only the authoritative latest attempt may override the task: a
    historical failure cannot beat a later success, and a newer active retry
    cannot be hidden by a historical success.
    """
    raw_task_status = _text(task.get("status"))
    if raw_task_status in _TERMINAL_TASK_STATUSES:
        return _TASK_STATES[raw_task_status]
    if current_attempt is None:
        return fallback
    return _worker_attempt_state(current_attempt, fallback=fallback)


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


def _workflow_attention(
    state: AgileDevState,
    outcomes: Sequence[Mapping[str, object]],
    execution_actors: Sequence[Mapping[str, object]],
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
    blocked_actors = [item for item in execution_actors if item.get("state") == "blocked"]
    if state.blockers or blocked_tasks or blocked_actors:
        refs = [_text(item.get("id")) for item in blocked_tasks]
        refs.extend(
            _text(item.get("current_attempt_id")) or _text(item.get("id"))
            for item in blocked_actors
        )
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
