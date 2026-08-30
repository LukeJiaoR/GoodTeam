from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.state import AgileDevState
from goodteam_os.workflow_table import resolve_profile


TEAM_WORKSPACE_SCHEMA_VERSION = "team_workspace.v1"
MANAGER_APPROVAL_BRIEF_SCHEMA_VERSION = "manager_approval_brief.v1"

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
_TERMINAL_EXECUTION_STATES = {"completed", "stopped"}
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
_WORKFLOW_STAGE_PREDECESSOR = {
    "architect": "product_requirement",
    "implementation": "architect",
    "qa": "implementation",
    "release": "qa",
}
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
_APPROVAL_TYPE_BY_GATE = {
    "takeover": "research_planning",
    "prd": "research_planning",
    "fix_plan": "research_planning",
    "rfc": "architecture",
    "security": "architecture",
    "code_review": "qa",
    "staging": "qa",
    "qa": "qa",
    "release": "release",
}
_SCOPE_HEADINGS_BY_TYPE = {
    "research_planning": (
        "v1 scope",
        "scope",
        "minimal change plan",
        "acceptance criteria",
        "v1 范围",
        "本次范围",
        "最小变更计划",
        "验收标准",
    ),
    "architecture": (
        "scope",
        "data model",
        "api contract",
        "frontend and ux contract",
        "security assessment",
        "verification plan",
        "范围",
        "数据模型",
        "api 契约",
        "接口契约",
        "前端与 ux 契约",
        "安全评估",
        "验证计划",
    ),
    "qa": (
        "scope",
        "test scope",
        "verification matrix",
        "acceptance results",
        "healthcheck results",
        "review findings",
        "范围",
        "测试范围",
        "验证矩阵",
        "验收结果",
        "健康检查结果",
        "审查发现",
    ),
    "release": (
        "release contents",
        "release scope",
        "impact",
        "changes",
        "发布内容",
        "发布范围",
        "影响",
        "变更",
    ),
    "general": ("scope", "acceptance criteria", "范围", "验收标准"),
}
_SUMMARY_HEADINGS_BY_TYPE = {
    "research_planning": (
        "product decision",
        "my current assessment",
        "conclusion",
        "summary",
        "产品决策",
        "当前判断",
        "结论",
        "摘要",
    ),
    "architecture": ("architecture decision", "proposal", "overview", "summary", "架构决策", "方案", "概述", "摘要"),
    "qa": ("conclusion", "summary", "result", "assessment", "结论", "摘要", "结果", "评估"),
    "release": ("release decision", "summary", "impact", "发布决策", "摘要", "影响"),
    "general": ("decision", "summary", "conclusion", "决策", "摘要", "结论"),
}
_EXCLUDED_SCOPE_HEADINGS = (
    "non-goal",
    "out of scope",
    "not in scope",
    "excluded",
    "非目标",
    "不在范围",
    "范围外",
    "排除",
)
_RISK_HEADINGS = (
    "risk",
    "caveat",
    "known issue",
    "limitation",
    "unknown",
    "open question",
    "assumption",
    "trade-off",
    "tradeoff",
    "风险",
    "已知问题",
    "限制",
    "未知",
    "待确认",
    "开放问题",
    "假设",
    "权衡",
    "取舍",
    "注意事项",
)
_RECOVERY_HEADINGS = ("rollback", "recovery", "回滚", "恢复")
_NEXT_TASK_STAGE = {
    "product_requirement": "product_requirement",
    "architect": "architect",
    "security": "architect",
    "planner": "implementation",
    "release": "release",
    "fix_implement": "implementation",
    "fix_qa": "qa",
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
    attention = _workflow_attention(state, tasks, outcomes, actors, approval_reviews)
    actors = _apply_workflow_attention_reason(actors, attention)
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


def _apply_workflow_attention_reason(
    actors: Sequence[Mapping[str, object]],
    attention: Mapping[str, object] | None,
) -> list[dict[str, Any]]:
    """Attach a user-attention reason only through the exact projected task id."""
    task_id = _text(attention.get("task_id")) if attention else ""
    is_exact_review = bool(
        task_id
        and _text(attention.get("kind")) == "approval"
        and _text(attention.get("target_type")) == "review"
    )
    return [
        {
            **actor,
            **(
                {"status_reason": "awaiting_your_approval"}
                if is_exact_review
                and _text(actor.get("task_id")) == task_id
                and _text(actor.get("state")) == "reviewing"
                else {}
            ),
        }
        for actor in actors
    ]


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
        dependencies = _workflow_task_dependencies(task, task_rows)
        unmet = [item for item in dependencies if status_by_id.get(item) != "done"]
        status = _TASK_STATES.get(raw_status, "up_next")
        if status == "up_next" and unmet:
            status = "waiting"
        matching_jobs = list(worker_jobs_by_task.get(task_id, ()))
        current_attempt = _authoritative_worker_attempt(matching_jobs)
        status = _workflow_assignment_state(task, current_attempt, fallback=status)
        review = _matching_review(task, approval_reviews, task_rows)
        current_attempt_state = (
            _worker_attempt_state(current_attempt, fallback=status)
            if current_attempt is not None
            else ""
        )
        if (
            review
            and bool(review.get("is_actionable"))
            and raw_status not in _TERMINAL_TASK_STATUSES
            and status != "blocked"
            and current_attempt_state not in {"working", "fixing", "blocked"}
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
                "unmet_dependencies": unmet,
                "acceptance_criteria": [_text(item) for item in _list(task.get("acceptance_criteria"))],
                "attempt_count": len(matching_jobs),
                "current_attempt_id": current_attempt_id,
                "review_state": _text(review.get("decision")) if review else "",
                "review_target_type": "review" if review else "",
                "review_target_id": _text(review.get("approval")) if review else "",
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
    attempt metadata. Terminal assignments do not remain as completed/stopped
    workers: their attempt history and deliverables belong to Outcomes and the
    secondary inspectors. A pending review, fix, or retry derives a non-terminal
    assignment state and therefore remains visible here. Jobs that cannot be
    associated without guessing are compressed into one unbound actor per role
    instead of being attached to an arbitrary same-role task.
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
        if state in _TERMINAL_EXECUTION_STATES:
            continue
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
                "attempt_count": len(task_jobs),
                "current_attempt_id": _text(current_attempt.get("id")) if current_attempt else "",
                "source": "worker_job" if current_attempt else "task_role",
                "updated_at": _text(task.get("updated_at")),
            }
        )

    for role_order, (role, role_jobs) in enumerate(sorted(unbound_jobs_by_role.items())):
        current_attempt = _authoritative_worker_attempt(role_jobs)
        if current_attempt is None:
            continue
        state = _worker_attempt_state(current_attempt, fallback="waiting")
        if state in _TERMINAL_EXECUTION_STATES:
            continue
        actor_id = f"unbound-role:{role}"
        stable_order[actor_id] = len(outcomes) + role_order
        actors.append(
            {
                "id": actor_id,
                "role": role,
                "state": state,
                "responsibility": _text(current_attempt.get("summary")),
                "task_id": "",
                "dependency_reason": "",
                "dependency_task_id": "",
                "dependency_task_title": "",
                "status_reason": _task_status_reason(state, ()),
                "attempt": _text(current_attempt.get("retry_attempt") or current_attempt.get("attempt")),
                "attempt_count": len(role_jobs),
                "current_attempt_id": _text(current_attempt.get("id")),
                "source": "unbound_worker_job",
                "updated_at": _updated_at(current_attempt),
            }
        )
    return _order_execution_actors(actors, stable_order)


