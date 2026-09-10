import pytest

from goodteam_os import web
from goodteam_os.assistant_drafts import confirm_and_prepare, handle_draft_action
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_assistant_drafts import send, setup_service


def action(service, root, proposal, **values):
    return handle_draft_action(service, project_key="DEMO", root=root, request={
        "action": "resolve_draft", "proposal_id": proposal["id"], "expected_revision": proposal["revision"], **values,
    })["session"]


@pytest.mark.parametrize("mode", ["existing_project", "new_project"])
def test_mode_selection_preserves_draft_without_second_model_call(tmp_path, mode):
    service, root, executor, calls = setup_service(tmp_path, mode="unconfirmed")
    proposal = send(service, root, "Add search.")["proposed_task"]
    for _replay in range(2):
        result = action(service, root, proposal, action="confirm_project_mode", mode=mode)
        assert proposal == result["proposed_task"]
        assert result.get("attention_request") is None
    assert [] == executor.started
    assert 1 == len(calls)
    _session, _state = confirm_and_prepare(service, project_key="DEMO", root=root, request={
        "proposal_id": proposal["id"], "expected_revision": 1,
    })
    assert 1 == len(executor.started)
    if mode == "new_project":
        assert {"goal": "Add search.", "brief": "Add search."} == executor.started[0][2]


def test_pending_inspection_resumes_after_mode_without_model_replay(tmp_path):
    service, root, executor, calls = setup_service(tmp_path, mode="unconfirmed", turn={
        "intent": "project_understanding", "response": "Inspect the project.",
    })
    send(service, root, "Inspect this project.")
    assert [] == executor.started
    assert "m1" == service.session("DEMO", root)["pending_inspection"]["message_id"]
    for _replay in range(2):
        handle_draft_action(service, project_key="DEMO", root=root, request={
            "action": "confirm_project_mode", "mode": "existing_project",
        })
    assert 1 == len(calls)
    assert 1 == len(executor.started)
    assert service.session("DEMO", root)["pending_inspection"] is None


def test_mode_saved_before_inspection_failure_still_exposes_resume(tmp_path):
    service, root, executor, calls = setup_service(tmp_path, mode="unconfirmed", turn={
        "intent": "project_understanding", "response": "Inspect the project.",
    })
    send(service, root, "Inspect this project.")
    start = executor.start

    def fail(*_args, **_kwargs):
        raise ValueError("Temporary preparation failure")

    executor.start = fail
    request = {"action": "confirm_project_mode", "mode": "existing_project"}
    with pytest.raises(ValueError, match="Temporary"):
        handle_draft_action(service, project_key="DEMO", root=root, request=request)
    assert "existing_project" == executor.state["project_mode"]
    assert "project_mode" == service.session("DEMO", root)["attention_request"]["kind"]
    executor.start = start
    handle_draft_action(service, project_key="DEMO", root=root, request=request)
    assert 1 == len(calls)
    assert 1 == len(executor.started)


