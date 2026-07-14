import json
import threading
import time
from pathlib import Path

import pytest

from goodteam_os import telegram
from goodteam_os.projects import write_project_config
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.telegram_runtime_controls import (
    RuntimeControlResolutionError,
    TelegramRuntimeControlService,
)
from goodteam_os.workers import WorkerJobStore


def _configured_workspace(tmp_path: Path, *, project_key: str = "GOODTEAM") -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (workspace / "projects").mkdir(parents=True)
    write_project_config(workspace / "projects", project_key, project_key, repo_path=str(project), worker_mode="pi")
    StateStore(workspace).write_project_current(create_initial_state("run-1", project_key, "test"))
    return workspace, project


def _write_job(
    project: Path,
    job_id: str,
    *,
    adapter: str = "pi",
    run_id: str = "run-1",
    role: str = "implementation",
    project_key: str = "GOODTEAM",
):
    root = project / ".goodteam" / "workers" / "jobs" / job_id
    root.mkdir(parents=True)
    (root / "job.json").write_text(
        json.dumps(
            {
                "id": job_id,
                "run_id": run_id,
                "execution_id": "execution-1",
                "project_key": project_key,
                "role": role,
                "adapter": adapter,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    return root


def _active_status(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "execution_id": "execution-1",
        "pi_session_id": "session-778c",
        "session_status": "running",
        "isStreaming": True,
        "isCompacting": False,
        "pendingMessageCount": 1,
        "provider": "DeepSeek",
        "model": "deepseek-v4-flash",
        "runtime_cwd": "/secret/project",
        "heartbeat": "now",
        "process_alive": True,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/gt steer fix tests", "steer"),
        ("/steer@GoodTeamBot fix tests", "steer"),
        ("/gt followup summarize", "followup"),
        ("/followup summarize", "followup"),
        ("/gt stop because scope changed", "stop"),
        ("/gt@GoodTeamBot status", "status"),
    ],
)
def test_runtime_command_parser(text, expected):
    assert telegram._parse_telegram_command(text) == expected


def test_resolver_selects_only_unique_active_session(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    _write_job(project, "job-active")
    _write_job(project, "job-one-shot", adapter="codex")
    _write_job(project, "job-old-run", run_id="run-old")
    _write_job(project, "job-cross-project", project_key="OTHER")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))

    session = TelegramRuntimeControlService(workspace, "GOODTEAM").resolve_one()

    assert session.job["id"] == "job-active"


def test_resolver_rejects_zero_multiple_stale_and_unknown(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    service = TelegramRuntimeControlService(workspace, "GOODTEAM")
    with pytest.raises(RuntimeControlResolutionError, match="No active Pi session"):
        service.resolve_one()

    _write_job(project, "job-first")
    _write_job(project, "job-second", role="qa")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))
    with pytest.raises(RuntimeControlResolutionError, match="Multiple active Pi sessions") as exc:
        service.resolve_one()
    assert "…ob-first" in str(exc.value)
    assert "…b-second" in str(exc.value)

    monkeypatch.setattr(
        WorkerJobStore,
        "session_status",
        lambda _self, job_id: {**_active_status(job_id), "process_alive": False},
    )
    with pytest.raises(RuntimeControlResolutionError, match="No active Pi session"):
        service.resolve_one()
    with pytest.raises(RuntimeControlResolutionError, match="Unknown configured project"):
        TelegramRuntimeControlService(workspace, "OTHER").resolve_one()


def test_status_is_safe_and_preserves_existing_fields(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    _write_job(project, "job-abcd1234")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))

    text = telegram._format_status_command(workspace=workspace, project_key="GOODTEAM")

    assert "GoodTeam status" in text
    assert "Project: GOODTEAM" in text
    assert "Pi session" in text
    assert "Job: …abcd1234" in text
    assert "Session: …778c" in text
    assert "Provider/model: DeepSeek / deepseek-v4-flash" in text
    assert "/secret/project" not in text
    assert "pid" not in text.lower()


