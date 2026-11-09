"""Project research uses task intake; import and reads never start inspection."""

import json

import pytest

from goodteam_os import assistant_requests, project_readiness, task_assistant, web
from goodteam_os.assistant_drafts import handle_draft_action
from goodteam_os.manager_execution import _parse_chat_turn
from goodteam_os.project_readiness import ProjectReadinessStore
from goodteam_os.project_readiness_executor import ProjectReadinessExecutor
from goodteam_os.state import StateStore
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_import_waits_for_user_and_research_uses_existing_task_intake(tmp_path, monkeypatch):
    workspace, root = tmp_path / "workspace", tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    calls = []

    def no_inspection(*_args, **_kwargs):
        pytest.fail("Project registration and task intake must not start inspection")

    monkeypatch.setattr(ProjectReadinessExecutor, "start", no_inspection)
    monkeypatch.setattr(ProjectReadinessExecutor, "artifact", no_inspection)
    # Even directory-based type detection waits for the first user message.
    with monkeypatch.context() as passive:
        passive.setattr(project_readiness, "assess_project_mode", no_inspection)
        passive.setattr(task_assistant, "assess_project_mode", no_inspection)
        passive.setattr(assistant_requests, "assess_project_mode", no_inspection)
        status, imported = web.handle_project_api_post(workspace, "/api/projects/import", {"root": str(root)})
        key = imported["project"]["project_key"]
        assert status == 201
        assert imported["readiness"]["project_mode"] == "unconfirmed"
        assert not imported["readiness"].get("execution")
        assert not imported["readiness"].get("artifacts")
        service = TaskAssistantService(workspace)
        first = service.session(key, root)
        assert service.session(key, root) == first
        assert len(first["timeline"]) == 1
        assert first["attention_request"] is None
        assert first["proposed_task"] is None
        assert not list(root.rglob(".goodteam"))

    def manager(**request):
        calls.append(request)
        return _parse_chat_turn(json.dumps({
            "intent": "work_request", "response": "已整理为待确认的研究任务。",
            "task_intake": {"ready": True, "missing_decisions": [], "source_message_ids": ["research"]},
        }))

    config = workspace / "projects" / f"{key.lower()}.local.yaml"
    service = TaskAssistantService(workspace, project_config=config, manager_turn=manager)
    text = "梳理当前项目的架构、关键流程和待办，交给 Researcher，只研究，不修改代码。"
    result = service.send_message(project_key=key, root=root, message_id="research", text=text)
    assert result["intent"] == "work_request"
    assert result["workflow_started"] is False
    proposal = result["session"]["proposed_task"]
    assert proposal["exact_request"] == text
    assert proposal["source_message_ids"] == ["research"]
    assert not proposal["confirmed"] and not proposal.get("run_id")
    assert StateStore(workspace).read_project_current(key) is None
    replay = service.send_message(project_key=key, root=root, message_id="research", text=text)
    assert replay["session"]["proposed_task"] == proposal
    assert len(calls) == 1
    assert ProjectReadinessStore(workspace).read(key)["project_mode"] == "existing_project"

    # Old pending records remain readable, but cannot restart the retired path.
    store = TaskAssistantSessionStore(workspace, key)
    store._mutate(lambda value: {**value, "pending_inspection": {"message_id": "old-inspection"}})
    store.append_event("old-inspection", "project_inspection_started")
    before = store.read()["timeline"]
    handle_draft_action(service, project_key=key, root=root,
                        request={"action": "confirm_project_mode", "mode": "existing_project"})
    assert service.session(key, root)["timeline"] == before
