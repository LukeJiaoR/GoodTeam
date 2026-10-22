"""Existing human gates + local derivation + real Builder dispatch boundary."""
import json

import pytest
import yaml

from goodteam_os import cli
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.execution.approved_plan import ApprovedPlanBinding, binding_for
from goodteam_os.execution.task_snapshot import TaskInputError, execution_key
from goodteam_os.state import AgileDevState, StateStore
from goodteam_os.web import decide_approval
from test_task_input_snapshot import ctx as ctx_fixture, start  # noqa: F401


def approved_workflow(c, *, profile="greenfield", run_id="approved-workflow"):
    config = c.workspace / "projects" / "DEMO.yaml"
    target = yaml.safe_load(config.read_text())
    target["worker_backend"] = {"mode": "fake"}
    # The approved workflow is model-free. The separate exact-source reviewer
    # keeps its configured runtime when this fixture follows team migration.
    target.setdefault("workers", {}).update({role: "fake" for role in ("ui", "implementation", "qa")})
    config.write_text(yaml.safe_dump(target, sort_keys=False))
    artifacts = ArtifactStore(c.workspace)
    key = "fix_plan" if profile == "fix" else "rfc"
    state = AgileDevState(run_id=run_id, project_key="DEMO", idea="Preserve the original goal",
        load_profile=profile, approvals={key: "pending", **({"security": "pending"} if profile == "greenfield" else {})},
        current_node=key + "_review_gate", next_node="await_" + key + "_approval")
    path = artifacts.write_markdown(run_id, key, key + ".md",
        "# Approved intent\nNo network or unrelated changes.\n\n## Selected task\nPreserve OLD_CONDITION.\n")
    state.artifacts[key] = str(path)
    StateStore(c.workspace).write_project_current(state)
    # Real decision persistence and operation IDs. No provider or configured
    # implementation runtime exists in this isolated workflow fixture.
    decide_approval(workspace=c.workspace, project_key="DEMO", approval=key, decision="approved",
                    note="Offline fixture reviewed the intended bounded change.")
    if profile == "greenfield":
        decide_approval(workspace=c.workspace, project_key="DEMO", approval="security", decision="approved",
                        note="Offline fixture accepts the stated security scope.")
        state = StateStore(c.workspace).read_project_current("DEMO")
        # Trusted Planner-derived task material, explicitly not a human approval.
        path = artifacts.write_markdown(run_id, "task_breakdown", "task-breakdown.md",
            "# Derived task breakdown\n\n## Selected task\nPreserve OLD_CONDITION with local deterministic checks.\n")
        state.artifacts["task_breakdown"] = str(path)
        state.resources = {}
        StateStore(c.workspace).write_project_current(state)
    return StateStore(c.workspace).read_project_current("DEMO")


def register(c, *, profile="greenfield"):
    state = approved_workflow(c, profile=profile)
    return ApprovedPlanBinding(c.project).register(c.execution_id, workflow_run_id=state.run_id,
                                                  task_anchor="Selected task")


@pytest.mark.parametrize("profile", ["greenfield", "fix"])
def test_existing_gates_register_exact_derived_task_and_replay(request, profile, capsys):
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    binding = register(c, profile=profile)
    assert binding["derived_task"]["human_approved"] is False
    assert binding["derived_task"]["task_id"] == c.store.load(c.execution_id).task.task_id
    assert [item["gate"] for item in binding["approvals"]] == (["rfc", "security"] if profile == "greenfield" else ["fix_plan"])
    assert cli.main(["execution", "bind-plan", "--project-root", str(c.project), "--execution-id", c.execution_id,
        "--workflow-run", "approved-workflow", "--task-anchor", "Selected task"]) == 0
    assert json.loads(capsys.readouterr().out)["binding"] == binding
    job = c.orchestrator.worker_store.list_jobs()[0]
    prompt = c.orchestrator.worker_store._worker_prompt(c.orchestrator.worker_store.root / job["id"], job)
    assert "No network or unrelated changes" in prompt and binding["binding_id"] in prompt
    assert c.orchestrator.run_execution(c.execution_id).status == "succeeded"


