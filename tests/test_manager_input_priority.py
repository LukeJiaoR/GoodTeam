"""Durable admission and native recovery do not block project conversation."""

import threading
from concurrent.futures import ThreadPoolExecutor

from goodteam_os import assistant_requests, web
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_manager_completion_planning import native_manager as native_manager_fixture

native_manager = native_manager_fixture


def test_second_input_and_native_recovery_while_first_response_is_running(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    first_started, release_first = threading.Event(), threading.Event()
    calls = []

    def turn(**request):
        calls.append(request["logical_message_id"])
        if request["logical_message_id"] == "first":
            first_started.set()
            assert release_first.wait(5), "The first response was not released by the test"
        return {"intent": "ambiguous", "response": "Current progress: one task is running."}

    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=turn)
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    path = "/api/projects/DEMO/task-assistant/messages"
    # A blocked model call must not hold admission or be declared interrupted
    # just because another reader can acquire the conversation lock.
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(service.send_message, project_key="DEMO", root=root,
                                message_id="first", text="Inspect the current work.")
        try:
            assert first_started.wait(5)
            running = service.session("DEMO", root)["assistant_requests"]["first"]
            assert running["status"] == "running"
            status, accepted = web.handle_project_api_post(tmp_path, path,
                {"message_id": "first", "text": "Inspect the current work.", "background": True})
            assert status == 202 and accepted["accepted"]
            second = service.send_message(project_key="DEMO", root=root, message_id="second",
                                          text="What is already running?")
            assert second["session"]["assistant_requests"]["second"]["status"] == "succeeded"
            assert second["session"]["assistant_requests"]["first"]["status"] == "running"
            assert calls == ["first", "second"]
            assert not first.done()
        finally:
            release_first.set()
        assert first.result(timeout=5)["session"]["assistant_requests"]["first"]["status"] == "succeeded"

    from goodteam_os import manager_app_server

    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    assistant_requests.update_request(store, "recovered", attempt=1, status="running", phase="model")
    monkeypatch.setattr(manager_app_server, "request_liveness", lambda **_kwargs: "active")
    assistant_requests.recover_interrupted(store, project_config=config)
    assert store.read()["assistant_requests"]["recovered"]["status"] == "running"
    monkeypatch.setattr(manager_app_server, "request_liveness", lambda **_kwargs: "stopped")
    assistant_requests.recover_interrupted(store, project_config=config)
    assert store.read()["assistant_requests"]["recovered"]["status"] == "stopped"
    assistant_requests.update_request(store, "completed", attempt=1, status="running", phase="model")
    evidence = {"adapter": "codex-app-server", "thread_id": "native-thread", "turn_id": "native-turn",
                "logical_id": "completed", "status": "completed"}
    store._mutate(lambda session: {**session, "manager_app_server": {"requests": {
        "completed": {"status": "completed", "phase": "conversation", "thread_id": "native-thread",
            "turn_id": "different-turn", "attempt_id": assistant_requests.worker_attempt_id("DEMO", "completed", 1),
            "result": {"text": "The Worker result is ready for review.", "manager_execution": evidence}},
    }}})
    assert not assistant_requests._recover_native_reply(store, "completed", 1)
    assert not any(item.get("message_id") == "reply:completed" for item in store.read()["timeline"])
    def repair(session):
        session["manager_app_server"]["requests"]["completed"]["turn_id"] = "native-turn"
        return session
    store._mutate(repair)
    monkeypatch.setattr(manager_app_server, "request_liveness", lambda **_kwargs: "completed")
    assistant_requests.recover_interrupted(store, project_config=config)
    assistant_requests.recover_interrupted(store, project_config=config)
    completed = store.read()
    assert completed["assistant_requests"]["completed"]["status"] == "succeeded"
    replies = [item for item in completed["timeline"] if item.get("message_id") == "reply:completed"]
    assert len(replies) == 1 and replies[0]["details"]["manager_execution"] == evidence
    assistant_requests.update_request(store, "earlier", attempt=1, status="running", phase="model")
    def suppressed(session):
        session["manager_app_server"]["requests"]["earlier"] = {
            **session["manager_app_server"]["requests"]["completed"],
            "attempt_id": assistant_requests.worker_attempt_id("DEMO", "earlier", 1),
            "result": {"text": replies[0]["content"], "reply_suppressed": True,
                       "manager_execution": {**evidence, "logical_id": "earlier"}}}
        return session
    store._mutate(suppressed)
    assistant_requests.recover_interrupted(store, project_config=config)
    assert store.read()["assistant_requests"]["earlier"]["status"] == "succeeded"
    assert not any(item.get("message_id") == "reply:earlier" for item in store.read()["timeline"])
    assert calls == ["first", "second"]  # Native completion recovery never reruns tools or the model.


def test_background_admission_stream_and_stop_keep_one_attempt(tmp_path, monkeypatch):
    from goodteam_os import manager_app_server

    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    began, release, finished = threading.Event(), threading.Event(), threading.Event()
    execute = assistant_requests._execute_accepted

    def tracked_execute(*args, **kwargs):
        try:
            return execute(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(assistant_requests, "_execute_accepted", tracked_execute)
    calls = []
    store = TaskAssistantSessionStore(tmp_path, "DEMO")

    def turn(**request):
        calls.append(request)
        assistant_requests.update_request(store, "stream", stream_text="Checking the active Worker.")
        began.set()
        assert release.wait(5)
        return {"intent": "ambiguous", "response": "Work inspected."}

    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=turn)
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(manager_app_server, "stop_manager_request", lambda **_kwargs: {"status": "interrupted"})
    path = "/api/projects/DEMO/task-assistant"
    try:
        status, accepted = web.handle_project_api_post(tmp_path, f"{path}/messages",
            {"message_id": "stream", "text": "Inspect progress.", "background": True})
        assert status == 202 and accepted["accepted"]
        assert began.wait(5)
        assert service.session("DEMO", root)["assistant_requests"]["stream"]["stream_text"] == "Checking the active Worker."
        status, stopped = web.handle_project_api_post(tmp_path, f"{path}/actions",
            {"action": "stop_manager_request", "message_id": "stream", "expected_attempt": 1})
        assert status == 200
        assert stopped["session"]["assistant_requests"]["stream"]["status"] == "stopped"
        # Reposting the accepted ID must never launch another model call.
        web.handle_project_api_post(tmp_path, f"{path}/messages",
            {"message_id": "stream", "text": "Inspect progress.", "background": True})
        assert len(calls) == 1
    finally:
        release.set()
    assert finished.wait(5)
    assert store.read()["assistant_requests"]["stream"]["status"] == "stopped"
