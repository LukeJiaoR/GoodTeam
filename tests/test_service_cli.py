from __future__ import annotations

import json
import subprocess
from pathlib import Path

from goodteam_os import cli, service, web


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
            "log": "/tmp/server.log",
        },
    )

    assert cli.main(["status"]) == 0
    assert "http://127.0.0.1:21001" in capsys.readouterr().out


def test_status_removes_stale_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))
    (tmp_path / "server.json").write_text(json.dumps({"pid": 987}), encoding="utf-8")
    monkeypatch.setattr(service, "_managed_process_is_running", lambda pid: False)

    assert service.service_status() == {"running": False, "stale_pid": 987}
    assert not (tmp_path / "server.json").exists()


def test_stop_without_state_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path))

    assert service.stop_service() == {"stopped": False, "reason": "not running"}


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
