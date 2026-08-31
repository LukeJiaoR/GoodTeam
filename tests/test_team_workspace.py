from pathlib import Path

import pytest

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.idempotency import execution_id_from_task
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.state import create_initial_state
from goodteam_os.team_workspace import build_team_workspace, empty_team_workspace


def _workflow_task(
    task_id: str,
    title: str,
    role: str,
    *,
    status: str = "in_progress",
    dependencies: list[str] | None = None,
    external_refs: list[str] | None = None,
    updated_at: str = "2026-08-30T10:00:00Z",
) -> dict[str, object]:
    return {
        "id": task_id,
        "title": title,
        "description": f"Logical assignment for {title}.",
        "role": role,
        "status": status,
        "dependencies": dependencies or [],
        "acceptance_criteria": [],
        "artifact_refs": [],
        "external_refs": external_refs or [],
        "updated_at": updated_at,
    }


def _workflow_job(
    job_id: str,
    status: str,
    updated_at: str,
    *,
    run_id: str = "run-identity",
    role: str = "architect",
    **identity: object,
) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": run_id,
        "role": role,
        "status": status,
        "updated_at": updated_at,
        **identity,
    }


def _workflow_identity_projection(
    tasks: list[dict[str, object]],
    jobs: list[dict[str, object]],
) -> dict[str, object]:
    state = create_initial_state("run-identity", "DEMO", "Deliver the architecture mission")
    state.current_node = "implementation"
    state.next_node = "code_review"
    return build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        worker_jobs=jobs,
    )


def test_workflow_projection_compresses_durable_state_into_team_contract(tmp_path: Path) -> None:
    state = create_initial_state("run-1", "DEMO", "Make OAuth production ready")
    state.current_node = "implementation"
    state.next_node = "code_review"
    tasks = [
        {
            "id": "DEMO-1",
            "title": "Implement OAuth",
            "description": "Harden the provider flow.",
            "role": "implementation",
            "status": "in_progress",
            "dependencies": [],
            "acceptance_criteria": ["CI passes"],
            "artifact_refs": ["/tmp/oauth-report.md", "/tmp/oauth-test.log"],
            "external_refs": ["stage:implementation"],
            "updated_at": "2026-08-30T10:05:00Z",
        },
        {
            "id": "DEMO-2",
            "title": "Review OAuth",
            "description": "Independent review.",
            "role": "reviewer",
            "status": "todo",
            "dependencies": ["DEMO-1"],
            "acceptance_criteria": [],
            "artifact_refs": [],
            "external_refs": [],
            "updated_at": "2026-08-30T10:00:00Z",
        },
    ]
    jobs = [
        {
            "id": "job-1",
            "run_id": "run-1",
            "role": "implementation",
            "status": "running",
            "updated_at": "2026-08-30T10:06:00Z",
        },
        {
            "id": "job-other-run",
            "run_id": "run-old",
            "role": "qa",
            "status": "failed",
            "updated_at": "2026-08-30T10:07:00Z",
        },
    ]
    actions = [
        {
            "run_id": "run-1",
            "action": "workflow_started",
            "actor": "human",
            "timestamp": "2026-08-30T10:00:00Z",
        },
        {
            "run_id": "run-1",
            "action": "implementation_dispatched",
            "actor": "system",
            "timestamp": "2026-08-30T10:05:00Z",
        },
        {
            "run_id": "run-1",
            "action": "worker_stdout",
            "actor": "system",
            "timestamp": "2026-08-30T10:05:30Z",
        },
        {
            "run_id": "run-old",
            "action": "qa_failed",
            "actor": "system",
            "timestamp": "2026-08-30T10:07:00Z",
        },
    ]

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=tmp_path,
        tasks=tasks,
        worker_jobs=jobs,
        action_items=actions,
    )

    assert projection["schema_version"] == "team_workspace.v1"
    assert projection["mission"]["objective"] == "Make OAuth production ready"
    assert projection["mission"]["status"] == "working"
    assert [item["event"] for item in projection["manager_timeline"]] == [
        "mission_requested",
        "mission_organized",
        "work_progressed",
    ]
    assert projection["execution_actors"][0]["state"] == "working"
    planned_reviewer = next(item for item in projection["execution_actors"] if item["role"] == "reviewer")
    assert planned_reviewer["state"] == "waiting"
    assert planned_reviewer["source"] == "task_role"
    assert projection["outcome_tasks"][0]["primary_outcome"] == {
        "kind": "document",
        "label": "oauth-report.md",
        "href": "",
        "path": "/tmp/oauth-report.md",
        "status": "ready",
    }
    assert projection["outcome_tasks"][0]["supporting_refs"] == ["/tmp/oauth-test.log"]
    assert projection["outcome_tasks"][1]["status_reason"] == "waiting_for_dependencies"
    assert projection["attention_request"] is None


def test_same_task_and_role_with_many_attempts_keeps_completion_in_outcomes() -> None:
    task = _workflow_task("DEMO-1", "Design RFC & Security Review", "architect")
    jobs = [
        _workflow_job("job-1", "blocked", "2026-08-30T10:01:00Z"),
        _workflow_job("job-2", "completed", "2026-08-30T10:02:00Z"),
        _workflow_job("job-3", "blocked", "2026-08-30T10:03:00Z"),
        _workflow_job("job-4", "completed", "2026-08-30T10:04:00Z"),
    ]

    projection = _workflow_identity_projection([task], jobs)

    assert projection["execution_actors"] == []
    assert projection["outcome_tasks"][0]["status"] == "completed"
    assert projection["outcome_tasks"][0]["attempt_count"] == 4
    assert projection["outcome_tasks"][0]["current_attempt_id"] == "job-4"


