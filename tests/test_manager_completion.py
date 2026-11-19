"""Completed native replies retain their exact thread/turn identity across restart."""
import pytest

from goodteam_os.manager_completion import completed_manager_result
from goodteam_os.manager_execution import ManagerExecutionError, run_manager_chat_turn
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_manager_completion_planning import native_manager as native_manager_fixture

native_manager = native_manager_fixture


def test_native_completion_receipt_is_reused_and_wrong_turn_is_rejected(native_manager):
    workspace, _root, config, response, calls = native_manager
    response.write_text("The actual task result is ready for independent review.")
    run_manager_chat_turn(project_config=config, project_key="DEMO", message_id="one", text="Report progress.", context="facts")
    store = TaskAssistantSessionStore(workspace, "DEMO")
    complete = completed_manager_result(store, "one")
    assert complete["text"] == response.read_text()
    assert completed_manager_result(store, "missing") is None
    def change(session):
        session["manager_app_server"]["requests"]["one"]["turn_id"] = "another-turn"
        return session
    store._mutate(change)
    with pytest.raises(ManagerExecutionError, match="identity"):
        completed_manager_result(store, "one")
    assert len(calls.read_text().splitlines()) == 1
