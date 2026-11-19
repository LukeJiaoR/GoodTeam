"""Original native observation recovery, without transport replay or new authority."""
from copy import deepcopy

import pytest

from goodteam_os.project_human_feedback import question_board
from goodteam_os.project_iteration_continuation import stop_project_continuation
from goodteam_os.project_observation_runtime import reconcile_completed_observations
from native_observation_fixtures import NativeObservationFixture


@pytest.mark.parametrize("mode", ["current", "permission_omitted", "stopped"])
def test_lost_native_reply_recovers_only_original_identity_and_allowed_human_question(tmp_path, monkeypatch, mode):
    fixture = NativeObservationFixture(tmp_path, monkeypatch, human_questions=mode != "permission_omitted")
    fixture.candidate()
    fixture.output = {"assessment": "needs_attention",
        "rationale": "The owner needs to choose the intended audience while the Worker continues its task.",
        "observed_job_ids": ["worker"], "human_question": {
            "question": "Should this API serve internal or external consumers?",
            "why_human": "Only the owner can choose the intended product audience."}}
    fixture.lose_reply = True
    uncertain = fixture.judge()
    assert uncertain["running_judgments"]["one"]["status"] == "unconfirmed"
    identity = fixture.calls[0]["logical_id"]
    original = deepcopy(fixture.store.read()["manager_app_server"]["requests"][identity])
    def save_receipt(receipt):
        def save(session):
            session["manager_app_server"]["requests"][identity] = receipt
            return session
        fixture.store._mutate(save)
    def reconcile():
        return reconcile_completed_observations(store=fixture.store, root=tmp_path, project_key="DEMO",
            value=fixture.store.read()["project_continuation"], authority_check=lambda: True)
    # A different turn/result cannot settle the existing budget reservation.
    foreign = deepcopy(original)
    foreign["result"]["manager_execution"]["turn_id"] = "other-turn"
    save_receipt(foreign)
    assert reconcile()["running_judgments"]["one"]["status"] == "unconfirmed"
    assert fixture.judge()["manager_calls"] == 1 and len(fixture.calls) == 1
    save_receipt(original)
    if mode == "stopped":
        stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id="grant")
    recovered = reconcile()
    record = recovered["running_judgments"]["one"]
    assert record["status"] == {"current": "recorded", "permission_omitted": "invalid_response", "stopped": "stale_result"}[mode]
    assert recovered["status"] == ("stopped" if mode == "stopped" else "active")
    assert record["manager_invocation"]["logical_id"] == identity
    assert record["reconciliation"]["turn_id"] == original["turn_id"]
    assert not record["actions_executed"] and not record["worker_reply_attributed"]
    board = question_board(fixture.store.read())["items"]
    assert len(board) == (1 if mode == "current" else 0)
    if mode == "current":
        assert record["human_question_id"] == board[0]["id"] and board[0]["can_answer"]
    reconcile()
    if mode != "stopped":
        fixture.judge()
    assert len(question_board(fixture.store.read())["items"]) == len(board)
    assert len(fixture.calls) == fixture.store.read()["project_continuation"]["manager_calls"] == 1
    assert fixture.store.read()["project_task_batches"]["batch"] == fixture.batch
