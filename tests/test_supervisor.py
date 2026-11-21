import socket

import pytest

from goodteam_os.cli import main
from goodteam_os.service import service_status, stop_service


@pytest.mark.parametrize(
    ("bot_token", "chat_id"),
    [(None, None), ("test-token", None), (None, "12345"), ("test-token", "12345")],
    ids=["unconfigured", "token-only", "chat-only", "configured"],
)
def test_restart_supervises_telegram_only_when_configured(tmp_path, monkeypatch, capsys, bot_token, chat_id):
    for name, value in (("GOODTEAM_TG_BOT_TOKEN", bot_token), ("GOODTEAM_TG_CHAT_ID", chat_id)):
        if value is not None:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOODTEAM_DISABLE_TG_NETWORK", "1")
    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    state_dir = tmp_path / "service"
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    (tmp_path / "index.html").write_text("<!doctype html><title>Supervisor test</title>", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(tmp_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    enabled = bool(bot_token and chat_id)
    try:
        assert main(["restart", "--workspace", str(tmp_path), "--port", str(port)]) == 0
        capsys.readouterr()
        assert main(["status"]) == 0
        status = capsys.readouterr().out
        assert "Supervisor: healthy" in status
        assert "Web: running (ready=true, restarts=0)" in status
        assert "Operator: running (ready=true, restarts=0)" in status
        if enabled:
            assert "Telegram: running (ready=true, restarts=0)" in status
        else:
            assert "Telegram: disabled (ready=false, restarts=0)" in status
        assert bool(service_status()["components"]["telegram"]["pid"]) is enabled
        assert (state_dir / "telegram.log").exists() is enabled
    finally:
        stop_service()