def _workflow_task_dependencies(
    task: Mapping[str, object],
    tasks: Sequence[Mapping[str, object]],
) -> list[str]:
    """Return explicit dependencies or the canonical-stage compatibility edge.

    Historical stage tasks were synced without dependency ids. When, and only
    when, exact ``stage:*`` durable refs identify both a canonical assignment
    and its single predecessor, the presentation projection reconstructs that
    one edge. Labels and roles never participate, and the task ledger remains
    unchanged.
    """
    explicit = [_text(item) for item in _list(task.get("dependencies")) if _text(item)]
    if explicit:
        return explicit

    external_refs = {_text(item) for item in _list(task.get("external_refs"))}
    stages = {
        stage
        for stage in ("product_requirement", "architect", "implementation", "qa", "release")
        if f"stage:{stage}" in external_refs
    }
    if len(stages) != 1:
        return []
    predecessor = _WORKFLOW_STAGE_PREDECESSOR.get(next(iter(stages)))
    if not predecessor:
        return []
    predecessor_ids = [
        _text(candidate.get("id"))
        for candidate in tasks
        if f"stage:{predecessor}"
        in {_text(item) for item in _list(candidate.get("external_refs"))}
        and _text(candidate.get("id"))
    ]
    return predecessor_ids if len(predecessor_ids) == 1 else []


