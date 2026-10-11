"""CI coverage for durable approvals, revision paths, and failed fix work."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from goodteam_os import graph, web
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.resources import register_markdown_artifact
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.workflow_mutations import WorkflowBusyError, begin_transition, workflow_guard


def gate_state(workspace, gate, *, profile="greenfield"):
    state = create_initial_state("recovery-run", "RECOVERY", "Fix the validation gap")
    state.load_profile = profile
    state.current_node = f"{gate}_review_gate"
    state.next_node = f"await_{gate}_approval"
    state.approvals.update({key: ApprovalDecision.APPROVED for key in state.approvals})
    state.approvals[gate] = ApprovalDecision.PENDING
    if profile == "fix":
        state.approvals.update(fix_plan=ApprovalDecision.APPROVED, code_review=ApprovalDecision.PENDING,
                               qa=ApprovalDecision.PENDING)
        state.approvals[gate] = ApprovalDecision.PENDING
    artifact = web._approval_artifact_for_gate(gate)
    path = ArtifactStore(workspace).write_markdown(state.run_id, artifact, f"{artifact}.md", "# Evidence\nCurrent")
    register_markdown_artifact(state, artifact, path)
    StateStore(workspace).write_project_current(state)
    return state


def binding_for(state):
    gate = state.next_node.removeprefix("await_").removesuffix("_approval")
    artifact = web._approval_artifact_for_gate(gate)
    return dict(expected_run_id=state.run_id, expected_target_type="review", expected_target_id=gate,
                expected_artifact_key=artifact, expected_artifact_version=web.artifact_version_for_path(state.artifacts[artifact]))


def decide(workspace, gate, **kwargs):
    # This fixture explicitly continues with simulated evidence; no provider PASS.
    kwargs.setdefault("note", "Model-free recovery fixture: continue with simulated evidence to test recovery only.")
    return web.decide_approval(workspace=workspace, project_key="RECOVERY", approval=gate,
                               decision=kwargs.pop("decision", "approved"), **kwargs)


def retry_binding(workspace):
    state = StateStore(workspace).read_project_current("RECOVERY")
    return {"expected_run_id": state.run_id,
            "expected_retry_version": web.workflow_retry_target(workspace, state)["version"]}


def test_concurrent_approval_is_rejected_before_second_dispatch(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "release")
    entered, release = Event(), Event()
    calls = []
    original = web.advance_approved_workflow

    def paused(**kwargs):
        calls.append(kwargs["state"].run_id)
        entered.set()
        assert release.wait(timeout=10)
        return original(**kwargs)

    monkeypatch.setattr(web, "advance_approved_workflow", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(decide, tmp_path, "release", **binding_for(state))
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(WorkflowBusyError):
                decide(tmp_path, "release", **binding_for(state))
            live = web.resolve_dashboard_state(tmp_path, "RECOVERY")
            assert "executing" == live.pending_transition["status"]
        finally:
            release.set()
        result = first.result(timeout=10)
    assert [state.run_id] == calls
    assert "complete" == result["current_node"]
    replay = decide(tmp_path, "release", **binding_for(state))
    assert replay["replayed"] is True
    assert [state.run_id] == calls


def test_execution_exception_preserves_approval_and_retry_does_not_redecide(tmp_path, monkeypatch):
    gate_state(tmp_path, "release")
    original = web.advance_approved_workflow

    def fail(**kwargs):
        raise RuntimeError("execution interrupted")

    monkeypatch.setattr(web, "advance_approved_workflow", fail)
    with pytest.raises(RuntimeError, match="execution interrupted"):
        decide(tmp_path, "release")
    state = StateStore(tmp_path).read_project_current("RECOVERY")
    assert ApprovalDecision.APPROVED == state.approvals["release"]
    assert "blocked" == state.next_node
    assert "interrupted" == state.pending_transition["status"]
    monkeypatch.setattr(web, "advance_approved_workflow", original)
    result = web.retry_blocked_workflow(workspace=tmp_path, project_key="RECOVERY", **retry_binding(tmp_path))
    assert "complete" == result["current_node"]
    assert [] == result["blockers"]
    actions = web.build_action_items(tmp_path, "RECOVERY")
    assert 1 == sum(item["action"] == "approval_decided" for item in actions)
    decision = next(item for item in actions if item["action"] == "approval_decided")
    assert result["pending_transition"]["id"] == decision["workflow_operation_id"]


def test_restart_recovers_recorded_decision_without_requiring_another_approval(tmp_path):
    state = gate_state(tmp_path, "release")
    binding = binding_for(state)
    state.approvals["release"] = ApprovalDecision.APPROVED
    state.next_node = "retro_memory"
    with workflow_guard(tmp_path, "RECOVERY"):
        begin_transition(tmp_path, state, dict(run_id=state.run_id, project_key=state.project_key,
                                               actor="human", action="approval_decided", gate="release",
                                               artifact=binding["expected_artifact_key"],
                                               artifact_version=binding["expected_artifact_version"],
                                               note="Model-free interrupted-recording fixture with simulated evidence.",
                                               decision="approved", before_node="await_release_approval", after_node="retro_memory"))
    recovered = web.resolve_dashboard_state(tmp_path, "RECOVERY")
    assert "blocked" == recovered.next_node
    assert "interrupted" == recovered.pending_transition["status"]
    assert "complete" == web.retry_blocked_workflow(workspace=tmp_path, project_key="RECOVERY", **retry_binding(tmp_path))["current_node"]


def test_failed_action_append_is_recoverable_without_losing_decision(tmp_path, monkeypatch):
    gate_state(tmp_path, "release")
    original = web.append_action
    monkeypatch.setattr(web, "append_action", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError, match="disk unavailable"):
        decide(tmp_path, "release")
    assert ApprovalDecision.APPROVED == StateStore(tmp_path).read_project_current("RECOVERY").approvals["release"]
    monkeypatch.setattr(web, "append_action", original)
    assert "complete" == web.retry_blocked_workflow(workspace=tmp_path, project_key="RECOVERY", **retry_binding(tmp_path))["current_node"]
    assert 1 == sum(item["action"] == "approval_decided" for item in web.build_action_items(tmp_path, "RECOVERY"))


@pytest.mark.parametrize("gate,profile,next_gate", [
    ("security", "greenfield", "rfc"), ("staging", "greenfield", "release"),
    ("release", "greenfield", "release"), ("fix_plan", "fix", "fix_plan"),
    ("code_review", "fix", "code_review"), ("qa", "fix", "code_review"),
])
def test_requested_changes_reenter_review_with_original_feedback(tmp_path, monkeypatch, gate, profile, next_gate):
    monkeypatch.setattr(graph, "run_implementation_workers", lambda *a, **k: [
        {"role": "implementation", "status": "completed", "summary": "Revision implemented"},
    ])
    gate_state(tmp_path, gate, profile=profile)
    result = decide(tmp_path, gate, decision="changes_requested", note="Preserve the missing validation")
    assert f"await_{next_gate}_approval" == result["next_node"]
    assert "Preserve the missing validation" == result["change_requests"][gate]
    state = StateStore(tmp_path).read_project_current("RECOVERY")
    assert "Preserve the missing validation" in graph.worker_context(state)
    assert any(item["is_actionable"] for item in web.build_approval_reviews(state, workspace=tmp_path))


def test_failed_fix_blocks_and_retry_keeps_successful_qa_evidence(tmp_path, monkeypatch):
    gate_state(tmp_path, "fix_plan", profile="fix")
    monkeypatch.setattr(graph, "run_implementation_workers", lambda *a, **k: [
        {"role": "implementation", "status": "failed", "summary": "Unable to implement"},
    ])
    result = decide(tmp_path, "fix_plan")
    assert "blocked" == result["next_node"]
    assert "fix_implement" == result["current_node"]
    assert result["blockers"]
    evidence = tmp_path / "qa-result.md"
    evidence.write_text("Status: passed\npytest verified the regression", encoding="utf-8")
    monkeypatch.setattr(graph, "run_implementation_workers", lambda *a, **k: [
        {"role": "qa", "status": "completed", "summary": "Regression verified", "result_path": str(evidence)},
    ])
    retried = web.retry_blocked_workflow(workspace=tmp_path, project_key="RECOVERY", **retry_binding(tmp_path))
    assert "await_code_review_approval" == retried["next_node"]
    assert [] == retried["blockers"]
    result = decide(tmp_path, "code_review")
    assert "await_qa_approval" == result["next_node"]
    assert "Regression verified" in web.read_artifact(workspace=tmp_path, project_key="RECOVERY", artifact_key="qa_report")["content"]


def test_completed_worker_with_failed_qa_still_blocks_fix(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "fix_plan", profile="fix")
    evidence = tmp_path / "qa.md"
    evidence.write_text("Status: failed\nAssertion failed", encoding="utf-8")
    monkeypatch.setattr(graph, "run_implementation_workers", lambda *a, **k: [
        {"role": "qa", "status": "completed", "result_path": str(evidence)},
    ])
    assert "blocked" == graph.run_fix_implementation(state, tmp_path).next_node


def test_topology_fix_profile_advances_all_its_gates(tmp_path, monkeypatch):
    gate_state(tmp_path, "fix_plan", profile="fix")
    monkeypatch.setattr(graph, "_workflow_runner_mode", lambda _: "topo_v2")
    monkeypatch.setattr(graph, "run_implementation_workers", lambda *a, **k: [
        {"role": "implementation", "status": "completed", "summary": "Fix implemented"},
    ])
    assert "await_code_review_approval" == decide(tmp_path, "fix_plan")["next_node"]
    assert "await_qa_approval" == decide(tmp_path, "code_review")["next_node"]
    assert "complete" == decide(tmp_path, "qa")["current_node"]


def test_full_artifact_read_and_version_bound_save(tmp_path):
    state = gate_state(tmp_path, "prd")
    path = state.resources["prd"].path
    content = "# PRD\n" + "Requirement\n" * 900 + "Final acceptance criterion"
    Path(path).write_text(content, encoding="utf-8")
    review = next(item for item in web.build_approval_reviews(state, workspace=tmp_path) if item["approval"] == "prd")
    assert review["content_truncated"] is True
    artifact = web.read_artifact(workspace=tmp_path, project_key="RECOVERY", artifact_key="prd")
    assert content == artifact["content"]
    assert review["artifact_version"] == artifact["artifact_version"]
    web.save_artifact(workspace=tmp_path, project_key="RECOVERY", artifact_key="prd", content=content + "\nEdit",
                      expected_run_id=artifact["run_id"], expected_artifact_version=artifact["artifact_version"])
    with pytest.raises(web.ProjectApiError, match="artifact changed"):
        web.save_artifact(workspace=tmp_path, project_key="RECOVERY", artifact_key="prd", content="Stale edit",
                          expected_run_id=artifact["run_id"], expected_artifact_version=artifact["artifact_version"])
    assert "Final acceptance criterion" in Path(path).read_text(encoding="utf-8")


@pytest.mark.parametrize("terminal", ["", "rejected"])
def test_next_workflow_archives_terminal_run(tmp_path, terminal):
    state = gate_state(tmp_path, "release")
    state.current_node, state.next_node = "complete" if not terminal else "release_review_gate", terminal
    StateStore(tmp_path).write_project_current(state)
    assert not web.is_active_run(state)
    result = web.start_workflow(workspace=tmp_path, idea="A second task", project_key="RECOVERY",
                                model_config=Path("models.yaml"), model_profile="balanced")
    assert state.run_id != result["run_id"]
    archived = StateStore(tmp_path).list_project_archived("RECOVERY")
    assert [state.run_id] == [item.run_id for _, item in archived]


def test_workflow_dry_run_does_not_create_lock_or_state(tmp_path):
    result = web.start_workflow(workspace=tmp_path, idea="Plan only", project_key="RECOVERY",
                                model_config=Path("models.yaml"), model_profile="balanced", dry_run=True)
    assert result["dry_run"] is True
    assert not (tmp_path / "state").exists()


def test_atomic_state_write_keeps_previous_record_on_replace_failure(tmp_path, monkeypatch):
    from goodteam_os import state as state_module

    state = gate_state(tmp_path, "release")
    store = StateStore(tmp_path)
    path = store.project_state_dir(state.project_key) / "current.json"
    before = path.read_bytes()
    state.next_node = "complete"

    def fail_replace(*args):
        raise OSError("replacement interrupted")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement interrupted"):
        store.write_project_current(state)
    assert before == path.read_bytes()


def test_missing_fix_execution_evidence_blocks_instead_of_approving_empty_work(tmp_path):
    state = gate_state(tmp_path, "fix_plan", profile="fix")
    result = graph.run_fix_implementation(state, tmp_path)
    assert "blocked" == result.next_node
    assert "fix_implement" == result.current_node
    assert result.blockers


def test_workflow_lock_survives_archiving_fallback_state(tmp_path):
    legacy = tmp_path / "legacy"
    workspace = legacy / "workspace"
    gate_state(legacy, "release")
    with workflow_guard(workspace, "RECOVERY"):
        StateStore(workspace).archive_project_current("RECOVERY")
        assert 1 == len(StateStore(workspace).list_project_archived("RECOVERY"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocked = pool.submit(decide, workspace, "release")
            with pytest.raises(WorkflowBusyError):
                blocked.result(timeout=10)


@pytest.mark.parametrize("operation,extra", [
    (graph.generate_prd, {}), (graph.generate_rfc, {}),
    (graph.refine_artifact, {"artifact_key": "prd", "content": "# Edit"}),
    (graph.accept_refinement, {"artifact_key": "prd"}),
    (graph.discard_refinement, {"artifact_key": "prd"}),
])
def test_artifact_mutations_share_the_workflow_guard(tmp_path, operation, extra):
    gate_state(tmp_path, "prd")
    with workflow_guard(tmp_path, "RECOVERY"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocked = pool.submit(operation, workspace=tmp_path, project_key="RECOVERY", **extra)
            with pytest.raises(WorkflowBusyError):
                blocked.result(timeout=10)
