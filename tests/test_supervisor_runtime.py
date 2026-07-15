from __future__ import annotations

import os
import signal
import socket
import time

import pytest

from goodteam_os import service


def _free_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])
    except PermissionError:
        pytest.skip("local socket binds are prohibited in this sandbox")


def _wait_for_web_restart(original_pid: int, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.service_status()
        web = status.get("components", {}).get("web", {})
        if web.get("restart_count") == 1 and int(web.get("pid", 0)) != original_pid and web.get("ready"):
            return status
        time.sleep(0.1)
    raise AssertionError("web component did not recover before timeout")


def test_detached_runtime_recovers_component_crash_and_leaves_no_orphans(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    frontend = tmp_path / "frontend"
    workspace.mkdir()
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>GoodTeam</html>", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(frontend))
    monkeypatch.delenv("GOODTEAM_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GOODTEAM_TG_CHAT_ID", raising=False)
    payload = service.restart_service(workspace, port=_free_port())
    children = {name: int(component["pid"]) for name, component in payload["components"].items()}
    try:
        assert payload["supervisor"]["status"] == "healthy"
        assert set(payload["components"]) == {"web", "telegram", "operator"}
        assert all(component["ready"] for component in payload["components"].values())
        assert service._process_is_running(int(payload["pid"]))
        os.kill(children["web"], signal.SIGKILL)
        recovered = _wait_for_web_restart(children["web"])
        children["web"] = int(recovered["components"]["web"]["pid"])
    finally:
        stopped = service.stop_service()
    assert stopped["stopped"] is True
    assert service.service_status()["running"] is False
    assert all(not service._process_is_running(pid) for pid in children.values())


@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP fault injection is POSIX-only")
def test_stale_child_heartbeat_restarts_live_but_stopped_component(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    frontend = tmp_path / "frontend"
    workspace.mkdir()
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>GoodTeam</html>", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(frontend))
    monkeypatch.setenv("GOODTEAM_COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", "1")
    monkeypatch.delenv("GOODTEAM_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GOODTEAM_TG_CHAT_ID", raising=False)
    payload = service.restart_service(workspace, port=_free_port())
    original_web = int(payload["components"]["web"]["pid"])
    replacement_web = 0
    try:
        os.kill(original_web, signal.SIGSTOP)
        recovered = _wait_for_web_restart(original_web, timeout=15.0)
        replacement_web = int(recovered["components"]["web"]["pid"])
    finally:
        service.stop_service()
    assert not service._process_is_running(original_web)
    assert replacement_web > 0
    assert not service._process_is_running(replacement_web)


def test_pid_identity_mismatch_is_never_killed(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))
    service._write_state({"pid": 4321, "identity": "expected"})
    monkeypatch.setattr(service, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(service, "_managed_process_is_running", lambda pid, **kwargs: False)
    monkeypatch.setattr(
        service.os,
        "kill",
        lambda *args: (_ for _ in ()).throw(AssertionError("identity mismatch must not be killed")),
    )
    assert service.stop_service() == {"stopped": False, "reason": "identity mismatch", "pid": 4321}


def test_occupied_unmanaged_port_fails_before_stopping_existing_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "state"))
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(frontend))

    class OccupiedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def setsockopt(self, *args):
            return None

        def bind(self, address):
            raise OSError("address already in use")

    monkeypatch.setattr(service.socket, "socket", lambda *args, **kwargs: OccupiedSocket())
    monkeypatch.setattr(
        service,
        "_stop_service_unlocked",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not stop before preflight")),
    )
    with pytest.raises(RuntimeError, match="occupied by an unmanaged process"):
        service.restart_service(tmp_path, port=23456)


def test_restart_to_different_occupied_port_keeps_healthy_old_service(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GOODTEAM_FRONTEND_DIST", str(frontend))
    service._write_state(
        {
            "pid": 4321,
            "identity": "old-service",
            "host": "127.0.0.1",
            "port": 21001,
        }
    )
    monkeypatch.setattr(service, "_state_process_is_running", lambda pid, identity: True)

    class OccupiedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def setsockopt(self, *args):
            return None

        def bind(self, address):
            raise OSError("address already in use")

    monkeypatch.setattr(service.socket, "socket", lambda *args, **kwargs: OccupiedSocket())
    monkeypatch.setattr(
        service,
        "_stop_service_unlocked",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("healthy old service must remain running")),
    )

    with pytest.raises(RuntimeError, match="occupied by an unmanaged process"):
        service.restart_service(tmp_path, port=23456)

    assert service._read_state()["pid"] == 4321