def test_authorized_steer_maps_durable_ack_and_audit_context(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    _write_job(project, "job-abcd1234")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))
    calls = []

    def session_control(_self, job_id, action, **kwargs):
        calls.append((job_id, action, kwargs))
        return {
            **_active_status(job_id),
            "accepted": True,
            "control_id": kwargs["control_id"],
            "control_state": "rpc_accepted",
        }

    sent = []
    monkeypatch.setattr(WorkerJobStore, "session_control", session_control)
    monkeypatch.setattr(telegram, "_telegram_request", lambda _token, _method, params: sent.append(params) or {"ok": True})
    monkeypatch.setenv(telegram.TOKEN_ENV, "fake-token")
    update = {
        "update_id": 7,
        "message": {
            "message_id": 42,
            "from": {"id": 111},
            "chat": {"id": 999},
            "text": "/gt steer fix current tests",
        },
    }

    result = telegram.process_telegram_update(
        update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )

    assert result["accepted"] is True
    assert result["control_state"] == "rpc_accepted"
    assert calls[0][0:2] == ("job-abcd1234", "steer")
    assert calls[0][2]["message"] == "fix current tests"
    assert calls[0][2]["actor"] == "telegram:user:111"
    assert calls[0][2]["source"] == "telegram"
    assert calls[0][2]["audit_context"] == {
        "telegram_chat_id": "999",
        "telegram_message_id": "42",
        "telegram_user_id": "111",
        "command": "steer",
        "project_key": "GOODTEAM",
    }
    assert "✅ Steering accepted" in sent[-1]["text"]
    assert "State: rpc_accepted" in sent[-1]["text"]


def test_unauthorized_control_does_not_touch_worker_store(monkeypatch, tmp_path):
    workspace, _project = _configured_workspace(tmp_path)
    monkeypatch.setattr(WorkerJobStore, "session_control", lambda *_a, **_kw: pytest.fail("must not write control"))
    update = {
        "message": {
            "message_id": 42,
            "from": {"id": 222},
            "chat": {"id": 999},
            "text": "/gt stop forged",
        }
    }
    result = telegram.process_telegram_update(
        update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )
    assert result == {"status": "ignored", "reason": "unauthorized user"}


@pytest.mark.parametrize(
    ("command", "action", "argument"),
    [("followup", "follow_up", "summarize risk"), ("stop", "abort", "scope changed")],
)
def test_followup_and_stop_keep_same_job_and_map_arguments(monkeypatch, tmp_path, command, action, argument):
    workspace, project = _configured_workspace(tmp_path)
    _write_job(project, "job-active")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))
    calls = []

    def control(_self, job_id, called_action, **kwargs):
        calls.append((job_id, called_action, kwargs))
        return {
            **_active_status(job_id),
            "accepted": True,
            "control_id": kwargs["control_id"],
            "control_state": "rpc_accepted",
        }

    monkeypatch.setattr(WorkerJobStore, "session_control", control)
    result, text = TelegramRuntimeControlService(workspace, "GOODTEAM").control(
        command,
        message=argument if command == "followup" else "",
        reason=argument if command == "stop" else "",
        chat_id=999,
        message_id=42,
        user_id="111",
    )
    assert result["accepted"] is True
    assert calls[0][0:2] == ("job-active", action)
    assert calls[0][2]["message"] == (argument if command == "followup" else "")
    assert calls[0][2]["reason"] == (argument if command == "stop" else "")
    if command == "stop":
        assert "Current Pi task stopped. GoodTeam service remains running." in text


