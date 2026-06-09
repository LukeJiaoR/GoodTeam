from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from goodteam_os.roles import list_role_definitions


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


@dataclass(frozen=True)
class WorkflowExecutionLevel:
    index: int
    identifiers: tuple[str, ...]
    runnable: tuple[str, ...]
    passive: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "identifiers": list(self.identifiers),
            "runnable": list(self.runnable),
            "passive": list(self.passive),
        }


@dataclass(frozen=True)
class WorkflowExecutionPlan:
    levels: tuple[WorkflowExecutionLevel, ...]
    scheduler: str = "sequential"
    parallel_level_execution: str = "deferred"

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(identifier for level in self.levels for identifier in level.identifiers)

    def level_for(self, identifier: str) -> int:
        for level in self.levels:
            if identifier in level.identifiers:
                return level.index
        raise ValueError(f"Unknown workflow execution identifier: {identifier}")

    def to_dict(self) -> dict[str, object]:
        return {
            "scheduler": self.scheduler,
            "parallel_level_execution": self.parallel_level_execution,
            "levels": [level.to_dict() for level in self.levels],
        }


def _workflow_node_write_domains(identifier: str) -> tuple[str, ...]:
    return (f"workflow:{identifier}",)


def _gate_write_domains(identifier: str) -> tuple[str, ...]:
    return (f"approval:{identifier}",)


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
        on_approved_next="security",
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


def validate_workflow_dag(
    nodes: Mapping[str, WorkflowNode] | None = None,
    *,
    gate_transitions: Mapping[str, GateTransition] | None = None,
    valid_advisors: Iterable[str] | None = None,
) -> None:
    node_table = nodes or WORKFLOW_NODES
    transition_table = gate_transitions or GATE_TRANSITIONS
    advisor_keys = (
        set(valid_advisors)
        if valid_advisors is not None
        else {str(role.get("key", "")) for role in list_role_definitions()}
    )
    identifiers = set(node_table) | set(transition_table) | {"product_requirement", "staging"}
    terminal_identifiers = {"blocked", "rejected"}
    revision_identifiers = {f"revise_{gate}" for gate in transition_table}
    for key, node in node_table.items():
        if node.name != key:
            raise ValueError(f"Workflow node mapping key {key!r} does not match node name {node.name!r}")
        if node.run_guard_gate not in transition_table:
            raise ValueError(f"Workflow node {node.name!r} has unknown run guard gate {node.run_guard_gate!r}")
        for dependency in getattr(node, "depends_on", ()):
            if dependency not in identifiers:
                raise ValueError(f"Workflow node {node.name!r} has unknown dependency {dependency!r}")
        for label, target in [("error", node.on_error), ("blocked", node.on_blocked)]:
            if target not in identifiers and target not in terminal_identifiers:
                raise ValueError(f"Workflow node {node.name!r} has unknown {label} target {target!r}")

    for gate, transition in transition_table.items():
        if transition.gate != gate:
            raise ValueError(f"Gate transition mapping key {gate!r} does not match gate name {transition.gate!r}")
        for advisor in transition.advisors:
            if advisor not in advisor_keys:
                raise ValueError(f"Gate transition {gate!r} has unknown advisor {advisor!r}")
        for label, target in [
            ("approved", transition.on_approved_next),
            ("changes_requested", transition.on_changes_requested),
            ("rejected", transition.on_rejected),
        ]:
            valid_targets = identifiers | terminal_identifiers
            if label == "changes_requested":
                valid_targets = valid_targets | revision_identifiers
            if target not in valid_targets:
                raise ValueError(f"Gate transition {gate!r} has unknown {label} target {target!r}")

    visited: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise ValueError(f"Workflow DAG cycle detected: {cycle}")
        if name in visited or name not in node_table:
            return
        visiting.append(name)
        for dependency in getattr(node_table[name], "depends_on", ()):
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


def workflow_execution_plan(
    nodes: Mapping[str, WorkflowNode] | None = None,
    *,
    gate_transitions: Mapping[str, GateTransition] | None = None,
) -> WorkflowExecutionPlan:
    node_table = nodes or WORKFLOW_NODES
    transition_table = gate_transitions or GATE_TRANSITIONS
    validate_workflow_dag(node_table, gate_transitions=transition_table)
    ordered = _topological_dependency_order(node_table, gate_transitions=transition_table)
    level_by_identifier: dict[str, int] = {}

    for identifier in ordered:
        if identifier not in node_table:
            level_by_identifier[identifier] = max(level_by_identifier.values(), default=0)
            continue

        dependencies = tuple(getattr(node_table[identifier], "depends_on", ()))
        if not dependencies:
            level = 0
        else:
            level = max(level_by_identifier[dependency] for dependency in dependencies) + 1
        level_by_identifier[identifier] = level

    levels: list[WorkflowExecutionLevel] = []
    for index in sorted(set(level_by_identifier.values())):
        identifiers = tuple(identifier for identifier in ordered if level_by_identifier[identifier] == index)
        runnable = tuple(identifier for identifier in identifiers if identifier in node_table)
        passive = tuple(identifier for identifier in identifiers if identifier not in node_table)
        levels.append(
            WorkflowExecutionLevel(
                index=index,
                identifiers=identifiers,
                runnable=runnable,
                passive=passive,
            )
        )
    return WorkflowExecutionPlan(levels=tuple(levels))