def test_blocked_attempt_followed_by_completed_attempt_leaves_no_primary_actor() -> None:
    projection = _workflow_identity_projection(
        [_workflow_task("DEMO-1", "Design RFC & Security Review", "architect")],
        [
            _workflow_job("job-blocked", "blocked", "2026-08-30T10:01:00Z"),
            _workflow_job("job-completed", "completed", "2026-08-30T10:02:00Z"),
        ],
    )

    assert projection["execution_actors"] == []
    assert projection["outcome_tasks"][0]["status"] == "completed"
    assert projection["attention_request"] is None


def test_newer_running_retry_overrides_historical_completed_attempt() -> None:
    projection = _workflow_identity_projection(
        [_workflow_task("DEMO-1", "Design RFC & Security Review", "architect")],
        [
            _workflow_job(
                "job-completed",
                "completed",
                "2026-08-30T10:01:00Z",
                idempotency_key="architect-base",
            ),
            _workflow_job(
                "job-retry",
                "running",
                "2026-08-30T10:02:00Z",
                retry_attempt=2,
                retry_of_idempotency_key="architect-base",
            ),
        ],
    )

    assert len(projection["execution_actors"]) == 1
    assert projection["execution_actors"][0]["state"] == "fixing"
    assert projection["execution_actors"][0]["current_attempt_id"] == "job-retry"
    assert projection["outcome_tasks"][0]["status"] == "fixing"


def test_completed_attempt_with_pending_review_remains_a_reviewing_actor() -> None:
    state = create_initial_state("run-identity", "DEMO", "Review the architecture")
    state.current_node = "security_review_gate"
    state.next_node = "await_security_approval"
    task = _workflow_task(
        "DEMO-1",
        "Design RFC & Security Review",
        "architect",
        external_refs=["stage:architect"],
    )

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=[task],
        worker_jobs=[
            _workflow_job(
                "job-completed",
                "completed",
                "2026-08-30T10:01:00Z",
                task_id="DEMO-1",
            )
        ],
        approval_reviews=[{"approval": "security", "is_actionable": True}],
    )

    assert [(actor["task_id"], actor["state"]) for actor in projection["execution_actors"]] == [
        ("DEMO-1", "reviewing")
    ]
    assert projection["outcome_tasks"][0]["status"] == "reviewing"


def test_same_role_with_two_durable_task_references_projects_two_assignments() -> None:
    tasks = [
        _workflow_task(
            "DEMO-1",
            "Design RFC",
            "architect",
            external_refs=["assignment:architecture-design"],
        ),
        _workflow_task(
            "DEMO-2",
            "Security architecture review",
            "architect",
            external_refs=["assignment:security-review"],
        ),
    ]
    jobs = [
        _workflow_job(
            "job-design",
            "completed",
            "2026-08-30T10:01:00Z",
            task_id="DEMO-1",
        ),
        _workflow_job(
            "job-security",
            "running",
            "2026-08-30T10:02:00Z",
            task_source="assignment:security-review",
        ),
    ]

    projection = _workflow_identity_projection(tasks, jobs)

    assert [(actor["task_id"], actor["responsibility"]) for actor in projection["execution_actors"]] == [
        ("DEMO-2", "Security architecture review"),
    ]
    assert [actor["id"] for actor in projection["execution_actors"]] == [
        "task-assignment:DEMO-2",
    ]
    completed = next(task for task in projection["outcome_tasks"] if task["id"] == "DEMO-1")
    assert completed["status"] == "completed"
    assert completed["current_attempt_id"] == "job-design"


def test_planned_task_without_worker_attempt_projects_once_as_waiting() -> None:
    projection = _workflow_identity_projection(
        [
            _workflow_task(
                "DEMO-2",
                "Review implementation",
                "reviewer",
                status="todo",
                dependencies=["DEMO-1"],
            )
        ],
        [],
    )

    assert projection["execution_actors"] == [
        {
            "id": "task-assignment:DEMO-2",
            "role": "reviewer",
            "state": "waiting",
            "responsibility": "Review implementation",
            "task_id": "DEMO-2",
            "dependency_reason": "waiting_for_dependencies",
            "dependency_task_id": "DEMO-1",
            "dependency_task_title": "",
            "status_reason": "waiting_for_dependencies",
            "attempt": "",
            "attempt_count": 0,
            "current_attempt_id": "",
            "source": "task_role",
            "updated_at": "2026-08-30T10:00:00Z",
        }
    ]


def test_canonical_stage_projection_derives_waiting_chain_and_current_first_order() -> None:
    state = create_initial_state("run-stage-chain", "DEMO", "Ship the approved implementation")
    state.next_node = "await_security_approval"
    tasks = [
        _workflow_task(
            "DEMO-1",
            "Draft and approve PRD",
            "product_requirement",
            status="done",
            external_refs=["stage:product_requirement"],
        ),
        _workflow_task(
            "DEMO-2",
            "Design RFC & Security Review",
            "architect",
            status="in_progress",
            external_refs=["stage:architect"],
        ),
        _workflow_task(
            "DEMO-3",
            "Implement approved design",
            "implementation",
            status="todo",
            external_refs=["stage:implementation"],
        ),
        _workflow_task(
            "DEMO-4",
            "Verify implementation",
            "qa",
            status="todo",
            external_refs=["stage:qa"],
        ),
        _workflow_task(
            "DEMO-5",
            "Approve and complete release",
            "release",
            status="todo",
            external_refs=["stage:release"],
        ),
    ]

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        approval_reviews=[{"approval": "security", "is_actionable": True}],
    )

    assert [
        (actor["task_id"], actor["state"])
        for actor in projection["execution_actors"]
    ] == [
        ("DEMO-2", "reviewing"),
        ("DEMO-3", "waiting"),
        ("DEMO-4", "waiting"),
        ("DEMO-5", "waiting"),
    ]
    waiting = {
        actor["task_id"]: (actor["dependency_task_id"], actor["dependency_task_title"])
        for actor in projection["execution_actors"]
        if actor["state"] == "waiting"
    }
    assert waiting == {
        "DEMO-3": ("DEMO-2", "Design RFC & Security Review"),
        "DEMO-4": ("DEMO-3", "Implement approved design"),
        "DEMO-5": ("DEMO-4", "Verify implementation"),
    }
    architect = projection["execution_actors"][0]
    assert architect["status_reason"] == "awaiting_your_approval"
    assert [task["dependencies"] for task in projection["outcome_tasks"]] == [
        [],
        ["DEMO-1"],
        ["DEMO-2"],
        ["DEMO-3"],
        ["DEMO-4"],
    ]


