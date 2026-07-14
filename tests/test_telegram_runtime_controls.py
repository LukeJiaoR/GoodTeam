import json
import multiprocessing
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


def _reconcile_terminal_in_process(project, job_id, control, state, start, errors):
    try:
        store = WorkerJobStore(Path(project))
        root = store.root / job_id
        job = json.loads((root / "job.json").read_text(encoding="utf-8"))
        start.wait()
        store._record_pi_control_terminal(root, job, control, state)
    except Exception as exc:  # pragma: no cover - reported to the parent process
        errors.put(repr(exc))


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


@pytest.mark.parametrize(
    ("command_text", "command", "action", "terminal_status"),
    [
        ("/gt stop scope changed", "stop", "abort", "aborted"),
        ("/gt steer focus tests", "steer", "steer", "settled"),
    ],
)
def test_terminal_session_duplicate_update_replays_durable_ack(
    monkeypatch,
    tmp_path,
    command_text,
    command,
    action,
    terminal_status,
):
    workspace, project = _configured_workspace(tmp_path)
    root = _write_job(project, "job-terminal")
    (root / "pi_session.json").write_text(
        json.dumps(
            {
                "session_id": "session-terminal",
                "status": terminal_status,
                "execution_id": "execution-1",
                "provider": "fake-provider",
                "model": "fake-model",
            }
        ),
        encoding="utf-8",
    )
    (root / "status.json").write_text(json.dumps({"status": "failed" if terminal_status == "aborted" else "completed"}), encoding="utf-8")
    store = WorkerJobStore(project)
    control_id = TelegramRuntimeControlService.stable_control_id(
        chat_id=999,
        message_id=42,
        user_id="111",
        command=command,
    )
    store._append_jsonl(
        root / "pi_control_queue.jsonl",
        {
            "id": control_id,
            "action": action,
            "message": "focus tests" if action == "steer" else "",
            "reason": "scope changed" if action == "abort" else "",
            "source": "telegram",
            "actor": "telegram:user:111",
            "job_id": "job-terminal",
            "execution_id": "execution-1",
            "audit_context": {
                "telegram_chat_id": "999",
                "telegram_message_id": "42",
                "telegram_user_id": "111",
                "command": command,
                "project_key": "GOODTEAM",
            },
        },
    )
    store._append_pi_control_ack(root, control_id, "rpc_accepted")
    sent = []
    monkeypatch.setenv(telegram.TOKEN_ENV, "fake-token")
    monkeypatch.setattr(telegram, "_telegram_request", lambda _token, _method, params: sent.append(params) or {"ok": True})
    update = {
        "update_id": 7,
        "message": {
            "message_id": 42,
            "from": {"id": 111},
            "chat": {"id": 999},
            "text": command_text,
        },
    }

    first_replay = telegram.process_telegram_update(
        update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )
    second_replay = telegram.process_telegram_update(
        update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )

    assert first_replay["accepted"] is second_replay["accepted"] is True
    assert first_replay["control_state"] == second_replay["control_state"] == "rpc_accepted"
    assert first_replay["control_id"] == second_replay["control_id"] == control_id
    assert len((root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "rpc_accepted"
    assert "accepted" in sent[-1]["text"]


def test_old_update_replays_run_a_control_without_touching_active_run_b(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    StateStore(workspace).write_project_current(create_initial_state("run-b", "GOODTEAM", "next run"))
    old_root = _write_job(project, "job-run-a", run_id="run-a")
    new_root = _write_job(project, "job-run-b", run_id="run-b")
    store = WorkerJobStore(project)
    control_id = TelegramRuntimeControlService.stable_control_id(
        chat_id=999,
        message_id=42,
        user_id="111",
        command="steer",
    )
    store._append_jsonl(
        old_root / "pi_control_queue.jsonl",
        {
            "id": control_id,
            "action": "steer",
            "message": "run A instruction",
            "source": "telegram",
            "actor": "telegram:user:111",
            "job_id": "job-run-a",
            "execution_id": "execution-a",
            "audit_context": {
                "telegram_chat_id": "999",
                "telegram_message_id": "42",
                "telegram_user_id": "111",
                "command": "steer",
                "project_key": "GOODTEAM",
            },
        },
    )
    store._append_pi_control_ack(old_root, control_id, "rpc_accepted")

    def status(_self, job_id):
        if job_id == "job-run-a":
            return {**_active_status(job_id), "session_status": "settled", "process_alive": False}
        return _active_status(job_id)

    monkeypatch.setattr(WorkerJobStore, "session_status", status)
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")
    monkeypatch.setenv(telegram.TOKEN_ENV, "fake-token")
    sent = []
    monkeypatch.setattr(telegram, "_telegram_request", lambda _token, _method, params: sent.append(params) or {"ok": True})
    update = {
        "update_id": 7,
        "message": {
            "message_id": 42,
            "from": {"id": 111},
            "chat": {"id": 999},
            "text": "/gt steer run A instruction",
        },
    }

    replay = telegram.process_telegram_update(
        update,
        workspace=workspace,
        allowed_users=["111"],
        processed_callbacks=set(),
        project_key="GOODTEAM",
    )

    assert replay["accepted"] is True
    assert replay["control_state"] == "rpc_accepted"
    assert replay["control_id"] == control_id
    assert len((old_root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert not (new_root / "pi_control_queue.jsonl").exists()
    assert "Job: …ob-run-a" in sent[-1]["text"]


def _historical_telegram_control(
    store: WorkerJobStore,
    root: Path,
    *,
    command: str,
    action: str,
    message_id: int = 42,
) -> tuple[str, dict[str, object]]:
    control_id = TelegramRuntimeControlService.stable_control_id(
        chat_id=999,
        message_id=message_id,
        user_id="111",
        command=command,
    )
    control = {
        "id": control_id,
        "action": action,
        "message": "run A instruction" if action == "steer" else "",
        "reason": "scope changed" if action == "abort" else "",
        "source": "telegram",
        "actor": "telegram:user:111",
        "job_id": "job-run-a",
        "execution_id": "execution-a",
        "audit_context": {
            "telegram_chat_id": "999",
            "telegram_message_id": str(message_id),
            "telegram_user_id": "111",
            "command": command,
            "project_key": "GOODTEAM",
        },
    }
    store._append_jsonl(root / "pi_control_queue.jsonl", control)
    return control_id, control


def test_old_stop_update_replays_run_a_without_aborting_active_run_b(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    StateStore(workspace).write_project_current(create_initial_state("run-b", "GOODTEAM", "next run"))
    old_root = _write_job(project, "job-run-a", run_id="run-a")
    new_root = _write_job(project, "job-run-b", run_id="run-b")
    store = WorkerJobStore(project)
    control_id, _control = _historical_telegram_control(store, old_root, command="stop", action="abort")
    store._append_pi_control_ack(old_root, control_id, "rpc_accepted")
    monkeypatch.setattr(
        WorkerJobStore,
        "session_status",
        lambda _self, job_id: {
            **_active_status(job_id),
            **({"session_status": "aborted", "process_alive": False} if job_id == "job-run-a" else {}),
        },
    )
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")

    result, text = TelegramRuntimeControlService(workspace, "GOODTEAM").control(
        "stop",
        reason="scope changed",
        chat_id=999,
        message_id=42,
        user_id="111",
    )

    assert result["control_id"] == control_id
    assert result["control_state"] == "rpc_accepted"
    assert result["job_id"] == "job-run-a"
    assert len((old_root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert not (new_root / "pi_control_queue.jsonl").exists()
    assert WorkerJobStore(project).session_status("job-run-b")["session_status"] == "running"
    assert "GoodTeam service remains running" in text


@pytest.mark.parametrize(
    ("terminal_state", "error"),
    [("queued", ""), ("failed", "run A rejection")],
)
def test_old_update_replays_nonaccepted_run_a_state_without_touching_run_b(
    monkeypatch,
    tmp_path,
    terminal_state,
    error,
):
    workspace, project = _configured_workspace(tmp_path)
    StateStore(workspace).write_project_current(create_initial_state("run-b", "GOODTEAM", "next run"))
    old_root = _write_job(project, "job-run-a", run_id="run-a")
    new_root = _write_job(project, "job-run-b", run_id="run-b")
    store = WorkerJobStore(project)
    control_id, _control = _historical_telegram_control(store, old_root, command="steer", action="steer")
    if terminal_state == "failed":
        store._append_pi_control_ack(old_root, control_id, terminal_state, error=error)
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")

    result, text = TelegramRuntimeControlService(workspace, "GOODTEAM").control(
        "steer",
        message="run A instruction",
        chat_id=999,
        message_id=42,
        user_id="111",
    )

    assert result["control_id"] == control_id
    assert result["control_state"] == terminal_state
    assert result["accepted"] is False
    assert len((old_root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert not (new_root / "pi_control_queue.jsonl").exists()
    if terminal_state == "failed":
        assert result["error"] == error
        assert error in text
    else:
        assert "not yet acknowledged" in text


def test_project_history_lookup_does_not_match_other_project(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    other_root = _write_job(project, "job-other", run_id="run-old", project_key="OTHER")
    current_root = _write_job(project, "job-current", run_id="run-1")
    store = WorkerJobStore(project)
    control_id, control = _historical_telegram_control(store, other_root, command="steer", action="steer")
    control["job_id"] = "job-other"
    control["audit_context"]["project_key"] = "OTHER"  # type: ignore[index]
    (other_root / "pi_control_queue.jsonl").write_text(json.dumps(control) + "\n", encoding="utf-8")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")

    result, _text = TelegramRuntimeControlService(workspace, "GOODTEAM").control(
        "steer",
        message="run A instruction",
        chat_id=999,
        message_id=42,
        user_id="111",
    )

    assert result["control_id"] == control_id
    assert len((other_root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len((current_root / "pi_control_queue.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_historical_control_context_mismatch_fails_closed_before_current_run(monkeypatch, tmp_path):
    workspace, project = _configured_workspace(tmp_path)
    old_root = _write_job(project, "job-run-a", run_id="run-a")
    new_root = _write_job(project, "job-current")
    store = WorkerJobStore(project)
    _control_id, control = _historical_telegram_control(store, old_root, command="steer", action="steer")
    control["audit_context"]["telegram_user_id"] = "forged"  # type: ignore[index]
    (old_root / "pi_control_queue.jsonl").write_text(json.dumps(control) + "\n", encoding="utf-8")
    monkeypatch.setattr(WorkerJobStore, "session_status", lambda _self, job_id: _active_status(job_id))

    with pytest.raises(ValueError, match="conflicts with its durable request context"):
        TelegramRuntimeControlService(workspace, "GOODTEAM").control(
            "steer",
            message="run A instruction",
            chat_id=999,
            message_id=42,
            user_id="111",
        )

    assert not (new_root / "pi_control_queue.jsonl").exists()


def test_duplicate_control_identity_across_project_history_fails_closed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first_root = _write_job(project, "job-run-a", run_id="run-a")
    second_root = _write_job(project, "job-run-b", run_id="run-b")
    store = WorkerJobStore(project)
    control_id = "control-tg-history123"
    store._append_jsonl(first_root / "pi_control_queue.jsonl", {"id": control_id, "action": "steer"})
    store._append_jsonl(second_root / "pi_control_queue.jsonl", {"id": control_id, "action": "steer"})

    with pytest.raises(ValueError, match="ambiguous within the configured project"):
        store.find_session_control(control_id, project_key="GOODTEAM")


def test_duplicate_control_identity_within_one_job_fails_closed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-run-a", run_id="run-a")
    store = WorkerJobStore(project)
    control_id = "control-tg-history123"
    store._append_jsonl(root / "pi_control_queue.jsonl", {"id": control_id, "action": "steer"})
    store._append_jsonl(root / "pi_control_queue.jsonl", {"id": control_id, "action": "steer"})

    with pytest.raises(ValueError, match="ambiguous within the worker job"):
        store.find_project_session_control(control_id, project_key="GOODTEAM")


def _queued_telegram_control(store: WorkerJobStore, root: Path, control_id: str = "control-tg-terminal123"):
    control = {
        "id": control_id,
        "action": "steer",
        "message": "focus tests",
        "source": "telegram",
        "actor": "telegram:user:111",
        "job_id": "job-active",
        "execution_id": "execution-1",
        "audit_context": {
            "telegram_chat_id": "999",
            "telegram_message_id": "42",
            "telegram_user_id": "111",
            "command": "steer",
            "project_key": "GOODTEAM",
        },
    }
    store._append_jsonl(root / "pi_control_queue.jsonl", control)
    return control


@pytest.mark.parametrize(
    "error",
    ["Pi session settled before RPC acknowledgement", "Pi session stopped before RPC acknowledgement: crash"],
)
def test_settle_or_crash_before_ack_records_one_failed_telegram_audit(tmp_path, error):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)

    store._fail_pending_pi_controls(root, job, error)
    store._fail_pending_pi_controls(root, job, error)

    acknowledgement = store._pi_control_ack_states(root)[control["id"]]
    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert acknowledgement["state"] == "failed"
    assert acknowledgement["error"] == error
    assert len(audits) == 1
    assert audits[0]["control_state"] == "failed"
    assert audits[0]["telegram_message_id"] == "42"


def test_restart_processing_failure_records_one_telegram_audit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    store._append_pi_control_ack(root, control["id"], "processing")

    store._process_pi_controls(root, job, None)  # type: ignore[arg-type]
    store._process_pi_controls(root, job, None)  # type: ignore[arg-type]

    acknowledgement = store._pi_control_ack_states(root)[control["id"]]
    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert acknowledgement["state"] == "failed"
    assert "ambiguous after worker restart" in acknowledgement["error"]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "failed"


def test_terminal_ack_without_audit_is_reconciled_once(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    store._append_pi_control_ack(root, control["id"], "failed", error="crash window")

    store._fail_pending_pi_controls(root, job, "unused")
    store._fail_pending_pi_controls(root, job, "unused")

    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "failed"
    assert audits[0]["error"] == "crash window"


def test_terminal_telegram_audit_check_and_append_are_atomic_across_store_instances(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    seed_store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(seed_store, root)
    seed_store._append_pi_control_ack(root, control["id"], "rpc_accepted")

    original_terminal_control = WorkerJobStore._telegram_terminal_control
    counter_lock = threading.Lock()
    active_checks = 0
    max_active_checks = 0

    def slow_terminal_control(job_root, control_id):
        nonlocal active_checks, max_active_checks
        result = original_terminal_control(job_root, control_id)
        with counter_lock:
            active_checks += 1
            max_active_checks = max(max_active_checks, active_checks)
        time.sleep(0.03)
        with counter_lock:
            active_checks -= 1
        return result

    monkeypatch.setattr(WorkerJobStore, "_telegram_terminal_control", staticmethod(slow_terminal_control))
    worker_count = 8
    start = threading.Barrier(worker_count)
    errors = []

    def reconcile():
        try:
            start.wait()
            WorkerJobStore(project)._record_pi_control_terminal(root, job, control, "rpc_accepted")
        except Exception as exc:  # pragma: no cover - asserted through errors
            errors.append(exc)

    threads = [threading.Thread(target=reconcile) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert max_active_checks == 1
    assert len(audits) == 1
    assert audits[0]["control_state"] == "rpc_accepted"


def test_terminal_telegram_audit_is_atomic_across_processes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    control = _queued_telegram_control(store, root)
    context = multiprocessing.get_context("spawn")
    start = context.Barrier(2)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_reconcile_terminal_in_process,
            args=(str(project), "job-active", control, "rpc_accepted", start, errors),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    assert errors.empty()
    events = [json.loads(line) for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    audits = [event for event in events if event.get("event") == "telegram_runtime_control"]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "rpc_accepted"


def test_terminal_ack_written_before_audit_failure_is_recoverable(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    original_append = WorkerJobStore._append_jsonl
    failed = False

    def fail_before_audit(path, payload):
        nonlocal failed
        if path.name == "pi_events.jsonl" and not failed:
            failed = True
            raise OSError("injected audit crash")
        original_append(path, payload)

    monkeypatch.setattr(WorkerJobStore, "_append_jsonl", staticmethod(fail_before_audit))
    with pytest.raises(OSError, match="injected audit crash"):
        store._record_pi_control_terminal(root, job, control, "rpc_accepted")
    assert store._pi_control_ack_states(root)[control["id"]]["state"] == "rpc_accepted"

    store._record_pi_control_terminal(root, job, control, "rpc_accepted")

    audits = [
        json.loads(line)
        for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "rpc_accepted"


def test_audit_written_before_append_returns_is_not_duplicated_on_recovery(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    original_append = WorkerJobStore._append_jsonl
    failed = False

    def fail_after_audit(path, payload):
        nonlocal failed
        original_append(path, payload)
        if path.name == "pi_events.jsonl" and not failed:
            failed = True
            raise OSError("injected post-audit crash")

    monkeypatch.setattr(WorkerJobStore, "_append_jsonl", staticmethod(fail_after_audit))
    with pytest.raises(OSError, match="injected post-audit crash"):
        store._record_pi_control_terminal(root, job, control, "failed", error="rejected")

    store._record_pi_control_terminal(root, job, control, "failed", error="rejected")

    audits = [
        json.loads(line)
        for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(audits) == 1
    assert audits[0]["control_state"] == "failed"


def test_terminal_audit_without_ack_recovers_matching_ack_on_duplicate(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    control = _queued_telegram_control(store, root)
    store._append_jsonl(
        root / "pi_events.jsonl",
        {
            "event": "telegram_runtime_control",
            "control_id": control["id"],
            "control_state": "failed",
            "error": "durable rejection",
        },
    )
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")

    result = store.session_control(
        "job-active",
        "steer",
        message="focus tests",
        control_id=str(control["id"]),
    )

    acknowledgement = store._pi_control_ack_states(root)[control["id"]]
    assert result["control_state"] == "failed"
    assert result["error"] == "durable rejection"
    assert acknowledgement["state"] == "failed"
    assert acknowledgement["error"] == "durable rejection"
    assert len((root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_worker_recovers_terminal_audit_without_ack_before_sending_rpc(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    store._append_jsonl(
        root / "pi_events.jsonl",
        {
            "event": "telegram_runtime_control",
            "control_id": control["id"],
            "control_state": "rpc_accepted",
        },
    )

    store._process_pi_controls(root, job, None)  # type: ignore[arg-type]

    acknowledgement = store._pi_control_ack_states(root)[control["id"]]
    assert acknowledgement["state"] == "rpc_accepted"
    assert len((root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_concurrent_terminal_state_conflict_fails_closed_with_one_winner(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    seed_store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(seed_store, root)
    start = threading.Barrier(2)
    outcomes = []

    def record(state):
        try:
            start.wait()
            WorkerJobStore(project)._record_pi_control_terminal(root, job, control, state, error="rejected")
            outcomes.append((state, "recorded"))
        except ValueError as exc:
            outcomes.append((state, str(exc)))

    threads = [threading.Thread(target=record, args=(state,)) for state in ("rpc_accepted", "failed")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result == "recorded" for _state, result in outcomes) == 1
    assert sum("conflicts with its durable" in result for _state, result in outcomes) == 1
    acknowledgement = seed_store._pi_control_ack_states(root)[control["id"]]
    audits = [
        json.loads(line)
        for line in (root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(audits) == 1
    assert audits[0]["control_state"] == acknowledgement["state"]
    terminal_acks = [
        json.loads(line)
        for line in (root / "pi_control_acks.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["state"] in {"rpc_accepted", "failed"}
    ]
    assert len(terminal_acks) == 1


def test_failed_ack_and_audit_error_conflict_fails_closed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = _write_job(project, "job-active")
    store = WorkerJobStore(project)
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    control = _queued_telegram_control(store, root)
    store._append_pi_control_ack(root, control["id"], "failed", error="ack rejection")
    store._append_jsonl(
        root / "pi_events.jsonl",
        {
            "event": "telegram_runtime_control",
            "control_id": control["id"],
            "control_state": "failed",
            "error": "audit rejection",
        },
    )

    with pytest.raises(ValueError, match="acknowledgement error conflicts"):
        store._record_pi_control_terminal(root, job, control, "failed", error="ack rejection")

    assert len((root / "pi_control_acks.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len((root / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()) == 1


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
