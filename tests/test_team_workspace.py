from pathlib import Path

import pytest

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.idempotency import execution_id_from_task
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent
from goodteam_os.operator_queue import DurableOperatorQueue
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
        assert first["execution_actors"][0]["state"] == "completed"
        assert any(actor["source"] == "operator_queue" for actor in first["execution_actors"])
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
    assert projection["execution_actors"][-1]["source"] == "operator_queue"


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