def _order_execution_actors(
    actors: Sequence[Mapping[str, object]],
    stable_order: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Order the live-team projection by urgency without refresh jitter.

    State priority is authoritative for presentation. Within a state, durable
    workflow/task order wins, followed by timestamp and actor id. Every client
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
    tasks: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    execution_actors: Sequence[Mapping[str, object]],
    approval_reviews: Sequence[Mapping[str, object]],
) -> dict[str, Any] | None:
    actionable = next((item for item in approval_reviews if bool(item.get("is_actionable"))), None)
    if actionable is not None:
        gate = _text(actionable.get("approval"))
        task_id = _review_task_id(actionable, tasks)
        return {
            "id": f"approval:{gate}",
            "kind": "approval",
            "target_type": "review",
            "target_id": gate,
            "task_id": task_id,
            "reason": "approval_required",
            "refs": [value for value in (gate, task_id) if value],
            "actions": ["approve", "request_changes", "review"],
            "approval_brief": _manager_approval_brief(
                state=state,
                review=actionable,
                task_id=task_id,
                tasks=tasks,
                outcomes=outcomes,
            ),
        }
    recovery = next(
        (item for item in approval_reviews if isinstance(item.get("recovery"), Mapping) and item["recovery"].get("available")),
        None,
    )
    if recovery is not None:
        gate = _text(recovery.get("approval")) or "takeover"
        task_id = _review_task_id(recovery, tasks)
        return {
            "id": f"recovery:{gate}",
            "kind": "blocker",
            "target_type": "review",
            "target_id": gate,
            "task_id": task_id,
            "reason": "review_recovery_required",
            "refs": [value for value in (gate, task_id) if value],
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


def _manager_approval_brief(
    *,
    state: AgileDevState,
    review: Mapping[str, object],
    task_id: str,
    tasks: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Compress durable approval inputs without asserting facts that are absent.

    The projection deliberately carries structured facts and source wording. The
    frontend localizes labels and consequence copy, while empty collections stay
    empty so the Manager can say that scope, risk, or evidence was not recorded.
    """

    gate = _text(review.get("approval"))
    approval_type = _APPROVAL_TYPE_BY_GATE.get(gate, "general")
    content = _text(review.get("content"))
    sections = _markdown_sections(content)
    outcome = next((item for item in outcomes if _text(item.get("id")) == task_id), None)
    artifact_path = _text(review.get("artifact_path"))
    approval_artifact_version = _text(review.get("artifact_version")) or "missing"
    artifact_version = "" if approval_artifact_version == "missing" else approval_artifact_version
    artifact_key = _text(review.get("artifact"))
    artifact_title = _clean_markdown_fact(review.get("title"), limit=120)
    artifact_label = _clean_markdown_fact(Path(artifact_path).name, limit=120) if artifact_path else artifact_title

    included = _section_groups(
        sections,
        _SCOPE_HEADINGS_BY_TYPE.get(approval_type, _SCOPE_HEADINGS_BY_TYPE["general"]),
        exclude_terms=(*_EXCLUDED_SCOPE_HEADINGS, *_RISK_HEADINGS, *_RECOVERY_HEADINGS),
    )
    if not included and outcome is not None:
        included = _task_contract_scope(outcome)

    return {
        "schema_version": MANAGER_APPROVAL_BRIEF_SCHEMA_VERSION,
        "approval": gate,
        "approval_type": approval_type,
        "approval_target": {
            "run_id": _text(state.run_id),
            "target_type": "review",
            "target_id": gate,
            "task_id": task_id,
            "artifact_key": artifact_key,
            "artifact_version": approval_artifact_version,
        },
        "decision": {
            "subject": (
                artifact_title
                or _clean_markdown_fact(outcome.get("objective") if outcome else "", limit=120)
                or gate
            ),
            "summary": _first_section_fact(
                sections,
                _SUMMARY_HEADINGS_BY_TYPE.get(approval_type, _SUMMARY_HEADINGS_BY_TYPE["general"]),
            ),
            "artifact": {
                "key": artifact_key,
                "label": artifact_label,
                "version": artifact_version,
            },
        },
        "scope": {
            "included": included,
            "excluded": _section_groups(sections, _EXCLUDED_SCOPE_HEADINGS),
        },
        "evidence": _approval_brief_evidence(review, approval_type),
        "mission_blockers": _approval_brief_mission_blockers(state),
        "risks": _approval_brief_risks(review, sections, approval_type),
        "consequence": _approval_brief_consequence(state, gate, tasks, sections),
        "recommendation": _approval_brief_recommendation(review),
    }


def _markdown_sections(content: str) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    heading = ""
    items: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        fact = _clean_markdown_fact(" ".join(paragraph))
        if fact:
            items.append(fact)
        paragraph.clear()

    def flush_section() -> None:
        flush_paragraph()
        if heading or items:
            sections.append({"heading": heading, "items": list(items)})
        items.clear()

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", raw_line)
        if heading_match:
            flush_section()
            heading = _clean_markdown_fact(heading_match.group(1), limit=120)
            continue
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith("|") or re.fullmatch(r"[-:|\s]+", stripped):
            flush_paragraph()
            continue
        bullet_match = re.match(r"^\s*(?:[-*+] |\d+[.)]\s+)(.+)$", raw_line)
        if bullet_match:
            flush_paragraph()
            fact = _clean_markdown_fact(bullet_match.group(1))
            if fact:
                items.append(fact)
            continue
        paragraph.append(stripped)
    flush_section()
    return sections


def _clean_markdown_fact(value: object, *, limit: int = 240) -> str:
    text = _text(value)
    if not text or text == "[Preview truncated]":
        return ""
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("`", "")
    text = re.sub(r"[*~]", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    text = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    text = re.sub(r"[a-zA-Z0-9_\-]+@\d{1,3}(?:\.\d{1,3}){3}", "staging-host", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "staging-ip", text)
    text = re.sub(r"/srv/[a-zA-Z0-9_\-/]+", "deployment-dir", text)
    text = re.sub(r"/Users/[^/\s]+/[a-zA-Z0-9_\-/.]+", "local-workspace-dir", text)
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _normalized_heading(value: object) -> str:
    return re.sub(r"[\W_]+", " ", _text(value).casefold(), flags=re.UNICODE).strip()


def _section_groups(
    sections: Sequence[Mapping[str, object]],
    terms: Sequence[str],
    *,
    exclude_terms: Sequence[str] = (),
    group_limit: int = 2,
    item_limit: int = 4,
) -> list[dict[str, object]]:
    normalized_terms = tuple(_normalized_heading(term) for term in terms)
    normalized_exclusions = tuple(_normalized_heading(term) for term in exclude_terms)
    groups: list[dict[str, object]] = []
    remaining = item_limit
    for section in sections:
        normalized = _normalized_heading(section.get("heading"))
        if not normalized or not any(term in normalized for term in normalized_terms):
            continue
        if any(term in normalized for term in normalized_exclusions):
            continue
        facts = []
        for raw_item in _list(section.get("items")):
            fact = _clean_markdown_fact(raw_item)
            if fact and fact not in facts:
                facts.append(fact)
            if len(facts) >= min(3, remaining):
                break
        if not facts:
            continue
        groups.append(
            {
                "source": "artifact_section",
                "heading": _text(section.get("heading")),
                "items": facts,
            }
        )
        remaining -= len(facts)
        if len(groups) >= group_limit or remaining <= 0:
            break
    return groups


def _first_section_fact(
    sections: Sequence[Mapping[str, object]],
    terms: Sequence[str],
) -> str:
    groups = _section_groups(sections, terms, group_limit=1, item_limit=1)
    return _text(_list(groups[0].get("items"))[0]) if groups else ""


def _task_contract_scope(outcome: Mapping[str, object]) -> list[dict[str, object]]:
    facts: list[str] = []
    for value in (
        outcome.get("description"),
        *_list(outcome.get("acceptance_criteria")),
    ):
        fact = _clean_markdown_fact(value)
        if fact and fact not in facts:
            facts.append(fact)
        if len(facts) >= 5:
            break
    if not facts:
        objective = _clean_markdown_fact(outcome.get("objective"))
        if objective:
            facts.append(objective)
    return [{"source": "task_contract", "heading": "", "items": facts}] if facts else []


def _approval_brief_evidence(
    review: Mapping[str, object],
    approval_type: str,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    artifact_path = _text(review.get("artifact_path"))
    artifact_title = _clean_markdown_fact(review.get("title"), limit=120)
    artifact_version = _text(review.get("artifact_version"))
    artifact_available = bool(
        artifact_path
        or _text(review.get("content"))
        or (artifact_version and artifact_version != "missing")
    )
    if artifact_available:
        evidence.append(
            {
                "kind": "artifact",
                "label": (
                    _clean_markdown_fact(Path(artifact_path).name, limit=120)
                    if artifact_path
                    else artifact_title
                ),
                "status": "versioned" if artifact_version and artifact_version != "missing" else "available",
                "detail": artifact_version if artifact_version != "missing" else "",
            }
        )

    multi = _mapping(review.get("multi_reviewer_projection"))
    reviewers = [item for item in _list(multi.get("reviewers")) if isinstance(item, Mapping)]
    if approval_type == "release":
        snapshot = _mapping(review.get("evidence_snapshot"))
        for reference in _list(snapshot.get("provenance_refs"))[:2]:
            if not isinstance(reference, Mapping):
                continue
            if not any(
                _text(reference.get(field))
                for field in ("pr", "branch", "merge_sha", "ci_status")
            ):
                continue
            evidence.append(
                {
                    "kind": "delivery",
                    "label": "",
                    "status": _text(reference.get("ci_status")) or "recorded",
                    "detail": "",
                    "pr": _text(reference.get("pr")),
                    "branch": _text(reference.get("branch")),
                    "commit": _text(reference.get("merge_sha")),
                }
            )

    for reviewer in reviewers[:2]:
        status = _mapping(reviewer.get("status"))
        policy = _mapping(reviewer.get("policy"))
        evidence.append(
            {
                "kind": "reviewer",
                "label": (
                    _clean_markdown_fact(reviewer.get("profile_label"), limit=120)
                    or _clean_markdown_fact(reviewer.get("profile_id"), limit=120)
                    or _clean_markdown_fact(status.get("role"), limit=120)
                ),
                "status": _text(policy.get("state")) or _text(status.get("status")) or "missing_evidence",
                "detail": _clean_markdown_fact(status.get("summary") or policy.get("rationale")),
            }
        )
    if not reviewers:
        policy = _mapping(review.get("review_evidence_policy"))
        if policy:
            evidence.append(
                {
                    "kind": "reviewer",
                    "label": artifact_title,
                    "status": _text(policy.get("state")) or "missing_evidence",
                    "detail": _clean_markdown_fact(policy.get("rationale")),
                }
            )

    snapshot = _mapping(review.get("evidence_snapshot"))
    include_unknown = approval_type in {"qa", "release"}
    for kind, field in (("ci", "ci_status"), ("smoke", "smoke_status"), ("qa", "qa_status")):
        status = _text(snapshot.get(field)).lower()
        if status and (include_unknown or status != "unknown"):
            evidence.append({"kind": kind, "label": "", "status": status, "detail": ""})
    screenshot_count = len(_list(snapshot.get("screenshot_refs")))
    if screenshot_count and approval_type in {"qa", "release"}:
        evidence.append(
            {
                "kind": "screenshots",
                "label": "",
                "status": "available",
                "detail": "",
                "count": screenshot_count,
            }
        )
    return evidence[:8]


def _approval_brief_mission_blockers(state: AgileDevState) -> list[str]:
    blockers: list[str] = []
    for raw_blocker in state.blockers:
        blocker = _clean_markdown_fact(raw_blocker)
        if blocker and blocker not in blockers:
            blockers.append(blocker)
        if len(blockers) >= 5:
            break
    return blockers


def _approval_brief_risks(
    review: Mapping[str, object],
    sections: Sequence[Mapping[str, object]],
    approval_type: str,
) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    for group in _section_groups(sections, _RISK_HEADINGS, group_limit=2, item_limit=4):
        for item in _list(group.get("items")):
            risks.append(
                {
                    "kind": "artifact",
                    "heading": _text(group.get("heading")),
                    "text": _text(item),
                }
            )

    recommendation = _mapping(review.get("recommendation"))
    for raw_reason in _list(recommendation.get("reasons")):
        reason = _mapping(raw_reason)
        kind = _text(reason.get("kind"))
        text = _clean_markdown_fact(reason.get("text") if reason else raw_reason)
        if kind == "clean" or not text:
            continue
        risks.append({"kind": "recommendation", "reason_kind": kind, "text": text})

    policy = _mapping(review.get("review_evidence_policy"))
    if policy and _text(policy.get("state")) != "ok":
        risks.append(
            {
                "kind": "review_evidence",
                "status": _text(policy.get("state")),
                "severity": _text(policy.get("severity")),
                "text": _clean_markdown_fact(policy.get("rationale")),
            }
        )
    aggregate = _mapping(_mapping(review.get("multi_reviewer_projection")).get("aggregate_policy"))
    if _text(aggregate.get("severity")) == "blocker":
        risks.append(
            {
                "kind": "reviewer_aggregate",
                "severity": "blocker",
                "state_counts": dict(_mapping(aggregate.get("state_counts"))),
                "text": "",
            }
        )

    snapshot = _mapping(review.get("evidence_snapshot"))
    if approval_type in {"qa", "release"}:
        for source, field in (("ci", "ci_status"), ("smoke", "smoke_status"), ("qa", "qa_status")):
            if _text(snapshot.get(field)).lower() in {"", "unknown"}:
                risks.append({"kind": "evidence_gap", "source": source, "text": ""})
    blocker_count = _integer(snapshot.get("blocker_count"))
    if blocker_count:
        risks.append({"kind": "blockers", "count": blocker_count, "text": ""})

    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    seen_text: set[str] = set()
    for risk in risks:
        key = (
            _text(risk.get("kind")),
            _text(risk.get("source")),
            _text(risk.get("text")),
        )
        text = _text(risk.get("text"))
        if key in seen or (text and text in seen_text):
            continue
        seen.add(key)
        if text:
            seen_text.add(text)
        deduped.append(risk)
        if len(deduped) >= 5:
            break
    return deduped


def _approval_brief_consequence(
    state: AgileDevState,
    gate: str,
    tasks: Sequence[Mapping[str, object]],
    sections: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    try:
        next_stage = resolve_profile(_text(getattr(state, "load_profile", ""))).approved_next_for_gate(gate)
    except ValueError:
        next_stage = ""
    task_stage = _NEXT_TASK_STAGE.get(next_stage, "")
    matches = [
        task
        for task in tasks
        if task_stage and f"stage:{task_stage}" in {_text(item) for item in _list(task.get("external_refs"))}
    ]
    next_task = matches[0] if len(matches) == 1 else None
    return {
        "next_stage": next_stage,
        "task_id": _text(next_task.get("id") if next_task else ""),
        "task_title": _clean_markdown_fact(next_task.get("title") if next_task else "", limit=120),
        "recovery": _section_groups(sections, _RECOVERY_HEADINGS, group_limit=2, item_limit=4),
    }


def _approval_brief_recommendation(review: Mapping[str, object]) -> dict[str, object]:
    recommendation = _mapping(review.get("recommendation"))
    reasons: list[dict[str, str]] = []
    for raw_reason in _list(recommendation.get("reasons")):
        reason = _mapping(raw_reason)
        text = _clean_markdown_fact(reason.get("text") if reason else raw_reason)
        if text:
            reasons.append({"kind": _text(reason.get("kind")), "text": text})
        if len(reasons) >= 3:
            break
    return {
        "verdict": _text(recommendation.get("verdict")),
        "confidence": _text(recommendation.get("confidence")),
        "blocking_count": _integer(recommendation.get("blocking_count")),
        "reasons": reasons,
    }


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
                "unmet_dependencies": [],
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
    stable_order: dict[str, int] = {}
    if run is not None:
        run_state = _OPERATOR_RUN_STATES.get(str(run.status), "working")
        if phase == "executing" and run_state != "blocked":
            run_state = "working"
        if run_state not in _TERMINAL_EXECUTION_STATES:
            actor_id = run.worker_job_id or f"{mission_id}:builder"
            stable_order[actor_id] = 0
            actors.append(
                {
                    "id": actor_id,
                    "role": "implementation",
                    "state": run_state,
                    "responsibility": responsibility,
                    "task_id": task_id,
                    "dependency_reason": "",
                    "dependency_task_id": "",
                    "dependency_task_title": "",
                    "status_reason": _task_status_reason(run_state, ()),
                    "attempt": "1",
                    "source": "execution_ledger",
                    "updated_at": run.updated_at,
                }
            )

    role = _OPERATOR_PHASE_ROLES.get(phase, "orchestrator")
    phase_state = _OPERATOR_PHASE_STATES.get(phase, "working")
    builder_is_current = run is not None and phase == "executing" and role == "implementation"
    if not builder_is_current:
        actor_id = f"{mission_id}:phase:{phase}"
        stable_order[actor_id] = 1
        actors.append(
            {
                "id": actor_id,
                "role": role,
                "state": phase_state,
                "responsibility": responsibility,
                "task_id": task_id,
                "dependency_reason": f"operator_{phase}" if phase_state in {"waiting", "blocked"} else "",
                "dependency_task_id": "",
                "dependency_task_title": "",
                "status_reason": f"operator_{phase}",
                "attempt": _text(queue_item.get("attempt")),
                "source": "operator_queue",
                "updated_at": updated_at,
            }
        )
    return _order_execution_actors(actors, stable_order)


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
    tasks: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    task_id = _text(task.get("id"))
    matches = [review for review in reviews if _review_task_id(review, tasks) == task_id]
    if not matches:
        return None
    return next((review for review in matches if bool(review.get("is_actionable"))), None) or next(
        (review for review in matches if bool(review.get("is_current"))),
        None,
    ) or next((review for review in matches if bool(review.get("is_pending"))), None) or matches[0]


def _review_task_id(
    review: Mapping[str, object],
    tasks: Sequence[Mapping[str, object]],
) -> str:
    """Resolve a review to one logical task without presentation heuristics.

    Durable task identifiers win. Compatibility matching then uses exact
    workflow-stage/gate references or an exact artifact path. A missing or
    ambiguous association intentionally returns blank: role and title strings
    are not safe task identity when one role owns multiple assignments.
    """

    task_rows = [dict(task) for task in tasks]
    tasks_by_id = {
        _text(task.get("id")): task
        for task in task_rows
        if _text(task.get("id"))
    }
    explicit_ids = {
        value
        for value in (
            _text(review.get("task_id")),
            _text(review.get("assignment_id")),
            _text(review.get("logical_task_id")),
        )
        if value
    }
    for reference in [
        *(_text(item) for item in _list(review.get("refs"))),
        *(_text(item) for item in _list(review.get("external_refs"))),
    ]:
        for prefix in ("task:", "task_id:", "assignment:", "logical_task:"):
            if reference.startswith(prefix) and reference.removeprefix(prefix):
                explicit_ids.add(reference.removeprefix(prefix))
    if explicit_ids:
        return next(iter(explicit_ids)) if len(explicit_ids) == 1 and explicit_ids <= tasks_by_id.keys() else ""

    gate = _text(review.get("approval"))
    stage = _WORKFLOW_STAGE_BY_GATE.get(gate, "")
    wanted_refs = {
        value
        for value in (
            f"stage:{stage}" if stage else "",
            f"review:{gate}" if gate else "",
            f"approval:{gate}" if gate else "",
            f"gate:{gate}" if gate else "",
        )
        if value
    }
    referenced_matches = [
        task_id
        for task_id, task in tasks_by_id.items()
        if wanted_refs & {_text(item) for item in _list(task.get("external_refs"))}
    ]
    if referenced_matches:
        return referenced_matches[0] if len(referenced_matches) == 1 else ""

    artifact_path = _text(review.get("artifact_path"))
    artifact_matches = [
        task_id
        for task_id, task in tasks_by_id.items()
        if artifact_path and artifact_path in {_text(item) for item in _list(task.get("artifact_refs"))}
    ]
    return artifact_matches[0] if len(artifact_matches) == 1 else ""


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
