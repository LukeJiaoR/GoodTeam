"""The coordinator, conversation, future drafts and business execution stay separate."""

from copy import deepcopy

import pytest

from goodteam_os.assistant_drafts import confirm_and_prepare
from goodteam_os.assistant_queue import handle_queue_action
from goodteam_os.assistant_workspace import build_project_assistant
from goodteam_os.delivery_outcomes import build_delivery_summary
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_workspace import build_team_workspace
from test_assistant_drafts import send, setup_service


def queue(service, root, **request):
    return handle_queue_action(service, project_key="DEMO", root=root, request=request)["session"]


def running_task(tmp_path):
    service, root, executor, calls = setup_service(tmp_path)
    proposal = send(service, root, "Implement search.")["proposed_task"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.confirm_task(proposal["id"])
    store.bind_workflow(proposal["id"], "run-live")
    state = create_initial_state("run-live", "DEMO", "Implement search.")
    state.current_node, state.next_node = "implementation", "code_review"
    StateStore(tmp_path).write_project_current(state)
    return service, root, executor, calls, store, state


def test_manager_calls_remain_auditable_without_becoming_tasks_or_delivery(tmp_path):
    state = create_initial_state("run-live", "DEMO", "Build search")
    jobs = [{"id": "manager", "role": "orchestrator", "phase": "manager_intent",
             "run_id": state.run_id, "status": "failed"},
            {"id": "worker", "role": "implementation", "run_id": state.run_id, "status": "running"}]
    session = {"project_key": "DEMO", "timeline": []}
    workspace = build_team_workspace(state=state, workflow_active=True, project_root=tmp_path,
                                     worker_jobs=jobs, task_assistant_session=session)
    assert [actor["role"] for actor in workspace["execution_actors"]] == ["implementation"]
    assistant = build_project_assistant(session, workspace, project_key="DEMO", workflow_active=True, jobs=jobs)
    assert [item["id"] for item in assistant["activity"]] == ["manager"]
    assert assistant["status"] == "available"
    delivery = build_delivery_summary(tmp_path, state=state, jobs=jobs)
    assert [item["id"] for item in delivery["attempts"]] == ["worker"]
    assert delivery["status"] == "working"


def test_manager_cannot_receive_business_assignments(tmp_path):
    store = LocalTaskStore(tmp_path)
    with pytest.raises(ValueError, match="Project Manager"):
        store.create_task(title="Build", description="", role="orchestrator")
    task = store.create_task(title="Build", description="", role="implementation")
    with pytest.raises(ValueError, match="Project Manager"):
        store.update_task(task["id"], role="orchestrator")
    assert store.get_task(task["id"])["role"] == "implementation"


def test_worker_failure_cannot_be_hidden_by_a_manager_like_phase(tmp_path):
    state = create_initial_state("run-live", "DEMO", "Build search")
    summary = build_delivery_summary(tmp_path, state=state, jobs=[
        {"id": "worker", "role": "implementation", "phase": "manager_intent",
         "run_id": state.run_id, "status": "failed"},
    ])
    assert summary["status"] == "blocked"
    assert summary["attempts"][0]["id"] == "worker"


def test_running_work_accepts_multiple_exact_drafts_without_execution(tmp_path):
    service, root, executor, calls, store, state = running_task(tmp_path)
    state_path = StateStore(tmp_path).project_state_dir("DEMO") / "current.json"
    original_state, original_proposal = state_path.read_bytes(), deepcopy(store.read()["proposed_task"])
    request = {"action": "queue_task", "text": "Next task\n\n  Keep whitespace.\n", "message_id": "future-one"}
    first = queue(service, root, **request)
    queue(service, root, **request)
    result = queue(service, root, action="queue_task", text="Another independent task", message_id="future-two")
    assert len(result["queued_task_proposals"]) == 2
    assert result["queued_task_proposals"][0]["exact_request"] == request["text"]
    assert result["proposed_task"] == original_proposal
    assert state_path.read_bytes() == original_state
    assert executor.started == [] and len(calls) == 1
    task = first["queued_task_proposals"][0]
    with pytest.raises(ValueError, match="active task"):
        queue(service, root, action="select_queued_task", proposal_id=task["id"], expected_revision=1)
    with pytest.raises(ValueError, match="different content"):
        queue(service, root, **{**request, "text": "Changed content"})
    assert state.run_id == store.read()["proposed_task"]["run_id"]


def test_queued_edit_is_version_bound_and_replay_safe(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    saved = queue(service, root, action="queue_task", text="Exact initial text", message_id="q1")
    task = saved["queued_task_proposals"][0]
    edit = {"action": "edit_queued_task", "proposal_id": task["id"], "expected_revision": 1,
            "message_id": "edit", "text": "Exact revised text"}
    queue(service, root, **edit)
    replay = queue(service, root, **edit)["queued_task_proposals"][0]
    assert replay["revision"] == 2 and replay["exact_request"] == edit["text"]
    with pytest.raises(ValueError, match="latest revision"):
        queue(service, root, action="discard_queued_task", proposal_id=task["id"], expected_revision=1)
    queue(service, root, action="discard_queued_task", proposal_id=task["id"], expected_revision=2)
    with pytest.raises(ValueError, match="no longer available"):
        queue(service, root, action="select_queued_task", proposal_id=task["id"], expected_revision=2)


def test_selecting_future_task_after_completion_does_not_confirm_or_dispatch(tmp_path):
    service, root, executor, calls, store, state = running_task(tmp_path)
    queued = queue(service, root, action="queue_task", text="Second task", message_id="q1")["queued_task_proposals"][0]
    state.current_node, state.next_node = "complete", ""
    StateStore(tmp_path).write_project_current(state)
    select = {"action": "select_queued_task", "proposal_id": queued["id"], "expected_revision": 1}
    selected = queue(service, root, **select)
    assert queue(service, root, **select)["proposed_task"] == selected["proposed_task"]
    assert selected["proposed_task"]["id"] == queued["id"]
    assert not selected["proposed_task"]["confirmed"] and not selected["proposed_task"]["run_id"]
    assert selected["archived_task_proposals"][0]["run_id"] == state.run_id
    assert StateStore(tmp_path).read_project_current("DEMO") == state
    assert executor.started == [] and len(calls) == 1
    assert len(store.read()["timeline"]) > 1


def test_existing_draft_must_be_resolved_before_selecting_future_task(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    original = send(service, root, "Original")["proposed_task"]
    task = queue(service, root, action="queue_task", text="Future", message_id="q1")["queued_task_proposals"][0]
    with pytest.raises(ValueError, match="current draft"):
        queue(service, root, action="select_queued_task", proposal_id=task["id"], expected_revision=1)
    assert TaskAssistantSessionStore(tmp_path, "DEMO").read()["proposed_task"] == original


def test_confirmation_cannot_start_another_draft_during_active_work(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    draft = send(service, root, "New draft")["proposed_task"]
    StateStore(tmp_path).write_project_current(create_initial_state("other", "DEMO", "Active"))
    with pytest.raises(ValueError, match="active task"):
        confirm_and_prepare(service, project_key="DEMO", root=root,
                            request={"proposal_id": draft["id"], "expected_revision": draft["revision"]})
    assert not TaskAssistantSessionStore(tmp_path, "DEMO").read()["proposed_task"]["confirmed"]


def test_historical_reference_answers_without_touching_active_task_or_team(tmp_path):
    service, root, executor, calls, store, state = running_task(tmp_path)
    states = StateStore(tmp_path)
    states.archive_project_current("DEMO", "complete")
    states.write_project_current(create_initial_state("current", "DEMO", "Current task"))
    before = states.read_project_current("DEMO")
    proposal = deepcopy(store.read()["proposed_task"])
    result = service.send_message(project_key="DEMO", root=root, text="Explain the old task", message_id="history", context_run_id=state.run_id)
    assert result["session"]["timeline"][-1]["run_id"] == state.run_id
    assert state.idea in calls[-1]["context"] and '"read_only": true' in calls[-1]["context"]
    assert states.read_project_current("DEMO") == before
    assert store.read()["proposed_task"] == proposal
    assert executor.started == []
    with pytest.raises(ValueError, match="different task context"):
        service.send_message(project_key="DEMO", root=root, text="Explain the old task", message_id="history", context_run_id="current")


def test_foreign_context_is_rejected_before_recording_or_model_execution(tmp_path):
    service, root, _executor, calls = setup_service(tmp_path)
    states = StateStore(tmp_path)
    states.write_project_current(create_initial_state("foreign", "OTHER", "Private"))
    with pytest.raises(ValueError, match="unavailable for this project"):
        service.send_message(project_key="DEMO", root=root, text="Explain", message_id="foreign", context_run_id="foreign")
    assert not calls
    assert TaskAssistantSessionStore(tmp_path, "DEMO").read()["timeline"] == []


def test_change_discussion_remains_in_project_conversation_after_its_run_ends():
    intent = {"id": "change-old", "kind": "request_changes", "status": "committed",
              "mission": {"id": "old-run"}, "target": {"target_id": "security"},
              "events": [{"id": "old-feedback", "actor": "user", "event": "change_request_shared",
                          "timestamp": "2026-09-01T10:00:00Z", "content": "Preserve the schema."}]}
    workspace = {"mission": None, "manager_timeline": [
        {"id": "new-question", "actor": "user", "content": "What next?", "timestamp": "2026-09-02T10:00:00Z"},
    ]}
    assistant = build_project_assistant({}, workspace, project_key="DEMO", intent_commits=[intent])
    assert [item["id"] for item in assistant["timeline"]] == ["old-feedback", "new-question"]
    assert assistant["timeline"][0]["run_id"] == "old-run"
    assert assistant["timeline"][0]["content"] == "Preserve the schema."
    assert assistant["status"] == "available"