def test_worker_control_id_is_restart_safe_and_does_not_duplicate_queue(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")
    monkeypatch.setattr(store, "session_status", lambda _job_id: _active_status("job-active"))
    control_id = "control-tg-12345678"

    first = store.session_control("job-active", "steer", message="once", control_id=control_id)
    second = WorkerJobStore(project)
    monkeypatch.setattr(second, "session_status", lambda _job_id: _active_status("job-active"))
    duplicate = second.session_control("job-active", "steer", message="once", control_id=control_id)

    queued = (root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert first["control_id"] == duplicate["control_id"] == control_id
    assert duplicate["idempotent"] is True
    assert len(queued) == 1


def test_queued_and_failed_are_never_reported_as_accepted(tmp_path):
    service = TelegramRuntimeControlService(tmp_path, "GOODTEAM")
    queued = service.format_control_result("steer", "GOODTEAM", {"accepted": False, "control_state": "queued"})
    failed = service.format_control_result(
        "followup",
        "GOODTEAM",
        {"accepted": False, "control_state": "failed", "error": "rejected"},
    )
    assert "queued but not yet acknowledged" in queued
    assert "accepted" not in queued.lower()
    assert "❌ Follow-up failed" in failed


def test_empty_runtime_messages_return_usage_without_control(monkeypatch, tmp_path):
    workspace, _project = _configured_workspace(tmp_path)
    monkeypatch.setattr(WorkerJobStore, "session_control", lambda *_a, **_kw: pytest.fail("must not write control"))
    sent = []
    monkeypatch.setattr(telegram, "_telegram_request", lambda _token, _method, params: sent.append(params) or {"ok": True})
    for text in ("/gt steer", "/gt followup"):
        telegram.process_telegram_update(
            {"message": {"message_id": 1, "from": {"id": 111}, "chat": {"id": 999}, "text": text}},
            workspace=workspace,
            allowed_users=["111"],
            processed_callbacks=set(),
            project_key="GOODTEAM",
        )
    assert "Usage: /gt steer <message>" in sent[0]["text"]
    assert "Usage: /gt followup <message>" in sent[1]["text"]


def test_fake_telegram_handler_controls_running_fake_pi(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    fake_pi = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(fake_pi))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "pi-sessions"))
    monkeypatch.setenv("GOODTEAM_PI_PROVIDER", "fake-provider")
    monkeypatch.setenv("GOODTEAM_PI_MODEL", "fake-model")
    monkeypatch.setenv("FAKE_PI_SETTLE_DELAY", "3")
    monkeypatch.setenv(telegram.TOKEN_ENV, "fake-telegram-token")
    sent = []
    monkeypatch.setattr(telegram, "_telegram_request", lambda _token, _method, params: sent.append(params) or {"ok": True})
    store = WorkerJobStore(project)
    job = store.create_job(
        run_id="run-1",
        execution_id="execution-1",
        project_key="GOODTEAM",
        role="implementation",
        adapter="pi",
        task="Wait for Telegram controls.",
        context="Fake Pi integration test.",
    )
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(store.run_job(job["id"])))
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if store.session_status(job["id"])["process_alive"] and store.session_status(job["id"])["pi_session_id"]:
            break
        time.sleep(0.01)
    else:
        pytest.fail("fake Pi session did not become active")

    steer_update = {
        "update_id": 10,
        "message": {
            "message_id": 20,
            "from": {"id": 111},
            "chat": {"id": 999},
            "text": "/gt steer focus the fake test",
        },
    }
    steer = telegram.process_telegram_update(
        steer_update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )
    duplicate = telegram.process_telegram_update(
        steer_update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )
    stop = telegram.process_telegram_update(
        {
            "update_id": 11,
            "message": {
                "message_id": 21,
                "from": {"id": 111},
                "chat": {"id": 999},
                "text": "/gt stop fake integration complete",
            },
        },
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )
    thread.join(timeout=5)

    queue = (store.root / job["id"] / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()
    events = (store.root / job["id"] / "pi_events.jsonl").read_text(encoding="utf-8")
    assert steer["control_state"] == duplicate["control_state"] == "rpc_accepted"
    assert steer["control_id"] == duplicate["control_id"]
    assert stop["control_state"] == "rpc_accepted"
    assert len(queue) == 2
    assert events.count('"event":"telegram_runtime_control"') == 2
    assert outcome["failure_class"] == "pi_aborted"
    assert not thread.is_alive()
    assert any("GoodTeam service remains running" in call["text"] for call in sent)
