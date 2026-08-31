import json
from pathlib import Path

import pytest

from goodteam_os import web
from goodteam_os.intent_commit import IntentCommitStore, deterministic_interpretation
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state


def _approval_fixture(workspace: Path) -> tuple[dict[str, str], str]:
    artifact = workspace / "prd.md"
    artifact.write_text("# PRD\n\nShip Google sign-in first.\n", encoding="utf-8")
    state = create_initial_state("run-intent", "DEMO", "Add provider-based authentication")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = str(artifact)
    StateStore(workspace).write_project_current(state)
    target = {
        "expected_run_id": state.run_id,
        "expected_target_type": "review",
        "expected_target_id": "prd",
        "expected_artifact_key": "prd",
        "expected_artifact_version": web.artifact_version_for_path(str(artifact)),
        "expected_task_id": "DEMO-PRD",
    }
    return target, str(artifact)


def _start(workspace: Path) -> dict[str, object]:
    target, _ = _approval_fixture(workspace)
    return web.start_request_changes_intent(
        workspace=workspace,
        project_key="DEMO",
        approval="prd",
        **target,
    )


def test_deterministic_interpretation_preserves_only_explicit_request() -> None:
    interpretation = deterministic_interpretation(
        "  Ship Google only.\n\nKeep the provider interface generic.  "
    )

    assert interpretation == {
        "schema_version": "intent_interpretation.v1",
        "source": "deterministic_explicit_request",
        "summary": "Ship Google only.\n\nKeep the provider interface generic.",
        "items": [
            {"kind": "explicit_request", "text": "Ship Google only."},
            {"kind": "explicit_request", "text": "Keep the provider interface generic."},
        ],
    }


def test_start_and_discussion_are_durable_without_approval_mutation(tmp_path: Path, monkeypatch) -> None:
    decide_calls: list[dict[str, object]] = []
    monkeypatch.setattr(web, "decide_approval", lambda **kwargs: decide_calls.append(kwargs))

    opened = _start(tmp_path)
    intent = opened["intent_commit"]
    assert opened["created"] is True
    assert intent["status"] == "discussing"
    assert decide_calls == []
    assert StateStore(tmp_path).read_project_current("DEMO").approvals["prd"] == ApprovalDecision.PENDING

    duplicate = web.start_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        approval="prd",
        expected_run_id=intent["target"]["run_id"],
        expected_target_type=intent["target"]["target_type"],
        expected_target_id=intent["target"]["target_id"],
        expected_artifact_key=intent["target"]["artifact_key"],
        expected_artifact_version=intent["target"]["artifact_version"],
        expected_task_id=intent["target"]["task_id"],
    )
    assert duplicate["created"] is False
    assert duplicate["intent_commit"]["id"] == intent["id"]

    discussed = web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text="Implement Google only, while keeping the provider interface generic.",
    )["intent_commit"]
    assert discussed["status"] == "ready_to_confirm"
    assert discussed["interpretation"]["summary"] == (
        "Implement Google only, while keeping the provider interface generic."
    )
    assert decide_calls == []

    restored = IntentCommitStore(tmp_path, "DEMO").get(intent["id"])
    assert restored == discussed
    assert not (tmp_path / "state" / "projects" / "DEMO" / "action-log.jsonl").exists()


def test_keep_discussing_returns_to_draft_without_committing(tmp_path: Path, monkeypatch) -> None:
    decide_calls: list[dict[str, object]] = []
    monkeypatch.setattr(web, "decide_approval", lambda **kwargs: decide_calls.append(kwargs))
    intent = _start(tmp_path)["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text="Keep the provider boundary generic.",
    )

    resumed = web.keep_discussing_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )["intent_commit"]

    assert resumed["status"] == "discussing"
    assert resumed["draft_text"] == "Keep the provider boundary generic."
    assert decide_calls == []
    assert StateStore(tmp_path).read_project_current("DEMO").approvals["prd"] == ApprovalDecision.PENDING


