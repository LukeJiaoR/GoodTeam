"""A single-draft Workflow must not silently replace a named Worker."""

from types import SimpleNamespace


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

    assert require_queued_project_task(root, "DEMO", proposal)["worker_id"] == "implementation-b"
    assert sessions.read() == before
    assert ledger.path.read_bytes() == ledger_before

    sessions._mutate(lambda session: _apply(session, {"action": "edit_queued_task",
        "proposal_id": proposal["id"], "expected_revision": 1, "text": "Clarified feature scope",
        "message_id": "clarify-scope"}))
    edited = sessions.read()["queued_task_proposals"][0]
    assert "project_task_id" not in edited
    assert require_queued_project_task(root, "DEMO", edited) is None
    handle_queue_action(SimpleNamespace(workspace=tmp_path, session=lambda *_: sessions.read()),
                        project_key="DEMO", root=root,
                        request={"action": "select_queued_task", "proposal_id": proposal["id"],
                                 "expected_revision": 2})
    assert sessions.read()["proposed_task"]["assignment"]["worker_id"] == "implementation-b"
    assert not sessions.read()["proposed_task"].get("run_id")
    ordinary = ledger.create_task(title="Ordinary work", description="Default role is intended.", role="implementation")
    assert require_queued_project_task(root, "DEMO", {"project_task_id": ordinary["id"],
        "project_task_digest": task_evidence_digest(ordinary)})["id"] == ordinary["id"]