def test_execution_order_uses_state_priority_and_is_stable() -> None:
    tasks = [
        _workflow_task("DEMO-DONE", "Completed task", "release", status="done"),
        _workflow_task(
            "DEMO-WAIT",
            "Waiting task",
            "qa",
            status="todo",
            dependencies=["DEMO-MISSING"],
        ),
        _workflow_task("DEMO-WORK", "Working task", "implementation", status="in_progress"),
        _workflow_task("DEMO-BLOCK", "Blocked task", "architect", status="blocked"),
        _workflow_task("DEMO-REVIEW", "Reviewing task", "reviewer", status="review"),
        _workflow_task("DEMO-NEXT", "Eligible task", "product_requirement", status="todo"),
    ]

    first = _workflow_identity_projection([dict(task) for task in tasks], [])
    second = _workflow_identity_projection([dict(task) for task in tasks], [])

    expected = ["blocked", "reviewing", "working", "waiting", "up_next"]
    assert [actor["state"] for actor in first["execution_actors"]] == expected
    assert next(
        task for task in first["outcome_tasks"] if task["id"] == "DEMO-DONE"
    )["status"] == "completed"
    assert [actor["id"] for actor in first["execution_actors"]] == [
        actor["id"] for actor in second["execution_actors"]
    ]
    assert first == second


def test_worker_jobs_from_another_run_do_not_contaminate_current_assignment() -> None:
    projection = _workflow_identity_projection(
        [_workflow_task("DEMO-1", "Design RFC & Security Review", "architect")],
        [
            _workflow_job("job-current", "running", "2026-08-30T10:01:00Z"),
            _workflow_job(
                "job-old-run",
                "failed",
                "2026-08-30T10:10:00Z",
                run_id="run-old",
                task_id="DEMO-1",
            ),
            _workflow_job(
                "job-no-run",
                "failed",
                "2026-08-30T10:11:00Z",
                run_id="",
                task_id="DEMO-1",
            ),
        ],
    )

    assert len(projection["execution_actors"]) == 1
    assert projection["execution_actors"][0]["state"] == "working"
    assert projection["execution_actors"][0]["attempt_count"] == 1
    assert projection["execution_actors"][0]["current_attempt_id"] == "job-current"
    assert projection["attention_request"] is None


def test_assignment_actor_id_is_stable_across_projection_order_and_reload() -> None:
    task = _workflow_task("DEMO-1", "Design RFC & Security Review", "architect")
    jobs = [
        _workflow_job("job-1", "failed", "2026-08-30T10:01:00Z"),
        _workflow_job(
            "job-2",
            "running",
            "2026-08-30T10:02:00Z",
            retry_attempt=2,
        ),
    ]

    first = _workflow_identity_projection([dict(task)], [dict(job) for job in jobs])
    reloaded = _workflow_identity_projection([dict(task)], [dict(job) for job in reversed(jobs)])

    assert [actor["id"] for actor in first["execution_actors"]] == ["task-assignment:DEMO-1"]
    assert [actor["id"] for actor in reloaded["execution_actors"]] == ["task-assignment:DEMO-1"]
    assert reloaded["execution_actors"][0]["current_attempt_id"] == "job-2"
    assert reloaded["execution_actors"][0]["state"] == "fixing"


def test_ambiguous_same_role_attempts_remain_one_unbound_actor_instead_of_fake_task_membership() -> None:
    tasks = [
        _workflow_task("DEMO-1", "Design RFC", "architect"),
        _workflow_task("DEMO-2", "Security architecture review", "architect"),
    ]
    jobs = [
        _workflow_job("job-unbound-old", "failed", "2026-08-30T10:01:00Z"),
        _workflow_job("job-unbound-current", "running", "2026-08-30T10:02:00Z"),
    ]

    projection = _workflow_identity_projection(tasks, jobs)
    unbound = [actor for actor in projection["execution_actors"] if actor["source"] == "unbound_worker_job"]

    assert len(unbound) == 1
    assert unbound[0]["id"] == "unbound-role:architect"
    assert unbound[0]["task_id"] == ""
    assert unbound[0]["current_attempt_id"] == "job-unbound-current"
    assert unbound[0]["attempt_count"] == 2


def test_approval_attention_outranks_existing_blockers() -> None:
    state = create_initial_state("run-2", "DEMO", "Approve takeover")
    state.blockers = ["Worker is unavailable"]
    state.next_node = "await_takeover_approval"
    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        approval_reviews=[{"approval": "takeover", "is_actionable": True}],
    )

    attention = projection["attention_request"]
    assert {key: value for key, value in attention.items() if key != "approval_brief"} == {
        "id": "approval:takeover",
        "kind": "approval",
        "target_type": "review",
        "target_id": "takeover",
        "task_id": "",
        "reason": "approval_required",
        "refs": ["takeover"],
        "actions": ["approve", "request_changes", "review"],
    }
    brief = attention["approval_brief"]
    assert brief["schema_version"] == "manager_approval_brief.v1"
    assert brief["approval_type"] == "research_planning"
    assert brief["approval_target"] == {
        "run_id": "run-2",
        "target_type": "review",
        "target_id": "takeover",
        "task_id": "",
        "artifact_key": "",
        "artifact_version": "missing",
    }
    assert brief["scope"] == {"included": [], "excluded": []}
    assert brief["evidence"] == []
    assert brief["mission_blockers"] == ["Worker is unavailable"]
    assert brief["risks"] == []
    assert brief["consequence"]["next_stage"] == "product_requirement"
    assert brief["recommendation"]["verdict"] == ""
    assert {item["action"] for item in projection["capabilities"]} >= {
        "approve",
        "request_changes",
        "review",
        "cancel_mission",
    }


