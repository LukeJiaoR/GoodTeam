"""Real registered candidate review consumed by the existing Operator gate."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from goodteam_os.execution.github import GitHubIssueSnapshot, execution_task_from_github_issue
from goodteam_os.execution.task_snapshot import TaskExecutionSnapshot
from goodteam_os.operator import LocalOperatorActions, AmbiguousOperation, OperatorExecutionStateMachine
from goodteam_os.skills.review_execution import selected_review_runtime as real_runtime_selection
from test_automatic_candidate_review import automatic, passed_candidate  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401


def snapshot():
    issue = GitHubIssueSnapshot(repo="example/project", issue_number=1, title="Keep the plan",
        body="Acceptance:\n- Keep OLD\n- Keep OLD", url="https://github.com/example/project/issues/1",
        labels=[], updated_at="revision-1")
    task = execution_task_from_github_issue(issue, clock=lambda: "2026-01-01T00:00:00Z")
    return TaskExecutionSnapshot.capture(task, scope="a" * 64, role="implementation", adapter="fake")


def operator_item(c):
    snap = c.store.load(c.execution_id)
    from goodteam_os.execution.github import GitHubExecutionBindingStore
    bindings = GitHubExecutionBindingStore(c.project)
    bindings.write_binding(replace(bindings.read_binding(c.execution_id), pr_number=1))
    return {"id": "op-1", "execution_id": c.execution_id, "repository_identity": "example/project",
        "source_url": "https://github.com/example/project/issues/1", "project_key": "DEMO", "attempt": 1,
        "metadata": {"project_root": str(c.project), "pr_number": 1, "head_sha": c.head,
            "base_sha": c.base, "branch_name": "feature", "task_contract": snap.task.task_contract.to_dict(),
            "task_snapshot": snap.reference, "worker_job_id": snap.job_id,
            "ci": {"status": "passed", "head_sha": c.head}, "decision": {"head_sha": c.head}}}


def actions(c):
    controller = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator)
    controller._client = lambda _: SimpleNamespace(fetch_pr=lambda *_: {"head_sha": c.head})
    return controller


def test_fix_hydration_cannot_replace_frozen_contract(tmp_path, monkeypatch):
    snap = snapshot()
    actions = LocalOperatorActions(workspace=tmp_path, state_dir=tmp_path / "state")
    changed = GitHubIssueSnapshot(repo="example/project", issue_number=1, title="Keep the plan",
        body="Acceptance:\n- New easier goal", url="https://github.com/example/project/issues/1", labels=[], updated_at="revision-2")
    monkeypatch.setattr(actions, "_client", lambda _: SimpleNamespace(fetch_issue=lambda *_: changed))
    monkeypatch.setattr(actions, "_frozen_snapshot", lambda _: snap)
    item = {"source_url": changed.url, "repository_identity": changed.repo, "execution_id": snap.execution_id,
            "metadata": {"task_contract": snap.task.task_contract.to_dict()}}
    with pytest.raises(AmbiguousOperation, match="contract"):
        actions._hydrate(item)


def test_frozen_snapshot_refuses_mutable_queue_contract(tmp_path, monkeypatch):
    from goodteam_os import operator
    snap = snapshot()
    actions = LocalOperatorActions(workspace=tmp_path, state_dir=tmp_path / "state")
    monkeypatch.setattr(actions, "_project", lambda _: tmp_path)
    monkeypatch.setattr(operator, "store_for_project", lambda *args, **kwargs: SimpleNamespace(load=lambda _: snap))
    item = {"execution_id": snap.execution_id, "metadata": {"task_contract": snap.task.task_contract.to_dict()}}
    assert actions._frozen_snapshot(item).data == snap.data
    item["metadata"]["task_contract"]["acceptance_criteria"] = ["easier"]
    with pytest.raises(AmbiguousOperation, match="contract"):
        actions._frozen_snapshot(item)


def test_fix_hydration_ignores_metadata_churn_and_preserves_original_input(tmp_path, monkeypatch):
    snap = snapshot()
    actions = LocalOperatorActions(workspace=tmp_path, state_dir=tmp_path / "state")
    source = snap.task.task_contract.source_intent
    changed = GitHubIssueSnapshot(repo="example/project", issue_number=1, title=source["title"],
        body=source["body"], url=source["url"], labels=["triaged"], updated_at="revision-2",
        comments=[{"id": "new-comment", "author": "reviewer", "created_at": "later"}],
        references=["https://github.com/example/project/issues/2"])
    monkeypatch.setattr(actions, "_client", lambda _: SimpleNamespace(fetch_issue=lambda *_: changed))
    monkeypatch.setattr(actions, "_frozen_snapshot", lambda _: snap)
    item = {"source_url": changed.url, "repository_identity": changed.repo, "execution_id": snap.execution_id,
            "metadata": {"task_contract": snap.task.task_contract.to_dict()}}
    result = actions._hydrate(item)
    assert result["task_contract"] == snap.task.task_contract.to_dict()
    assert result["source_revision"] == "revision-1"
    assert result["observed_source_revision"] == "revision-2"
    assert result["provenance_references"] == [source["url"]]
    assert snap.task.task_contract.acceptance_criteria == ["Keep OLD", "Keep OLD"]



def test_real_registered_reviewer_generates_actionable_plan_findings(request):
    c = request.getfixturevalue("automatic")
    result = actions(c)._review(operator_item(c))
    assert result["recommendation"] == "request_changes"
    assert result["deterministic"] and "Fix VALUE" in result["blockers"][0]
    assert result["machine_review"]["snapshot"] == c.store.load(c.execution_id).reference
    assert result["reviewer_identity"] != c.store.load(c.execution_id).job_id
    assert len(c.runtime.records) == 1
    assert len(result["criterion_checks"]) == 2
    from test_operator_state_machine import FakeActions, _queue
    queue, _ = _queue(c.workspace / "queue")
    machine = OperatorExecutionStateMachine(queue, FakeActions(reviews=[result]))
    for _ in range(7):
        machine.tick()
    assert queue.active_item()["phase"] == "fixing"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "empty", "quote"])
def test_real_store_incomplete_reports_remain_unknown(request, mutation):
    c = request.getfixturevalue("automatic")
    passed_candidate(c)
    def transform(report):
        if mutation == "missing":
            report["checks"].pop()
        elif mutation == "duplicate":
            report["checks"].append(deepcopy(report["checks"][0]))
        elif mutation == "unknown":
            report["checks"][0]["status"] = "not_run"
        elif mutation == "quote":
            report["evidence"][0]["quote"] = "invented runtime proof"
        else:
            return None
        return report
    c.runtime.transform = transform
    result = actions(c)._review(operator_item(c))
    assert result["acceptance_status"] == "unknown" and result["blockers"]
    from test_operator_state_machine import FakeActions, _queue
    queue, original = _queue(c.workspace / "queue")
    fake = FakeActions(reviews=[result])
    machine = OperatorExecutionStateMachine(queue, fake)
    for _ in range(7):
        machine.tick()
    assert queue.get(original["id"])["phase"] == "paused"
    assert fake.calls["send_approval"] == fake.calls["fix"] == 0


def test_same_head_valid_plan_reuses_real_review_without_new_commit(request):
    c = request.getfixturevalue("automatic")
    passed_candidate(c)
    item = operator_item(c)
    first = actions(c)._review(item)
    assert first["recommendation"] == "approve", first
    assert actions(c)._review(item) == first
    assert len(c.runtime.records) == 1


@pytest.mark.parametrize("mutation", ["bare", "forged", "stale", "valid"])
def test_merge_revalidates_protected_receipt_not_approval_string(request, monkeypatch, mutation):
    c = request.getfixturevalue("automatic")
    passed_candidate(c)
    item = operator_item(c)
    controller = actions(c)
    review = controller._review(item)
    assert review["recommendation"] == "approve"
    if mutation == "bare":
        review.pop("machine_review")
    elif mutation == "forged":
        review["machine_review"]["reason"] = "operator said approved"
    elif mutation == "stale":
        review["machine_review"]["validation_receipt_id"] = "old-receipt"
    item["metadata"]["review"] = review
    calls = []
    monkeypatch.setattr(controller, "_client", lambda _: SimpleNamespace(fetch_pr=lambda *_: {"head_sha": c.head},
        merge_pr=lambda *args, **kwargs: calls.append(kwargs) or {"merged": True, "sha": "merged"}))
    monkeypatch.setattr(controller, "observe_ci", lambda _: {"status": "passed", "head_sha": c.head})
    if mutation == "valid":
        assert controller._merge(item)["merged"] and len(calls) == 1
    else:
        with pytest.raises(AmbiguousOperation, match="plan review"):
            controller._merge(item)
        assert not calls


def test_missing_configuration_is_explicitly_unconnected(request):
    c = request.getfixturevalue("automatic")
    from goodteam_os.execution.task_snapshot import execution_key
    # Separate registered execution with its frozen input but no automatic start.
    root = c.store.root / execution_key(c.execution_id)
    for name in ("automatic-review-start.json", "automatic-review-binding.json"):
        (root / name).unlink()
    result = actions(c)._review(operator_item(c))
    assert result["acceptance_status"] == "unknown"
    assert result["summary"] == "automatic_review_configuration_required"
    assert not c.runtime.records


def test_production_fix_is_blocked_by_existing_admission_not_legacy_job_guard(request):
    c = request.getfixturevalue("automatic")
    item = operator_item(c)
    controller = actions(c)
    item["metadata"]["review"] = controller._review(item)
    result = controller._fix(item)
    assert result["status"] == "blocked"
    assert result["reason"] == "correction_verified_runtime_unavailable"
    assert not c.effects.exists()


def test_existing_noop_correction_runs_and_stops_on_real_no_progress(request, monkeypatch):
    from test_task_input_snapshot import start
    from test_candidate_validation import git
    from test_automatic_candidate_review import CandidateRuntime
    from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
    from goodteam_os.execution.candidate_validation import CandidateValidationService
    from goodteam_os.execution.correction_loop import CorrectionLoopService
    from goodteam_os.team_control_plane import ensure_team_state
    from goodteam_os.skills import review_execution
    import sys

    c = request.getfixturevalue("ctx")
    run = start(c, adapter="noop")
    c.execution_id = run.execution_id
    ensure_team_state(c.workspace, "DEMO", c.workspace / "projects" / "DEMO.yaml")
    git(c.project, "init", "-q")
    (c.project / ".gitignore").write_text(".goodteam/\n")
    (c.project / "feature.py").write_text("VALUE = 'base'\n")
    git(c.project, "add", ".")
    git(c.project, "commit", "-qm", "Base")
    c.base = git(c.project, "rev-parse", "HEAD")
    (c.project / "feature.py").write_text("VALUE = 'candidate'\n")
    git(c.project, "commit", "-qam", "Candidate")
    c.head = git(c.project, "rev-parse", "HEAD")
    validation = CandidateValidationService(c.project)
    receipt = validation.validate(c.execution_id, operation_id="initial", head_sha=c.head,
        argv=[sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"], criteria=[0, 1])
    service = AutomaticCandidateReviewService(c.project)
    service.start(c.execution_id, base_sha=c.base, head_sha=c.head, receipt_id=receipt["receipt_id"],
                  reviewer_adapter="codex", reviewer_model="fixture-model", fix_seconds=30)
    c.runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: c.runtime)
    controller = actions(c)
    item = operator_item(c)
    item["metadata"]["review"] = controller._review(item)
    result = controller._fix(item)
    assert result["status"] == "blocked"
    assert result["reason"] == "correction_no_progress", result
    evidence = CorrectionLoopService(c.project).evidence(c.execution_id)
    assert evidence["reserved_rounds"] >= 1


def test_unadmitted_reviewer_is_unknown_without_provider_invocation(request, monkeypatch):
    c = request.getfixturevalue("automatic")
    from goodteam_os.skills import review_execution
    monkeypatch.setattr(review_execution, "selected_review_runtime", real_runtime_selection)
    result = actions(c)._review(operator_item(c))
    assert result["acceptance_status"] == "unknown"
    assert "runtime" in result["summary"] or "admission" in result["summary"], result
    assert not c.runtime.records


def test_completed_plan_receipt_survives_budget_expiry_without_new_review(request, monkeypatch):
    import time
    from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
    from goodteam_os.execution.candidate_validation import _read
    from goodteam_os.execution.task_snapshot import execution_key

    c = request.getfixturevalue("automatic")
    passed_candidate(c)
    controller = actions(c)
    item = operator_item(c)
    first = controller._review(item)
    assert first["recommendation"] == "approve"
    service = AutomaticCandidateReviewService(c.project, clock=lambda: time.time() + 1000)
    assert service.advance(c.execution_id)["status"] == "reviewed"
    monkeypatch.setattr(controller, "_automatic_review", lambda _: service)
    second = controller._review(item)
    assert second == first
    assert len(c.runtime.records) == 1
    root = c.store.root / execution_key(c.execution_id)
    assert _read(root / "automatic-review-halt.json") is None


def test_same_head_new_check_cannot_reuse_expired_old_review(request, monkeypatch):
    import time
    from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService

    c = request.getfixturevalue("automatic")
    passed_candidate(c)
    controller = actions(c)
    item = operator_item(c)
    assert controller._review(item)["recommendation"] == "approve"
    c.validation.validate(c.execution_id, operation_id="replacement-check", head_sha=c.head,
                          argv=c.argv, criteria=[0, 1])
    service = AutomaticCandidateReviewService(c.project, clock=lambda: time.time() + 1000)
    monkeypatch.setattr(controller, "_automatic_review", lambda _: service)
    result = controller._review(item)
    assert result["acceptance_status"] == "unknown"
    assert result["summary"] == "correction_time_budget_exhausted"
    assert len(c.runtime.records) == 1