def test_confirmation_uses_the_latest_interpretation_after_more_discussion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = _start(tmp_path)["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text="Implement Google only.",
    )
    web.keep_discussing_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )
    latest = "Implement Google only and preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=latest,
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        web,
        "decide_approval",
        lambda **kwargs: calls.append(kwargs) or {"run_id": "run-intent", "next_node": "revise_prd"},
    )

    web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    assert calls[0]["note"] == latest


def test_confirm_commits_confirmed_interpretation_exactly_once(tmp_path: Path, monkeypatch) -> None:
    intent = _start(tmp_path)["intent_commit"]
    instruction = "Implement Google only; preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=instruction,
    )
    decide_calls: list[dict[str, object]] = []

    def fake_decide_approval(**kwargs):
        decide_calls.append(kwargs)
        return {"run_id": "run-intent", "next_node": "revise_prd"}

    monkeypatch.setattr(web, "decide_approval", fake_decide_approval)

    first = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )
    second = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    assert first["committed"] is True
    assert first["idempotent"] is False
    assert second["committed"] is True
    assert second["idempotent"] is True
    assert len(decide_calls) == 1
    call = decide_calls[0]
    assert call["decision"] == "changes_requested"
    assert call["note"] == instruction
    assert call["decision_origin"] == "manager_intent_commit"
    assert call["review_mode"] == "intent_commit"
    assert call["review_session_id"] == intent["id"]
    assert call["expected_run_id"] == "run-intent"
    assert call["expected_artifact_version"] == intent["target"]["artifact_version"]
    assert IntentCommitStore(tmp_path, "DEMO").get(intent["id"])["status"] == "committed"


def test_confirm_uses_existing_approval_authority_and_writes_one_decision(tmp_path: Path) -> None:
    intent = _start(tmp_path)["intent_commit"]
    instruction = "Implement Google only and preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=instruction,
    )

    first = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )
    second = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    decisions = [
        item
        for item in web.build_action_items(tmp_path, project_key="DEMO")
        if item["action"] == "approval_decided"
    ]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "changes_requested"
    assert decisions[0]["note"] == instruction
    assert decisions[0]["review_mode"] == "intent_commit"
    assert decisions[0]["review_session_id"] == intent["id"]
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["run_id"] == "run-intent"
    assert second["approval"] == "prd"


def test_confirm_uses_existing_approval_authority_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    intent = _start(tmp_path)["intent_commit"]
    instruction = "Ship Google only and preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=instruction,
    )

    first = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )
    second = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    assert first["committed"] is True
    assert first["idempotent"] is False
    assert second["committed"] is True
    assert second["idempotent"] is True
    actions = [
        json.loads(line)
        for line in (
            tmp_path / "state" / "projects" / "DEMO" / "action-log.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    decisions = [item for item in actions if item.get("action") == "approval_decided"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "changes_requested"
    assert decisions[0]["note"] == instruction
    assert decisions[0]["review_mode"] == "intent_commit"
    assert decisions[0]["review_session_id"] == intent["id"]
    assert decisions[0]["client_context"]["intent_commit_id"] == intent["id"]


def test_restart_after_begin_confirm_without_decision_evidence_retries_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = _start(tmp_path)["intent_commit"]
    instruction = "Ship Google only and preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=instruction,
    )
    first_store = IntentCommitStore(tmp_path, "DEMO")
    interrupted, already_committed = first_store.begin_confirm(intent["id"])
    assert already_committed is False
    assert interrupted["status"] == "committing"

    restarted_store = IntentCommitStore(tmp_path, "DEMO")
    assert restarted_store.get(intent["id"])["status"] == "committing"
    decide_calls: list[dict[str, object]] = []

    def fake_decide_approval(**kwargs):
        decide_calls.append(kwargs)
        return {"run_id": "run-intent", "next_node": "revise_prd"}

    monkeypatch.setattr(web, "decide_approval", fake_decide_approval)

    recovered = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    assert recovered["committed"] is True
    assert recovered["idempotent"] is False
    assert len(decide_calls) == 1
    assert decide_calls[0]["note"] == instruction
    assert IntentCommitStore(tmp_path, "DEMO").get(intent["id"])["status"] == "committed"


def test_restart_after_authority_decision_reconciles_without_replay(tmp_path: Path) -> None:
    intent = _start(tmp_path)["intent_commit"]
    instruction = "Ship Google only and preserve a generic provider interface."
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text=instruction,
    )
    first_store = IntentCommitStore(tmp_path, "DEMO")
    confirming, already_committed = first_store.begin_confirm(intent["id"])
    assert already_committed is False
    interpretation = dict(confirming["confirmed_interpretation"])
    target = dict(confirming["target"])

    web.decide_approval(
        workspace=tmp_path,
        project_key="DEMO",
        approval=str(target["approval"]),
        decision="changes_requested",
        note=instruction,
        decision_origin="manager_intent_commit",
        review_mode="intent_commit",
        review_session_id=intent["id"],
        client_context={
            "intent_commit_id": intent["id"],
            "interpretation_schema_version": interpretation["schema_version"],
        },
        expected_run_id=str(target["run_id"]),
        expected_target_type=str(target["target_type"]),
        expected_target_id=str(target["target_id"]),
        expected_artifact_key=str(target["artifact_key"]),
        expected_artifact_version=str(target["artifact_version"]),
    )
    restarted_store = IntentCommitStore(tmp_path, "DEMO")
    assert restarted_store.get(intent["id"])["status"] == "committing"

    recovered = web.confirm_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
    )

    decisions = [
        item
        for item in web.build_action_items(tmp_path, project_key="DEMO")
        if item["action"] == "approval_decided"
        and item.get("review_session_id") == intent["id"]
    ]
    assert len(decisions) == 1
    assert recovered["committed"] is True
    assert recovered["idempotent"] is True
    assert recovered["reconciled"] is True
    assert recovered["intent_commit"]["status"] == "committed"