def test_edit_withdraw_reload_and_stale_buttons(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    proposal = send(service, root, "Add search.")["proposed_task"]
    edited = action(service, root, proposal, action="edit_task", text="Add search and tests.", message_id="edit1")["proposed_task"]
    assert 2 == edited["revision"]
    assert ["m1", "edit1"] == edited["source_message_ids"]
    assert edited == service.session("DEMO", root)["proposed_task"]
    with pytest.raises(ValueError, match="changed"):
        action(service, root, proposal, choice="withdraw")
    cancelled = action(service, root, edited, choice="withdraw")["proposed_task"]
    assert "cancelled" == cancelled["status"]
    with pytest.raises(ValueError):
        TaskAssistantSessionStore(tmp_path, "DEMO").confirm_task(cancelled["id"], expected_revision=cancelled["revision"])
    new = send(service, root, "Build settings.", "m2")["proposed_task"]
    assert new["id"] != cancelled["id"]


@pytest.mark.parametrize("choice", ["append", "replace", "discard"])
def test_unclear_change_requires_explicit_choice(tmp_path, choice):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    pending = send(service, root, "Build login instead.", "m2")["proposed_task"]
    result = action(service, root, pending, choice=choice)["proposed_task"]
    assert first["id"] == result["id"]
    assert 3 == result["revision"]
    assert result["pending_change"] is None
    expected = {"append": "Add search.\n\nBuild login instead.", "replace": "Build login instead.", "discard": "Add search."}
    assert expected[choice] == result["exact_request"]


def test_cancellation_message_requests_withdrawal_without_claiming_it_happened(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    send(service, root, "Add search.")
    result = send(service, root, "Cancel the task. Do not start any work.", "m2")
    assert "withdraw" == result["proposed_task"]["pending_change"]["kind"]
    assert "task_withdrawal_requested" == result["timeline"][-1]["event"]
    assert "cancelled" != result["proposed_task"].get("status")


def test_api_requires_revision_and_starts_exact_multi_turn_contract_once(tmp_path, monkeypatch):
    service, root, executor, _calls = setup_service(tmp_path)
    write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(web, "ProjectReadinessExecutor", lambda *_args: executor)
    monkeypatch.setattr(web, "build_capabilities", lambda _state: {"start_workflow": True})
    calls = []
    monkeypatch.setattr(web, "start_workflow", lambda **values: calls.append(values) or {"run_id": values["run_id"]})
    first = send(service, root, "Add task search by title.")["proposed_task"]
    proposal = send(service, root, "Also add regression tests, with no new dependencies.", "m2")["proposed_task"]
    executor.state["lifecycle"] = "ready"
    path = "/api/projects/DEMO/task-assistant/actions"
    for revision in (None, first["revision"], True):
        with pytest.raises(web.ProjectApiError) as exc:
            web.handle_project_api_post(tmp_path, path, {"action": "confirm_task", "proposal_id": proposal["id"], "expected_revision": revision})
        assert 409 == exc.value.status
    assert [] == calls
    for _replay in range(2):
        status, result = web.handle_project_api_post(tmp_path, path, {
            "action": "confirm_task", "proposal_id": proposal["id"], "expected_revision": proposal["revision"],
        })
        assert 200 == status
        assert result["workflow_started"]
    assert 1 == len(calls)
    assert proposal["exact_request"] == calls[0]["idea"]


def test_api_edit_conflict_returns_409_without_losing_latest_draft(tmp_path, monkeypatch):
    service, root, _executor, _calls = setup_service(tmp_path)
    write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    proposal = send(service, root, "Add search.")["proposed_task"]
    request = {"action": "edit_task", "proposal_id": proposal["id"], "expected_revision": 1,
               "text": "Add search and tests.", "message_id": "edit1"}
    path = "/api/projects/DEMO/task-assistant/actions"
    status, _payload = web.handle_project_api_post(tmp_path, path, request)
    assert 200 == status
    with pytest.raises(web.ProjectApiError) as exc:
        web.handle_project_api_post(tmp_path, path, {**request, "message_id": "edit2"})
    assert 409 == exc.value.status
    assert 2 == service.session("DEMO", root)["proposed_task"]["revision"]


def test_concurrent_edit_and_confirmation_cannot_approve_unseen_text(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from goodteam_os.assistant_drafts import edit_draft

    service, root, _executor, _calls = setup_service(tmp_path)
    proposal = send(service, root, "Add search.")["proposed_task"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    barrier = Barrier(2)

    def race(edit):
        barrier.wait(timeout=5)
        try:
            if edit:
                edit_draft(store, {"proposal_id": proposal["id"], "expected_revision": 1,
                                   "text": "Different task.", "message_id": "edit"})
            else:
                store.confirm_task(proposal["id"], expected_revision=1)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(race, [True, False]))
    assert 1 == sum(outcomes)
    final = store.read()["proposed_task"]
    assert not final["confirmed"] or final["exact_request"] == "Add search."
