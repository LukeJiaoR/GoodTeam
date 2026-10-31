from pathlib import Path

from goodteam_os import graph
from goodteam_os import web
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_table import (
    GateTransition,
    WorkflowNode,
    approved_next_for_gate,
    gate_transition,
    validate_workflow_dag,
    validate_workflow_execution_plan_v2,
    workflow_parallel_execution_plan,
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


def test_workflow_parallel_execution_plan_fails_closed_for_real_workers_and_write_collisions():
    nodes = {
        "implementation": WorkflowNode(
            name="implementation",
            run_guard_gate="rfc",
            runner_fn="run_real_worker",
            depends_on=("rfc",),
        ),
        "qa": WorkflowNode(
            name="qa",
            run_guard_gate="rfc",
            runner_fn="fake_qa_worker",
            depends_on=("rfc",),
        ),
    }
    gate_transitions = {
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="implementation",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        )
    }

    plan = workflow_parallel_execution_plan(
        nodes,
        gate_transitions=gate_transitions,
        runner_kinds={
            "implementation": "sequential_worker",
            "qa": "fake_worker",
        },
        write_domains={
            "implementation": ("repo:shared",),
            "qa": ("repo:shared",),
        },
    )

    assert plan["valid"] is False
    assert plan["batches"] == []
    assert plan["parallel_level_execution"] == "blocked"
    assert plan["parallel_readiness"] == {
        "status": "blocked_for_real_workers",
        "safe_runner_kinds": ["fake_worker", "read_only_advisor"],
        "blocked_runner_kinds": ["sequential_worker"],
        "requires_worker_isolation": True,
        "requires_live_smoke": True,
        "worker_isolation": {
            "status": "required_before_real_parallel_workers",
            "required_strategy": "worktree_per_worker",
            "allowed_strategies": ["worktree_per_worker"],
            "rejected_strategies": ["shared_worktree", "single_runtime_root"],
            "evidence_required": [
                "per-worker worktree path outside repository root",
                "declared write domains mapped to one worker each",
                "fan-in merge or review step after all workers finish",
                "live smoke run using fake/read-only workers before real workers",
            ],
        },
        "blockers": [
            "Workflow execution plan v2 write-domain collision repo:shared: implementation, qa",
            "Parallel execution refuses runner 'implementation' with kind 'sequential_worker'",
        ],
    }
    assert plan["validation_errors"] == [
        "Workflow execution plan v2 write-domain collision repo:shared: implementation, qa",
        "Parallel execution refuses runner 'implementation' with kind 'sequential_worker'",
    ]

