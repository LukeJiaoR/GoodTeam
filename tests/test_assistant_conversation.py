"""Conversation continuity must not weaken task or approval authority."""
from copy import deepcopy

import pytest

from goodteam_os.assistant_conversation import conversation_scope
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_assistant_drafts import send, setup_service


def pending_draft(tmp_path):
    service, root, executor, calls = setup_service(tmp_path)
    send(service, root, "Add search.")
    pending = send(service, root, "Build login instead.", "m2")["proposed_task"]
    return service, root, executor, calls, deepcopy(pending)


def test_pending_draft_accepts_questions_without_resolving_or_replacing_it(tmp_path):
    service, root, executor, calls, pending = pending_draft(tmp_path)
    result = send(service, root, "Why replace rather than append?", "question")
    assert result["proposed_task"] == pending
    assert result["timeline"][-1]["event"] == "conversation_replied"
    assert result["timeline"][-1]["details"]["actions_applied"] is False
    assert result["assistant_requests"]["question"]["status"] == "succeeded"
    assert len(calls) == 3
    assert pending["pending_change"]["text"] in calls[-1]["context"]
    assert executor.started == []
    with pytest.raises(ValueError, match="Resolve the pending"):
        TaskAssistantSessionStore(tmp_path, "DEMO").confirm_task(
            pending["id"], expected_revision=pending["revision"],
        )


@pytest.mark.parametrize("intent", ["work_request", "project_understanding", "team_management"])
def test_pending_discussion_does_not_execute_model_proposed_actions(tmp_path, intent):
    service, root, executor, _calls, pending = pending_draft(tmp_path)
    service.manager_turn = lambda **_values: {
        "intent": intent, "response": "Let's discuss the scope.",
        "team_commands": [{"action": "remove_worker", "worker_id": "builder"}],
        "team_authorization": "user_authorized",
    }
    service._handle_team_management = lambda *_args, **_kwargs: pytest.fail("Discussion mutated Team")
    result = send(service, root, "What does this change affect?", "question")
    assert result["proposed_task"] == pending
    assert result.get("pending_team_decision") is None
    assert StateStore(tmp_path).read_project_current("DEMO") is None
    assert executor.started == []


def test_running_question_uses_real_run_snapshot_and_preserves_work(tmp_path):
    service, root, executor, calls = setup_service(tmp_path)
    proposal = send(service, root, "Add search.")["proposed_task"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.confirm_task(proposal["id"], expected_revision=proposal["revision"])
    store.bind_workflow(proposal["id"], "run-live")
    state = create_initial_state("run-live", "DEMO", "Add search.")
    state.current_node = "implementation"
    state.next_node = "code_review"
    state.blockers = ["Dependency installation failed"]
    state_path = StateStore(tmp_path).write_project_current(state)
    before_state = state_path.read_bytes()
    before_proposal = store.read()["proposed_task"]
    result = send(service, root, "Why are we blocked? Also keep the schema unchanged.", "running-question")
    assert state_path.read_bytes() == before_state
    assert result["proposed_task"] == before_proposal
    assert result["archived_task_proposals"] == []
    reply = result["timeline"][-1]
    assert reply["event"] == "conversation_replied"
    assert reply["run_id"] == "run-live"
    assert reply["details"]["actions_applied"] is False
    assert all(value in calls[-1]["context"] for value in ("run-live", "implementation", "Dependency installation failed"))
    assert executor.started == []


def test_pending_question_retry_is_deduplicated_and_preserves_draft(tmp_path):
    service, root, _executor, _calls, pending = pending_draft(tmp_path)
    attempts = []

    def manager(**values):
        attempts.append(values["message_id"])
        if len(attempts) == 1:
            raise RuntimeError("temporary model failure")
        return {"intent": "ambiguous", "response": "The original scope is preserved."}

    service.manager_turn = manager
    with pytest.raises(ManagerExecutionError):
        send(service, root, "Explain the scope.", "retry-question")
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    assert store.read()["assistant_requests"]["retry-question"]["status"] == "failed"
    assert store.read()["proposed_task"] == pending
    result = service.retry_message(project_key="DEMO", root=root, message_id="retry-question", expected_attempt=1)
    assert result["workflow_started"] is False
    send(service, root, "Explain the scope.", "retry-question")
    assert len(attempts) == 2
    assert attempts[0] != attempts[1]
    session = store.read()
    assert sum(item["message_id"] == "retry-question" for item in session["timeline"]) == 1
    assert sum(item["message_id"] == "reply:retry-question" for item in session["timeline"]) == 1
    assert session["proposed_task"] == pending


def test_terminal_run_falls_through_to_existing_new_task_intake(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    state = create_initial_state("run-done", "DEMO", "Old task")
    state.current_node = "complete"
    state.next_node = ""
    StateStore(tmp_path).write_project_current(state)
    assert conversation_scope(store) is None


def test_running_discussion_reads_persisted_change_context(tmp_path):
    from goodteam_os.intent_commit import IntentCommitStore
    from test_intent_commit import _start

    record = _start(tmp_path)["intent_commit"]
    intent_store = IntentCommitStore(tmp_path, "DEMO")
    intent_store.discuss(record["id"], "Keep the database schema unchanged.")
    before = intent_store.path.read_bytes()
    context = conversation_scope(TaskAssistantSessionStore(tmp_path, "DEMO"))
    assert context["change_discussions"][0]["id"] == record["id"]
    assert context["change_discussions"][0]["draft_text"] == "Keep the database schema unchanged."
    assert intent_store.path.read_bytes() == before
