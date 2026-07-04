from goodteam_os.execution.models import ExecutionRun, ExecutionTask, GitHubBinding, LedgerEvent


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
