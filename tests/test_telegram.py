import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
import goodteam_os.telegram as telegram
from goodteam_os.telegram import (
    TelegramApproval,
    build_approval_keyboard,
    decode_callback_data,
    encode_callback_data,
    poll_telegram_approvals,
    process_telegram_update,
    telegram_config_from_env,
)


def write_project_state(
    tmp_path,
    *,
    run_id: str = "run-1",
    next_node: str = "await_prd_approval",
    approvals: dict[str, ApprovalDecision] | None = None,
):
    state = create_initial_state(run_id, "CONTENTOPS", "Build AI Content Studio")
    state.next_node = next_node
    if approvals:
        state.approvals.update(approvals)
    StateStore(tmp_path).write_project_current(state)
    return state


def write_project_config(tmp_path, project_root: Path) -> Path:
    project_root.mkdir(parents=True, exist_ok=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    config_path = projects_dir / "contentops.local.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  app: {project_root}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def telegram_text_update(text: str, *, user_id: int = 111, chat_id: int = 12345, message_id: int = 10):
    return {
        "message": {
            "message_id": message_id,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        }
    }


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


def test_telegram_request_uses_telebot_apihelper_and_preserves_raw_response_contract(monkeypatch):
    calls = []

    def fake_make_request(token, method_name, method="get", params=None, files=None):
        calls.append(
            {
                "token": token,
                "method_name": method_name,
                "method": method,
                "params": dict(params or {}),
                "files": files,
            }
        )
        if method_name == "sendMessage":
            return {"message_id": 42, "chat": {"id": 12345}, "text": params["text"]}
        if method_name == "getUpdates":
            return [{"update_id": 7, "message": {"text": "/gt status"}}]
        raise AssertionError(f"unexpected method {method_name}")

    reply_markup = {"inline_keyboard": [[{"text": "Approve", "callback_data": "ok"}]]}
    monkeypatch.setattr("telebot.apihelper._make_request", fake_make_request)

    send_payload = telegram._telegram_request(
        "secret-token",
        "sendMessage",
        {
            "chat_id": "12345",
            "text": "hello",
            "parse_mode": "Markdown",
            "reply_markup": reply_markup,
        },
    )
    updates_payload = telegram._telegram_request("secret-token", "getUpdates", {"offset": 8, "timeout": 20})

    assert send_payload == {"ok": True, "result": {"message_id": 42, "chat": {"id": 12345}, "text": "hello"}}
    assert updates_payload == {"ok": True, "result": [{"update_id": 7, "message": {"text": "/gt status"}}]}
    assert calls == [
        {
            "token": "secret-token",
            "method_name": "sendMessage",
            "method": "post",
            "params": {
                "chat_id": "12345",
                "text": "hello",
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup),
            },
            "files": None,
        },
        {
            "token": "secret-token",
            "method_name": "getUpdates",
            "method": "get",
            "params": {
                "offset": 8,
                "timeout": 20,
                "long_polling_timeout": 10,
            },
            "files": None,
        },
    ]