@pytest.mark.parametrize("mutation", ["artifact", "breakdown", "run", "project", "decision", "operation", "missing", "marker", "scope"])
def test_changed_or_missing_authority_blocks_before_worker_dispatch(request, monkeypatch, mutation):
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    register(c)
    root = c.store.root / execution_key(c.execution_id)
    states = StateStore(c.workspace)
    state = states.read_project_current("DEMO")
    if mutation in {"artifact", "breakdown"}:
        from pathlib import Path
        Path(state.artifacts["rfc" if mutation == "artifact" else "task_breakdown"]).write_text("changed")
    elif mutation in {"run", "project"}:
        if mutation == "run":
            state.run_id = "other-run"
        else:
            state.project_key = "OTHER"
        states._write(states.project_state_dir("DEMO") / "current.json", state)
    elif mutation in {"decision", "operation", "scope"}:
        path = states.project_state_dir("DEMO") / "action-log.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        event = next(item for item in events if item.get("gate") == "rfc" and item.get("action") == "approval_decided")
        event[{"decision": "decision", "operation": "workflow_operation_id", "scope": "project_key"}[mutation]] = {
            "decision": "rejected", "operation": "another-operation", "scope": "OTHER"}[mutation]
        path.write_text("\n".join(json.dumps(item) for item in events) + "\n")
    else:
        (root / ("approved-plan.json" if mutation == "missing" else "approved-plan-required.json")).unlink()
    called = []
    monkeypatch.setattr(c.orchestrator.worker_store, "run_job", lambda *_a, **_k: called.append(True))
    before = c.orchestrator.ledger.read_run(c.execution_id).to_dict()
    for _ in range(2):
        with pytest.raises(TaskInputError, match="approved_plan"):
            c.orchestrator.run_execution(c.execution_id)
        assert c.orchestrator.ledger.read_run(c.execution_id).to_dict() == before
    assert not called


@pytest.mark.parametrize("anchor", ["not a member", "", "Selected task\nextra"])
def test_missing_task_membership_never_registers(request, anchor):
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    state = approved_workflow(c)
    with pytest.raises(TaskInputError):
        ApprovedPlanBinding(c.project).register(c.execution_id, workflow_run_id=state.run_id, task_anchor=anchor)
    assert binding_for(c.project, c.store.load(c.execution_id)) is None


def test_existing_unscoped_approval_uses_exact_durable_checkpoint(request):
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    state = approved_workflow(c)
    path = StateStore(c.workspace).project_state_dir("DEMO") / "action-log.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    for event in events:
        event.pop("project_key", None)
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n")
    binding = ApprovedPlanBinding(c.project).register(c.execution_id, workflow_run_id=state.run_id,
                                                     task_anchor="Selected task")
    assert len(binding["approvals"]) == 2
    checkpoints = StateStore(c.workspace).project_state_dir("DEMO") / "checkpoints" / state.run_id
    for checkpoint in checkpoints.glob("rfc_review_gate-*.json"):
        value = json.loads(checkpoint.read_text())
        value["project_key"] = "OTHER"
        checkpoint.write_text(json.dumps(value))
    with pytest.raises(TaskInputError, match="reapproval_required"):
        binding_for(c.project, c.store.load(c.execution_id))


@pytest.mark.parametrize("change", ["duplicate_heading", "empty_heading", "operation_reuse", "missing_checkpoint", "symlink"])
def test_invalid_membership_or_approval_proof_cannot_register(request, change):
    from pathlib import Path
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    state = approved_workflow(c)
    if change in {"duplicate_heading", "empty_heading", "symlink"}:
        path = Path(state.artifacts["task_breakdown"])
        if change == "symlink":
            outside = c.workspace / "outside.md"
            outside.write_text(path.read_text())
            path.unlink()
            path.symlink_to(outside)
        else:
            path.write_text("## Selected task\n" + ("one\n## Selected task\ntwo\n" if change == "duplicate_heading" else ""))
    else:
        path = StateStore(c.workspace).project_state_dir("DEMO") / "action-log.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        if change == "operation_reuse":
            source = next(item for item in events if item.get("gate") == "rfc" and item.get("action") == "approval_decided")
            events.append({**source, "run_id": "another-run"})
        else:
            for item in events:
                item.pop("project_key", None)
            root = StateStore(c.workspace).project_state_dir("DEMO") / "checkpoints" / state.run_id
            for checkpoint in root.glob("*.json"):
                checkpoint.unlink()
        path.write_text("\n".join(json.dumps(item) for item in events) + "\n")
    with pytest.raises(TaskInputError):
        ApprovedPlanBinding(c.project).register(c.execution_id, workflow_run_id=state.run_id, task_anchor="Selected task")
    assert binding_for(c.project, c.store.load(c.execution_id)) is None


def test_completed_builder_cannot_gain_approval_provenance_afterward(request):
    c = request.getfixturevalue("ctx_fixture")
    c.execution_id = start(c).execution_id
    approved_workflow(c)
    c.orchestrator.run_execution(c.execution_id)
    with pytest.raises(TaskInputError, match="too_late"):
        ApprovedPlanBinding(c.project).register(c.execution_id, workflow_run_id="approved-workflow", task_anchor="Selected task")
    assert binding_for(c.project, c.store.load(c.execution_id)) is None
