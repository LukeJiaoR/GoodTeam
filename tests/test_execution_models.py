import pytest

from goodteam_os.execution.models import (
    ExecutionActor,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionTask,
    ExecutionTaskStatus,
    LedgerEvent,
    LedgerEventType,
    GitHubBinding,
    TaskContract,
)


def test_execution_task_serializes_to_stable_json_and_roundtrips():
    task = ExecutionTask(
        task_id="task_123",
        source_type="github_issue",
        repo="LukeJiaoR/GoodTeam",
        issue_number=220,
        title="P0-1A: Execution Models and Deterministic IDs",
        body="Add foundational runtime models.",
        goal="Create execution data contracts.",
        status="queued",
        created_at="2026-07-04T03:16:40Z",
        updated_at="2026-07-04T03:16:40Z",
    )

    payload = task.to_json()

    assert payload == (
        '{"body":"Add foundational runtime models.",'
        '"created_at":"2026-07-04T03:16:40Z",'
        '"goal":"Create execution data contracts.",'
        '"issue_number":220,'
        '"repo":"LukeJiaoR/GoodTeam",'
        '"source_type":"github_issue",'
        '"status":"queued",'
        '"task_id":"task_123",'
        '"title":"P0-1A: Execution Models and Deterministic IDs",'
        '"updated_at":"2026-07-04T03:16:40Z"}'
    )
    assert ExecutionTask.from_json(payload) == task
    assert task.status is ExecutionTaskStatus.QUEUED


def test_task_contract_v1_serializes_to_stable_json_and_roundtrips():
    contract = TaskContract(
        source_intent={
            "source_type": "github_issue",
            "title": "Typed Task Contract",
            "body": "Add typed contract data.",
        },
        task_identity={
            "task_id": "task_317",
            "repo": "LukeJiaoR/GoodTeam",
            "issue_number": 317,
        },
        summary="Typed Task Contract",
        instructions="Add typed contract data.",
        constraints=["Preserve legacy execution intake."],
        expected_artifacts=[
            {
                "name": "contract schema",
                "kind": "file",
                "path": "src/goodteam_os/execution/models.py",
                "required": True,
            }
        ],
        path_scope={
            "focus_paths": ["src/goodteam_os/execution/models.py"],
            "allowed_paths": ["src/goodteam_os/execution/"],
            "excluded_paths": [],
        },
        acceptance_criteria=["Stable JSON serialization."],
        verification_expectations=["PYTHONPATH=src .venv/bin/python -m pytest tests -q"],
        review_expectations=["Review contract summary in Workbench."],
        runtime_hints={"adapter": "codex"},
        pr_metadata={"title": "feat(execution): add typed task contract", "body": "Refs #317"},
        metadata={"labels": ["execution"]},
    )

    payload = contract.to_json()

    assert payload == (
        '{"acceptance_criteria":["Stable JSON serialization."],'
        '"constraints":["Preserve legacy execution intake."],'
        '"expected_artifacts":[{"kind":"file","name":"contract schema",'
        '"path":"src/goodteam_os/execution/models.py","required":true}],'
        '"instructions":"Add typed contract data.",'
        '"metadata":{"labels":["execution"]},'
        '"path_scope":{"allowed_paths":["src/goodteam_os/execution/"],'
        '"excluded_paths":[],"focus_paths":["src/goodteam_os/execution/models.py"]},'
        '"pr_metadata":{"body":"Refs #317","title":"feat(execution): add typed task contract"},'
        '"review_expectations":["Review contract summary in Workbench."],'
        '"runtime_hints":{"adapter":"codex"},'
        '"schema_version":"task_contract.v1",'
        '"source_intent":{"body":"Add typed contract data.",'
        '"source_type":"github_issue","title":"Typed Task Contract"},'
        '"summary":"Typed Task Contract",'
        '"task_identity":{"issue_number":317,"repo":"LukeJiaoR/GoodTeam","task_id":"task_317"},'
        '"verification_expectations":["PYTHONPATH=src .venv/bin/python -m pytest tests -q"]}'
    )
    assert TaskContract.from_json(payload) == contract


def test_execution_task_roundtrips_embedded_task_contract():
    contract = TaskContract(
        source_intent={"source_type": "github_issue"},
        task_identity={"task_id": "task_123"},
        summary="Add contract",
        instructions="Persist task_contract.v1.",
    )
    task = ExecutionTask(
        task_id="task_123",
        source_type="github_issue",
        repo="LukeJiaoR/GoodTeam",
        issue_number=317,
        title="Typed Task Contract",
        body="Persist task_contract.v1.",
        goal="Typed Task Contract",
        status="queued",
        created_at="2026-07-04T03:16:40Z",
        updated_at="2026-07-04T03:16:40Z",
        task_contract=contract,
    )

    payload = task.to_json()

    assert '"task_contract":{"acceptance_criteria":[]' in payload
    assert ExecutionTask.from_json(payload) == task
    assert isinstance(ExecutionTask.from_json(payload).task_contract, TaskContract)


