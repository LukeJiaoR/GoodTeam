from pathlib import Path

from goodteam_os import graph
from goodteam_os import web
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_table import (
    GATE_TRANSITIONS,
    GateTransition,
    WorkflowNode,
    advisors_for_gate,
    approved_next_for_gate,
    disposition_for_failure_class,
    gate_transition,
    topological_workflow_nodes,
    validate_workflow_dag,
    workflow_node,
)


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


def test_advisors_for_gate_declares_rfc_committee_only():
    assert advisors_for_gate("rfc") == ("qa",)

    for gate in GROUND_TRUTH_APPROVED_NEXT:
        if gate != "rfc":
            assert advisors_for_gate(gate) == ()


def test_workflow_node_failure_policy_defaults_preserve_blocked_routing():
    node = WorkflowNode(name="example", run_guard_gate="rfc", runner_fn="run_example")

    assert node.on_error == "blocked"
    assert node.on_blocked == "blocked"
    assert node.retry_max == 0
    assert node.retry_on_classes == ()
    assert node.depends_on == ()


def test_workflow_node_dependencies_describe_current_linear_dag():
    assert workflow_node("architect").depends_on == ("product_requirement", "prd")
    assert workflow_node("planner").depends_on == ("architect", "rfc", "security")
    assert workflow_node("release").depends_on == ("planner", "staging")
    assert workflow_node("retro_memory").depends_on == ("release",)
    assert workflow_node("security").depends_on == ("rfc",)

    assert topological_workflow_nodes() == [
        "product_requirement",
        "prd",
        "architect",
        "rfc",
        "security",
        "planner",
        "staging",
        "release",
        "retro_memory",
    ]


def test_workflow_dag_validation_rejects_unknown_dependency():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
            depends_on=("missing_node",),
        )
    }

    try:
        validate_workflow_dag(nodes)
    except ValueError as exc:
        assert "unknown dependency" in str(exc)
        assert "missing_node" in str(exc)
    else:
        raise AssertionError("expected unknown dependency validation failure")


def test_workflow_dag_validation_rejects_unknown_run_guard_gate():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="missing_gate",
            runner_fn="run_delivery_until_release_gate",
        )
    }

    try:
        validate_workflow_dag(nodes)
    except ValueError as exc:
        assert "unknown run guard gate" in str(exc)
        assert "missing_gate" in str(exc)
    else:
        raise AssertionError("expected unknown run guard gate validation failure")


def test_workflow_dag_validation_rejects_unknown_failure_targets():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
            on_error="missing_error_target",
            on_blocked="missing_blocked_target",
        )
    }

    try:
        validate_workflow_dag(nodes)
    except ValueError as exc:
        assert "unknown error target" in str(exc)
        assert "missing_error_target" in str(exc)
    else:
        raise AssertionError("expected unknown error target validation failure")

    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
            on_blocked="missing_blocked_target",
        )
    }

    try:
        validate_workflow_dag(nodes)
    except ValueError as exc:
        assert "unknown blocked target" in str(exc)
        assert "missing_blocked_target" in str(exc)
    else:
        raise AssertionError("expected unknown blocked target validation failure")


def test_workflow_dag_validation_rejects_node_key_name_mismatch():
    nodes = {
        "planner": WorkflowNode(
            name="delivery_planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
        )
    }
    gate_transitions = {
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="planner",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "mapping key" in str(exc)
        assert "planner" in str(exc)
        assert "delivery_planner" in str(exc)
    else:
        raise AssertionError("expected node mapping key validation failure")


def test_workflow_dag_validation_rejects_gate_key_name_mismatch():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
        )
    }
    gate_transitions = {
        "rfc": GateTransition(
            gate="architecture",
            on_approved_next="planner",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "mapping key" in str(exc)
        assert "rfc" in str(exc)
        assert "architecture" in str(exc)
    else:
        raise AssertionError("expected gate mapping key validation failure")


def test_workflow_dag_validation_rejects_unknown_gate_transition_targets():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
        )
    }

    gate_transitions = {
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="missing_approved_target",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "unknown approved target" in str(exc)
        assert "missing_approved_target" in str(exc)
    else:
        raise AssertionError("expected unknown approved target validation failure")

    gate_transitions = {
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="planner",
            on_changes_requested="missing_changes_target",
            on_rejected="missing_rejected_target",
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "unknown changes_requested target" in str(exc)
        assert "missing_changes_target" in str(exc)
    else:
        raise AssertionError("expected unknown changes requested target validation failure")

    gate_transitions = {
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="planner",
            on_changes_requested="revise_rfc",
            on_rejected="missing_rejected_target",
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "unknown rejected target" in str(exc)
        assert "missing_rejected_target" in str(exc)
    else:
        raise AssertionError("expected unknown rejected target validation failure")


def test_workflow_dag_validation_rejects_cycles():
    nodes = {
        "a": WorkflowNode(name="a", run_guard_gate="prd", runner_fn="run_a", depends_on=("b",)),
        "b": WorkflowNode(name="b", run_guard_gate="rfc", runner_fn="run_b", depends_on=("a",)),
    }
    gate_transitions = {
        "prd": GateTransition(
            gate="prd",
            on_approved_next="a",
            on_changes_requested="revise_prd",
            on_rejected="rejected",
        ),
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="b",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        ),
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions)
    except ValueError as exc:
        assert "cycle" in str(exc).lower()
        assert "a" in str(exc)
        assert "b" in str(exc)
    else:
        raise AssertionError("expected cycle validation failure")


def test_disposition_for_failure_class_maps_declared_classes():
    for failure_class in ["auth", "rate_limit", "network", "timeout", "stream_disconnect", "infra"]:
        assert disposition_for_failure_class(failure_class) == "escalate_human"

    for failure_class in ["scaffold", "code_bug", "unknown", "", "unlisted"]:
        assert disposition_for_failure_class(failure_class) == "block"


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
        f"name: Test\nkey: CONTENTOPS\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
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
