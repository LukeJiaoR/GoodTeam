"""Normal Operator execution prepares exact local evidence without a live model."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_validation import _read
from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes, digest
from goodteam_os.operator import LocalOperatorActions, OperatorService, OperatorExecutionStateMachine
from goodteam_os.operator_candidate_review import OperatorCandidateReviewConfig
from goodteam_os.skills import review_execution
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore
from test_automatic_candidate_review import CandidateRuntime
from test_candidate_validation import candidate, protected, git  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401
from test_operator_state_machine import FakeActions, _queue


@pytest.fixture
def entry(request, monkeypatch):
    c = request.getfixturevalue("candidate")
    from test_approved_plan_binding import register
    register(c)
    snapshot = c.store.load(c.execution_id)
    contract = snapshot.task.task_contract.to_dict()
    c.item = {"id": "operator-entry", "execution_id": "", "repository_identity": "example/project",
              "source_url": "https://github.com/example/project/issues/1", "project_key": "DEMO",
              "metadata": {"project_root": str(c.project), "task_contract": contract, "worker_backend": "fake"}}
    c.config = OperatorCandidateReviewConfig(contract_digest=digest(canonical_bytes(contract)), base_sha=c.head,
        argv=(sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'original'"),
        criteria=(0, 1), reviewer_model="offline-fixture")
    ensure_team_state(c.workspace, "DEMO", c.workspace / "projects" / "DEMO.yaml")
    c.actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator,
                                    candidate_reviews={c.item["id"]: c.config})
    monkeypatch.setattr(c.actions, "_client", lambda _: c.client)
    c.runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: c.runtime)
    original_run = WorkerJobStore.run_job
    def offline_builder(workers, job_id, **kwargs):
        result = original_run(workers, job_id, **kwargs)
        if kwargs.get("sandbox_context") and result.get("status") == "completed":
            root = Path(kwargs["sandbox_context"]["worktree_path"])
            (root / "feature.py").write_text("VALUE = 'original'\n# Offline Builder change\n")
            git(root, "commit", "-qam", "Offline Builder implementation")
        return result
    monkeypatch.setattr(WorkerJobStore, "run_job", offline_builder)
    return c


def test_normal_entry_long_ci_wait_does_not_consume_first_review_budget(entry, monkeypatch):
    c = entry
    clock = [100.0]
    service = AutomaticCandidateReviewService(c.project, clock=lambda: clock[0])
    monkeypatch.setattr(c.actions, "_automatic_review", lambda _: service)
    assert c.orchestrator.ledger.read_run(c.execution_id).status.value == "queued"
    result = c.actions._execute(c.item)
    assert result["status"] == "succeeded"
    assert result["candidate_check_status"] == "passed" and result["candidate_review_prepared"]
    assert c.runtime.records == []
    assert service.configuration(c.execution_id) is None
    clock[0] = 2001.0  # PR/CI wait exceeds the default 900-second review budget.
    item = {**c.item, "execution_id": result["execution_id"], "metadata": {**c.item["metadata"], **result}}
    review = c.actions._review(item)
    assert review["recommendation"] == "request_changes"
    assert len(c.runtime.records) == 1
    assert len(review["criterion_checks"]) == 2
    config = service.configuration(c.execution_id)
    assert config["base_sha"] == c.config.base_sha
    assert config["initial_receipt"] == result["candidate_check_receipt"]
    assert config["started_at"] == 2001.0


def test_recover_before_queue_execution_id_publication_reuses_both_operations(entry, monkeypatch):
    c = entry
    first = c.actions._execute(c.item)
    assert c.item["execution_id"] == ""
    monkeypatch.setattr("goodteam_os.execution.orchestrator.ExecutionOrchestrator.run_execution",
                        lambda *_: pytest.fail("must not redispatch Builder"))
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not rerun check"))
    # A fresh service has only trusted startup configuration and retained evidence.
    resumed = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator,
                                  candidate_reviews={c.item["id"]: c.config})
    monkeypatch.setattr(resumed, "_client", lambda _: c.client)
    assert resumed.reconcile("execute", c.item, {}) == first


def test_interrupt_between_check_receipt_and_review_start_recovers(entry, monkeypatch):
    c = entry
    clock = [100.0]
    service = AutomaticCandidateReviewService(c.project, clock=lambda: clock[0])
    monkeypatch.setattr(c.actions, "_automatic_review", lambda _: service)
    result = c.actions._execute(c.item)
    item = {**c.item, "execution_id": result["execution_id"], "metadata": {**c.item["metadata"], **result}}
    real_capture = service._capture
    monkeypatch.setattr(service, "_capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        c.actions._review(item)
    assert len(list(protected(c).glob("validation-result-*"))) == 1
    assert service.configuration(c.execution_id)["started_at"] == 100.0
    clock[0] = 200.0
    monkeypatch.setattr(service, "_capture", real_capture)
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not rerun check"))
    assert c.actions.reconcile("execute", c.item, {})["candidate_review_prepared"]
    assert c.actions._review(item)["recommendation"] == "request_changes"
    assert service.configuration(c.execution_id)["started_at"] == 100.0


def test_already_started_expired_review_cannot_reset_budget(entry, monkeypatch):
    from goodteam_os.operator_candidate_review import start_candidate_review
    c = entry
    clock = [100.0]
    service = AutomaticCandidateReviewService(c.project, clock=lambda: clock[0])
    monkeypatch.setattr(c.actions, "_automatic_review", lambda _: service)
    result = c.actions._execute(c.item)
    item = {**c.item, "execution_id": result["execution_id"], "metadata": {**c.item["metadata"], **result}}
    start_candidate_review(service, c.store.load(c.execution_id), item, c.config)
    clock[0] = 2001.0
    review = c.actions._review(item)
    assert review["acceptance_status"] == "unknown"
    assert review["blockers"] == ["correction_time_budget_exhausted"]
    assert c.runtime.records == []
    assert service.configuration(c.execution_id)["started_at"] == 100.0


def test_indeterminate_check_is_blocked_without_retry(entry, monkeypatch):
    c = entry
    c.actions._execute(c.item)
    next(protected(c).glob("validation-result-*")).unlink()
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not retry unknown invocation"))
    with pytest.raises(TaskInputError, match="validation_attempt_indeterminate"):
        c.actions.reconcile("execute", c.item, {})


@pytest.mark.parametrize("change", ["command", "base", "model", "criteria"])
def test_changed_startup_configuration_cannot_rewrite_original_request(entry, monkeypatch, change):
    c = entry
    c.actions._execute(c.item)
    edits = {"command": {"argv": (sys.executable, "-c", "print('different')")},
             "base": {"base_sha": "a" * 40}, "model": {"reviewer_model": "other"}, "criteria": {"criteria": (0,)}}
    c.actions.candidate_reviews[c.item["id"]] = replace(c.config, **edits[change])
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not invoke changed command"))
    with pytest.raises((TaskInputError, review_execution.SkillError)):
        c.actions.reconcile("execute", c.item, {})


def test_changed_builder_head_does_not_authorize_another_check(entry, monkeypatch):
    c = entry
    c.actions._execute(c.item)
    worktree = c.project / ".goodteam" / "sandboxes" / c.execution_id / "worktree"
    (worktree / "feature.py").write_text("VALUE = 'changed'\n")
    git(worktree, "commit", "-qam", "Changed after check")
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not check a different candidate on replay"))
    with pytest.raises(TaskInputError, match="validation_operation_conflict"):
        c.actions.reconcile("execute", c.item, {})


@pytest.mark.parametrize("change", ["missing", "wrong_item", "contract", "untyped"])
def test_missing_or_untrusted_configuration_blocks_before_builder(entry, monkeypatch, change):
    c = entry
    if change == "missing":
        c.actions.candidate_reviews.clear()
    elif change == "wrong_item":
        c.item["id"] = "other-item"
    elif change == "contract":
        c.item["metadata"]["task_contract"]["summary"] = "changed goal"
    else:
        c.actions.candidate_reviews[c.item["id"]] = {"argv": ["echo", "untrusted"]}
    monkeypatch.setattr("goodteam_os.execution.orchestrator.ExecutionOrchestrator.run_execution",
                        lambda *_: pytest.fail("must not run Builder"))
    with pytest.raises(TaskInputError):
        c.actions._execute(c.item)
    assert not list(protected(c).glob("validation-started-*"))


def test_unknown_builder_is_not_replayed(entry, monkeypatch):
    c = entry
    monkeypatch.setattr("goodteam_os.execution.orchestrator.ExecutionOrchestrator.run_execution",
                        lambda *_: pytest.fail("must not retry unknown Builder"))
    assert c.actions.reconcile("execute", c.item, {}) is None


def test_missing_frozen_configuration_cannot_be_reconstructed_after_builder(entry):
    c = entry
    c.actions._execute(c.item)
    (protected(c) / "operator-candidate-configuration.json").unlink()
    with pytest.raises(TaskInputError, match="operator_candidate_configuration_missing"):
        c.actions._execute(c.item)
    with pytest.raises(TaskInputError, match="operator_candidate_configuration_missing"):
        c.actions.reconcile("execute", c.item, {})


def test_operator_service_accepts_only_explicit_startup_composition(entry):
    c = entry
    service = OperatorService(workspace=c.workspace, state_dir=c.operator,
                             candidate_reviews={c.item["id"]: c.config})
    assert service.actions.candidate_reviews[c.item["id"]] == c.config
    assert service.actions.review_runtime_kwargs == {}  # Selection grants no runtime authority.


def test_ready_backend_without_candidate_configuration_is_not_ready(entry, monkeypatch):
    c = entry
    c.actions.candidate_reviews.clear()
    c.item["metadata"].update(project_config=str(c.workspace / "projects" / "DEMO.yaml"), required_checks=["CI"])
    monkeypatch.setattr("goodteam_os.operator.load_project_target", lambda _: {})
    monkeypatch.setattr("goodteam_os.operator.worker_backend_doctor", lambda _: {"ready": True})
    assert c.actions._preflight(c.item) == {"ready": False, "reason": "operator_candidate_configuration_required"}


def test_setup_block_keeps_exact_reason_without_retrying(tmp_path):
    queue, _ = _queue(tmp_path)
    class Blocked(FakeActions):
        def perform(self, kind, item):
            if kind == "execute":
                raise TaskInputError("operator_candidate_configuration_missing")
            return super().perform(kind, item)
    machine = OperatorExecutionStateMachine(queue, Blocked())
    for _ in range(4):
        result = machine.tick()
    assert result["phase"] == "paused"
    assert result["metadata"]["pause_reason"] == "operator_candidate_configuration_missing"
    assert result["last_failure_class"] == "candidate_review_setup_block"


def test_configuration_frozen_before_builder_admission(entry, monkeypatch):
    c = entry
    def stop(_self, _execution):
        saved = _read(protected(c) / "operator-candidate-configuration.json")
        assert saved["snapshot"] == c.store.load(c.execution_id).reference
        assert saved["configuration"]["argv"] == list(c.config.argv)
        raise RuntimeError("stop before Builder")
    monkeypatch.setattr("goodteam_os.execution.orchestrator.ExecutionOrchestrator.run_execution", stop)
    with pytest.raises(RuntimeError, match="stop before Builder"):
        c.actions._execute(c.item)
