import pytest

from goodteam_os import web
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService


def setup_api(tmp_path, monkeypatch):
    root = tmp_path / "target"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    calls = []
    def turn(**request):
        calls.append(request)
        if len(calls) == 1:
            raise ManagerExecutionError("unsupported model: private-token")
        return {"intent": "ambiguous", "response": "Ready"}
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=turn)
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    return calls, service, root


def test_api_failure_reload_retry_and_replay(tmp_path, monkeypatch):
    calls, service, root = setup_api(tmp_path, monkeypatch)
    path = "/api/projects/DEMO/task-assistant"
    with pytest.raises(web.ProjectApiError) as error:
        web.handle_project_api_post(tmp_path, f"{path}/messages", {"message_id": "m1", "text": "Original"})
    assert 502 == error.value.status
    assert "private-token" not in str(error.value)
    request = service.session("DEMO", root)["assistant_requests"]["m1"]
    assert "model_unavailable" == request["error"]["code"]
    retry = {"action": "retry_message", "message_id": "m1", "expected_attempt": 1}
    for _attempt in range(2):
        status, payload = web.handle_project_api_post(tmp_path, f"{path}/actions", retry)
        assert 200 == status
        assert "succeeded" == payload["session"]["assistant_requests"]["m1"]["status"]
    assert 2 == len(calls)


def test_runtime_check_requires_explicit_consent(tmp_path, monkeypatch):
    calls, _service, _root = setup_api(tmp_path, monkeypatch)
    with pytest.raises(web.ProjectApiError) as error:
        web.handle_project_api_post(tmp_path, "/api/projects/DEMO/task-assistant/actions",
                                    {"action": "check_runtime", "message_id": "c1"})
    assert 409 == error.value.status
    assert [] == calls