def test_send_approval_notification_omits_localhost_url_button_and_includes_text_link(tmp_path, monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.delenv("GOODTEAM_DASHBOARD_URL", raising=False)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        artifact_path="/tmp/artifacts/runs/run-1/prd/prd.md",
        summary="ContentOps Studio v1 PRD is ready for review.",
        workspace=tmp_path,
    )

    assert result["status"] == "sent"
    assert result["enabled"] is True
    assert result["chat_id"] == "12345"
    assert result["gate"] == "prd"

    session_id = result["session_id"]
    expected_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ 快速批准", "callback_data": f"v1:A:{session_id}"},
                {"text": "🔍 逐项 review", "callback_data": f"v1:R:{session_id}"},
            ],
            [
                {"text": "❌ 拒绝", "callback_data": f"v1:J:{session_id}"},
            ]
        ]
    }

    assert len(calls) == 1
    assert calls[0]["token"] == "secret-token"
    assert calls[0]["method"] == "sendMessage"
    params = calls[0]["params"]
    assert params["chat_id"] == "12345"
    assert params["reply_markup"] == expected_keyboard
    # Plain text: parse_mode dropped to survive artifact-derived text.
    assert "parse_mode" not in params

    text = params["text"]
    # New digest-style header carries the decision, not machine addressing.
    assert "🔔 PRD 待审 · CONTENTOPS" in text
    assert "建议:" in text
    assert "证据:" in text
    # Machine noise is gone.
    assert "run-1" not in text
    assert "is waiting for" not in text
    assert "http://localhost" not in text

    assert "secret-token" not in json.dumps(result)
    assert "secret-token" not in json.dumps(params)
    buttons = [
        button
        for row in params["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert not any(button.get("url", "").startswith("http://localhost") for button in buttons)
    # Localhost link is replaced by a /gt view hint, not a dead URL line.
    assert "回复本卡发 /gt view 读全文" in text


def test_approval_notification_payload_surfaces_digest_recommendation_evidence():
    payload = telegram._approval_notification_payload(
        project_key="DEMO",
        run_id="2de0b4a9-8ee2-4e1e-8a05-febfd5aace4d",
        gate="security",
        summary="GoodTeam is waiting for SECURITY approval on DEMO",
        session_id="abc123",
        digest="Implement OAuth token rotation for the staging deploy",
        recommendation={
            "verdict": "caution",
            "blocking_count": 2,
            "reasons": [{"kind": "open_objection", "text": "Secret scan found a leaked token"}],
        },
        evidence={"ci_status": "passed", "smoke_status": "failed", "qa_status": "unknown", "blocker_count": 0},
    )

    text = payload["text"]
    assert text.startswith("🔔 SECURITY 待审 · DEMO")
    assert "本次:Implement OAuth token rotation for the staging deploy" in text
    assert "建议:⚠️ caution · 阻断 2 — Secret scan found a leaked token" in text
    assert "证据:CI 通过 · Smoke 失败 · QA 未知 · 阻断 0" in text
    # Machine addressing must not leak onto the card.
    assert "2de0b4a9-8ee2-4e1e-8a05-febfd5aace4d" not in text
    assert "is waiting for" not in text
    assert "http://localhost" not in text
    # Plain text only — no Markdown parse_mode that could choke on artifact text.
    assert "parse_mode" not in payload


def test_artifact_digest_strips_markdown_and_truncates():
    assert telegram._artifact_digest("") == ""
    assert telegram._artifact_digest("\n\n# \n## Heading\nBody") == "Heading"
    assert telegram._artifact_digest("- first real line\nsecond") == "first real line"
    long = "x" * 200
    digest = telegram._artifact_digest(long)
    assert len(digest) == 140
    assert digest.endswith("...")


def test_send_approval_notification_includes_public_https_dashboard_url_button(tmp_path, monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setenv("GOODTEAM_DASHBOARD_URL", "https://goodteam.example.com/dashboard")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        summary="ContentOps Studio v1 PRD is ready for review.",
        workspace=tmp_path,
    )

    session_id = result["session_id"]
    expected_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ 快速批准", "callback_data": f"v1:A:{session_id}"},
                {"text": "🔍 逐项 review", "callback_data": f"v1:R:{session_id}"},
            ],
            [
                {"text": "❌ 拒绝", "callback_data": f"v1:J:{session_id}"},
                {"text": "📖 完整报告", "url": "https://goodteam.example.com/dashboard?project=CONTENTOPS"},
            ]
        ]
    }

    assert calls[0]["params"]["reply_markup"] == expected_keyboard
    assert "Dashboard: https://goodteam.example.com/dashboard?project=CONTENTOPS" not in calls[0]["params"]["text"]


def test_send_approval_notification_refreshes_existing_live_gate_card(tmp_path, monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 42}}
        return {"ok": True, "result": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.delenv("GOODTEAM_DASHBOARD_URL", raising=False)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    first = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        summary="PRD ready.",
        workspace=tmp_path,
    )
    second = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        summary="PRD ready.",
        workspace=tmp_path,
    )

    assert first["status"] == "sent"
    assert second["status"] == "refreshed"
    assert second["session_id"] == first["session_id"]
    assert second["message_id"] == 42
    assert [call["method"] for call in calls] == ["sendMessage", "editMessageText"]
    assert calls[1]["params"]["message_id"] == 42
    assert calls[1]["params"]["reply_markup"] == calls[0]["params"]["reply_markup"]
    assert len(list((tmp_path / "state" / "tg-reviews").glob("*.json"))) == 1


