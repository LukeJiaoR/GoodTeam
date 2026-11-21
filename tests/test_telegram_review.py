from datetime import datetime, UTC, timedelta

from goodteam_os.telegram import (
    TelegramReviewSessionStore,
    mask_sensitive_data,
)


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


def _review_note_session_data(
    session_id: str,
    *,
    chat_id: int = 12345,
    card_message_id: int = 9999,
    note_message_id: int = 8888,
    decided: bool = False,
    expires_delta: timedelta = timedelta(hours=1),
) -> dict[str, object]:
    data = _review_session_data(
        session_id,
        chat_id=chat_id,
        message_id=card_message_id,
        decided=decided,
        expires_delta=expires_delta,
    )
    data.update(
        {
            "cursor": 1,
            "items": [
                {"id": "acceptance", "title": "验收", "body": "OK", "state": "ok", "note": None},
                {"id": "health", "title": "健康", "body": "OK", "state": "pending", "note": None},
            ],
            "awaiting_note_for": 1,
            "awaiting_note_message_id": note_message_id,
        }
    )
    return data


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
