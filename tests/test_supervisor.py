import socket

from goodteam_os.cli import main
from goodteam_os.service import service_status, stop_service


def test_restart_supervises_web_service(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    state_dir = tmp_path / "service"
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    (tmp_path / "index.html").write_text("<!doctype html><title>Supervisor test</title>", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(tmp_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    try:
        assert main(["restart", "--workspace", str(tmp_path), "--port", str(port)]) == 0
        capsys.readouterr()
        assert main(["status"]) == 0
        status = capsys.readouterr().out
        assert "Supervisor: healthy" in status
        assert "Web: running (ready=true, restarts=0)" in status
        assert set(service_status()["components"]) == {"web"}
    finally:
        stop_service()
