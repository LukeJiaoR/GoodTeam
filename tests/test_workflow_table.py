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
    validate_workflow_execution_plan_v2,
    workflow_parallel_execution_plan,
    workflow_execution_plan,
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
    assert gate_transition("rfc").on_approved_next == "security"

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


def test_workflow_execution_plan_groups_fanout_and_fanin_levels():
    nodes = {
        "research": WorkflowNode(
            name="research",
            run_guard_gate="prd",
            runner_fn="run_research",
            depends_on=("product_requirement",),
        ),
        "design": WorkflowNode(
            name="design",
            run_guard_gate="prd",
            runner_fn="run_design",
            depends_on=("product_requirement",),
        ),
        "review": WorkflowNode(
            name="review",
            run_guard_gate="rfc",
            runner_fn="run_review",
            depends_on=("research", "design"),
        ),
    }
    gate_transitions = {
        "prd": GateTransition(
            gate="prd",
            on_approved_next="research",
            on_changes_requested="revise_prd",
            on_rejected="rejected",
        ),
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="review",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        ),
    }

    plan = workflow_execution_plan(nodes, gate_transitions=gate_transitions)

    assert plan.scheduler == "sequential"
    assert plan.parallel_level_execution == "deferred"
    assert [level.identifiers for level in plan.levels] == [
        ("product_requirement",),
        ("research", "design"),
        ("review",),
    ]
    assert [level.runnable for level in plan.levels] == [
        (),
        ("research", "design"),
        ("review",),
    ]
    assert [level.passive for level in plan.levels] == [
        ("product_requirement",),
        (),
        (),
    ]
    assert plan.level_for("research") == 1
    assert plan.level_for("design") == 1
    assert plan.level_for("review") == 2


def test_workflow_execution_plan_exposes_current_runner_levels_without_enabling_parallelism():
    plan = workflow_execution_plan()

    assert plan.scheduler == "sequential"
    assert plan.parallel_level_execution == "deferred"
    assert plan.order == (
        "product_requirement",
        "prd",
        "architect",
        "rfc",
        "security",
        "planner",
        "staging",
        "release",
        "retro_memory",
    )
    assert plan.level_for("architect") < plan.level_for("planner")
    assert plan.level_for("rfc") < plan.level_for("security")
    assert plan.level_for("security") < plan.level_for("planner")
    assert plan.level_for("planner") < plan.level_for("release")
    assert plan.level_for("release") < plan.level_for("retro_memory")


def test_workflow_execution_plan_v2_projects_nodes_and_validation_metadata():
    plan = workflow_execution_plan_v2()

    assert plan["version"] == 2
    assert plan["valid"] is True
    assert plan["scheduler"] == "sequential"
    assert plan["runner_kind"] == "read_only_projection"
    assert plan["parallel_level_execution"] == "deferred"
    assert plan["validation_errors"] == []
    assert plan["order"] == [
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
    assert plan["levels"][0] == {
        "index": 0,
        "identifiers": ["product_requirement", "prd"],
        "runnable": [],
        "passive": ["product_requirement", "prd"],
    }
    assert {
        "id": "architect",
        "kind": "stage",
        "dependencies": ["product_requirement", "prd"],
        "write_domains": ["workflow:architect"],
        "runner": "run_architecture_until_gate",
        "runner_kind": "sequential_worker",
        "gate": "prd",
        "fan_in_required": True,
    } in plan["nodes"]
    assert {
        "id": "prd",
        "kind": "gate",
        "dependencies": [],
        "write_domains": ["approval:prd"],
        "runner": "",
        "runner_kind": "human_gate",
        "gate": "prd",
        "fan_in_required": False,
    } in plan["nodes"]


def test_workflow_execution_plan_v2_reports_unknown_dependency_without_raising():
    nodes = {
        "planner": WorkflowNode(
            name="planner",
            run_guard_gate="rfc",
            runner_fn="run_delivery_until_release_gate",
            depends_on=("missing_node",),
        )
    }

    plan = workflow_execution_plan_v2(nodes)

    assert plan["version"] == 2
    assert plan["valid"] is False
    assert plan["levels"] == []
    assert plan["validation_errors"] == [
        "Workflow node 'planner' has unknown dependency 'missing_node'",
        "Workflow execution plan v2 has missing fan-in node 'missing_node' for 'planner'",
    ]


def test_workflow_execution_plan_v2_reports_cycles_missing_fanin_and_write_domain_collisions():
    nodes = {
        "a": WorkflowNode(
            name="a",
            run_guard_gate="prd",
            runner_fn="run_shared",
            depends_on=("b", "missing_fanin"),
        ),
        "b": WorkflowNode(
            name="b",
            run_guard_gate="prd",
            runner_fn="run_shared",
            depends_on=("a",),
        ),
    }
    write_domains = {
        "a": ("repo:shared",),
        "b": ("repo:shared",),
    }
    gate_transitions = {
        "prd": GateTransition(
            gate="prd",
            on_approved_next="a",
            on_changes_requested="revise_prd",
            on_rejected="rejected",
        )
    }

    errors = validate_workflow_execution_plan_v2(
        nodes,
        gate_transitions=gate_transitions,
        write_domains=write_domains,
    )

    assert "Workflow node 'a' has unknown dependency 'missing_fanin'" in errors
    assert "Workflow DAG cycle detected: a -> b -> a" in errors
    assert "Workflow execution plan v2 has missing fan-in node 'missing_fanin' for 'a'" in errors
    assert "Workflow execution plan v2 write-domain collision repo:shared: a, b" in errors


def test_workflow_parallel_execution_plan_allows_fake_readonly_levels_and_preserves_fanin_order():
    nodes = {
        "research": WorkflowNode(
            name="research",
            run_guard_gate="prd",
            runner_fn="fake_research_worker",
            depends_on=("product_requirement",),
        ),
        "advisor": WorkflowNode(
            name="advisor",
            run_guard_gate="prd",
            runner_fn="readonly_advisor",
            depends_on=("product_requirement",),
        ),
        "summary": WorkflowNode(
            name="summary",
            run_guard_gate="rfc",
            runner_fn="fake_summary_worker",
            depends_on=("research", "advisor"),
        ),
    }
    gate_transitions = {
        "prd": GateTransition(
            gate="prd",
            on_approved_next="research",
            on_changes_requested="revise_prd",
            on_rejected="rejected",
        ),
        "rfc": GateTransition(
            gate="rfc",
            on_approved_next="summary",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
        ),
    }

    plan = workflow_parallel_execution_plan(
        nodes,
        gate_transitions=gate_transitions,
        runner_kinds={
            "research": "fake_worker",
            "advisor": "read_only_advisor",
            "summary": "fake_worker",
        },
        write_domains={
            "research": ("artifact:research",),
            "advisor": ("artifact:advisor",),
            "summary": ("artifact:summary",),
        },
    )

    assert plan == {
        "version": 1,
        "valid": True,
        "scheduler": "parallel_levels",
        "runner_kind": "read_only_fake_projection",
        "parallel_level_execution": "enabled_for_fake_and_readonly",
        "parallel_readiness": {
            "status": "ready_for_fake_and_readonly",
            "safe_runner_kinds": ["fake_worker", "read_only_advisor"],
            "blocked_runner_kinds": [],
            "requires_worker_isolation": False,
            "requires_live_smoke": False,
            "worker_isolation": {
                "status": "none_required_for_fake_and_readonly",
                "required_strategy": "",
                "allowed_strategies": ["none"],
                "rejected_strategies": [],
                "evidence_required": [],
            },
            "blockers": [],
        },
        "validation_errors": [],
        "batches": [
            {"level": 1, "nodes": ["research", "advisor"], "mode": "parallel"},
            {"level": 2, "nodes": ["summary"], "mode": "sequential"},
        ],
    }


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


def test_workflow_dag_validation_rejects_unknown_gate_advisors():
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
            on_approved_next="planner",
            on_changes_requested="revise_rfc",
            on_rejected="rejected",
            advisors=("qa", "missing_role"),
        )
    }

    try:
        validate_workflow_dag(nodes, gate_transitions=gate_transitions, valid_advisors={"qa"})
    except ValueError as exc:
        assert "unknown advisor" in str(exc)
        assert "missing_role" in str(exc)
        assert "rfc" in str(exc)
    else:
        raise AssertionError("expected unknown advisor validation failure")


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


