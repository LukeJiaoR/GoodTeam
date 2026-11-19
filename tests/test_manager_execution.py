"""Manager conversation uses native threads and plain replies, never WorkerJobs."""
import json

import pytest

from goodteam_os import manager_execution
from goodteam_os.manager_app_server import close_runtimes
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_manager_completion_planning import native_manager as native_manager_fixture

native_manager = native_manager_fixture


def test_manager_conversation_keeps_native_thread_and_reuses_original_completed_turn(native_manager):
    workspace, root, config, response, calls = native_manager
    response.write_text("We have one concrete task to discuss. What should its acceptance prove?")
    values = dict(project_config=config, project_key="DEMO", message_id="attempt-1",
                  logical_message_id="first", text="Let's discuss the goal.", context="Original project context")
    first = manager_execution.run_manager_chat_turn(**values)
    assert first["response"] == response.read_text()
    assert first["native_manager"] and first["manager_execution"]["adapter"] == "codex-app-server"
    assert not (root / ".goodteam/workers/jobs").exists()
    response.write_text("The requirement is now clear; the earlier discussion is retained.")
    second = manager_execution.run_manager_chat_turn(**{
        **values, "logical_message_id": "second", "message_id": "attempt-2", "text": "Keep it read-only."})
    assert second["manager_execution"]["thread_id"] == first["manager_execution"]["thread_id"]
    assert second["manager_execution"]["turn_id"] != first["manager_execution"]["turn_id"]
    assert len(calls.read_text().splitlines()) == 2
    close_runtimes()
    replay = manager_execution.run_manager_chat_turn(**{**values, "message_id": "retry", "context": "New UI snapshot"})
    assert replay == first
    assert len(calls.read_text().splitlines()) == 2
    record = TaskAssistantSessionStore(workspace, "DEMO").read()["manager_app_server"]["requests"]["first"]
    assert record["context"] == "Original project context"
    with pytest.raises(manager_execution.ManagerExecutionError, match="different input"):
        manager_execution.run_manager_chat_turn(**{**values, "text": "A different request under the same identity"})


def test_assignment_validation_keeps_bounded_role_contract():
    action = {"kind": "queue_task", "role": "research", "execution_policy": "read_only", "write_domains": [],
              "acceptance_criteria": ["Cite the retained project evidence."],
              "delegation": {"purpose": "Understand the project", "context": "Current repository", "scope": "Read only"}}
    payload = {"intent": "work_request", "response": "Queue request.", "task_action": action,
               "task_intake": {"ready": True, "missing_decisions": [], "source_message_ids": ["request"]}}
    assert manager_execution._parse_chat_turn(json.dumps(payload))["task_action"] == action
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_execution._parse_chat_turn(json.dumps({**payload, "task_action": {**action, "worker_id": "invented"}}))