def validate_workflow_execution_plan_v2(
    nodes: Mapping[str, WorkflowNode] | None = None,
    *,
    gate_transitions: Mapping[str, GateTransition] | None = None,
    write_domains: Mapping[str, Iterable[str]] | None = None,
) -> list[str]:
    node_table = nodes or WORKFLOW_NODES
    transition_table = gate_transitions or GATE_TRANSITIONS
    errors: list[str] = []

    try:
        validate_workflow_dag(node_table, gate_transitions=transition_table)
    except ValueError as exc:
        errors.append(str(exc))
    errors.extend(_workflow_execution_plan_v2_cycle_errors(node_table, existing_errors=errors))

    passive_identifiers = set(transition_table) | {"product_requirement", "staging"}
    known_identifiers = set(node_table) | passive_identifiers
    for node in node_table.values():
        for dependency in getattr(node, "depends_on", ()):
            if dependency not in known_identifiers:
                message = f"Workflow execution plan v2 has missing fan-in node {dependency!r} for {node.name!r}"
                if message not in errors:
                    errors.append(message)

    domain_owners: dict[str, list[str]] = {}
    for identifier in node_table:
        domains = (
            tuple(write_domains.get(identifier, _workflow_node_write_domains(identifier)))
            if write_domains
            else _workflow_node_write_domains(identifier)
        )
        for domain in domains:
            domain_owners.setdefault(str(domain), []).append(identifier)
    for domain, owners in sorted(domain_owners.items()):
        if len(owners) > 1:
            errors.append(f"Workflow execution plan v2 write-domain collision {domain}: {', '.join(sorted(owners))}")

    return errors


