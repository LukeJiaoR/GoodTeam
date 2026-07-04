from __future__ import annotations

import json

import pytest

from goodteam_os.execution.idempotency import execution_id_from_task
from goodteam_os.execution import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent


def _task(task_id: str = "task_123") -> ExecutionTask:
    return ExecutionTask(
        task_id=task_id,
        source_type="github_issue",
        repo="LukeJiaoR/GoodTeam",
        issue_number=226,
        title="P0-1B: Execution Ledger Facade",
        body="Add append-only local ledger.",
        goal="Create local execution truth layer.",
        status="queued",
        created_at="2026-07-04T03:16:40Z",
        updated_at="2026-07-04T03:16:40Z",
    )


def _run(execution_id: str, task_id: str = "task_123") -> ExecutionRun:
    return ExecutionRun(
        execution_id=execution_id,
        task_id=task_id,
        worker_job_id="worker_789",
        adapter="codex",
        status="running",
        branch_name=f"goodteam/{execution_id}",
        pr_number=None,
        created_at="2026-07-04T04:00:00Z",
        updated_at="2026-07-04T04:10:00Z",
    )


def _event(
    event_id: str,
    execution_id: str,
    *,
    event_type: str = "status_changed",
    created_at: str = "2026-07-04T04:00:00Z",
) -> LedgerEvent:
    return LedgerEvent(
        event_id=event_id,
        execution_id=execution_id,
        task_id="task_123",
        event_type=event_type,
        actor="orchestrator",
        payload={"event_id": event_id},
        created_at=created_at,
    )


def test_task_and_run_json_write_and_read_roundtrip(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    task = _task()
    execution_id = execution_id_from_task(task.task_id)
    run = _run(execution_id)

    task_path = ledger.create_task(task)
    run_path = ledger.create_run(run)

    assert task_path == tmp_path / ".goodteam" / "executions" / execution_id / "task.json"
    assert run_path == tmp_path / ".goodteam" / "executions" / execution_id / "run.json"
    assert ledger.read_task(execution_id) == task
    assert ledger.read_run(execution_id) == run


def test_event_append_preserves_file_order(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    execution_id = "exec_456"
    first = _event("evt_001", execution_id, event_type="created")
    second = _event("evt_002", execution_id, created_at="2026-07-04T04:05:00Z")

    first_path = ledger.append_event(first)
    second_path = ledger.append_event(second)

    assert first_path == second_path
    assert ledger.read_events(execution_id) == [first, second]

    raw_lines = first_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in raw_lines] == ["evt_001", "evt_002"]


def test_replay_uses_disk_state_from_fresh_ledger(tmp_path):
    task = _task()
    execution_id = execution_id_from_task(task.task_id)
    run = _run(execution_id)
    first = _event("evt_001", execution_id, event_type="created")
    second = _event("evt_002", execution_id)

    writer = ExecutionLedger(tmp_path)
    writer.create_task(task)
    writer.create_run(run)
    writer.append_event(first)
    writer.append_event(second)

    replay = ExecutionLedger(tmp_path).replay(execution_id)

    assert replay == {
        "execution_id": execution_id,
        "task": task,
        "run": run,
        "events": [first, second],
    }


def test_partial_execution_replay_allows_missing_task_or_run(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    execution_id = "exec_partial"
    event = _event("evt_001", execution_id, event_type="created")

    ledger.append_event(event)

    assert ledger.read_task(execution_id) is None
    assert ledger.read_run(execution_id) is None
    assert ledger.replay(execution_id) == {
        "execution_id": execution_id,
        "task": None,
        "run": None,
        "events": [event],
    }


def test_malformed_event_file_fails_with_path_and_line_number(tmp_path):
    ledger = ExecutionLedger(tmp_path)
    execution_id = "exec_bad"
    events_path = tmp_path / ".goodteam" / "executions" / execution_id / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(f"{_event('evt_001', execution_id).to_json()}\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        ledger.read_events(execution_id)

    message = str(exc_info.value)
    assert str(events_path) in message
    assert "line 2" in message
