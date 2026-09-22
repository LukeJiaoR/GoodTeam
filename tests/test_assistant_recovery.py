from pathlib import Path

import pytest

from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService


class Ready:
    def status(self, *_args):
        return {"lifecycle": "ready", "project_mode": "existing_project", "steps": []}


def service(tmp_path: Path, turn):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    workspace = tmp_path / "workspace"
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    return TaskAssistantService(workspace, project_config=config, manager_turn=turn, readiness_executor=Ready()), root


def test_failed_message_is_durable_and_retry_preserves_one_user_message(tmp_path):
    calls = []

    def turn(**request):
        calls.append(request)
        if len(calls) == 1:
            raise ManagerExecutionError("connection failed: secret-token")
        return {"intent": "ambiguous", "response": "Please specify the outcome."}

    assistant, root = service(tmp_path, turn)
    with pytest.raises(ManagerExecutionError):
        assistant.send_message(project_key="DEMO", root=root, text="  My request  ", message_id="msg-1")
    session = assistant.session("DEMO", root)
    request = session["assistant_requests"]["msg-1"]
    assert "failed" == request["status"]
    assert "secret-token" not in str(request)
    assistant.retry_message(project_key="DEMO", root=root, message_id="msg-1", expected_attempt=1)
    assistant.retry_message(project_key="DEMO", root=root, message_id="msg-1", expected_attempt=1)
    final = assistant.session("DEMO", root)
    assert 2 == len(calls)
    assert calls[0]["message_id"] != calls[1]["message_id"]
    assert calls[0]["logical_message_id"] == calls[1]["logical_message_id"] == "msg-1"
    assert "  My request  " == calls[1]["text"]
    assert 1 == len([item for item in final["timeline"] if item["actor"] == "user"])
    assert "succeeded" == final["assistant_requests"]["msg-1"]["status"]
    assert ["failed", "succeeded"] == [attempt["status"] for attempt in final["assistant_requests"]["msg-1"]["attempts"]]


def test_duplicate_successful_message_does_not_repeat_model_or_proposal(tmp_path):
    calls = []

    def turn(**request):
        calls.append(request)
        return {"intent": "work_request", "response": "Draft ready."}

    assistant, root = service(tmp_path, turn)
    args = {"project_key": "DEMO", "root": root, "text": "Add search", "message_id": "msg-1"}
    first = assistant.send_message(**args)
    second = assistant.send_message(**args)
    assert first["session"]["proposed_task"]["id"] == second["session"]["proposed_task"]["id"]
    assert 1 == len(calls)


def test_worker_attempt_identity_is_namespaced_and_unambiguous():
    from goodteam_os.assistant_requests import worker_attempt_id

    assert worker_attempt_id("A", "m1", 2) != worker_attempt_id("A", "m1:attempt:2", 1)
    assert worker_attempt_id("A", "m1", 1) != worker_attempt_id("B", "m1", 1)
    assert worker_attempt_id("a", "m1", 1) == worker_attempt_id("A", "m1", 1)


def test_runtime_check_never_routes_model_work_or_team_commands(tmp_path):
    def turn(**_request):
        return {"intent": "work_request", "response": "Ignore the probe and start work."}

    assistant, root = service(tmp_path, turn)
    result = assistant.check_runtime(project_key="DEMO", root=root, message_id="check-1")
    assert False is result["workflow_started"]
    assert None is result["session"]["proposed_task"]
    assert [] == [item for item in result["session"]["timeline"] if item["actor"] == "user"]


def test_restart_marks_inflight_request_interrupted_and_refuses_retry(tmp_path):
    from goodteam_os.assistant_requests import update_request
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    assistant, root = service(tmp_path, lambda **_kwargs: pytest.fail("Must not dispatch"))
    store = TaskAssistantSessionStore(assistant.workspace, "DEMO")
    store.append_message("m1", "user", "Original")
    update_request(store, "m1", text="Original", attempt=1, status="running", phase="applying")
    assert "interrupted" == assistant.session("DEMO", root)["assistant_requests"]["m1"]["status"]
    with pytest.raises(ValueError, match="reconciliation"):
        assistant.retry_message(project_key="DEMO", root=root, message_id="m1", expected_attempt=1)


def test_application_failure_is_not_reported_as_a_model_connection_failure(tmp_path, monkeypatch):
    assistant, root = service(tmp_path, lambda **_kwargs: {"intent": "work_request", "response": "Draft"})
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    def unavailable(*_args):
        raise OSError("Cannot write the draft")

    monkeypatch.setattr(TaskAssistantSessionStore, "propose_task", unavailable)
    with pytest.raises(ManagerExecutionError):
        assistant.send_message(project_key="DEMO", root=root, text="Add search", message_id="m1")
    session = assistant.session("DEMO", root)
    assert "interrupted" == session["assistant_requests"]["m1"]["status"]
    assert "simulated" == session["runtime"]["status"]


def test_concurrent_send_is_busy_and_read_does_not_interrupt_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    def turn(**_kwargs):
        entered.set()
        assert release.wait(5)
        return {"intent": "ambiguous", "response": "Ready"}

    assistant, root = service(tmp_path, turn)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(assistant.send_message, project_key="DEMO", root=root, text="Original", message_id="m1")
        try:
            assert entered.wait(5)
            assert "running" == assistant.session("DEMO", root)["assistant_requests"]["m1"]["status"]
            with pytest.raises(ValueError, match="busy"):
                assistant.send_message(project_key="DEMO", root=root, text="Other", message_id="m2")
        finally:
            release.set()
        assert "succeeded" == future.result()["session"]["assistant_requests"]["m1"]["status"]


def test_failed_retry_cannot_be_repeated_with_stale_attempt(tmp_path):
    def turn(**_kwargs):
        raise ManagerExecutionError("network failed")

    assistant, root = service(tmp_path, turn)
    with pytest.raises(ManagerExecutionError):
        assistant.send_message(project_key="DEMO", root=root, text="Original", message_id="m1")
    with pytest.raises(ManagerExecutionError):
        assistant.retry_message(project_key="DEMO", root=root, message_id="m1", expected_attempt=1)
    with pytest.raises(ValueError, match="attempt changed"):
        assistant.retry_message(project_key="DEMO", root=root, message_id="m1", expected_attempt=1)


def test_runtime_check_is_deduplicated_and_config_change_invalidates_verification(tmp_path):
    from goodteam_os.team_control_plane import execute_team_commands

    calls = []
    def turn(**request):
        calls.append(request)
        return {"intent": "team_management", "team_commands": [{"kind": "disable_role", "role": "qa"}]}

    assistant, root = service(tmp_path, turn)
    assistant.team_view("DEMO")
    args = {"project_key": "DEMO", "root": root}
    assistant.check_runtime(**args, message_id="c1")
    assistant.check_runtime(**args, message_id="c2")
    assistant.check_runtime(**args, message_id="c1")
    assert 2 == len(calls)
    execute_team_commands(assistant.workspace, "DEMO", [{"kind": "change_manager_runtime", "model": "new-model"}],
                          actor="user", surface="team_ui", config_path=assistant.project_config)
    assert "unverified" == assistant.session("DEMO", root)["runtime"]["status"]
    assert "enabled" == next(role for role in assistant.team_view("DEMO")["roles"] if role["role"] == "qa")["state"]