def _workflow_execution_plan_v2_cycle_errors(
    nodes: Mapping[str, WorkflowNode],
    *,
    existing_errors: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    existing = set(existing_errors)
    visited: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            message = f"Workflow DAG cycle detected: {cycle}"
            if message not in existing:
                errors.append(message)
                existing.add(message)
            return
        if name in visited or name not in nodes:
            return
        visiting.append(name)
        for dependency in getattr(nodes[name], "depends_on", ()):
            if dependency in nodes:
                visit(dependency)
        visiting.pop()
        visited.add(name)

    for name in nodes:
        visit(name)
    return errors


def workflow_execution_plan_v2(
    nodes: Mapping[str, WorkflowNode] | None = None,
    *,
    gate_transitions: Mapping[str, GateTransition] | None = None,
    write_domains: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, object]:
    node_table = nodes or WORKFLOW_NODES
    transition_table = gate_transitions or GATE_TRANSITIONS
    inferred_runner_kinds = {
        identifier: ("human_gate" if workflow.runner_fn == "noop_security_gate" else "sequential_worker")
        for identifier, workflow in node_table.items()
    }
    validation_errors = validate_workflow_execution_plan_v2(
        node_table,
        gate_transitions=transition_table,
        write_domains=write_domains,
    )

    try:
        plan = workflow_execution_plan(node_table, gate_transitions=transition_table)
    except ValueError:
        plan = WorkflowExecutionPlan(levels=())

    passive_identifiers = set(transition_table) | {"product_requirement", "staging"}
    ordered_identifiers = list(plan.order)
    for identifier in sorted((set(node_table) | passive_identifiers) - set(ordered_identifiers)):
        ordered_identifiers.append(identifier)

    plan_nodes: list[dict[str, object]] = []
    for identifier in ordered_identifiers:
        workflow = node_table.get(identifier)
        if workflow is not None:
            domains = (
                tuple(write_domains.get(identifier, _workflow_node_write_domains(identifier)))
                if write_domains
                else _workflow_node_write_domains(identifier)
            )
            dependencies = list(getattr(workflow, "depends_on", ()))
            runner_kind = inferred_runner_kinds[identifier]
            plan_nodes.append(
                {
                    "id": identifier,
                    "kind": "stage",
                    "dependencies": dependencies,
                    "write_domains": list(domains),
                    "runner": workflow.runner_fn,
                    "runner_kind": runner_kind,
                    "gate": workflow.run_guard_gate,
                    "fan_in_required": bool(dependencies),
                }
            )
            continue

        kind = "gate" if identifier in transition_table else "role"
        domains = _gate_write_domains(identifier) if kind == "gate" else (f"artifact:{identifier}",)
        plan_nodes.append(
            {
                "id": identifier,
                "kind": kind,
                "dependencies": [],
                "write_domains": list(domains),
                "runner": "",
                "runner_kind": "human_gate" if kind == "gate" else "passive_artifact",
                "gate": identifier if kind == "gate" else "",
                "fan_in_required": False,
            }
        )

    return {
        "version": 2,
        "valid": not validation_errors,
        "scheduler": "sequential",
        "runner_kind": "read_only_projection",
        "parallel_level_execution": "deferred",
        "validation_errors": validation_errors,
        "order": list(plan.order),
        "levels": [level.to_dict() for level in plan.levels],
        "nodes": plan_nodes,
        "parallel_execution_plan": workflow_parallel_execution_plan(
            node_table,
            gate_transitions=transition_table,
            runner_kinds=inferred_runner_kinds,
            write_domains=write_domains,
        ),
    }


PARALLEL_SAFE_RUNNER_KINDS = frozenset({"fake_worker", "read_only_advisor"})


def _worker_isolation_readiness(*, required: bool) -> dict[str, object]:
    if not required:
        return {
            "status": "none_required_for_fake_and_readonly",
            "required_strategy": "",
            "allowed_strategies": ["none"],
            "rejected_strategies": [],
            "evidence_required": [],
        }
    return {
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
    }


def _parallel_readiness(
    *,
    validation_errors: Iterable[str],
    blocked_runner_kinds: Iterable[str],
) -> dict[str, object]:
    blockers = list(validation_errors)
    blocked_kinds = sorted({kind for kind in blocked_runner_kinds if kind})
    blocked = bool(blockers)
    return {
        "status": "blocked_for_real_workers" if blocked else "ready_for_fake_and_readonly",
        "safe_runner_kinds": sorted(PARALLEL_SAFE_RUNNER_KINDS),
        "blocked_runner_kinds": blocked_kinds,
        "requires_worker_isolation": blocked,
        "requires_live_smoke": blocked,
        "worker_isolation": _worker_isolation_readiness(required=blocked),
        "blockers": blockers,
    }


def workflow_parallel_execution_plan(
    nodes: Mapping[str, WorkflowNode] | None = None,
    *,
    gate_transitions: Mapping[str, GateTransition] | None = None,
    runner_kinds: Mapping[str, str] | None = None,
    write_domains: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, object]:
    node_table = nodes or WORKFLOW_NODES
    transition_table = gate_transitions or GATE_TRANSITIONS
    validation_errors = validate_workflow_execution_plan_v2(
        node_table,
        gate_transitions=transition_table,
        write_domains=write_domains,
    )

    blocked_runner_kinds: list[str] = []
    for identifier in node_table:
        runner_kind = str((runner_kinds or {}).get(identifier, "sequential_worker"))
        if runner_kind not in PARALLEL_SAFE_RUNNER_KINDS:
            blocked_runner_kinds.append(runner_kind)
            validation_errors.append(f"Parallel execution refuses runner {identifier!r} with kind {runner_kind!r}")

    readiness = _parallel_readiness(
        validation_errors=validation_errors,
        blocked_runner_kinds=blocked_runner_kinds,
    )
    if validation_errors:
        return {
            "version": 1,
            "valid": False,
            "scheduler": "parallel_levels",
            "runner_kind": "read_only_fake_projection",
            "parallel_level_execution": "blocked",
            "parallel_readiness": readiness,
            "validation_errors": validation_errors,
            "batches": [],
        }

    plan = workflow_execution_plan(node_table, gate_transitions=transition_table)
    batches = [
        {
            "level": level.index,
            "nodes": list(level.runnable),
            "mode": "parallel" if len(level.runnable) > 1 else "sequential",
        }
        for level in plan.levels
        if level.runnable
    ]
    return {
        "version": 1,
        "valid": True,
        "scheduler": "parallel_levels",
        "runner_kind": "read_only_fake_projection",
        "parallel_level_execution": "enabled_for_fake_and_readonly",
        "parallel_readiness": readiness,
        "validation_errors": [],
        "batches": batches,
    }


def _topological_dependency_order(
    nodes: Mapping[str, WorkflowNode],
    *,
    gate_transitions: Mapping[str, GateTransition],
) -> list[str]:
    passive_identifiers = set(gate_transitions) | {"product_requirement", "staging"}
    ordered: list[str] = []
    seen: set[str] = set()
    visiting: list[str] = []

    def append_dependency(name: str) -> None:
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise ValueError(f"Workflow DAG cycle detected: {cycle}")
        if name in seen:
            return
        if name not in nodes and name not in passive_identifiers:
            raise ValueError(f"Workflow DAG has unknown dependency {name!r}")
        visiting.append(name)
        if name in nodes:
            for dependency in getattr(nodes[name], "depends_on", ()):
                append_dependency(dependency)
        visiting.pop()
        seen.add(name)
        ordered.append(name)

    for name in nodes:
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