def test_advance_approved_workflow_uses_legacy_runner_by_default(tmp_path, monkeypatch):
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

    def fail_if_topology_runner_is_used(*args, **kwargs):
        raise AssertionError("topology runner must be opt-in")

    monkeypatch.setattr(graph, "advance_approved_workflow_topology", fail_if_topology_runner_is_used, raising=False)

    result = graph.advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "await_rfc_approval"
    assert Path(result.artifacts["rfc"]).is_file()


def test_advance_approved_workflow_uses_topology_runner_when_project_opts_in(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: Test",
                "key: CONTENTOPS",
                "workflow_runner: topo_v2",
                "worker_backend:",
                "  mode: fake",
                "repos:",
                f"  docs: {docs}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"
    calls = []
    original_topology_runner = graph.advance_approved_workflow_topology

    def spy_topology_runner(*, state, workspace, project_config):
        calls.append((state.next_node, project_config))
        return original_topology_runner(state=state, workspace=workspace, project_config=project_config)

    monkeypatch.setattr(graph, "advance_approved_workflow_topology", spy_topology_runner)

    result = graph.advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert calls == [("architect", project_config)]
    assert result.next_node == "await_rfc_approval"
    assert Path(result.artifacts["rfc"]).is_file()


def test_topology_runner_consumes_plan_v2_nodes_without_legacy_node_lookup(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: Test",
                "key: CONTENTOPS",
                "workflow_runner: topo_v2",
                "worker_backend:",
                "  mode: fake",
                "repos:",
                f"  docs: {docs}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"

    def fail_legacy_lookup(node):
        raise AssertionError(f"topology runner must use plan v2 node metadata, not workflow_node({node!r})")

    monkeypatch.setattr(graph, "workflow_node", fail_legacy_lookup)

    result = graph.advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "await_rfc_approval"
    assert Path(result.artifacts["rfc"]).is_file()


def test_topology_runner_fails_closed_when_plan_v2_is_invalid(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: Test\nkey: CONTENTOPS\nworkflow_runner: topo_v2\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"
    monkeypatch.setattr(
        graph,
        "workflow_execution_plan_v2",
        lambda *args, **kwargs: {
            "valid": False,
            "validation_errors": ["Workflow DAG cycle detected: a -> b -> a"],
        },
    )

    result = graph.advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "architect"
    assert result.next_node == "blocked"
    assert result.blockers == ["workflow execution plan v2 invalid: Workflow DAG cycle detected: a -> b -> a"]
