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