def test_intent_commit_reentry_projects_discussion_and_semantic_timeline() -> None:
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    intent = {
        "schema_version": "intent_commit.v1",
        "id": "intent-1",
        "kind": "request_changes",
        "mission": {"id": "run-intent", "run_id": "run-intent", "source": "workflow"},
        "target": {
            "approval": "prd",
            "run_id": "run-intent",
            "target_type": "review",
            "target_id": "prd",
            "task_id": "DEMO-PRD",
            "artifact_key": "prd",
            "artifact_version": "sha256:prd-v1",
            "head_sha": "",
        },
        "status": "ready_to_confirm",
        "draft_text": "Ship Google only and preserve a generic provider interface.",
        "interpretation": {
            "schema_version": "intent_interpretation.v1",
            "source": "deterministic_explicit_request",
            "summary": "Ship Google only and preserve a generic provider interface.",
            "items": [
                {
                    "kind": "explicit_request",
                    "text": "Ship Google only and preserve a generic provider interface.",
                }
            ],
        },
        "events": [
            {
                "id": "intent-1:event:001",
                "actor": "manager",
                "event": "change_discussion_started",
                "timestamp": "2026-08-31T10:00:00Z",
                "content": "",
            },
            {
                "id": "intent-1:event:002",
                "actor": "user",
                "event": "change_request_shared",
                "timestamp": "2026-08-31T10:01:00Z",
                "content": "Ship Google only and preserve a generic provider interface.",
            },
            {
                "id": "intent-1:event:003",
                "actor": "manager",
                "event": "change_intent_summarized",
                "timestamp": "2026-08-31T10:01:00Z",
                "content": "Ship Google only and preserve a generic provider interface.",
            },
        ],
        "created_at": "2026-08-31T10:00:00Z",
        "updated_at": "2026-08-31T10:01:00Z",
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        approval_reviews=[
            {
                "approval": "prd",
                "artifact": "prd",
                "artifact_version": "sha256:prd-v1",
                "is_actionable": True,
            }
        ],
        intent_commits=[intent],
    )

    projected_intent = projection["attention_request"]["intent_commit"]
    assert projected_intent["id"] == "intent-1"
    assert projected_intent["status"] == "ready_to_confirm"
    assert projected_intent["stale"] is False
    assert [
        item["event"]
        for item in projection["manager_timeline"]
        if item.get("details", {}).get("intent_id") == "intent-1"
    ] == [
        "change_discussion_started",
        "change_request_shared",
        "change_intent_summarized",
    ]
    intent_timeline = [
        item
        for item in projection["manager_timeline"]
        if item.get("details", {}).get("intent_id") == "intent-1"
    ]
    assert all(item["details"]["gate"] == "prd" for item in intent_timeline)


def test_intent_commit_projection_fails_closed_when_artifact_binding_changes() -> None:
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    intent = {
        "schema_version": "intent_commit.v1",
        "id": "intent-stale",
        "kind": "request_changes",
        "mission": {"id": "run-intent", "run_id": "run-intent"},
        "target": {
            "approval": "prd",
            "run_id": "run-intent",
            "target_type": "review",
            "target_id": "prd",
            "artifact_key": "prd",
            "artifact_version": "sha256:old",
        },
        "status": "ready_to_confirm",
        "draft_text": "Keep the provider interface generic.",
        "interpretation": None,
        "events": [],
        "created_at": "2026-08-31T10:00:00Z",
        "updated_at": "2026-08-31T10:01:00Z",
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        approval_reviews=[
            {
                "approval": "prd",
                "artifact": "prd",
                "artifact_version": "sha256:new",
                "is_actionable": True,
            }
        ],
        intent_commits=[intent],
    )

    projected_intent = projection["attention_request"]["intent_commit"]
    assert projected_intent["status"] == "stale"
    assert projected_intent["stale"] is True


def test_interrupted_confirmation_projects_recovery_attention_after_authority_moves() -> None:
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "security_review_gate"
    state.next_node = "revise_security"
    intent = {
        "schema_version": "intent_commit.v1",
        "id": "intent-recover",
        "kind": "request_changes",
        "mission": {"id": "run-intent", "run_id": "run-intent", "source": "workflow"},
        "target": {
            "approval": "security",
            "run_id": "run-intent",
            "target_type": "review",
            "target_id": "security",
            "task_id": "DEMO-SECURITY",
            "artifact_key": "rfc",
            "artifact_version": "sha256:rfc-v1",
        },
        "status": "committing",
        "draft_text": "Keep the provider boundary generic.",
        "interpretation": {
            "schema_version": "intent_interpretation.v1",
            "source": "deterministic_explicit_request",
            "summary": "Keep the provider boundary generic.",
            "items": [
                {"kind": "explicit_request", "text": "Keep the provider boundary generic."}
            ],
        },
        "confirmed_interpretation": {
            "schema_version": "intent_interpretation.v1",
            "source": "deterministic_explicit_request",
            "summary": "Keep the provider boundary generic.",
            "items": [
                {"kind": "explicit_request", "text": "Keep the provider boundary generic."}
            ],
        },
        "events": [],
        "created_at": "2026-08-31T10:00:00Z",
        "updated_at": "2026-08-31T10:01:00Z",
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        intent_commits=[intent],
    )

    attention = projection["attention_request"]
    assert attention["id"] == "intent-recovery:intent-recover"
    assert attention["kind"] == "blocker"
    assert attention["reason"] == "intent_confirmation_recovery"
    assert attention["intent_commit"]["status"] == "committing"
    assert attention["intent_commit"]["stale"] is False


