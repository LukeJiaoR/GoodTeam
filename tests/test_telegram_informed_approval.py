"""Telegram buttons fail closed and leave a recoverable, undecided review."""

from pathlib import Path

import pytest

from goodteam_os import telegram, web
from test_approval_rationale_boundary import durable_files
from test_workflow_recovery import gate_state


@pytest.mark.parametrize("missing_artifact", [False, True])
@pytest.mark.parametrize("entrypoint", ["card", "legacy", "command"])
def test_missing_reason_or_material_is_reported_without_deciding(tmp_path, monkeypatch, missing_artifact, entrypoint):
    state = gate_state(tmp_path, "release")
    if missing_artifact:
        for path in state.artifacts.values():
            Path(path).unlink()
    store = telegram.TelegramReviewSessionStore(tmp_path)
    session = {"session_id": "informed", "project_key": "RECOVERY", "run_id": state.run_id,
               "gate": "release", "chat_id": 100, "message_id": 200, "items": [], "decided": False}
    store.save("informed", session)
    before = durable_files(tmp_path)
    sent = []
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram, "_telegram_request", lambda token, method, params: sent.append((method, params)) or {"ok": True})
    if entrypoint == "command":
        update = {"message": {"message_id": 201, "from": {"id": 111}, "chat": {"id": 100},
                              "reply_to_message": {"message_id": 200}, "text": "/gt approve"}}
    else:
        data = "v1:A:informed" if entrypoint == "card" else telegram.encode_callback_data(
            telegram.TelegramApproval("RECOVERY", state.run_id, "release", "approved"))
        update = {"callback_query": {"id": "click", "from": {"id": 111},
                                     "message": {"chat": {"id": 100}, "message_id": 200}, "data": data}}
    result = telegram.process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=set())
    assert result["status"] == "processed"
    assert result["accepted"] is False
    assert result["reason"] == ("stale_approval" if missing_artifact else "approval_reason_required")
    assert store.get("informed")["decided"] is False
    assert durable_files(tmp_path) == before
    assert not web.build_action_items(tmp_path, "RECOVERY")
    assert any(method == "sendMessage" and "No approval was recorded" in params["text"] for method, params in sent)


def test_polling_never_reports_a_rejected_approval_as_recorded(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "release")
    store = telegram.TelegramReviewSessionStore(tmp_path)
    store.save("informed", {"session_id": "informed", "project_key": "RECOVERY", "run_id": state.run_id,
                           "gate": "release", "chat_id": 100, "message_id": 200, "items": [], "decided": False})
    update = {"update_id": 1, "callback_query": {"id": "click", "from": {"id": 111},
              "message": {"chat": {"id": 100}, "message_id": 200}, "data": "v1:A:informed"}}
    sent = []

    def transport(token, method, params):
        sent.append((method, params))
        return {"ok": True, "result": [update] if method == "getUpdates" else {}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOODTEAM_TG_ALLOWED_USERS", "111")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "100")
    monkeypatch.setattr(telegram, "_telegram_request", transport)
    before = durable_files(tmp_path)
    telegram.poll_telegram_approvals(workspace=tmp_path, once=True)
    answers = [params["text"] for method, params in sent if method == "answerCallbackQuery"]
    assert answers == ["GoodTeam approval was not recorded. Open the current Review page."]
    assert durable_files(tmp_path) == before
