"""Crash/retirement boundary before the native Reviewer has a report attempt.

Admission and Docker below are explicitly model-free fixtures. These tests
prove reservation ownership/replay behavior, not native or provider conformance.
"""
import pytest

from goodteam_os.skills import codex_container
from goodteam_os.skills.models import SkillError
from goodteam_os.workers import WorkerJobStore
from test_codex_review_runtime import AdmissionFixture, ScriptedEngine
from test_skill_review_execution import ready_job
from test_task_skill_entrypoints import ctx as ctx


def test_failed_preparation_with_unknown_retirement_cannot_acquire_again_after_restart(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    admission = AdmissionFixture(fail_check=True, fail_finish=True)
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    first = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert first["skill_error_code"] == "review_termination_unknown"
    admission.fail_check = admission.fail_finish = False
    replay = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert len(admission.acquires) == 1, "A second provider reservation was acquired before the first retired"
    assert replay["skill_error_code"] == "review_termination_unknown"
    assert replay["skill_runtime_status"] == "recovery_required"
    assert engine.attaches == 0


def test_acquire_exception_does_not_prove_no_external_reservation(ctx, monkeypatch):
    job, *_ = ready_job(ctx)

    class InterruptedAcquire(AdmissionFixture):
        def acquire(self, snapshot, *, context_id):
            self.acquires.append(context_id)
            raise SkillError("provider_acquire_interrupted", "The provider reply was lost after reservation.")

    admission = InterruptedAcquire()
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No admitted lease"))
    first = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert first["skill_error_code"] == "review_termination_unknown"
    replay = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert replay["skill_runtime_status"] == "recovery_required"
    assert len(admission.acquires) == 1


def test_confirmed_unstarted_retirement_allows_explicit_replay(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    admission = AdmissionFixture(fail_check=True)
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    first = workers.run_job(job["id"])
    assert first["skill_error_code"] == "fixture_admission_denied"
    assert admission.finishes == [(admission.acquires[0], True)]
    admission.fail_check = False
    second = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert second["status"] == "review_received", second
    assert len(admission.acquires) == 2 and engine.attaches == 1


def test_crash_after_acquisition_never_relaunches_without_a_saved_attempt(ctx, monkeypatch):
    job, *_ = ready_job(ctx)

    class SimulatedCrash(BaseException):
        pass

    class CrashingAdmission(AdmissionFixture):
        def check(self, *args, **kwargs):
            raise SimulatedCrash()

    admission = CrashingAdmission()
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No process was admitted"))
    with pytest.raises(SimulatedCrash):
        WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    # A restart with default construction must preserve unknown state too; it
    # cannot overwrite it with the old pre-invocation/not-connected projection.
    replay = WorkerJobStore(ctx.repo).run_job(job["id"])
    assert replay["skill_error_code"] == "review_termination_unknown"
    assert replay["skill_runtime_status"] == "recovery_required"
    assert len(admission.acquires) == 1 and admission.finishes == []


def test_overlapping_dispatch_cannot_borrow_or_retire_first_preparation(ctx, monkeypatch):
    job, *_ = ready_job(ctx)

    class OverlappingAdmission(AdmissionFixture):
        def acquire(self, snapshot, *, context_id):
            lease = super().acquire(snapshot, context_id=context_id)
            if len(self.acquires) == 1:
                self.overlap = WorkerJobStore(ctx.repo, codex_review_admission=self).run_job(job["id"])
            return lease

    admission = OverlappingAdmission()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["status"] == "review_received", result
    assert admission.overlap["skill_error_code"] == "review_termination_unknown"
    assert len(admission.acquires) == 1
    assert admission.finishes == [(admission.acquires[0], True)]
    assert engine.attaches == 1


def test_unknown_preparation_cannot_be_retried_as_a_new_worker(ctx, monkeypatch):
    from goodteam_os.skills import worker_requests
    from goodteam_os.skills.review_results import ReviewResultStore

    job, *_ = ready_job(ctx)
    admission = AdmissionFixture(fail_check=True, fail_finish=True)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No admitted process"))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    workers.run_job(job["id"])
    with pytest.raises(SkillError, match="Partial review preparation"):
        ReviewResultStore(workers, job["id"]).assert_retryable()
    with pytest.raises(SkillError):
        worker_requests.create_skill_reviewer_retry(workers, {"id": job["id"]}, retry_attempt=2,
                                                    idempotency_key="unknown-preparation-retry")
    assert len(admission.acquires) == 1