def test_committed_intent_remains_in_timeline_without_blocking_the_revised_review() -> None:
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    intent = {
        "schema_version": "intent_commit.v1",
        "id": "intent-committed",
        "kind": "request_changes",
        "mission": {"id": "run-intent", "run_id": "run-intent", "source": "workflow"},
        "target": {
            "approval": "prd",
            "run_id": "run-intent",
            "target_type": "review",
            "target_id": "prd",
            "artifact_key": "prd",
            "artifact_version": "sha256:old",
        },
        "status": "committed",
        "draft_text": "Keep the provider interface generic.",
        "interpretation": None,
        "events": [
            {
                "id": "intent-committed:event:1",
                "actor": "user",
                "event": "change_intent_confirmed",
                "timestamp": "2026-08-31T10:02:00Z",
                "content": "Keep the provider interface generic.",
            },
            {
                "id": "intent-committed:event:2",
                "actor": "manager",
                "event": "change_request_committed",
                "timestamp": "2026-08-31T10:02:01Z",
                "content": "Keep the provider interface generic.",
            },
        ],
        "created_at": "2026-08-31T10:00:00Z",
        "updated_at": "2026-08-31T10:02:01Z",
    }
    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        approval_reviews=[
            {
                "approval": "prd",
                "artifact": "prd",
                "artifact_version": "sha256:revised",
                "is_actionable": True,
            }
        ],
        intent_commits=[intent],
    )

    assert "intent_commit" not in projection["attention_request"]
    assert [
        item["event"]
        for item in projection["manager_timeline"]
        if item.get("details", {}).get("intent_id") == "intent-committed"
    ] == ["change_intent_confirmed", "change_request_committed"]
    assert projection["attention_request"]["actions"] == ["approve", "request_changes", "review"]


def test_committed_intent_timeline_uses_semantic_chain_not_raw_approval_event() -> None:
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "implementation"
    state.next_node = "planner"
    intent = {
        "schema_version": "intent_commit.v1",
        "id": "intent-committed",
        "kind": "request_changes",
        "mission": {"id": "run-intent", "run_id": "run-intent"},
        "target": {"target_type": "review", "target_id": "prd"},
        "status": "committed",
        "draft_text": "Keep the provider interface generic.",
        "interpretation": None,
        "events": [
            {
                "id": f"intent-committed:event:{index}",
                "actor": actor,
                "event": event,
                "timestamp": f"2026-08-31T10:0{index}:00Z",
                "content": "Keep the provider interface generic." if index in {2, 3, 4, 5} else "",
            }
            for index, (actor, event) in enumerate(
                [
                    ("manager", "change_discussion_started"),
                    ("user", "change_request_shared"),
                    ("manager", "change_intent_summarized"),
                    ("user", "change_intent_confirmed"),
                    ("manager", "change_request_committed"),
                ],
                start=1,
            )
        ],
        "created_at": "2026-08-31T10:01:00Z",
        "updated_at": "2026-08-31T10:05:00Z",
    }
    approval_action = {
        "run_id": "run-intent",
        "action": "approval_decided",
        "actor": "human",
        "gate": "prd",
        "decision": "changes_requested",
        "review_mode": "intent_commit",
        "timestamp": "2026-08-31T10:03:30Z",
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        action_items=[approval_action],
        intent_commits=[intent],
    )

    semantic_events = [
        item["event"]
        for item in projection["manager_timeline"]
        if item.get("details", {}).get("intent_id") == "intent-committed"
    ]
    assert semantic_events == [
        "change_discussion_started",
        "change_request_shared",
        "change_intent_summarized",
        "change_intent_confirmed",
        "change_request_committed",
    ]
    assert not any(
        item["event"] == "approval_decided"
        for item in projection["manager_timeline"]
    )


def test_approval_attention_targets_one_outcome_by_durable_stage_identity() -> None:
    state = create_initial_state("run-review", "DEMO", "Review the architecture")
    state.next_node = "await_security_approval"
    tasks = [
        _workflow_task(
            "DEMO-ARCH",
            "Design RFC & Security Review",
            "architect",
            status="review",
            external_refs=["stage:architect"],
        ),
        _workflow_task(
            "DEMO-AUDIT",
            "Audit infrastructure boundaries",
            "architect",
            status="todo",
            external_refs=["assignment:infrastructure-audit"],
        ),
        _workflow_task(
            "DEMO-IMPL",
            "Implement approved design",
            "implementation",
            status="todo",
            external_refs=["stage:implementation"],
        ),
    ]
    reviews = [
        {"approval": "rfc", "decision": "approved", "is_actionable": False},
        {"approval": "security", "decision": "pending", "is_actionable": True},
    ]

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        approval_reviews=reviews,
    )

    attention = projection["attention_request"]
    assert {key: value for key, value in attention.items() if key != "approval_brief"} == {
        "id": "approval:security",
        "kind": "approval",
        "target_type": "review",
        "target_id": "security",
        "task_id": "DEMO-ARCH",
        "reason": "approval_required",
        "refs": ["security", "DEMO-ARCH"],
        "actions": ["approve", "request_changes", "review"],
    }
    brief = attention["approval_brief"]
    assert brief["approval_type"] == "architecture"
    assert brief["scope"]["included"] == [
        {
            "source": "task_contract",
            "heading": "",
            "items": ["Logical assignment for Design RFC & Security Review."],
        }
    ]
    assert brief["consequence"] == {
        "next_stage": "planner",
        "task_id": "DEMO-IMPL",
        "task_title": "Implement approved design",
        "recovery": [],
    }
    outcomes = {item["id"]: item for item in projection["outcome_tasks"]}
    assert outcomes["DEMO-ARCH"]["review_target_type"] == "review"
    assert outcomes["DEMO-ARCH"]["review_target_id"] == "security"
    assert outcomes["DEMO-AUDIT"]["review_target_id"] == ""
    assert outcomes["DEMO-IMPL"]["review_target_id"] == ""