def test_send_approval_notification_sends_new_card_for_different_gate(tmp_path, monkeypatch):
    calls = []
    next_message_id = 41

    def fake_request(token, method, params):
        nonlocal next_message_id
        calls.append({"token": token, "method": method, "params": params})
        next_message_id += 1
        return {"ok": True, "result": {"message_id": next_message_id}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    first = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)
    second = telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="implementation",
        workspace=tmp_path,
    )

    assert first["status"] == "sent"
    assert second["status"] == "sent"
    assert second["session_id"] != first["session_id"]
    assert [call["method"] for call in calls] == ["sendMessage", "sendMessage"]
    assert len(list((tmp_path / "state" / "tg-reviews").glob("*.json"))) == 2


def test_send_approval_notification_sends_new_card_when_same_gate_session_is_decided(tmp_path, monkeypatch):
    calls = []
    next_message_id = 41

    def fake_request(token, method, params):
        nonlocal next_message_id
        calls.append({"token": token, "method": method, "params": params})
        next_message_id += 1
        return {"ok": True, "result": {"message_id": next_message_id}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    first = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)
    store = telegram.TelegramReviewSessionStore(tmp_path)
    session = store.get(str(first["session_id"]))
    session["decided"] = True
    store.save(str(first["session_id"]), session)

    second = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)

    assert second["status"] == "sent"
    assert second["session_id"] != first["session_id"]
    assert [call["method"] for call in calls] == ["sendMessage", "sendMessage"]
    assert len(list((tmp_path / "state" / "tg-reviews").glob("*.json"))) == 2


def test_send_approval_notification_treats_not_modified_refresh_as_success(tmp_path, monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 42}}
        return {"ok": False, "description": "Bad Request: message is not modified"}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    first = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)
    second = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)

    assert second["status"] == "refreshed"
    assert second["session_id"] == first["session_id"]
    assert [call["method"] for call in calls] == ["sendMessage", "editMessageText"]
    assert len(list((tmp_path / "state" / "tg-reviews").glob("*.json"))) == 1


def test_send_approval_notification_resends_when_existing_message_is_missing(tmp_path, monkeypatch):
    calls = []
    next_message_id = 41

    def fake_request(token, method, params):
        nonlocal next_message_id
        calls.append({"token": token, "method": method, "params": params})
        if method == "editMessageText":
            return {"ok": False, "description": "Bad Request: message to edit not found"}
        next_message_id += 1
        return {"ok": True, "result": {"message_id": next_message_id}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    first = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)
    second = telegram.send_approval_notification(project_key="CONTENTOPS", run_id="run-1", gate="prd", workspace=tmp_path)

    assert second["status"] == "sent"
    assert second["session_id"] != first["session_id"]
    assert [call["method"] for call in calls] == ["sendMessage", "editMessageText", "sendMessage"]
    assert len(list((tmp_path / "state" / "tg-reviews").glob("*.json"))) == 2


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
    write_project_state(tmp_path)

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
    assert calls[0]["decision_origin"] == "telegram"
    assert calls[0]["actor_ref"] == "telegram:user:111"
    assert calls[0]["review_mode"] == "quick"
    assert calls[0]["client_context"] == {
        "telegram_chat_id": "",
        "telegram_message_id": "",
        "telegram_user_id": "111",
    }