def test_stale_artifact_version_prevents_confirm_and_marks_intent_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = _start(tmp_path)["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text="Keep the provider abstraction generic.",
    )
    _, artifact_path = _approval_fixture(tmp_path)
    Path(artifact_path).write_text("# PRD\n\nA different version.\n", encoding="utf-8")
    decide_calls: list[dict[str, object]] = []
    monkeypatch.setattr(web, "decide_approval", lambda **kwargs: decide_calls.append(kwargs))

    with pytest.raises(web.ProjectApiError) as raised:
        web.confirm_request_changes_intent(
            workspace=tmp_path,
            project_key="DEMO",
            intent_id=intent["id"],
        )

    assert raised.value.code == "stale_intent_target"
    assert decide_calls == []
    assert IntentCommitStore(tmp_path, "DEMO").get(intent["id"])["status"] == "stale"


def test_ambiguous_partial_commit_fails_closed_without_replaying_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intent = _start(tmp_path)["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="DEMO",
        intent_id=intent["id"],
        text="Keep the provider abstraction generic.",
    )
    decide_calls: list[dict[str, object]] = []

    def partially_recorded_decision(**kwargs):
        decide_calls.append(kwargs)
        web.append_action(
            tmp_path,
            run_id="run-intent",
            actor="human",
            action="approval_decided",
            project_key="DEMO",
            gate="prd",
            decision="changes_requested",
            note=str(kwargs["note"]),
            review_mode="intent_commit",
            review_session_id=intent["id"],
        )
        raise RuntimeError("state persistence interrupted after action evidence")

    monkeypatch.setattr(web, "decide_approval", partially_recorded_decision)

    with pytest.raises(web.ProjectApiError) as first:
        web.confirm_request_changes_intent(
            workspace=tmp_path,
            project_key="DEMO",
            intent_id=intent["id"],
        )
    with pytest.raises(web.ProjectApiError) as duplicate:
        web.confirm_request_changes_intent(
            workspace=tmp_path,
            project_key="DEMO",
            intent_id=intent["id"],
        )

    assert first.value.code == "intent_commit_ambiguous"
    assert duplicate.value.code == "intent_commit_ambiguous"
    assert len(decide_calls) == 1
    assert IntentCommitStore(tmp_path, "DEMO").get(intent["id"])["status"] == "ambiguous"