def test_architecture_approval_brief_compresses_durable_decision_facts() -> None:
    state = create_initial_state("run-brief", "DEMO", "Ship federated login")
    state.next_node = "await_security_approval"
    tasks = [
        _workflow_task(
            "DEMO-ARCH",
            "Design provider architecture",
            "architect",
            status="review",
            external_refs=["stage:architect"],
        ),
        _workflow_task(
            "DEMO-IMPL",
            "Implement approved provider design",
            "implementation",
            status="todo",
            external_refs=["stage:implementation"],
        ),
    ]
    review = {
        "approval": "security",
        "title": "Federated login RFC",
        "artifact": "rfc",
        "artifact_path": "/workspace/rfc.md",
        "artifact_version": "sha256:abc123",
        "decision": "pending",
        "is_actionable": True,
        "content": """# Federated login RFC

## Architecture Decision

Keep one generic provider interface while shipping Google first.

## Scope

- Google login in this iteration.
- Preserve the current session and user model.

## Non-Goals

- GitHub login is not implemented in this iteration.

## Risks and trade-offs

- The provider abstraction adds initial implementation complexity.
""",
        "review_evidence_policy": {
            "state": "ok",
            "severity": "ok",
            "rationale": "Provider-backed security review completed.",
        },
        "multi_reviewer_projection": {
            "aggregate_policy": {"severity": "ok", "state_counts": {"ok": 1}},
            "reviewers": [
                {
                    "profile_id": "correctness-security",
                    "profile_label": "Security review",
                    "status": {"status": "completed", "summary": "No blocking security issues found."},
                    "policy": {"state": "ok", "severity": "ok"},
                }
            ],
        },
        "evidence_snapshot": {
            "ci_status": "passed",
            "smoke_status": "unknown",
            "qa_status": "unknown",
            "blocker_count": 0,
        },
        "recommendation": {
            "verdict": "approve",
            "confidence": "high",
            "blocking_count": 0,
            "reasons": [{"kind": "clean", "text": "No blocking signals were found."}],
        },
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        approval_reviews=[review],
    )

    brief = projection["attention_request"]["approval_brief"]
    assert brief["approval_target"] == {
        "run_id": "run-brief",
        "target_type": "review",
        "target_id": "security",
        "task_id": "DEMO-ARCH",
        "artifact_key": "rfc",
        "artifact_version": "sha256:abc123",
    }
    assert brief["mission_blockers"] == []
    assert brief["decision"] == {
        "subject": "Federated login RFC",
        "summary": "Keep one generic provider interface while shipping Google first.",
        "artifact": {"key": "rfc", "label": "rfc.md", "version": "sha256:abc123"},
    }
    assert brief["scope"] == {
        "included": [
            {
                "source": "artifact_section",
                "heading": "Scope",
                "items": [
                    "Google login in this iteration.",
                    "Preserve the current session and user model.",
                ],
            }
        ],
        "excluded": [
            {
                "source": "artifact_section",
                "heading": "Non-Goals",
                "items": ["GitHub login is not implemented in this iteration."],
            }
        ],
    }
    assert [(item["kind"], item["status"]) for item in brief["evidence"]] == [
        ("artifact", "versioned"),
        ("reviewer", "ok"),
        ("ci", "passed"),
    ]
    assert brief["risks"] == [
        {
            "kind": "artifact",
            "heading": "Risks and trade-offs",
            "text": "The provider abstraction adds initial implementation complexity.",
        }
    ]
    assert brief["consequence"] == {
        "next_stage": "planner",
        "task_id": "DEMO-IMPL",
        "task_title": "Implement approved provider design",
        "recovery": [],
    }
    assert brief["recommendation"]["verdict"] == "approve"
    assert brief["recommendation"]["reasons"] == [
        {"kind": "clean", "text": "No blocking signals were found."}
    ]


