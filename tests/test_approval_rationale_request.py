"""Real HTTP type validation against a seeded, model-free approval gate.

These tests isolate the request-to-ledger boundary; they do not claim project
preparation, a real reviewer, or the full product loop has passed.
"""

from types import SimpleNamespace

import pytest

from goodteam_os import web
from goodteam_os.state import StateStore
from test_approval_rationale_boundary import durable_files
from test_skill_review_http import post, server_url
from test_workflow_recovery import binding_for, gate_state


@pytest.mark.parametrize("note", [None, 123, True, [], {}, ["I accept the gap"]])
def test_http_approval_rejects_nontext_reasons_before_any_decision(tmp_path, monkeypatch, note):
    state = gate_state(tmp_path, "release")
    before = durable_files(tmp_path)
    executed = []
    original = web.advance_approved_workflow

    def record_dispatch(**kwargs):
        executed.append(kwargs["state"].run_id)
        return original(**kwargs)

    monkeypatch.setattr(web, "advance_approved_workflow", record_dispatch)
    ctx = SimpleNamespace(root=tmp_path, workspace=tmp_path)
    with server_url(ctx, monkeypatch, "product") as base:
        status, payload = post(base, "/api/approve", {
            "project_key": "RECOVERY", "approval": "release", "decision": "approved",
            "note": note, **binding_for(state),
        })
    assert status == 400, payload
    assert payload["code"] == "invalid_approval_note"
    assert durable_files(tmp_path) == before
    assert executed == []


@pytest.mark.parametrize("note", [None, 123, True, [], {}, ["I accept the gap"]])
def test_shared_decision_boundary_rejects_nontext_notes(tmp_path, note):
    state = gate_state(tmp_path, "release")
    before = durable_files(tmp_path)
    with pytest.raises(web.ProjectApiError) as error:
        web.decide_approval(workspace=tmp_path, project_key="RECOVERY", approval="release",
                            decision="approved", note=note, **binding_for(state))
    assert error.value.code == "invalid_approval_note"
    assert error.value.status == 400
    assert durable_files(tmp_path) == before


def test_http_text_reason_is_retained_and_completed_replay_does_not_redecide(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "release")
    note = "I accept this model-free fixture solely to test the request-to-ledger boundary."
    ctx = SimpleNamespace(root=tmp_path, workspace=tmp_path)
    request = {"project_key": "RECOVERY", "approval": "release", "decision": "approved", **binding_for(state)}
    with server_url(ctx, monkeypatch, "product") as base:
        status, payload = post(base, "/api/approve", {**request, "note": f"  {note}\n"})
        assert status == 200 and payload["current_node"] == "complete"
        first = StateStore(tmp_path).read_project_current("RECOVERY").pending_transition
        status, payload = post(base, "/api/approve", {**request, "note": ""})
        assert status == 200 and payload["replayed"] is True
    saved = StateStore(tmp_path).read_project_current("RECOVERY")
    assert saved.pending_transition == first
    decisions = [row for row in web.build_action_items(tmp_path, "RECOVERY")
                 if row["action"] == "approval_decided"]
    assert len(decisions) == 1 and decisions[0]["note"] == note
