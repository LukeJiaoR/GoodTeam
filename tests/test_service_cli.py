from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from goodteam_os import cli, service, web


def test_service_lock_uses_platform_specific_backend() -> None:
    if os.name == "nt":
        assert service.msvcrt is not None
        assert service.fcntl is None
    else:
        assert service.fcntl is not None
        assert service.msvcrt is None


def test_restart_parser_defaults_to_port_21001() -> None:
    args = cli.build_parser().parse_args(["restart"])

    assert args.port == 21001
    assert args.host == "127.0.0.1"
    assert args.workspace == Path(".")


def test_status_reports_running_service(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "service_status",
        lambda: {
            "running": True,
            "pid": 123,
            "host": "127.0.0.1",
            "port": 21001,
            "workspace": "/tmp/workspace",
            "frontend_dist": "/opt/goodteam/frontend/dist",
            "log": "/tmp/server.log",
            "started_at": 1783872000.0,
        },
    )

    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "Running: yes" in output
    assert "URL: http://127.0.0.1:21001" in output
    assert "PID: 123" in output
    assert "Workspace: /tmp/workspace" in output
    assert "Frontend dist: /opt/goodteam/frontend/dist" in output
    assert "Log path: /tmp/server.log" in output
    assert "Started at:" in output


def test_restart_records_and_passes_resolved_frontend_dist(monkeypatch, tmp_path) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "data workspace"
    frontend_dist = tmp_path / "application" / "frontend" / "dist"
    workspace.mkdir()
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("react-build", encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    monkeypatch.setattr(service, "stop_service", lambda: {"stopped": False})
    monkeypatch.setattr(service, "resolve_frontend_dist", lambda: frontend_dist.resolve())
    monkeypatch.setattr(service, "_wait_until_ready", lambda process, path, token: True)
    captured = {}

    class Process:
        pid = 4321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)

    payload = service.restart_service(workspace=workspace)

    state = json.loads((state_dir / "server.json").read_text(encoding="utf-8"))
    assert payload["frontend_dist"] == str(frontend_dist.resolve())
    assert state["frontend_dist"] == str(frontend_dist.resolve())
    assert state["workspace"] == str(workspace.resolve())
    frontend_arg = captured["command"].index("--frontend-dist")
    assert captured["command"][frontend_arg + 1] == str(frontend_dist.resolve())
    assert captured["kwargs"]["start_new_session"] is True


def test_restart_missing_frontend_does_not_stop_or_spawn_service(monkeypatch, tmp_path) -> None:
    from goodteam_os.frontend_assets import FrontendAssetsNotFound

    attempted = tmp_path / "missing" / "frontend" / "dist"
    monkeypatch.setattr(
        service,
        "resolve_frontend_dist",
        lambda: (_ for _ in ()).throw(FrontendAssetsNotFound([attempted])),
    )
    monkeypatch.setattr(service, "stop_service", lambda: (_ for _ in ()).throw(AssertionError("must not stop")))
    monkeypatch.setattr(
        service.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    try:
        service.restart_service(workspace=tmp_path)
    except FrontendAssetsNotFound as exc:
        assert str(attempted.resolve()) in str(exc)
    else:
        raise AssertionError("missing frontend must fail restart")


def test_status_removes_stale_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))
    (tmp_path / "server.json").write_text(json.dumps({"pid": 987}), encoding="utf-8")
    monkeypatch.setattr(service, "_managed_process_is_running", lambda pid: False)

    assert service.service_status() == {"running": False, "stale_pid": 987}
    assert not (tmp_path / "server.json").exists()


def test_stop_without_state_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))

    assert service.stop_service() == {"stopped": False, "reason": "not running"}


def test_service_status_and_stop_use_service_lifecycle_lock(monkeypatch, tmp_path) -> None:
    events = []

    class Lock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, traceback):
            events.append("exit")

    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(service, "_service_lock", lambda: Lock())

    assert service.service_status() == {"running": False}
    assert service.stop_service() == {"stopped": False, "reason": "not running"}
    assert events == ["enter", "exit", "enter", "exit"]


def test_process_is_running_treats_zombie_as_stopped(monkeypatch) -> None:
    monkeypatch.setattr(service.os, "kill", lambda pid, sig: None)

    class Result:
        returncode = 0
        stdout = "Z+\n"

    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: Result())

    assert service._process_is_running(123) is False


def test_wait_until_ready_requires_matching_child_token(monkeypatch, tmp_path) -> None:
    ready_path = tmp_path / "ready"
    ready_path.write_text("another-process", encoding="utf-8")
    process = type("Process", (), {"poll": lambda self: None})()
    clock = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service, "START_TIMEOUT_SECONDS", 0.1)

    assert service._wait_until_ready(process, ready_path, "expected-token") is False


def test_terminate_process_kills_child_that_ignores_sigterm() -> None:
    class Process:
        terminate_called = False
        kill_called = False
        wait_calls = 0

        def terminate(self) -> None:
            self.terminate_called = True

        def wait(self, timeout=None) -> None:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("goodteam", timeout)

        def kill(self) -> None:
            self.kill_called = True

    process = Process()

    service._terminate_process(process)

    assert process.terminate_called is True
    assert process.kill_called is True
    assert process.wait_calls == 2


def test_serve_writes_ready_token_only_after_server_binds(monkeypatch, tmp_path) -> None:
    ready_path = tmp_path / "ready"

    class FakeServer:
        def __init__(self, address, handler) -> None:
            self.address = address

        def serve_forever(self) -> None:
            assert ready_path.read_text(encoding="utf-8") == "token-123"

        def server_close(self) -> None:
            return None

    monkeypatch.setattr(web, "ThreadingHTTPServer", FakeServer)

    web.serve(tmp_path, ready_file=ready_path, ready_token="token-123")

    assert not ready_path.exists()


def test_serve_rejects_invalid_explicit_frontend_before_binding(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        web,
        "ThreadingHTTPServer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not bind")),
    )

    with pytest.raises(RuntimeError, match="GOODTEAM_FRONTEND_DIST"):
        web.serve(tmp_path, frontend_dist=tmp_path / "missing-dist")
