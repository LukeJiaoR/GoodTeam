from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class GateTransition:
    gate: str
    on_approved_next: str
    on_changes_requested: str
    on_rejected: str
    advisors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowNode:
    name: str
    run_guard_gate: str
    runner_fn: str
    on_error: str = "blocked"
    on_blocked: str = "blocked"
    retry_max: int = 0
    retry_on_classes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


_GATE_TRANSITIONS = {
    "takeover": GateTransition(
        gate="takeover",
        on_approved_next="product_requirement",
        on_changes_requested="revise_takeover",
        on_rejected="rejected",
    ),
    "prd": GateTransition(
        gate="prd",
        on_approved_next="architect",
        on_changes_requested="revise_prd",
        on_rejected="rejected",
    ),
    "rfc": GateTransition(
        gate="rfc",
        on_approved_next="planner",
        on_changes_requested="revise_rfc",
        on_rejected="rejected",
        advisors=("qa",),
    ),
    "staging": GateTransition(
        gate="staging",
        on_approved_next="release",
        on_changes_requested="revise_staging",
        on_rejected="rejected",
    ),
    "security": GateTransition(
        gate="security",
        on_approved_next="planner",
        on_changes_requested="revise_security",
        on_rejected="rejected",
    ),
    "release": GateTransition(
        gate="release",
        on_approved_next="retro_memory",
        on_changes_requested="revise_release",
        on_rejected="rejected",
    ),
}

GATE_TRANSITIONS: Mapping[str, GateTransition] = MappingProxyType(_GATE_TRANSITIONS)

_WORKFLOW_NODES = {
    "architect": WorkflowNode(
        name="architect",
        run_guard_gate="prd",
        runner_fn="run_architecture_until_gate",
        depends_on=("product_requirement", "prd"),
    ),
    "planner": WorkflowNode(
        name="planner",
        run_guard_gate="rfc",
        runner_fn="run_delivery_until_release_gate",
        depends_on=("architect", "rfc", "security"),
    ),
    "release": WorkflowNode(
        name="release",
        run_guard_gate="staging",
        runner_fn="run_release_until_gate",
        depends_on=("planner", "staging"),
    ),
    "retro_memory": WorkflowNode(
        name="retro_memory",
        run_guard_gate="release",
        runner_fn="run_retro_until_complete",
        depends_on=("release",),
    ),
    "security": WorkflowNode(
        name="security",
        run_guard_gate="security",
        runner_fn="noop_security_gate",
        depends_on=("rfc",),
    ),
}

WORKFLOW_NODES: Mapping[str, WorkflowNode] = MappingProxyType(_WORKFLOW_NODES)

_FAILURE_DISPOSITIONS = {
    "auth": "escalate_human",
    "rate_limit": "escalate_human",
    "network": "escalate_human",
    "timeout": "escalate_human",
    "stream_disconnect": "escalate_human",
    "infra": "escalate_human",
    "scaffold": "block",
    "code_bug": "block",
    "unknown": "block",
}

FAILURE_DISPOSITIONS: Mapping[str, str] = MappingProxyType(_FAILURE_DISPOSITIONS)


def gate_transition(gate: str) -> GateTransition:
    try:
        return GATE_TRANSITIONS[gate]
    except KeyError as exc:
        raise ValueError(f"Unknown approval gate: {gate}") from exc


def approved_next_for_gate(gate: str) -> str:
    return gate_transition(gate).on_approved_next


def advisors_for_gate(gate: str) -> tuple[str, ...]:
    return gate_transition(gate).advisors


def workflow_node(node: str) -> WorkflowNode | None:
    return WORKFLOW_NODES.get(node)


def validate_workflow_dag(nodes: Mapping[str, WorkflowNode] | None = None) -> None:
    node_table = nodes or WORKFLOW_NODES
    identifiers = set(node_table) | set(GATE_TRANSITIONS) | {"product_requirement", "staging"}
    for node in node_table.values():
        if node.run_guard_gate not in GATE_TRANSITIONS:
            raise ValueError(f"Workflow node {node.name!r} has unknown run guard gate {node.run_guard_gate!r}")
        for dependency in node.depends_on:
            if dependency not in identifiers:
                raise ValueError(f"Workflow node {node.name!r} has unknown dependency {dependency!r}")
        for label, target in [("error", node.on_error), ("blocked", node.on_blocked)]:
            if target not in identifiers and target not in {"blocked", "rejected"}:
                raise ValueError(f"Workflow node {node.name!r} has unknown {label} target {target!r}")

    visited: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise ValueError(f"Workflow DAG cycle detected: {cycle}")
        if name in visited or name not in node_table:
            return
        visiting.append(name)
        for dependency in node_table[name].depends_on:
            visit(dependency)
        visiting.pop()
        visited.add(name)

    for name in node_table:
        visit(name)


def topological_workflow_nodes(nodes: Mapping[str, WorkflowNode] | None = None) -> list[str]:
    node_table = nodes or WORKFLOW_NODES
    validate_workflow_dag(node_table)
    ordered: list[str] = []
    seen: set[str] = set()

    def append_dependency(name: str) -> None:
        if name in seen:
            return
        if name in node_table:
            for dependency in node_table[name].depends_on:
                append_dependency(dependency)
        seen.add(name)
        ordered.append(name)

    for name in node_table:
        append_dependency(name)
    return ordered


def error_next_for_node(node: str) -> str:
    workflow = workflow_node(node)
    if workflow is None:
        return "blocked"
    return workflow.on_error


def blocked_next_for_node(node: str) -> str:
    workflow = workflow_node(node)
    if workflow is None:
        return "blocked"
    return workflow.on_blocked


def retry_policy_for_node(node: str) -> tuple[int, tuple[str, ...]]:
    workflow = workflow_node(node)
    if workflow is None:
        return 0, ()
    return workflow.retry_max, workflow.retry_on_classes


def disposition_for_failure_class(failure_class: str) -> str:
    return FAILURE_DISPOSITIONS.get((failure_class or "").strip(), "block")


validate_workflow_dag(WORKFLOW_NODES)