def test_release_approval_brief_binds_delivery_validation_and_recovery() -> None:
    state = create_initial_state("run-release-brief", "DEMO", "Release version 2.4")
    state.next_node = "await_release_approval"
    tasks = [
        _workflow_task(
            "DEMO-RELEASE",
            "Release version 2.4",
            "release",
            status="review",
            external_refs=["stage:release"],
        )
    ]
    review = {
        "approval": "release",
        "title": "Release notes 2.4",
        "artifact": "release_notes",
        "artifact_path": "/workspace/release-notes.md",
        "artifact_version": "sha256:release24",
        "decision": "pending",
        "is_actionable": True,
        "content": """# Release notes 2.4

## Release Decision

Release version 2.4 to the local starter channel.

## Release Contents

- Manager approval briefs and their localized presentation.

## Known Caveats

- Older archived runs do not contain structured approval briefs.

## Rollback

- Restore the previous project directory and restart GoodTeam.
""",
        "evidence_snapshot": {
            "ci_status": "passed",
            "smoke_status": "passed",
            "qa_status": "passed",
            "blocker_count": 0,
            "screenshot_refs": ["release-manager.png"],
            "provenance_refs": [
                {
                    "pr": "383",
                    "branch": "codex/issue-383-decision-ready-manager",
                    "ci_status": "passed",
                    "merge_sha": "0123456789abcdef0123456789abcdef01234567",
                }
            ],
        },
        "recommendation": {
            "verdict": "approve",
            "confidence": "high",
            "blocking_count": 0,
            "reasons": [{"kind": "clean", "text": "Release evidence is complete."}],
        },
    }

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        approval_reviews=[review],
    )

    brief = projection["attention_request"]["approval_brief"]
    assert brief["approval_type"] == "release"
    assert brief["decision"]["summary"] == "Release version 2.4 to the local starter channel."
    assert brief["scope"]["included"][0]["heading"] == "Release Contents"
    evidence_statuses = {item["kind"]: item["status"] for item in brief["evidence"]}
    assert evidence_statuses.items() >= {
        "artifact": "versioned",
        "ci": "passed",
        "smoke": "passed",
        "qa": "passed",
        "screenshots": "available",
        "delivery": "passed",
    }.items()
    delivery = next(item for item in brief["evidence"] if item["kind"] == "delivery")
    assert delivery["pr"] == "383"
    assert delivery["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert brief["risks"][0]["text"] == "Older archived runs do not contain structured approval briefs."
    assert brief["consequence"]["next_stage"] == "retro_memory"
    assert brief["consequence"]["recovery"] == [
        {
            "source": "artifact_section",
            "heading": "Rollback",
            "items": ["Restore the previous project directory and restart GoodTeam."],
        }
    ]


def test_approval_brief_preserves_chinese_decision_sections() -> None:
    state = create_initial_state("run-zh-brief", "DEMO", "交付登录能力")
    state.next_node = "await_prd_approval"
    task = _workflow_task(
        "DEMO-PRD",
        "定义登录范围",
        "product_requirement",
        status="review",
        external_refs=["stage:product_requirement"],
    )
    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=[task],
        approval_reviews=[
            {
                "approval": "prd",
                "title": "登录 PRD",
                "decision": "pending",
                "is_actionable": True,
                "content": """# 登录 PRD

## 产品决策

本轮只交付 Google 登录。

## 本次范围

- 保留通用 Provider 接口。

## 非目标

- 本轮不实现 GitHub 登录。

## 风险与待确认

- 企业域账号尚未验证。
""",
            }
        ],
    )

    brief = projection["attention_request"]["approval_brief"]
    assert brief["decision"]["summary"] == "本轮只交付 Google 登录。"
    assert brief["scope"]["included"][0]["items"] == ["保留通用 Provider 接口。"]
    assert brief["scope"]["excluded"][0]["items"] == ["本轮不实现 GitHub 登录。"]
    assert brief["risks"][0]["text"] == "企业域账号尚未验证。"


def test_approval_brief_masks_sensitive_artifact_facts() -> None:
    state = create_initial_state("run-redacted-brief", "DEMO", "Review the deployment plan")
    state.next_node = "await_prd_approval"
    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        approval_reviews=[
            {
                "approval": "prd",
                "title": "Deployment PRD",
                "decision": "pending",
                "is_actionable": True,
                "content": """# Deployment PRD

## Scope

- Use API_TOKEN=very-secret-value from /Users/alice/private/config.json.

## Risks

- Staging host deploy@10.2.3.4 is not reachable.
""",
            }
        ],
    )

    brief = projection["attention_request"]["approval_brief"]
    serialized = str(brief)
    assert "very-secret-value" not in serialized
    assert "/Users/alice/private" not in serialized
    assert "10.2.3.4" not in serialized
    assert "API_TOKEN=[REDACTED]" in serialized
    assert "local-workspace-dir" in serialized
    assert "staging-host" in serialized


def test_approval_attention_refuses_ambiguous_stage_task_membership() -> None:
    state = create_initial_state("run-review", "DEMO", "Review the architecture")
    state.next_node = "await_security_approval"
    tasks = [
        _workflow_task(
            "DEMO-ARCH-A",
            "Architecture A",
            "architect",
            status="review",
            external_refs=["stage:architect"],
        ),
        _workflow_task(
            "DEMO-ARCH-B",
            "Architecture B",
            "architect",
            status="review",
            external_refs=["stage:architect"],
        ),
    ]

    projection = build_team_workspace(
        state=state,
        workflow_active=True,
        project_root=None,
        tasks=tasks,
        approval_reviews=[{"approval": "security", "is_actionable": True}],
    )

    assert projection["attention_request"]["task_id"] == ""
    assert all(not item["review_target_id"] for item in projection["outcome_tasks"])


def _operator_projection_fixture(
    tmp_path: Path,
    phase: str,
) -> tuple[Path, Path, dict[str, object], str]:
    project_root = tmp_path / "project"
    state_dir = tmp_path / "service-state"
    queue = DurableOperatorQueue(state_dir)
    item, _ = queue.enqueue(
        idempotency_key="GOODTEAM:issue:375",
        source_url="https://github.com/LukeJiaoR/GoodTeam/issues/375",
        source_revision="revision-375",
        project_key="GOODTEAM",
        repository_identity="LukeJiaoR/GoodTeam",
        metadata={
            "issue_number": 375,
            "issue_title": "Manager-centered team interaction",
            "pr_number": 412 if phase not in {"executing", "pr_ready"} else 0,
            "pr_url": (
                "https://github.com/LukeJiaoR/GoodTeam/pull/412"
                if phase not in {"executing", "pr_ready"}
                else ""
            ),
            "head_sha": "head-412" if phase not in {"executing", "pr_ready"} else "",
            "branch_name": "issue-375",
            "task_contract": {
                "summary": "Make Work feel like one AI team.",
                "instructions": "Build the Work vertical slice.",
                "acceptance_criteria": ["Operator remains visible after execution"],
            },
            "review": {"recommendation": "approve"} if phase == "waiting_telegram" else {},
        },
    )
    queue.claim_next()
    execution_id = execution_id_from_task("task-375")
    status = "waiting" if phase == "waiting_telegram" else "active"
    queue.transition(
        item["id"],
        status=status,
        phase=phase,
        expected_phase="hydrating",
        updates={"execution_id": execution_id},
        event=f"entered_{phase}",
    )

    ledger = ExecutionLedger(project_root)
    task = ExecutionTask(
        task_id="task-375",
        source_type="github_issue",
        repo="LukeJiaoR/GoodTeam",
        issue_number=375,
        title="Manager-centered team interaction",
        body="Build the Work vertical slice.",
        goal="Make Work feel like one AI team.",
        status="running",
        created_at="2026-08-30T10:00:00Z",
        updated_at="2026-08-30T10:04:00Z",
    )
    run = ExecutionRun(
        execution_id=execution_id,
        task_id=task.task_id,
        worker_job_id="job-375",
        adapter="codex",
        status="running" if phase == "executing" else "succeeded",
        branch_name="issue-375",
        pr_number=412 if phase not in {"executing", "pr_ready"} else None,
        created_at="2026-08-30T10:01:00Z",
        updated_at="2026-08-30T10:05:00Z",
    )
    ledger.create_task(task)
    ledger.create_run(run)
    ledger.append_event(
        LedgerEvent(
            event_id=f"{execution_id}:0001:created:intake_created",
            execution_id=run.execution_id,
            task_id=task.task_id,
            event_type="created",
            actor="orchestrator",
            payload={"transition": "intake_created"},
            created_at="2026-08-30T10:01:00Z",
        )
    )
    if phase not in {"executing", "pr_ready"}:
        ledger.append_event(
            LedgerEvent(
                event_id=f"{execution_id}:0002:github:pr_opened",
                execution_id=run.execution_id,
                task_id=task.task_id,
                event_type="pr_opened",
                actor="github",
                payload={"transition": "pr_opened", "pr_number": 412},
                created_at="2026-08-30T10:03:00Z",
            )
        )
    return project_root, state_dir, item, execution_id