def test_legacy_callback_rejects_stale_run(tmp_path, monkeypatch):
    decisions = []
    telegram_requests = []
    write_project_state(tmp_path, run_id="run-current")

    def fake_decide_approval(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    def fake_telegram_request(token, method, params):
        telegram_requests.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        {
            "callback_query": {
                "id": "callback-stale",
                "from": {"id": 111},
                "data": "gt:CONTENTOPS:run-old:prd:approved",
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result == {"status": "ignored", "reason": "run mismatch"}
    assert decisions == []
    assert telegram_requests == [
        {
            "token": "secret-token",
            "method": "answerCallbackQuery",
            "params": {
                "callback_query_id": "callback-stale",
                "text": "GoodTeam approval ignored; this button is stale or no longer actionable.",
            },
        }
    ]


def test_legacy_callback_rejects_non_actionable_gate(tmp_path, monkeypatch):
    decisions = []
    telegram_requests = []
    write_project_state(tmp_path, next_node="planner")

    def fake_decide_approval(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    def fake_telegram_request(token, method, params):
        telegram_requests.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        {
            "callback_query": {
                "id": "callback-non-actionable",
                "from": {"id": 111},
                "data": "gt:CONTENTOPS:run-1:prd:approved",
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result == {"status": "ignored", "reason": "gate not actionable"}
    assert decisions == []
    assert telegram_requests == [
        {
            "token": "secret-token",
            "method": "answerCallbackQuery",
            "params": {
                "callback_query_id": "callback-non-actionable",
                "text": "GoodTeam approval ignored; this button is stale or no longer actionable.",
            },
        }
    ]


def test_legacy_callback_rejects_already_decided_gate(tmp_path, monkeypatch):
    decisions = []
    telegram_requests = []
    write_project_state(tmp_path, approvals={"prd": ApprovalDecision.APPROVED})

    def fake_decide_approval(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    def fake_telegram_request(token, method, params):
        telegram_requests.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        {
            "callback_query": {
                "id": "callback-decided",
                "from": {"id": 111},
                "data": "gt:CONTENTOPS:run-1:prd:rejected",
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result == {"status": "ignored", "reason": "gate not actionable"}
    assert decisions == []
    assert telegram_requests == [
        {
            "token": "secret-token",
            "method": "answerCallbackQuery",
            "params": {
                "callback_query_id": "callback-decided",
                "text": "GoodTeam approval ignored; this button is stale or no longer actionable.",
            },
        }
    ]


def test_legacy_callback_current_actionable_gate_applies_decision(tmp_path, monkeypatch):
    decisions = []
    telegram_requests = []
    write_project_state(tmp_path)

    def fake_decide_approval(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    def fake_telegram_request(token, method, params):
        telegram_requests.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        {
            "callback_query": {
                "id": "callback-current",
                "from": {"id": 111},
                "data": "gt:CONTENTOPS:run-1:prd:approved",
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result == {"status": "processed", "callback_id": "callback-current"}
    assert decisions == [
        {
            "workspace": tmp_path,
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "approved",
            "decision_origin": "telegram",
            "actor_ref": "telegram:user:111",
            "review_mode": "quick",
            "review_session_id": "",
            "client_context": {
                "telegram_chat_id": "",
                "telegram_message_id": "",
                "telegram_user_id": "111",
            },
            "review_items": [],
        }
    ]
    assert telegram_requests == []


def test_poll_telegram_approvals_still_processes_approval_callback(tmp_path, monkeypatch):
    decisions = []
    telegram_requests = []
    write_project_state(tmp_path)

    def fake_decide_approval(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    def fake_telegram_request(token, method, params):
        telegram_requests.append({"token": token, "method": method, "params": params})
        if method == "getUpdates":
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 7,
                        "callback_query": {
                            "id": "callback-poll",
                            "from": {"id": 111},
                            "data": "gt:CONTENTOPS:run-1:prd:approved",
                        },
                    }
                ],
            }
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setenv("GOODTEAM_TG_ALLOWED_USERS", "111")
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide_approval)
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = poll_telegram_approvals(workspace=tmp_path, project_key="CONTENTOPS", once=True)

    assert result == {"processed": 1, "offset": 8}
    assert decisions[0]["decision"] == "approved"
    assert decisions[0]["decision_origin"] == "telegram"
    assert telegram_requests[-1] == {
        "token": "secret-token",
        "method": "answerCallbackQuery",
        "params": {
            "callback_query_id": "callback-poll",
            "text": "GoodTeam approval recorded.",
        },
    }


def test_gt_status_command_sends_masked_active_run_summary(tmp_path, monkeypatch):
    calls = []
    project_root = tmp_path / "runtime"
    write_project_config(tmp_path, project_root)
    state = write_project_state(tmp_path)
    state.current_node = "architect"
    state.blockers = ["needs staging evidence", "waiting on qa"]
    StateStore(tmp_path).write_project_current(state)
    now = datetime.now(UTC)
    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-1"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-1",
                "run_id": "run-1",
                "role": "qa",
                "status": "running",
                "created_at": (now - timedelta(minutes=4)).isoformat(),
                "updated_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(
        json.dumps({"job_id": "job-1", "run_id": "run-1", "role": "qa", "status": "running"}),
        encoding="utf-8",
    )

    def fake_telegram_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        telegram_text_update("/gt status"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "status"}
    assert calls[0]["method"] == "sendMessage"
    text = calls[0]["params"]["text"]
    assert "Project: CONTENTOPS" in text
    # Full run UUID is dropped; only the last-6 tail is shown for reference.
    assert "Run: …" in text
    assert "Run: run-1" not in text
    # Internal node jargon is gone in favor of the human-readable gate.
    assert "Current node:" not in text
    assert "Pending gate: prd" in text
    assert "Active workers: 1" in text
    assert "Blockers: 2" in text


def test_gt_command_rejects_unauthorized_user(tmp_path):
    write_project_state(tmp_path)

    result = process_telegram_update(
        telegram_text_update("/gt status", user_id=999),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "ignored", "reason": "unauthorized user"}


def test_gt_workers_masks_worker_error_paths_and_ips(tmp_path, monkeypatch):
    calls = []
    project_root = tmp_path / "runtime"
    write_project_config(tmp_path, project_root)
    write_project_state(tmp_path)
    now = datetime.now(UTC)
    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-secret"
    job_root.mkdir(parents=True)
    stderr_path = job_root / "stderr.txt"
    stderr_path.write_text("failed opening /Users/luke/private/app on 10.1.2.3\n", encoding="utf-8")
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-secret",
                "run_id": "run-1",
                "role": "implementation",
                "status": "failed",
                "created_at": (now - timedelta(seconds=90)).isoformat(),
                "updated_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": "job-secret",
                "run_id": "run-1",
                "role": "implementation",
                "status": "failed",
                "stderr_path": str(stderr_path),
                "summary": "failed opening /Users/luke/private/app on 10.1.2.3",
            }
        ),
        encoding="utf-8",
    )

    def fake_telegram_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        telegram_text_update("/gt workers"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "workers"}
    text = calls[0]["params"]["text"]
    # Job UUID is shortened to its last-8 chars for readability.
    assert "…b-secret" in text
    assert "implementation" in text
    assert "failed" in text
    assert "local-workspace-dir" in text
    assert "staging-ip" in text
    assert "/Users/luke/private/app" not in text
    assert "10.1.2.3" not in text


def test_gt_evidence_command_uses_unknown_when_data_is_missing(tmp_path, monkeypatch):
    calls = []
    write_project_state(tmp_path)

    def fake_telegram_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        telegram_text_update("/gt evidence"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "evidence"}
    text = calls[0]["params"]["text"]
    assert "CI: unknown" in text
    assert "Smoke: unknown" in text
    assert "QA: unknown" in text
    assert "Release readiness: unknown" in text
    assert "Blockers: 0" in text


def test_gt_approvals_command_sends_pending_gate_with_quick_decision_buttons(tmp_path, monkeypatch):
    calls = []
    write_project_state(tmp_path)

    def fake_telegram_request(token, method, params):
        calls.append({"token": token, "method": method, "params": params})
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_telegram_request)

    result = process_telegram_update(
        telegram_text_update("/gt approvals"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "approvals"}
    assert calls[0]["method"] == "sendMessage"
    assert "🔔 PRD 待审 · CONTENTOPS" in calls[0]["params"]["text"]
    keyboard = calls[0]["params"]["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"].startswith("v1:A:")
    assert keyboard[0][1]["callback_data"].startswith("v1:R:")
    assert keyboard[1][0]["callback_data"].startswith("v1:J:")


def test_plain_non_reply_non_command_message_still_ignored(tmp_path):
    write_project_state(tmp_path)

    result = process_telegram_update(
        telegram_text_update("hello goodteam"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "ignored", "reason": "not a reply message"}


# ---------------------------------------------------------------------------
# /gt view
# ---------------------------------------------------------------------------

def _write_prd_artifact(tmp_path: Path, content: str = "# PRD\n\nThis is the PRD content.") -> Path:
    """Write a fake PRD artifact and update state to point to it."""
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    prd_file = art_dir / "prd.md"
    prd_file.write_text(content, encoding="utf-8")
    return prd_file


def _write_state_with_artifact(tmp_path, artifact_path: Path):
    """Write project state with a prd artifact pointing to artifact_path."""
    state = write_project_state(tmp_path)
    state.artifacts["prd"] = str(artifact_path)
    StateStore(tmp_path).write_project_current(state)
    return state


def test_gt_view_sends_artifact_content(tmp_path, monkeypatch):
    calls = []
    prd_file = _write_prd_artifact(tmp_path, "# PRD\n\nThis is the PRD content.")
    _write_state_with_artifact(tmp_path, prd_file)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = process_telegram_update(
        telegram_text_update("/gt view"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "view"}
    assert len(calls) == 1
    assert calls[0]["method"] == "sendMessage"
    text = calls[0]["params"]["text"]
    assert text.startswith("📄 prd:\n\n")
    assert "PRD content" in text


def test_gt_view_no_pending_gate(tmp_path, monkeypatch):
    calls = []
    # State with no pending gate (next_node is not await_*_approval)
    write_project_state(tmp_path, next_node="planner")

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = process_telegram_update(
        telegram_text_update("/gt view"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "view"}
    assert len(calls) == 1
    assert "No pending approval gate" in calls[0]["params"]["text"]


def test_gt_view_artifact_not_found(tmp_path, monkeypatch):
    calls = []
    state = write_project_state(tmp_path)
    # Point artifact to a file that does not exist
    state.artifacts["prd"] = str(tmp_path / "does_not_exist.md")
    StateStore(tmp_path).write_project_current(state)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = process_telegram_update(
        telegram_text_update("/gt view"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "view"}
    assert "No artifact found for gate prd" in calls[0]["params"]["text"]


def test_gt_view_long_content_sends_chunks(tmp_path, monkeypatch):
    calls = []
    # Generate content slightly over 2 * 3800 chars to produce 3 chunks
    long_content = "A" * 7700
    prd_file = _write_prd_artifact(tmp_path, long_content)
    _write_state_with_artifact(tmp_path, prd_file)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)

    result = process_telegram_update(
        telegram_text_update("/gt view"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "view"}
    # 7700 chars → ceil(7700/3800) = 3 chunks (but 2*3800=7600 so 2 chunks if split evenly)
    # Actually 7700 / 3800 = 2.03 → 3 chunks (3800 + 3800 + 100)
    assert len(calls) == 3
    assert "part 1/3" in calls[0]["params"]["text"]
    assert "part 2/3" in calls[1]["params"]["text"]
    assert "part 3/3" in calls[2]["params"]["text"]


# ---------------------------------------------------------------------------
# /gt approve
# ---------------------------------------------------------------------------

def test_gt_approve_calls_decide_approval_and_sends_confirmation(tmp_path, monkeypatch):
    calls = []
    decisions = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    def fake_decide(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    result = process_telegram_update(
        telegram_text_update("/gt approve"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "approve"}
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "approved"
    assert decisions[0]["approval"] == "prd"
    assert decisions[0]["review_mode"] == "quick"
    assert decisions[0]["decision_origin"] == "telegram"
    assert decisions[0]["actor_ref"] == "telegram:user:111"

    confirm_text = calls[-1]["params"]["text"]
    assert "✅ PRD approved" in confirm_text


def test_gt_approve_with_note_captures_note(tmp_path, monkeypatch):
    decisions = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        return {"ok": True}

    def fake_decide(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    process_telegram_update(
        telegram_text_update("/gt approve looks good to me"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert decisions[0]["note"] == "looks good to me"


def test_gt_approve_edits_live_card_if_session_exists(tmp_path, monkeypatch):
    calls = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 77}}
        return {"ok": True}

    def fake_decide(**kwargs):
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "12345")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    # Create a live session first (simulates an existing card)
    telegram.send_approval_notification(
        project_key="CONTENTOPS",
        run_id="run-1",
        gate="prd",
        workspace=tmp_path,
    )
    calls.clear()

    result = process_telegram_update(
        telegram_text_update("/gt approve"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "approve"}
    methods = [c["method"] for c in calls]
    assert "editMessageText" in methods


# ---------------------------------------------------------------------------
# /gt reject
# ---------------------------------------------------------------------------

def test_gt_reject_empty_reason_sends_usage_hint_and_no_decide(tmp_path, monkeypatch):
    calls = []
    decisions = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    def fake_decide(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    result = process_telegram_update(
        telegram_text_update("/gt reject"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "reject"}
    assert decisions == []
    assert "Usage: /gt reject" in calls[0]["params"]["text"]


def test_gt_reject_with_reason_calls_decide_approval(tmp_path, monkeypatch):
    decisions = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        return {"ok": True}

    def fake_decide(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    process_telegram_update(
        telegram_text_update("/gt reject security concerns"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert len(decisions) == 1
    assert decisions[0]["decision"] == "rejected"
    assert decisions[0]["review_mode"] == "reject_confirmed"
    assert decisions[0]["note"] == "security concerns"


# ---------------------------------------------------------------------------
# /gt changes
# ---------------------------------------------------------------------------

def test_gt_changes_calls_decide_approval_changes_requested(tmp_path, monkeypatch):
    decisions = []
    write_project_state(tmp_path)

    def fake_request(token, method, params):
        return {"ok": True}

    def fake_decide(**kwargs):
        decisions.append(kwargs)
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.telegram.decide_approval", fake_decide)

    result = process_telegram_update(
        telegram_text_update("/gt changes needs more detail in section 3"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "changes"}
    assert decisions[0]["decision"] == "changes_requested"
    assert decisions[0]["review_mode"] == "request_changes_with_notes"
    assert decisions[0]["note"] == "needs more detail in section 3"


def test_gt_bug_files_bug_and_confirms(tmp_path, monkeypatch):
    calls = []
    bug_calls = []

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    def fake_report_bug(**kwargs):
        bug_calls.append(kwargs)
        return {"next_node": "await_fix_plan_approval", "severity": kwargs.get("severity")}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.web.report_bug", fake_report_bug)

    result = process_telegram_update(
        telegram_text_update("/gt bug Something broke"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "bug"}
    assert len(bug_calls) == 1
    assert bug_calls[0]["description"] == "Something broke"
    assert bug_calls[0]["severity"] == "p2"
    assert bug_calls[0]["project_key"] == "CONTENTOPS"
    assert len(calls) == 1
    text = calls[0]["params"]["text"]
    assert "fix" in text
    assert "FIX_PLAN" in text


def test_gt_bug_parses_leading_severity_tag(tmp_path, monkeypatch):
    bug_calls = []

    def fake_request(token, method, params):
        return {"ok": True}

    def fake_report_bug(**kwargs):
        bug_calls.append(kwargs)
        return {"next_node": "await_fix_plan_approval"}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.web.report_bug", fake_report_bug)

    result = process_telegram_update(
        telegram_text_update("/gt bug [p1] Critical crash"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "bug"}
    assert len(bug_calls) == 1
    assert bug_calls[0]["severity"] == "p1"
    assert bug_calls[0]["description"] == "Critical crash"


def test_gt_bug_empty_sends_usage_and_skips_report(tmp_path, monkeypatch):
    calls = []
    bug_calls = []

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    def fake_report_bug(**kwargs):
        bug_calls.append(kwargs)
        return {}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.web.report_bug", fake_report_bug)

    result = process_telegram_update(
        telegram_text_update("/gt bug"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "bug"}
    assert bug_calls == []
    assert len(calls) == 1
    assert "Usage: /gt bug" in calls[0]["params"]["text"]


def test_gt_bug_active_run_error_is_sent_without_crash(tmp_path, monkeypatch):
    calls = []

    def fake_request(token, method, params):
        calls.append({"method": method, "params": params})
        return {"ok": True}

    def fake_report_bug(**kwargs):
        raise ValueError("An active run already exists for project CONTENTOPS.")

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "secret-token")
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", fake_request)
    monkeypatch.setattr("goodteam_os.web.report_bug", fake_report_bug)

    result = process_telegram_update(
        telegram_text_update("/gt bug Something broke again"),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="CONTENTOPS",
    )

    assert result == {"status": "processed", "command": "bug"}
    assert len(calls) == 1
    assert "active run already exists" in calls[0]["params"]["text"]
