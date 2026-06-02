from pathlib import Path

from goodteam_os import graph
from goodteam_os import web
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_table import GATE_TRANSITIONS, approved_next_for_gate, gate_transition, workflow_node


GROUND_TRUTH_APPROVED_NEXT = {
    "takeover": "product_requirement",
    "prd": "architect",
    "rfc": "planner",
    "staging": "release",
    "security": "planner",
    "release": "retro_memory",
}


def test_gate_transition_table_matches_ground_truth():
    assert {
        gate: transition.on_approved_next
        for gate, transition in GATE_TRANSITIONS.items()
    } == GROUND_TRUTH_APPROVED_NEXT
    for gate in GROUND_TRUTH_APPROVED_NEXT:
        transition = gate_transition(gate)
        assert transition.on_changes_requested == f"revise_{gate}"
        assert transition.on_rejected == "rejected"


def test_security_gate_is_declared_as_human_only_transition():
    transition = gate_transition("security")

    assert transition.on_approved_next == "planner"
    assert transition.on_changes_requested == "revise_security"
    assert transition.on_rejected == "rejected"
    assert workflow_node("security").runner_fn == "noop_security_gate"


def test_no_gate_transition_is_self_referential():
    for gate, transition in GATE_TRANSITIONS.items():
        assert transition.on_approved_next != gate


def test_decide_approval_routes_approved_gates_from_transition_table(tmp_path, monkeypatch):
    calls = []

    def spy_approved_next_for_gate(gate):
        calls.append(gate)
        return approved_next_for_gate(gate)

    def fake_advance_approved_workflow(*, state, workspace, project_config):
        return state

    def fake_run_until_next_gate(state, workspace, project_config=None):
        return state

    monkeypatch.setattr(web, "approved_next_for_gate", spy_approved_next_for_gate)
    monkeypatch.setattr(web, "advance_approved_workflow", fake_advance_approved_workflow)
    monkeypatch.setattr(web, "run_until_next_gate", fake_run_until_next_gate)
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda state, workspace: None)

    for gate, approved_next in GROUND_TRUTH_APPROVED_NEXT.items():
        workspace = tmp_path / gate
        state = create_initial_state(f"run-{gate}", "CONTENTOPS", "Existing AI content studio run")
        state.current_node = f"{gate}_review_gate"
        state.next_node = f"await_{gate}_approval"
        state.approvals[gate] = "pending"
        StateStore(workspace).write_project_current(state)

        payload = web.decide_approval(
            workspace=workspace,
            project_key="CONTENTOPS",
            approval=gate,
            decision="approved",
        )

        assert payload["next_node"] == approved_next

    assert calls == list(GROUND_TRUTH_APPROVED_NEXT)


def test_advance_approved_workflow_uses_node_table(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: Test\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"
    calls = []
    original_workflow_node = graph.workflow_node

    def spy_workflow_node(node):
        calls.append(node)
        return original_workflow_node(node)

    monkeypatch.setattr(graph, "workflow_node", spy_workflow_node)

    result = graph.advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert calls[0] == "architect"
    assert result.next_node == "await_rfc_approval"
    assert Path(result.artifacts["rfc"]).is_file()
