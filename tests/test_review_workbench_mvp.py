from __future__ import annotations

import pytest

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.models import ExecutionRun
from goodteam_os.execution.review_workbench import render_review_workbench
from tests.review_workbench_helpers import (
    api as _api,
    failed_event as _failed_event,
    full_lifecycle as _full_lifecycle,
    memory_event_files as _memory_event_files,
    warning_codes as _warning_codes,
)


def test_completed_execution_builds_evidence_backed_workbench_object(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    workbench = api.get_review_workbench(execution_id)

    assert workbench["schema_version"] == "review_workbench.v1"
    assert workbench["execution_id"] == execution_id
    assert workbench["evidence"] == api.get_evidence(execution_id)
    assert workbench["source_issue"] == "LukeJiaoR/GoodTeam#216"
    assert workbench["task"]["title"] == "P0-2: Review Workbench MVP"
    assert workbench["pr"]["ref"] == "LukeJiaoR/GoodTeam#2160"
    assert workbench["pr"]["commit_sha"] == "commit-sha-216"
    assert workbench["status"] == "completed"
    assert workbench["rationale"]["available"] is True
    assert workbench["rationale"]["worker_result"]["available"] is True
    assert workbench["reviewer_decision"]["available"] is False
    assert workbench["sandbox"]["available"] is False


def test_pending_execution_returns_warnings_and_incomplete_reason(tmp_path):
    api = _api(tmp_path)
    intake = api.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=216,
        adapter="fake",
    )
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)

    workbench = api.get_review_workbench(execution_id)

    assert workbench["status"] == "pending"
    assert workbench["evidence"]["incomplete_reason"] == "pr_missing"
    assert workbench["pr"]["ref"] == ""
    assert _warning_codes(workbench) >= {"pr_missing", "diff_artifact_missing"}


def test_failed_execution_returns_failed_status_and_failure_evidence(tmp_path):
    execution_id = "exec_failed_workbench"
    ledger = ExecutionLedger(tmp_path)
    ledger.create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_workbench",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )
    ledger.append_event(_failed_event(execution_id))

    workbench = ExecutionAPI(tmp_path).get_review_workbench(execution_id)

    assert workbench["status"] == "failed"
    assert workbench["evidence"]["incomplete_reason"] == "execution_failed:worker_failed"
    assert _warning_codes(workbench) >= {"execution_failed", "diff_artifact_missing"}
    assert workbench["evidence"]["ledger_events"][0]["payload"]["failure_class"] == "worker_failed"


def test_memory_provenance_integrity_is_exposed(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    finalized = api.finalize(execution_id)

    workbench = api.get_review_workbench(execution_id)

    assert workbench["memory_provenance_integrity"]["complete"] is True
    assert workbench["memory_refs"][0]["memory_event_id"] == finalized["memory_event_id"]
    assert workbench["memory_refs"][0]["ledger_event_id"] == finalized["ledger_event_id"]


def test_missing_diff_artifact_returns_explicit_empty_state(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    diff = api.get_review_workbench(execution_id)["diff"]

    assert diff == {
        "available": False,
        "reason": "diff_artifact_missing",
        "changed_files": [],
        "artifact_path": "",
        "summary": "",
    }


def test_record_approve_decision_writes_ledger_event(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    result = api.record_review_decision(
        execution_id,
        "approve",
        "LukeJiaoR",
        reason="Evidence checks out.",
    )

    event = result["event"]
    assert result["status"] == "recorded"
    assert event["event_type"] == "review_recorded"
    assert event["actor"] == "system"
    assert event["payload"]["transition"] == "review_decision_recorded"
    assert event["payload"]["source"] == "review_workbench"
    assert event["payload"]["decision"] == "approve"
    assert event["payload"]["reviewer"] == "LukeJiaoR"
    assert event["payload"]["reason"] == "Evidence checks out."
    assert event["payload"]["evidence_hash"]


def test_record_request_changes_decision_writes_ledger_event(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    api.record_review_decision(
        execution_id,
        "request_changes",
        "reviewer-b",
        reason="Need a persisted diff artifact.",
    )

    event = ExecutionLedger(tmp_path).read_events(execution_id)[-1]
    assert event.payload["transition"] == "review_decision_recorded"
    assert event.payload["decision"] == "request_changes"
    assert event.payload["reviewer"] == "reviewer-b"


def test_invalid_review_decision_is_rejected(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    with pytest.raises(ValueError, match="Review decision must be one of"):
        api.record_review_decision(execution_id, "maybe", "LukeJiaoR")


def test_repeated_same_decision_is_idempotent(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    first = api.record_review_decision(execution_id, "approve", "LukeJiaoR", "ok")
    second = api.record_review_decision(execution_id, "approve", "LukeJiaoR", "ok")

    events = [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "review_decision_recorded"
    ]
    assert second["status"] == "deduped"
    assert second["event"]["event_id"] == first["event"]["event_id"]
    assert len(events) == 1


def test_review_decision_appears_in_subsequent_workbench_object(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    recorded = api.record_review_decision(execution_id, "reject", "reviewer-c", "unsafe")

    decision = api.get_review_workbench(execution_id)["reviewer_decision"]

    assert decision["available"] is True
    assert decision["event_id"] == recorded["event"]["event_id"]
    assert decision["decision"] == "reject"
    assert decision["reviewer"] == "reviewer-c"
    assert decision["reason"] == "unsafe"


def test_review_decision_does_not_write_memory(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    before = _memory_event_files(tmp_path)

    api.record_review_decision(execution_id, "approve", "LukeJiaoR")

    assert _memory_event_files(tmp_path) == before


def test_review_decision_does_not_mutate_existing_ledger_events(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    ledger = ExecutionLedger(tmp_path)
    before = ledger.read_events(execution_id)

    api.record_review_decision(execution_id, "approve", "LukeJiaoR")

    after = ledger.read_events(execution_id)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 1


def test_text_renderer_consumes_canonical_review_object(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.finalize(execution_id)
    api.record_review_decision(execution_id, "approve", "LukeJiaoR", "ok")

    output = render_review_workbench(api.get_review_workbench(execution_id))

    assert f"Execution: {execution_id}" in output
    assert "Issue: LukeJiaoR/GoodTeam#216" in output
    assert "PR: LukeJiaoR/GoodTeam#2160" in output
    assert "CI: success" in output
    assert "Memory provenance: complete" in output
    assert "Decision: approve by LukeJiaoR" in output
    assert "diff_artifact_missing" in output
