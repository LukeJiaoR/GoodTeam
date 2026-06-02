from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class GateTransition:
    gate: str
    on_approved_next: str
    on_changes_requested: str
    on_rejected: str


@dataclass(frozen=True)
class WorkflowNode:
    name: str
    run_guard_gate: str
    runner_fn: str


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
    ),
    "planner": WorkflowNode(
        name="planner",
        run_guard_gate="rfc",
        runner_fn="run_delivery_until_release_gate",
    ),
    "release": WorkflowNode(
        name="release",
        run_guard_gate="staging",
        runner_fn="run_release_until_gate",
    ),
    "retro_memory": WorkflowNode(
        name="retro_memory",
        run_guard_gate="release",
        runner_fn="run_retro_until_complete",
    ),
    "security": WorkflowNode(
        name="security",
        run_guard_gate="security",
        runner_fn="noop_security_gate",
    ),
}

WORKFLOW_NODES: Mapping[str, WorkflowNode] = MappingProxyType(_WORKFLOW_NODES)


def gate_transition(gate: str) -> GateTransition:
    try:
        return GATE_TRANSITIONS[gate]
    except KeyError as exc:
        raise ValueError(f"Unknown approval gate: {gate}") from exc


def approved_next_for_gate(gate: str) -> str:
    return gate_transition(gate).on_approved_next


def workflow_node(node: str) -> WorkflowNode | None:
    return WORKFLOW_NODES.get(node)
