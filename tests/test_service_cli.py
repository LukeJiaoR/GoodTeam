from __future__ import annotations

import json
from pathlib import Path

from goodteam_os import cli, service


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
