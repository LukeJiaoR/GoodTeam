"""Offline original Builder worktree -> exact check -> independent gate."""
from dataclasses import replace
import json
import sys

import pytest

from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_validation import _read, _write
from goodteam_os.execution.sandbox import SandboxService
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills import review_execution
from goodteam_os.team_control_plane import ensure_team_state
from test_automatic_candidate_review import CandidateRuntime
from test_candidate_validation import candidate, git, protected, validate  # noqa: F401
from test_operator_plan_gate import actions as operator_actions, operator_item
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture
def builder(request):
    c = request.getfixturevalue("candidate")
    c.base = c.head
    c.orchestrator.sandbox_enabled = True
    c.orchestrator.sandbox_mode = "worktree"
    run = c.orchestrator.run_execution(c.execution_id)
    assert run.status.value == "succeeded"
    c.worker = c.store.load(c.execution_id).job_id
    c.worktree = c.project / ".goodteam" / "sandboxes" / c.execution_id / "worktree"
    (c.worktree / "feature.py").write_text("VALUE = 'fixed'\n")
    git(c.worktree, "add", "feature.py")
    git(c.worktree, "commit", "-qm", "Offline Builder candidate")
    c.head = git(c.worktree, "rev-parse", "HEAD")
    c.command = [sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    return c


def check(c, **kwargs):
    return validate(c, builder_worker=c.worker, argv=c.command, criteria=[0, 1], **kwargs)


def test_explicit_builder_source_checks_worktree_and_replays_once(builder, monkeypatch):
    c = builder
    assert git(c.project, "rev-parse", "HEAD") == c.base != c.head
    with pytest.raises(TaskInputError, match="validation_head_changed"):
        validate(c)  # No implicit source switch for legacy calls.
    result = check(c)
    assert result["observation"]["status"] == "passed"
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not run twice"))
    assert check(c) == result
    assert c.validation.evidence(c.execution_id)["check_status"] == "passed"
    plan = _read(next(protected(c).glob("validation-plan-*")))
    assert plan["request"]["builder_worker"] == c.worker
    assert plan["request"]["builder_source"]["snapshot"] == c.store.load(c.execution_id).reference


@pytest.mark.parametrize("mutation", ["worker", "run", "metadata", "missing", "symlink", "branch", "dirty", "head", "other_repository"])
def test_unrelated_or_unavailable_builder_never_invokes(builder, monkeypatch, mutation, tmp_path):
    c = builder
    if mutation == "worker":
        c.worker = "job-input-" + "a" * 64
    elif mutation == "run":
        run = c.orchestrator.ledger.read_run(c.execution_id)
        c.orchestrator.ledger.create_run(replace(run, worker_job_id="other"))
    elif mutation == "metadata":
        path = c.project / ".goodteam" / "executions" / c.execution_id / "sandbox.json"
        data = json.loads(path.read_text())
        data["worktree_path"] = str(c.project)
        path.write_text(json.dumps(data))
    elif mutation in {"missing", "symlink", "other_repository"}:
        moved = c.worktree.with_name("moved")
        c.worktree.rename(moved)
        if mutation == "symlink":
            c.worktree.symlink_to(moved, target_is_directory=True)
        elif mutation == "other_repository":
            git(c.project, "clone", "--quiet", str(c.project), str(c.worktree))
            git(c.worktree, "checkout", "-qb", "sandbox/" + c.execution_id)
    elif mutation == "branch":
        git(c.worktree, "checkout", "--detach", "-q")
    elif mutation == "dirty":
        (c.worktree / "feature.py").write_text("changed")
    elif mutation == "head":
        git(c.worktree, "reset", "--hard", c.base)
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not invoke"))
    with pytest.raises(TaskInputError):
        check(c)
    assert not list(protected(c).glob("validation-started-*"))


def test_builder_and_correction_selection_cannot_mix(builder):
    with pytest.raises(TaskInputError, match="validation_source_conflict"):
        check(builder, correction_worker="some-correction")


def test_worktree_replacement_invalidates_frozen_receipt(builder):
    c = builder
    check(c)
    # Keep the old directory alive so its inode cannot be immediately reused.
    c.worktree.rename(c.worktree.with_name("retained-old"))
    git(c.project, "worktree", "prune")
    git(c.project, "worktree", "add", str(c.worktree), "sandbox/" + c.execution_id)
    assert c.validation.evidence(c.execution_id)["check_status"] == "unknown"
    with pytest.raises(TaskInputError, match="validation_operation_conflict"):
        check(c)


def test_interrupted_builder_validation_is_not_retried(builder, monkeypatch):
    c = builder
    check(c)
    next(protected(c).glob("validation-result-*")).unlink()
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("indeterminate attempt must not retry"))
    with pytest.raises(TaskInputError, match="validation_attempt_indeterminate"):
        check(c)


def test_builder_receipt_enters_independent_operator_gate(builder, monkeypatch):
    c = builder
    result = check(c)
    ensure_team_state(c.workspace, "DEMO", c.workspace / "projects" / "DEMO.yaml")
    runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    service = AutomaticCandidateReviewService(c.project)
    service.start(c.execution_id, base_sha=c.base, receipt_id=result["receipt_id"], head_sha=c.head,
                  reviewer_adapter="codex", reviewer_model="fixture-model")
    actions = operator_actions(c)
    item = operator_item(c)
    review = actions._review(item)
    assert review["recommendation"] == "approve"
    assert review["reviewer_identity"] != c.worker
    assert review["machine_review"]["validation_receipt_id"] == result["receipt_id"]
    assert len(runtime.records) == 1
    assert actions._review(item) == review
    assert len(runtime.records) == 1
    assert c.validation.evidence(c.execution_id)["acceptance_status"] == "accepted_by_review_policy"
    SandboxService(c.project).destroy_sandbox(c.execution_id)
    assert actions._review(item)["acceptance_status"] == "unknown"
    assert c.validation.evidence(c.execution_id)["acceptance_status"] == "unknown"


def test_binding_cannot_be_dropped_from_builder_plan(builder):
    c = builder
    check(c)
    plan_path = next(protected(c).glob("validation-plan-*"))
    plan = _read(plan_path)
    plan["request"].pop("builder_source")
    _write(plan_path, plan)
    assert c.validation.evidence(c.execution_id)["check_status"] == "unknown"
