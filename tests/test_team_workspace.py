from pathlib import Path

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.idempotency import execution_id_from_task
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent
from goodteam_os.state import create_initial_state
from goodteam_os.team_workspace import build_team_workspace, empty_team_workspace


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

    assert projection["attention_request"] == {
        "id": "approval:takeover",
        "kind": "approval",
        "target_id": "takeover",
        "reason": "approval_required",
        "refs": ["takeover"],
        "actions": ["approve", "request_changes", "review"],
    }
    assert {item["action"] for item in projection["capabilities"]} >= {
        "approve",
        "request_changes",
        "review",
        "cancel_mission",
    }


def test_operator_execution_uses_same_projection_without_writing(tmp_path: Path) -> None:
    ledger = ExecutionLedger(tmp_path)
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
    execution_id = execution_id_from_task(task.task_id)
    run = ExecutionRun(
        execution_id=execution_id,
        task_id=task.task_id,
        worker_job_id="job-375",
        adapter="codex",
        status="running",
        branch_name="issue-375",
        pr_number=None,
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
    corrupt = ledger.executions_root / "corrupt-execution"
    corrupt.mkdir()
    (corrupt / "task.json").write_text("{not-json", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    first = build_team_workspace(state=None, workflow_active=False, project_root=tmp_path)
    second = build_team_workspace(state=None, workflow_active=False, project_root=tmp_path)

    assert first == second
    assert first["mission"]["source"] == "operator_execution"
    assert first["mission"]["id"] == execution_id
    assert first["execution_actors"][0]["state"] == "working"
    assert first["outcome_tasks"][0]["id"] == "task-375"
    assert first["outcome_tasks"][0]["primary_outcome"] == {
        "kind": "pull_request",
        "label": "PR #412",
        "href": "https://github.com/LukeJiaoR/GoodTeam/pull/412",
        "path": "",
        "status": "ready",
    }
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_no_active_durable_work_returns_stable_empty_contract(tmp_path: Path) -> None:
    assert build_team_workspace(state=None, workflow_active=False, project_root=tmp_path) == empty_team_workspace()
