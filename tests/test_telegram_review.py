import json
from datetime import datetime, UTC, timedelta

from goodteam_os.telegram import (
    TelegramReviewSessionStore,
    mask_sensitive_data,
    split_artifact_into_review_items,
    process_telegram_update,
    update_active_tg_review_on_web_decision,
)
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore


def _review_session_data(
    session_id: str,
    *,
    chat_id: int = 12345,
    message_id: int = 9999,
    decided: bool = False,
    expires_delta: timedelta = timedelta(hours=1),
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "run_id": "run-xyz",
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": chat_id,
        "message_id": message_id,
        "cursor": 0,
        "items": [],
        "decided": decided,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + expires_delta).isoformat(),
    }


def test_session_store_lifecycle(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    session_id = "test12"

    # 1. Get non-existent
    assert store.get(session_id) is None

    # 2. Save and get
    session_data = {
        "session_id": session_id,
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": 12345,
        "decided": False,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    store.save(session_id, session_data)
    loaded = store.get(session_id)
    assert loaded is not None
    assert loaded["project_key"] == "CONTENTOPS"
    assert loaded["chat_id"] == 12345

    # 3. Find by chat and status
    found = store.find_by_chat_and_status(12345, decided=False)
    assert found is not None
    assert found["session_id"] == session_id

    # 4. Expiration
    session_data_expired = {
        "session_id": "exp123",
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": 12345,
        "decided": False,
        "created_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
        "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    store.save("exp123", session_data_expired)
    assert store.get("exp123") is None  # Should be expired and deleted
    assert (tmp_path / "state" / "tg-reviews" / "exp123.json").exists() is False


def test_session_store_finds_live_session_by_chat_and_card_message_id(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    store.save("target", _review_session_data("target", chat_id=12345, message_id=777))
    store.save("other-chat", _review_session_data("other-chat", chat_id=54321, message_id=777))

    found = store.find_live_by_card_message(12345, 777)

    assert found is not None
    assert found["session_id"] == "target"
    assert store.find_live_by_card_message(12345, 778) is None


def test_session_store_card_message_lookup_does_not_cross_chats(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    store.save("target", _review_session_data("target", chat_id=12345, message_id=777))
    store.save("same-message-other-chat", _review_session_data("same-message-other-chat", chat_id=54321, message_id=777))

    found = store.find_live_by_card_message(54321, 777)

    assert found is not None
    assert found["session_id"] == "same-message-other-chat"


def test_session_store_card_message_lookup_ignores_expired_sessions(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    store.save("expired", _review_session_data("expired", message_id=777, expires_delta=timedelta(minutes=-1)))

    assert store.find_live_by_card_message(12345, 777) is None
    assert (tmp_path / "state" / "tg-reviews" / "expired.json").exists() is False


def test_session_store_lists_only_live_sessions_for_chat(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    store.save("live-a", _review_session_data("live-a", chat_id=12345, message_id=701))
    store.save("live-b", _review_session_data("live-b", chat_id=12345, message_id=702))
    store.save("decided", _review_session_data("decided", chat_id=12345, message_id=703, decided=True))
    store.save("expired", _review_session_data("expired", chat_id=12345, message_id=704, expires_delta=timedelta(minutes=-1)))
    store.save("other-chat", _review_session_data("other-chat", chat_id=54321, message_id=705))

    sessions = store.list_live_for_chat(12345)

    assert [session["session_id"] for session in sessions] == ["live-a", "live-b"]
    assert (tmp_path / "state" / "tg-reviews" / "expired.json").exists() is False


def test_session_store_existing_live_gate_lookup_still_works(tmp_path):
    store = TelegramReviewSessionStore(tmp_path)
    store.save("target", _review_session_data("target", message_id=777))

    found = store.find_live_for_gate("CONTENTOPS", "run-xyz", "staging")

    assert found is not None
    assert found["session_id"] == "target"


def test_mask_sensitive_data():
    raw_ip = "Connecting to 192.168.1.100 on port 22"
    assert "192.168.1.100" not in mask_sensitive_data(raw_ip)
    assert "staging-ip" in mask_sensitive_data(raw_ip)

    raw_ssh = "SSH target is root@107.148.180.35"
    assert "107.148.180.35" not in mask_sensitive_data(raw_ssh)
    assert "staging-host" in mask_sensitive_data(raw_ssh)

    raw_user_dir = "Files saved in /Users/luke/workspace/GoodTeam/output"
    assert "/Users/luke/" not in mask_sensitive_data(raw_user_dir)
    assert "local-workspace-dir" in mask_sensitive_data(raw_user_dir)

    raw_srv_dir = "Deployed to /srv/agentsmesh/deploy"
    assert "/srv/agentsmesh" not in mask_sensitive_data(raw_srv_dir)
    assert "deployment-dir" in mask_sensitive_data(raw_srv_dir)


def test_split_artifact_into_review_items_staging():
    content = """# Staging Execution Summary
Staging run completed successfully with exit code 0.

# Healthcheck Results
All container health checks returned pass status.

# Acceptance Results
- check 1: pass
- check 2: pass
"""
    items = split_artifact_into_review_items("staging", content)
    assert len(items) == 4
    # Acceptance
    acc_item = next(it for it in items if it["id"] == "acceptance")
    assert "check 1" in acc_item["body"]
    assert acc_item["state"] == "pending"

    # Healthcheck
    hc_item = next(it for it in items if it["id"] == "healthcheck")
    assert "returned pass status" in hc_item["body"]

    # Evidence
    ev_item = next(it for it in items if it["id"] == "evidence")
    assert "completed successfully" in ev_item["body"]


def test_split_artifact_into_review_items_security_and_release():
    sec_content = """# Dependencies
No vulnerabilities found.
# Privileges
Standard developer role matches.
"""
    sec_items = split_artifact_into_review_items("security", sec_content)
    assert len(sec_items) == 4
    dep_item = next(it for it in sec_items if it["id"] == "dependencies")
    assert "No vulnerabilities" in dep_item["body"]

    rel_content = """# Release Notes
ContentOps Studio version 1.0.0 released.
"""
    rel_items = split_artifact_into_review_items("release", rel_content)
    assert len(rel_items) == 4
    notes_item = next(it for it in rel_items if it["id"] == "release_notes")
    assert "version 1.0.0" in notes_item["body"]


def test_interactive_review_callbacks(tmp_path, monkeypatch):
    tg_calls = []

    def fake_tg_request(token, method, params):
        tg_calls.append({"method": method, "params": params})
        return {"ok": True, "result": {"message_id": 9999}}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_tg_request)
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")

    store = TelegramReviewSessionStore(tmp_path)
    session_id = "sesabc"
    session_data = {
        "session_id": session_id,
        "run_id": "run-xyz",
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": 12345,
        "message_id": 9999,
        "cursor": 0,
        "items": [
            {"id": "acceptance", "title": "验收", "body": "OK", "state": "pending", "note": None},
            {"id": "health", "title": "健康", "body": "OK", "state": "pending", "note": None},
        ],
        "decided": False,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    store.save(session_id, session_data)

    seen = set()

    # 1. Action R (Review starts)
    update = {
        "callback_query": {
            "id": "cb_1",
            "from": {"id": 111},
            "message": {"chat": {"id": 12345}, "message_id": 9999},
            "data": f"v1:R:{session_id}",
        }
    }
    res = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    assert res["status"] == "processed"
    assert tg_calls[-1]["method"] == "editMessageText"
    assert "第 1/2 项" in tg_calls[-1]["params"]["text"]

    # 2. Action O (OK current item)
    update = {
        "callback_query": {
            "id": "cb_2",
            "from": {"id": 111},
            "message": {"chat": {"id": 12345}, "message_id": 9999},
            "data": f"v1:O:{session_id}:0",
        }
    }
    res = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    assert res["status"] == "processed"
    session = store.get(session_id)
    assert session["items"][0]["state"] == "ok"
    assert session["cursor"] == 1  # Moved to next item

    # 3. Action F (Flag current item)
    update = {
        "callback_query": {
            "id": "cb_3",
            "from": {"id": 111},
            "message": {"chat": {"id": 12345}, "message_id": 9999},
            "data": f"v1:F:{session_id}:1",
        }
    }
    res = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    assert res["status"] == "processed"
    assert tg_calls[-1]["method"] == "sendMessage"
    assert tg_calls[-1]["params"]["reply_markup"]["force_reply"] is True
    session = store.get(session_id)
    assert session["awaiting_note_for"] == 1


def test_forcereply_update_handling(tmp_path, monkeypatch):
    tg_calls = []

    def fake_tg_request(token, method, params):
        tg_calls.append({"method": method, "params": params})
        return {"ok": True, "result": {"message_id": 8888}}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_tg_request)
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")

    store = TelegramReviewSessionStore(tmp_path)
    session_id = "sesrep"
    session_data = {
        "session_id": session_id,
        "run_id": "run-xyz",
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": 12345,
        "message_id": 9999,
        "cursor": 1,
        "items": [
            {"id": "acceptance", "title": "验收", "body": "OK", "state": "ok", "note": None},
            {"id": "health", "title": "健康", "body": "OK", "state": "pending", "note": None},
        ],
        "awaiting_note_for": 1,
        "decided": False,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    store.save(session_id, session_data)

    # Simulate message reply
    update = {
        "message": {
            "message_id": 12000,
            "from": {"id": 111},
            "chat": {"id": 12345},
            "text": "Redis connection timed out twice",
            "reply_to_message": {
                "message_id": 8888,
                "text": "请输入这一项的问题",
            }
        }
    }

    seen = set()
    res = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    assert res["status"] == "processed"

    session = store.get(session_id)
    assert session["items"][1]["state"] == "flagged"
    assert session["items"][1]["note"] == "Redis connection timed out twice"
    assert session["awaiting_note_for"] is None

    # Summary card should have been sent since no pending items remain
    assert tg_calls[0]["method"] == "editMessageText"
    assert "Review 完成" in tg_calls[0]["params"]["text"]
    assert "Redis connection timed out twice" in tg_calls[0]["params"]["text"]


def test_final_decision_approves_workflow(tmp_path, monkeypatch):
    tg_calls = []

    def fake_tg_request(token, method, params):
        tg_calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_tg_request)
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")

    # Set up initial workflow state
    state = AgileDevState(
        run_id="run-final",
        project_key="CONTENTOPS",
        idea="Build something",
        current_node="staging_review_gate",
        next_node="await_staging_approval",
    )
    state.approvals["staging"] = ApprovalDecision.PENDING
    StateStore(tmp_path).write_project_current(state)

    store = TelegramReviewSessionStore(tmp_path)
    session_id = "sesfin"
    session_data = {
        "session_id": session_id,
        "run_id": "run-final",
        "project_key": "CONTENTOPS",
        "gate": "staging",
        "chat_id": 12345,
        "message_id": 9999,
        "cursor": 0,
        "items": [
            {"id": "acceptance", "title": "验收", "body": "OK", "state": "ok", "note": None},
            {"id": "health", "title": "健康", "body": "OK", "state": "pending", "note": None},
        ],
        "decided": False,
    }
    store.save(session_id, session_data)

    update = {
        "callback_query": {
            "id": "cb_fin",
            "from": {"id": 111},
            "message": {"chat": {"id": 12345}, "message_id": 9999},
            "data": f"v1:A:{session_id}",
        }
    }

    seen = set()
    res = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    assert res["status"] == "processed"

    # Session decided state is True
    session = store.get(session_id)
    assert session["decided"] is True

    # StateStore shows approved
    current_state = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert current_state.approvals["staging"] == ApprovalDecision.APPROVED

    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    approval_action = next(action for action in actions if action["action"] == "approval_decided")
    assert approval_action["decision_origin"] == "telegram"
    assert approval_action["actor_ref"] == "telegram:user:111"
    assert approval_action["review_mode"] == "step_by_step"
    assert approval_action["review_session_id"] == session_id
    assert approval_action["client_context"] == {
        "telegram_chat_id": "12345",
        "telegram_message_id": "9999",
        "telegram_user_id": "111",
    }
    assert approval_action["review_items"] == [
        {"id": "acceptance", "title": "验收", "state": "ok", "note": ""},
        {"id": "health", "title": "健康", "state": "not_reviewed", "note": ""},
    ]


def test_sync_web_decision_updates_telegram(tmp_path, monkeypatch):
    tg_calls = []

    def fake_tg_request(token, method, params):
        tg_calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_tg_request)
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")

    store = TelegramReviewSessionStore(tmp_path)
    session_id = "sessync"
    session_data = {
        "session_id": session_id,
        "run_id": "run-sync",
        "project_key": "CONTENTOPS",
        "gate": "release",
        "chat_id": 12345,
        "message_id": 9999,
        "cursor": 0,
        "items": [],
        "decided": False,
    }
    store.save(session_id, session_data)

    update_active_tg_review_on_web_decision(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        gate="release",
        decision="approved",
    )

    # Should edit TG message to say "已在 Web 端批准"
    assert len(tg_calls) == 1
    assert tg_calls[0]["method"] == "editMessageText"
    assert "已在 Web 端批准" in tg_calls[0]["params"]["text"]

    # Session is marked decided
    session = store.get(session_id)
    assert session["decided"] is True
