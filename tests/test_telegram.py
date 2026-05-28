import json

import goodteam_os.telegram as telegram
from goodteam_os.telegram import (
    TelegramApproval,
    build_approval_keyboard,
    decode_callback_data,
    encode_callback_data,
    process_telegram_update,
    telegram_config_from_env,
)


def test_telegram_config_reads_only_env_metadata(monkeypatch):
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setenv("GOODTEAM_TG_ALLOWED_USERS", "111,222")

    config = telegram_config_from_env()

    assert config["enabled"] is True
    assert config["token_env"] == "GOODTEAM_TG_BOT_TOKEN"
    assert config["chat_id"] == "12345"
    assert config["allowed_users"] == ["111", "222"]
    assert "secret-token" not in json.dumps(config)


def test_telegram_callback_round_trips_approval_identity():
    approval = TelegramApproval(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        decision="approved",
    )

    encoded = encode_callback_data(approval)
    decoded = decode_callback_data(encoded)

    assert decoded == approval


def test_telegram_callback_rejects_malformed_payload():
    try:
        decode_callback_data("not-goodteam")
    except ValueError as exc:
        assert "Invalid Telegram approval callback" in str(exc)
    else:
        raise AssertionError("Expected malformed callback to fail")


def test_approval_keyboard_uses_inline_decision_buttons():
    keyboard = build_approval_keyboard(project_key="CONTENTOPS", run_id="run-1", gate="release")

    assert keyboard["inline_keyboard"][0][0]["text"] == "Approve"
    assert "gt:CONTENTOPS:run-1:release:approved" in keyboard["inline_keyboard"][0][0]["callback_data"]
    assert keyboard["inline_keyboard"][0][1]["text"] == "Request changes"


def test_send_approval_notification_posts_message_with_inline_keyboard(monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        artifact_path="/tmp/artifacts/runs/run-1/prd/prd.md",
        summary="ContentOps Studio v1 PRD is ready for review.",
    )

    assert result["status"] == "sent"
    assert result["enabled"] is True
    assert result["chat_id"] == "12345"
    assert result["gate"] == "prd"
    assert calls == [
        {
            "token": "secret-token",
            "method": "sendMessage",
            "params": {
                "chat_id": "12345",
                "text": (
                    "GoodTeam approval needed\n"
                    "Project: CONTENTOPS\n"
                    "Run: run-1\n"
                    "Gate: prd\n"
                    "Artifact: /tmp/artifacts/runs/run-1/prd/prd.md\n"
                    "Summary: ContentOps Studio v1 PRD is ready for review."
                ),
                "reply_markup": build_approval_keyboard(project_key="CONTENTOPS", run_id="run-1", gate="prd"),
            },
        }
    ]
    assert "secret-token" not in json.dumps(result)
    assert "secret-token" not in json.dumps(calls[0]["params"])


def test_send_approval_notification_skips_when_env_is_incomplete(monkeypatch):
    monkeypatch.delenv("GOODTEAM_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GOODTEAM_TG_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")

    result = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd")

    assert result == {
        "status": "disabled",
        "enabled": False,
        "token_env": "GOODTEAM_TG_BOT_TOKEN",
        "chat_id": "12345",
        "allowed_users": [],
    }


def test_process_telegram_update_enforces_allowlist_and_idempotency(tmp_path, monkeypatch):
    calls = []

    def fake_decide_approval(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    seen = set()
    update = {
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 111},
            "data": "gt:CONTENTOPS:run-1:prd:approved",
        }
    }

    first = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    second = process_telegram_update(update, workspace=tmp_path, allowed_users=["111"], processed_callbacks=seen)
    denied = process_telegram_update(
        {"callback_query": {"id": "callback-2", "from": {"id": 999}, "data": "gt:CONTENTOPS:run-1:prd:approved"}},
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=seen,
    )

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert denied["status"] == "ignored"
    assert len(calls) == 1
    assert calls[0]["project_key"] == "CONTENTOPS"