def test_execution_task_legacy_json_without_contract_still_loads():
    payload = (
        '{"body":"Legacy task.",'
        '"created_at":"2026-07-04T03:16:40Z",'
        '"goal":"Legacy goal.",'
        '"issue_number":220,'
        '"repo":"LukeJiaoR/GoodTeam",'
        '"source_type":"github_issue",'
        '"status":"queued",'
        '"task_id":"task_legacy",'
        '"title":"Legacy task",'
        '"updated_at":"2026-07-04T03:16:40Z"}'
    )

    task = ExecutionTask.from_json(payload)

    assert task.task_contract is None
    assert task.to_json() == payload


def test_execution_run_serializes_to_stable_json_and_roundtrips():
    run = ExecutionRun(
        execution_id="exec_456",
        task_id="task_123",
        worker_job_id="worker_789",
        adapter="codex",
        status="running",
        branch_name="goodteam/exec_456",
        pr_number=42,
        created_at="2026-07-04T04:00:00Z",
        updated_at="2026-07-04T04:10:00Z",
    )

    payload = run.to_json()

    assert payload == (
        '{"adapter":"codex",'
        '"branch_name":"goodteam/exec_456",'
        '"created_at":"2026-07-04T04:00:00Z",'
        '"execution_id":"exec_456",'
        '"pr_number":42,'
        '"status":"running",'
        '"task_id":"task_123",'
        '"updated_at":"2026-07-04T04:10:00Z",'
        '"worker_job_id":"worker_789"}'
    )
    assert ExecutionRun.from_json(payload) == run
    assert run.status is ExecutionRunStatus.RUNNING


def test_ledger_event_serializes_payload_and_roundtrips():
    event = LedgerEvent(
        event_id="evt_001",
        execution_id="exec_456",
        task_id="task_123",
        event_type="status_changed",
        actor="orchestrator",
        payload={"next": "running", "previous": "queued"},
        created_at="2026-07-04T04:00:00Z",
    )

    payload = event.to_json()

    assert payload == (
        '{"actor":"orchestrator",'
        '"created_at":"2026-07-04T04:00:00Z",'
        '"event_id":"evt_001",'
        '"event_type":"status_changed",'
        '"execution_id":"exec_456",'
        '"payload":{"next":"running","previous":"queued"},'
        '"task_id":"task_123"}'
    )
    assert LedgerEvent.from_json(payload) == event
    assert event.event_type is LedgerEventType.STATUS_CHANGED
    assert event.actor is ExecutionActor.ORCHESTRATOR


def test_github_binding_serializes_to_stable_json_and_roundtrips():
    binding = GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=220,
        pr_number=42,
        branch_name="goodteam/exec_456",
        execution_id="exec_456",
    )

    payload = binding.to_json()

    assert payload == (
        '{"branch_name":"goodteam/exec_456",'
        '"execution_id":"exec_456",'
        '"issue_number":220,'
        '"pr_number":42,'
        '"repo":"LukeJiaoR/GoodTeam"}'
    )
    assert GitHubBinding.from_json(payload) == binding


def test_execution_models_reject_unknown_status_event_type_and_actor():
    with pytest.raises(ValueError, match="ExecutionTask.status"):
        ExecutionTask(
            task_id="task_123",
            source_type="github_issue",
            repo="LukeJiaoR/GoodTeam",
            issue_number=220,
            title="Title",
            body="Body",
            goal="Goal",
            status="queded",
            created_at="2026-07-04T03:16:40Z",
            updated_at="2026-07-04T03:16:40Z",
        )

    with pytest.raises(ValueError, match="ExecutionRun.status"):
        ExecutionRun(
            execution_id="exec_456",
            task_id="task_123",
            worker_job_id="worker_789",
            adapter="codex",
            status="done",
            branch_name="goodteam/exec_456",
            pr_number=None,
            created_at="2026-07-04T04:00:00Z",
            updated_at="2026-07-04T04:10:00Z",
        )

    with pytest.raises(ValueError, match="LedgerEvent.event_type"):
        LedgerEvent(
            event_id="evt_001",
            execution_id="exec_456",
            task_id="task_123",
            event_type="status_chagned",
            actor="orchestrator",
            payload={},
            created_at="2026-07-04T04:00:00Z",
        )

    with pytest.raises(ValueError, match="LedgerEvent.actor"):
        LedgerEvent(
            event_id="evt_001",
            execution_id="exec_456",
            task_id="task_123",
            event_type="status_changed",
            actor="orchstrator",
            payload={},
            created_at="2026-07-04T04:00:00Z",
        )
