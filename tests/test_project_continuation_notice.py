"""A durable handoff is a user message, never permission to resume execution."""
import pytest

from goodteam_os.project_attention import build_project_attention
from goodteam_os.project_continuation_notice import project_continuation_notice
from goodteam_os.project_iteration_continuation import _save, stop_project_continuation
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_terminal_handoff_is_atomic_bound_to_delivery_and_retains_stop(tmp_path, monkeypatch):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    active = {"id": "grant", "status": "active", "reason": "authorized", "task_ids": ["DEMO-1"],
              "batches_started": 1, "max_batches": 2, "manager_calls": 2, "max_manager_calls": 2}
    _save(store, active)
    attention = {"id": "other-approval", "kind": "approval"}
    store.set_attention(attention)
    store._mutate(lambda session: {**session, "project_task_batches": {
        "own": {"expected_continuation_id": "grant", "status": "delivered", "task_ids": ["DEMO-1"],
                "delivery_receipt": {"receipt_id": "sha256:own"}},
        "other": {"expected_continuation_id": "other", "status": "delivered", "task_ids": ["DEMO-99"],
                  "delivery_receipt": {"receipt_id": "sha256:other"}},
    }})
    terminal = {**active, "status": "completed", "reason": "discovery_no_followup",
                "discovery_last": {"rationale": "The delivered work supplied no justified next task in this scope."}}
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:
        def failed_write(_payload):
            raise OSError("injected write failure before atomic replacement")
        patch.setattr(store, "_write", failed_write)
        with pytest.raises(OSError, match="injected write failure"):
            _save(store, terminal)
    assert store.path.read_bytes() == before
    _save(store, terminal)
    session = store.read()
    handoff = session["timeline"][-1]
    assert handoff["event"] == "project_continuation_handoff" and handoff["actor"] == "task_assistant"
    assert handoff["details"]["phase"] == "no_followup"
    assert "does not mean the project is complete" in handoff["content"]
    assert terminal["discovery_last"]["rationale"] in handoff["content"]
    assert handoff["details"]["delivered_task_ids"] == ["DEMO-1"]
    assert handoff["details"]["delivery_receipt_ids"] == ["sha256:own"]
    assert session["attention_request"] == attention  # Do not overwrite another gate.
    _save(store, terminal)
    assert store.read()["timeline"] == session["timeline"]
    snapshot = build_project_attention(project_key="DEMO", readiness={}, session=store.read())
    assert snapshot["items"] == [{"id": handoff["message_id"], "kind": "needs_input", "source": "continuation",
                                  "run_id": "", "target_id": "grant", "title": "Project iteration needs attention",
                                  "reason": "discovery_no_followup"}]
    # The existing owner Stop can close this handoff without new task/model authority.
    stopped = stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id="grant")
    assert stopped["status"] == "stopped"
    stopped_notice = project_continuation_notice(store.read())
    assert stopped_notice["phase"] == "stopped" and stopped_notice["kind"] == ""
    assert any(item["event"] == "project_continuation_handoff" and item["details"] == stopped_notice
               for item in store.read()["timeline"])
    assert _save(store, active)["status"] == "stopped"  # A late result cannot revive it.
    assert store.read()["project_continuation"]["manager_calls"] == 2
    assert build_project_attention(project_key="DEMO", readiness={}, session=store.read())["counts"]["total"] == 0