@pytest.mark.parametrize(
    ("phase", "expected_status"),
    [
        ("executing", "working"),
        ("pr_ready", "working"),
        ("observing_ci", "reviewing"),
        ("review_ready", "reviewing"),
        ("fixing", "fixing"),
        ("waiting_telegram", "waiting"),
        ("merging", "working"),
    ],
)
def test_operator_queue_keeps_mission_visible_across_post_execution_lifecycle(
    tmp_path: Path,
    phase: str,
    expected_status: str,
) -> None:
    project_root, state_dir, item, execution_id = _operator_projection_fixture(tmp_path, phase)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    first = build_team_workspace(
        state=None,
        workflow_active=False,
        project_root=project_root,
        project_key="GOODTEAM",
        operator_state_dir=state_dir,
    )
    second = build_team_workspace(
        state=None,
        workflow_active=False,
        project_root=project_root,
        project_key="GOODTEAM",
        operator_state_dir=state_dir,
    )

    assert first == second
    assert first["mission"] == {
        "id": item["id"],
        "run_id": "",
        "execution_id": execution_id,
        "project_key": "GOODTEAM",
        "source": "operator_queue",
        "title": "Manager-centered team interaction",
        "objective": "Make Work feel like one AI team.",
        "status": expected_status,
        "phase": phase,
        "started_at": first["mission"]["started_at"],
        "updated_at": first["mission"]["updated_at"],
    }
    assert first["outcome_tasks"][0]["id"] == "task-375"
    assert first["outcome_tasks"][0]["status"] == expected_status
    assert first["outcome_tasks"][0]["status_reason"] == f"operator_{phase}"
    assert first["outcome_tasks"][0]["acceptance_criteria"] == [
        "Operator remains visible after execution"
    ]
    if phase == "executing":
        assert first["execution_actors"][0]["state"] == "working"
    else:
        assert len(first["execution_actors"]) == 1
        assert first["execution_actors"][0]["state"] == expected_status
        assert first["execution_actors"][0]["source"] == "operator_queue"
        assert all(actor["state"] != "completed" for actor in first["execution_actors"])
    if phase not in {"executing", "pr_ready"}:
        assert first["outcome_tasks"][0]["primary_outcome"] == {
            "kind": "pull_request",
            "label": "PR #412",
            "href": "https://github.com/LukeJiaoR/GoodTeam/pull/412",
            "path": "",
            "status": "ready",
        }
    if phase == "waiting_telegram":
        assert first["attention_request"]["reason"] == "operator_waiting_telegram"
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


def test_operator_queue_remains_authoritative_when_joined_ledger_is_malformed(tmp_path: Path) -> None:
    project_root, state_dir, item, execution_id = _operator_projection_fixture(tmp_path, "review_ready")
    execution_root = ExecutionLedger(project_root).executions_root / execution_id
    (execution_root / "task.json").write_text("{not-json", encoding="utf-8")
    (execution_root / "run.json").write_text("{not-json", encoding="utf-8")

    projection = build_team_workspace(
        state=None,
        workflow_active=False,
        project_root=project_root,
        project_key="GOODTEAM",
        operator_state_dir=state_dir,
    )

    assert projection["mission"]["id"] == item["id"]
    assert projection["mission"]["phase"] == "review_ready"
    assert projection["mission"]["title"] == "Manager-centered team interaction"
    assert projection["execution_actors"][0]["source"] == "operator_queue"


def test_running_execution_without_an_active_operator_queue_is_not_a_mission(tmp_path: Path) -> None:
    project_root, state_dir, item, _execution_id = _operator_projection_fixture(tmp_path, "executing")
    queue = DurableOperatorQueue(state_dir)
    queue.transition(item["id"], status="completed", phase="completed", expected_phase="executing")

    projection = build_team_workspace(
        state=None,
        workflow_active=False,
        project_root=project_root,
        project_key="GOODTEAM",
        operator_state_dir=state_dir,
    )

    assert projection == empty_team_workspace()


def test_active_operator_queue_is_scoped_to_the_current_project(tmp_path: Path) -> None:
    project_root, state_dir, _item, _execution_id = _operator_projection_fixture(tmp_path, "executing")

    projection = build_team_workspace(
        state=None,
        workflow_active=False,
        project_root=project_root,
        project_key="OTHER",
        operator_state_dir=state_dir,
    )

    assert projection == empty_team_workspace()


def test_no_active_durable_work_returns_stable_empty_contract(tmp_path: Path) -> None:
    assert build_team_workspace(state=None, workflow_active=False, project_root=tmp_path) == empty_team_workspace()
