"""A single-draft Workflow must not silently replace a named Worker."""

from types import SimpleNamespace

import pytest

from goodteam_os.assistant_queue import _apply, handle_queue_action, require_queued_project_task
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, task_evidence_digest


def test_named_worker_stays_queued_for_identity_preserving_batch(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    ledger = LocalTaskStore(root, "DEMO")
    task = ledger.create_task(title="Fix the selected feature", description="Keep the assigned Worker.",
                             role="implementation", worker_id="implementation-b",
                             acceptance_criteria=["The assigned Worker executes."], write_domains=["src/"])
    proposal = {"id": "named-worker", "revision": 1, "status": "queued", "confirmed": False,
                "run_id": "", "exact_request": task["description"], "source_message_ids": [],
                "project_task_id": task["id"], "project_task_digest": task_evidence_digest(task),
                "assignment": {"role": "implementation", "worker_id": "implementation-b"}}
    sessions = TaskAssistantSessionStore(tmp_path, "DEMO")
    sessions._mutate(lambda session: {**session, "queued_task_proposals": [proposal]})
    before = sessions.read()
    ledger_before = ledger.path.read_bytes()

    # The real selection entry rejects before changing the queue or claiming
    # the task. Existing batch execution can still consume the same proposal.
    with pytest.raises(ValueError, match="named Worker.*batch"):
        handle_queue_action(SimpleNamespace(workspace=tmp_path, session=lambda *_: sessions.read()),
                            project_key="DEMO", root=root,
                            request={"action": "select_queued_task", "proposal_id": proposal["id"],
                                     "expected_revision": 1})
    assert sessions.read() == before
    assert ledger.path.read_bytes() == ledger_before
    assert sessions.read()["queued_task_proposals"][0]["assignment"]["worker_id"] == "implementation-b"

    # Previously selected drafts pass the same boundary again at confirmation
    # and start. The durable ledger is authoritative, not editable UI metadata.
    selected = {**proposal, "status": "draft", "assignment": {}}
    with pytest.raises(ValueError, match="named Worker.*batch"):
        require_queued_project_task(root, "DEMO", selected)
    sessions._mutate(lambda session: _apply(session, {"action": "edit_queued_task",
        "proposal_id": proposal["id"], "expected_revision": 1, "text": "Clarified feature scope",
        "message_id": "clarify-scope"}))
    edited = sessions.read()["queued_task_proposals"][0]
    assert "project_task_id" not in edited
    with pytest.raises(ValueError, match="named Worker.*batch"):
        handle_queue_action(SimpleNamespace(workspace=tmp_path, session=lambda *_: sessions.read()),
                            project_key="DEMO", root=root,
                            request={"action": "select_queued_task", "proposal_id": proposal["id"],
                                     "expected_revision": 2})
    assert sessions.read()["queued_task_proposals"][0]["status"] == "queued"
    ordinary = ledger.create_task(title="Ordinary work", description="Default role is intended.", role="implementation")
    assert require_queued_project_task(root, "DEMO", {"project_task_id": ordinary["id"],
        "project_task_digest": task_evidence_digest(ordinary)})["id"] == ordinary["id"]


def test_previously_confirmed_edited_named_draft_cannot_start_workflow(tmp_path, monkeypatch):
    from goodteam_os import web

    root = tmp_path / "project"
    root.mkdir()
    sessions = TaskAssistantSessionStore(tmp_path, "DEMO")
    sessions.append_message("request", "user", "Implement the feature")
    proposal = sessions.propose_task(["request"], "Implement the feature")["proposed_task"]
    sessions.confirm_task(proposal["id"])
    sessions._mutate(lambda session: {**session, "proposed_task": {
        **session["proposed_task"], "assignment": {"worker_id": "implementation-b"}}})
    # Readiness is a test fixture, not runtime/Git acceptance. The actual API
    # start boundary must reject before invoking any Workflow or Worker.
    monkeypatch.setattr(web, "_project_target_and_root", lambda *_: ("DEMO", {}, root))
    monkeypatch.setattr(web, "_project_config_for_key", lambda *_: tmp_path / "project.yaml")
    monkeypatch.setattr(web, "confirm_and_prepare", lambda *_args, **_kwargs: (sessions.read(), {}))
    monkeypatch.setattr(web, "build_capabilities", lambda *_: {"start_workflow": True})
    monkeypatch.setattr(web, "_workflow_requires_repository", lambda *_args, **_kwargs: False)
    calls = []
    monkeypatch.setattr(web, "start_workflow", lambda **kwargs: calls.append(kwargs) or {"run_id": kwargs["run_id"]})
    with pytest.raises(web.ProjectApiError, match="named Worker") as error:
        web.handle_project_api_post(tmp_path, "/api/projects/DEMO/task-assistant/actions", {
            "action": "confirm_task", "proposal_id": proposal["id"], "expected_revision": proposal["revision"]})
    assert error.value.code == "iteration_task_changed"
    assert not calls
    after = sessions.read()["proposed_task"]
    assert not after.get("start_claim_id") and not after.get("run_id")
    assert after["assignment"]["worker_id"] == "implementation-b"
