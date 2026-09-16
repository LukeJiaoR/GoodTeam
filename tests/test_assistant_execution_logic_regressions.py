import pytest

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import (
    ExecutionActor,
    LedgerEvent,
    LedgerEventType,
)
from goodteam_os.execution.review_workbench_contract import stable_hash
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_assistant_drafts import send, setup_service
from tests.review_workbench_helpers import full_lifecycle


@pytest.mark.parametrize(
    ("current_node", "next_node", "archived", "expected_terminal_status"),
    [
        ("complete", "", False, "complete"),
        ("release_review_gate", "rejected", False, "rejected"),
        ("implementation", "code_review", True, "archived"),
    ],
)
def test_new_request_archives_only_terminal_bound_proposal_and_preserves_history(
    tmp_path, current_node, next_node, archived, expected_terminal_status,
):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    session_store = TaskAssistantSessionStore(tmp_path, "DEMO")
    session_store.confirm_task(first["id"], expected_revision=first["revision"])
    session_store.bind_workflow(first["id"], "run-first")

    state = create_initial_state("run-first", "DEMO", first["exact_request"])
    state.current_node = current_node
    state.next_node = next_node
    state_store = StateStore(tmp_path)
    state_store.write_project_current(state)
    if archived:
        state_store.archive_project_current("DEMO", reason="cancelled")

    result = send(service, root, "Build the second change.", "m2")

    history = result["archived_task_proposals"]
    assert len(history) == 1
    assert history[0]["id"] == first["id"]
    assert history[0]["run_id"] == "run-first"
    assert history[0]["source_message_ids"] == ["m1"]
    assert history[0]["exact_request"] == "Add search."
    assert history[0]["terminal_status"] == expected_terminal_status
    assert result["proposed_task"]["exact_request"] == "Build the second change."
    assert result["proposed_task"]["source_message_ids"] == ["m2"]
    assert {"m1", "m2"} <= {item["message_id"] for item in result["timeline"]}


def test_missing_current_state_does_not_release_proposal_with_start_claim(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    session_store = TaskAssistantSessionStore(tmp_path, "DEMO")
    session_store.confirm_task(first["id"], expected_revision=first["revision"])
    claimed = session_store.claim_workflow_start(first["id"], "claim-1", "run-starting")

    result = send(service, root, "Build the second change.", "m2")

    assert result["proposed_task"]["id"] == first["id"]
    assert result["proposed_task"]["start_claim_id"] == "claim-1"
    assert result["proposed_task"]["start_run_id"] == "run-starting"
    assert result["archived_task_proposals"] == []
    assert result["timeline"][-1]["event"] == "task_draft_locked"
    assert claimed["proposed_task"]["start_claim_id"] == "claim-1"


def test_terminal_state_for_another_run_does_not_release_proposal(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    session_store = TaskAssistantSessionStore(tmp_path, "DEMO")
    session_store.confirm_task(first["id"], expected_revision=first["revision"])
    session_store.bind_workflow(first["id"], "run-first")
    other_run = create_initial_state("run-other", "DEMO", "Other work")
    other_run.current_node = "complete"
    other_run.next_node = ""
    StateStore(tmp_path).write_project_current(other_run)

    result = send(service, root, "Build the second change.", "m2")

    assert result["proposed_task"]["id"] == first["id"]
    assert result["proposed_task"]["run_id"] == "run-first"
    assert result["archived_task_proposals"] == []
    assert result["timeline"][-1]["event"] == "task_draft_locked"


def test_reviewer_can_return_to_an_earlier_decision_after_requesting_changes(tmp_path):
    api, execution_id = full_lifecycle(tmp_path)

    first = api.record_review_decision(execution_id, "approve", "reviewer-a")
    api.record_review_decision(execution_id, "request_changes", "reviewer-a", "Fix tests")
    final = api.record_review_decision(execution_id, "approve", "reviewer-a")

    decisions = [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "review_decision_recorded"
    ]
    assert first["status"] == "recorded"
    assert final["status"] == "recorded"
    assert len(decisions) == 3
    assert api.get_review_workbench(execution_id)["reviewer_decision"]["decision"] == "approve"


def test_decision_keeps_full_evidence_hash_and_stable_dedup_hash(tmp_path):
    api, execution_id = full_lifecycle(tmp_path)
    evidence = api.get_evidence(execution_id)

    first = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")
    repeated = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")

    first_payload = first["event"]["payload"]
    repeated_payload = repeated["event"]["payload"]
    assert first_payload["evidence_hash"] == stable_hash(evidence)
    assert first_payload["decision_evidence_hash"]
    assert repeated["status"] == "deduped"
    assert repeated_payload["decision_evidence_hash"] == first_payload["decision_evidence_hash"]


def test_legacy_decision_without_dedup_hash_allows_one_new_record(tmp_path):
    api, execution_id = full_lifecycle(tmp_path)
    evidence = api.get_evidence(execution_id)
    ExecutionLedger(tmp_path).append_event(
        LedgerEvent(
            event_id=f"{execution_id}:legacy:review_decision_recorded",
            execution_id=execution_id,
            task_id=str(evidence["task_id"]),
            event_type=LedgerEventType.REVIEW_RECORDED,
            actor=ExecutionActor.SYSTEM,
            payload={
                "transition": "review_decision_recorded",
                "execution_id": execution_id,
                "decision": "approve",
                "reviewer": "reviewer-a",
                "reason": "ok",
                "source": "review_workbench",
                "evidence_hash": stable_hash(evidence),
            },
            created_at="2026-09-12T00:00:00Z",
        )
    )

    recorded = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")
    repeated = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")

    assert recorded["status"] == "recorded"
    assert recorded["event"]["payload"]["decision_evidence_hash"]
    assert repeated["status"] == "deduped"
    assert repeated["event"]["event_id"] == recorded["event"]["event_id"]


def test_same_decision_is_recorded_again_when_execution_evidence_changes(tmp_path):
    api, execution_id = full_lifecycle(tmp_path)
    first = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")
    ledger = ExecutionLedger(tmp_path)
    ledger.append_event(
        LedgerEvent(
            event_id=f"{execution_id}:evidence-change:review_recorded",
            execution_id=execution_id,
            task_id="task-evidence-change",
            event_type=LedgerEventType.REVIEW_RECORDED,
            actor=ExecutionActor.GITHUB,
            payload={
                "transition": "review_recorded",
                "github_id": "new-review-1",
                "review_state": "commented",
                "actor_login": "reviewer-b",
                "submitted_at": "2026-09-12T01:00:00Z",
                "body_excerpt": "Additional evidence",
            },
            created_at="2026-09-12T01:00:00Z",
        )
    )

    second = api.record_review_decision(execution_id, "approve", "reviewer-a", "ok")

    assert second["status"] == "recorded"
    assert second["event"]["event_id"] != first["event"]["event_id"]
    assert second["event"]["payload"]["evidence_hash"] != first["event"]["payload"]["evidence_hash"]
    assert second["event"]["payload"]["decision_evidence_hash"] != first["event"]["payload"]["decision_evidence_hash"]
