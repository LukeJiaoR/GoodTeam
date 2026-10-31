
from goodteam_os.workflow_table import (
    GateTransition,
    WorkflowNode,
    gate_transition,
    workflow_execution_plan_v2,
    workflow_node,
)


GROUND_TRUTH_APPROVED_NEXT = {
    "takeover": "product_requirement",
    "prd": "architect",
    "rfc": "security",
    "staging": "release",
    "security": "planner",
    "release": "retro_memory",
}


def test_security_gate_is_declared_as_human_only_transition():
    assert gate_transition("rfc").on_approved_next == "security"

    transition = gate_transition("security")

    assert transition.on_approved_next == "planner"
    assert transition.on_changes_requested == "revise_security"
    assert transition.on_rejected == "rejected"
    assert workflow_node("security").runner_fn == "noop_security_gate"



def test_workflow_dag_rejects_shared_write_domains_without_a_fake_parallel_runner():
    nodes = {
        "implementation": WorkflowNode(
            name="implementation", run_guard_gate="rfc",
            runner_fn="run_real_worker", depends_on=("rfc",),
        ),
        "qa": WorkflowNode(
            name="qa", run_guard_gate="rfc",
            runner_fn="run_real_qa", depends_on=("rfc",),
        ),
    }
    transitions = {
        "rfc": GateTransition(
            gate="rfc", on_approved_next="implementation",
            on_changes_requested="revise_rfc", on_rejected="rejected",
        )
    }
    plan = workflow_execution_plan_v2(
        nodes, gate_transitions=transitions,
        write_domains={"implementation": ("repo:shared",), "qa": ("repo:shared",)},
    )
    assert plan["valid"] is False
    assert plan["validation_errors"] == [
        "Workflow execution plan v2 write-domain collision repo:shared: implementation, qa"
    ]
    assert "parallel_execution_plan" not in plan
