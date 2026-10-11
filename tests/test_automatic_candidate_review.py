"""Real registered Reviewer producer/consumer and Git, with offline subprocesses."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
import sys

import pytest

from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_validation import CandidateValidationService, _read, _write
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills import review_execution
from goodteam_os.skills.review_process import ReviewProcess
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore
from test_candidate_validation import git, protected
from test_skill_content import FixtureAuthority
from test_skill_review_results import channel, report
from test_task_input_snapshot import ctx, start  # noqa: F401


class CandidateRuntime(FixtureAuthority):
    def __init__(self, root):
        super().__init__()
        self.root, self.records, self.transform, self.body = root, [], lambda x: x, ""

    @contextmanager
    def execution(self, record, provider, ref):
        stage = self.root / ("candidate-review-" + str(len(self.records)))
        self.records.append(deepcopy(record))
        prompt = review_execution.materialize_review_inputs(provider, ref, record, stage)
        source = record["sources"]["head/feature.py"]
        # Criterion names are read from actual immutable request context.
        payload = json.loads((stage / "context.md").read_text()) if (stage / "context.md").exists() else None
        if payload is None:
            # The materialized fixture's prompt contains the original request;
            # get the same controller-captured bytes from provider input.
            from goodteam_os.skills.worker_requests import _services
            snapshot = _services(provider.workers)[2].find_snapshot(command_key=provider.job_id)["snapshot"]
            payload = json.loads(snapshot["job_input"]["request"]["context"])
        ids = payload["policy"]["criterion_ids"]
        value = report(evidence=[{"id": "E1", "path": "head/feature.py", "line_start": 1, "line_end": 1,
            "quote": source.splitlines()[0], "observation": "Exact current source"}],
            checks=[{"name": name, "status": "checked", "evidence_ids": ["E1"], "detail": "Assessed original requirement"} for name in ids])
        if "'fixed'" not in source:
            value["findings"] = [{"id": "F1", "severity": "medium", "title": "Fix VALUE", "detail": "Condition unmet",
                                   "evidence_ids": ["E1"], "recommendation": "Set VALUE to fixed; preserve OLD_CONDITION"}]
        value = self.transform(value)
        output = channel(value, thread=ref["context_id"]) if value is not None else b""
        script = "import sys\nassert sys.stdin.buffer.read()\n" + self.body + "\nsys.stdout.buffer.write(" + repr(output) + ")\n"
        yield ReviewProcess((sys.executable, "-I", "-c", script), stage, {}, 5), prompt

    def check_current(self, provider, ref):
        provider.private_input(ref)


@pytest.fixture
def automatic(request, monkeypatch):
    c = request.getfixturevalue("ctx")
    run = start(c, adapter="codex")
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
    c.argv = [sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    c.validation = CandidateValidationService(c.project)
    c.first = c.validation.validate(c.execution_id, operation_id="initial", head_sha=c.head, argv=c.argv, criteria=[0, 1])
    c.auto = AutomaticCandidateReviewService(c.project)
    c.auto.start(c.execution_id, base_sha=c.base, receipt_id=c.first["receipt_id"], head_sha=c.head,
                 reviewer_adapter="codex", reviewer_model="fixture-model", fix_seconds=30,
                 max_rounds=getattr(request, "param", 3))
    c.runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: c.runtime)
    monkeypatch.setattr(CandidateValidationService, "attest_review", lambda *a, **k: pytest.fail("automatic path must not attest"))
    c.effects = c.workspace / "correction-calls"
    script = c.workspace / "fix.py"
    script.write_text("""import os,sys,subprocess
from pathlib import Path
prompt=sys.stdin.read()
assert 'OLD_CONDITION' in prompt and 'Fix VALUE' in prompt
Path(os.environ['CALLS']).open('a').write('call\\n')
Path('feature.py').write_text("VALUE = 'fixed'\\n")
subprocess.run(['git','add','feature.py'],check=True)
subprocess.run(['git','commit','-qm','Offline fix'],check=True)
print('Corrected')
""")
    c.fix_script = script
    env = {"PATH": os.defpath, "HOME": str(c.workspace / "empty-home"), "CALLS": str(c.effects),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture",
           "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    def offline(store, job_root, job, spec):
        return store._run_cli_worker(job_root, job, command=[sys.executable, str(script)], model="offline",
            preflight={"available": True, "ready": True}, label="Offline fixture", capture="stdout",
            summarize=lambda *_: "Offline returned", worker_env=env, timeout_seconds=5)
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    return c


def admit_correction(monkeypatch):
    monkeypatch.setattr("goodteam_os.execution.correction_input.correction_runtime_capability",
        lambda _: {"available": True, "status": "offline_test_composition", "reason": "synthetic_fixture"})


def test_findings_fix_fresh_check_and_report_without_manual_approval(automatic, monkeypatch):
    c = automatic
    admit_correction(monkeypatch)
    original = c.store.load(c.execution_id).data
    result = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert result["status"] == "reviewed", result
    assert result["acceptance_status"] == "accepted_by_review_policy"
    assert len(c.runtime.records) == 2
    assert c.effects.read_text() == "call\n"
    evidence = c.validation.evidence(c.execution_id)
    assert evidence["review_status"] == "machine_recorded"
    assert evidence["review"]["provenance"]["runtime_verification"] == "contract_fixture"
    assert evidence["review"]["base_sha"] == c.base
    assert evidence["review"]["candidate"]["head_sha"] != c.head
    assert evidence["review"]["provenance"]["methods"] == []
    assert not list(protected(c).glob("validation-review-*.json"))
    assert c.store.load(c.execution_id).data == original
    assert git(c.project, "rev-parse", "HEAD") == c.head
    replay = AutomaticCandidateReviewService(c.project).advance(c.execution_id, acknowledge_external_cost=True)
    assert replay["status"] == "reviewed", replay
    assert len(c.runtime.records) == 2 and c.effects.read_text() == "call\n"
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["reserved_rounds"] == 1


def test_external_correction_admission_stays_blocked(automatic):
    result = automatic.auto.advance(automatic.execution_id, acknowledge_external_cost=True)
    assert result["status"] == "blocked" and result["reason"] == "correction_verified_runtime_unavailable", result
    assert len(automatic.runtime.records) == 1 and not automatic.effects.exists()


def passed_candidate(c):
    (c.project / "feature.py").write_text("VALUE = 'fixed'\n")
    git(c.project, "commit", "-qam", "Already fixed")
    c.head = git(c.project, "rev-parse", "HEAD")
    return c.validation.validate(c.execution_id, operation_id="passed", head_sha=c.head, argv=c.argv, criteria=[0, 1])


@pytest.mark.parametrize("kind", ["missing", "duplicate", "inconclusive", "empty", "malformed"])
def test_coverage_and_empty_process_never_accept(automatic, kind):
    c = automatic
    passed_candidate(c)
    def transform(value):
        if kind == "missing":
            value["checks"].pop()
        elif kind == "duplicate":
            value["checks"].append(value["checks"][0])
        elif kind == "inconclusive":
            value["checks"][0]["status"] = "inconclusive"
        elif kind == "empty":
            return None
        else:
            value["summary"] = ""
        return value
    c.runtime.transform = transform
    result = c.auto.advance(c.execution_id)
    assert result["status"] in {"inconclusive", "blocked"}, result
    assert c.validation.evidence(c.execution_id)["acceptance_status"] == "unknown"
    c.auto.advance(c.execution_id)
    assert len(c.runtime.records) == 1


def test_same_sha_different_receipt_requires_fresh_reviewer(automatic):
    c = automatic
    first = passed_candidate(c)
    assert c.auto.review_current(c.execution_id)["decision"] == "approve"
    second = c.validation.validate(c.execution_id, operation_id="same-sha-new-check", head_sha=c.head, argv=c.argv, criteria=[0, 1])
    assert first["receipt_id"] != second["receipt_id"]
    assert c.auto.review_current(c.execution_id)["decision"] == "approve"
    assert len(c.runtime.records) == 2
    assert c.runtime.records[0]["context_ref"] != c.runtime.records[1]["context_ref"]


def test_replay_different_policy_or_base_cannot_reset_budget(automatic):
    c = automatic
    with pytest.raises(TaskInputError, match="already_started"):
        c.auto.start(c.execution_id, base_sha=c.base, receipt_id=c.first["receipt_id"], head_sha=c.head,
                     reviewer_adapter="codex", reviewer_model="fixture-model", max_rounds=5)
    with pytest.raises(TaskInputError, match="already_started"):
        c.auto.start(c.execution_id, base_sha=c.head, receipt_id=c.first["receipt_id"], head_sha=c.head,
                     reviewer_adapter="codex", reviewer_model="fixture-model", fix_seconds=30)


def test_late_result_after_stop_cannot_trigger_fix(automatic, monkeypatch):
    c = automatic
    original = c.auto.workers.run_job
    def stop_after(job_id):
        result = original(job_id)
        c.auto.stop(c.execution_id)
        return result
    monkeypatch.setattr(c.auto.workers, "run_job", stop_after)
    result = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert result["reason"] == "correction_stopped_by_operator", result
    assert not c.effects.exists() and not list(protected(c).glob("validation-machine-review-*"))


def test_machine_report_receipt_tamper_is_not_authority(automatic):
    c = automatic
    passed_candidate(c)
    c.auto.review_current(c.execution_id)
    path = next(protected(c).glob("validation-machine-review-*"))
    value = _read(path)
    value["decision"] = "request_changes"
    _write(path, value)
    assert c.validation.evidence(c.execution_id)["available"] is False


def test_terminal_without_loop_survives_deadline_and_never_readmits(automatic, monkeypatch):
    c = automatic
    passed_candidate(c)
    assert c.auto.advance(c.execution_id)["status"] == "reviewed"
    start = _read(protected(c) / "automatic-review-start.json")
    reopened = AutomaticCandidateReviewService(c.project, clock=lambda: start["started_at"] + 99999)
    monkeypatch.setattr(reopened.workers, "run_job", lambda *_: pytest.fail("completed receipt must not dispatch"))
    assert reopened.advance(c.execution_id)["status"] == "reviewed"
    assert len(c.runtime.records) == 1


@pytest.mark.parametrize("change", ["expired", "rollback"])
def test_budget_stop_is_latched_after_clock_changes(automatic, change):
    c = automatic
    start = _read(protected(c) / "automatic-review-start.json")
    c.auto.clock = lambda: start["started_at"] + (99999 if change == "expired" else -1)
    assert c.auto.advance(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    c.auto.clock = lambda: start["started_at"] + 1
    assert c.auto.advance(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    assert not c.runtime.records


def test_late_review_after_expiry_is_not_consumed(automatic, monkeypatch):
    c = automatic
    start = _read(protected(c) / "automatic-review-start.json")
    original = c.auto.workers.run_job
    def expire_after(job_id):
        result = original(job_id)
        c.auto.clock = lambda: start["started_at"] + 99999
        return result
    monkeypatch.setattr(c.auto.workers, "run_job", expire_after)
    assert c.auto.advance(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    assert not list(protected(c).glob("validation-machine-review-*"))


@pytest.mark.parametrize("kind", ["candidate", "receipt", "task", "project", "lock", "criteria", "method"])
def test_cross_binding_and_source_changes_never_reuse_review(automatic, kind):
    c = automatic
    passed_candidate(c)
    c.auto.review_current(c.execution_id)
    if kind == "candidate":
        (c.project / "feature.py").write_text("VALUE = 'drift'\n")
        assert c.auto.advance(c.execution_id)["reason"] == "validation_worktree_dirty"
        return
    path = next(protected(c).glob("validation-machine-review-*"))
    value = _read(path)
    if kind == "receipt":
        value["validation_receipt_id"] = c.first["receipt_id"]
    elif kind in {"task", "project", "lock"}:
        context = value["provenance"]["task_skill_context"]
        if kind == "task":
            context["task"]["logical_id"] = "another-task"
        elif kind == "project":
            context["task"]["project_key"] = "OTHER"
        else:
            context["task_lock_ref"]["digest"] = "sha256:" + "f" * 64
    elif kind == "criteria":
        value["policy"]["criterion_ids"].pop()
    else:
        value["provenance"]["methods"].append({"version": "fake"})
    from goodteam_os.execution.candidate_validation import _bound
    _write(path, _bound(value))
    assert c.validation.evidence(c.execution_id)["available"] is False


def test_changed_validation_contract_cannot_weaken_original(automatic):
    c = automatic
    c.validation.validate(c.execution_id, operation_id="weaker", head_sha=c.head,
                          argv=[sys.executable, "-c", "pass"], criteria=[0])
    assert c.auto.advance(c.execution_id)["reason"] == "correction_check_changed"
    assert not c.runtime.records


def test_missing_lock_does_not_get_recreated_by_evidence(automatic, monkeypatch):
    c = automatic
    passed_candidate(c)
    c.auto.review_current(c.execution_id)
    from goodteam_os.skills.worker_requests import _services
    _root, _operator, locks, _availability = _services(c.auto.workers)
    lock = _read(protected(c) / "automatic-review-binding.json")["task_skill_context"]["task_lock_ref"]
    root = locks.root
    candidates = list(root.rglob(lock["digest"][7:] + ".json"))
    assert len(candidates) == 1
    candidates[0].unlink()
    monkeypatch.setattr("goodteam_os.execution.automatic_review.capture_task_start",
                        lambda **_: pytest.fail("reads must never repair authority"))
    assert c.validation.evidence(c.execution_id)["available"] is False
    assert not candidates[0].exists()


def test_linked_correction_worktree_drift_invalidates_reviewed_result(automatic, monkeypatch):
    c = automatic
    admit_correction(monkeypatch)
    assert c.auto.advance(c.execution_id, acknowledge_external_cost=True)["status"] == "reviewed"
    loop = CorrectionLoopService(c.project).evidence(c.execution_id)
    authority = CorrectionAttemptStore(c.project, loop["worker_job_id"])
    workspace = authority.workspace(authority.load())
    (workspace / "feature.py").write_text("VALUE = 'drift'\n")
    assert c.auto.advance(c.execution_id)["reason"] == "validation_worktree_dirty"
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["status"] == "needs_human"


def test_concurrent_advance_dispatches_each_worker_once(automatic, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    c = automatic
    admit_correction(monkeypatch)
    def advance():
        return AutomaticCandidateReviewService(c.project).advance(c.execution_id, acknowledge_external_cost=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: advance(), range(2)))
    assert all(row["status"] == "reviewed" for row in outcomes), outcomes
    assert len(c.runtime.records) == 2 and c.effects.read_text() == "call\n"


def test_old_head_result_cannot_publish_over_new_candidate(automatic, monkeypatch):
    c = automatic
    original = c.auto.workers.run_job
    def move_after(job_id):
        result = original(job_id)
        (c.project / "feature.py").write_text("VALUE = 'newer'\n")
        git(c.project, "commit", "-qam", "Later candidate")
        return result
    monkeypatch.setattr(c.auto.workers, "run_job", move_after)
    assert c.auto.advance(c.execution_id)["reason"] == "validation_head_changed"
    assert not list(protected(c).glob("validation-machine-review-*"))


def test_unknown_reviewer_attempt_never_reinvokes(automatic):
    from goodteam_os.skills.content import SkillContextProvider
    from goodteam_os.skills.review_results import ReviewResultStore
    c = automatic
    snapshot = c.validation._snapshot(c.execution_id)
    start, context = c.auto._load(snapshot)
    plan, receipt, _ = c.validation._current(snapshot)
    job = c.auto._registered_job(snapshot, start, context, plan, receipt, create=True)
    provider = SkillContextProvider(c.auto.workers, job, authority=FixtureAuthority())
    ref = provider.prepare("interrupted-review")
    results = ReviewResultStore(c.auto.workers, job)
    results.begin(provider, ref)
    results.mark_running()
    assert c.auto.advance(c.execution_id)["status"] == "blocked"
    assert c.auto.advance(c.execution_id)["status"] == "blocked"
    assert not c.runtime.records and not c.effects.exists()


def test_cancelled_reviewer_result_cannot_drive_correction(automatic, monkeypatch):
    from goodteam_os.skills.review_cancel import request_cancellation
    from goodteam_os.skills.review_results import ReviewResultStore
    c = automatic
    original = c.runtime.execution
    @contextmanager
    def cancel(record, provider, ref):
        results = ReviewResultStore(provider.workers, provider.job_id)
        request_cancellation(results, {"snapshot_digest": record["snapshot_ref"]["digest"],
                             "context_digest": ref["digest"]}, check_task=lambda: None)
        with original(record, provider, ref) as value:
            yield value
    monkeypatch.setattr(c.runtime, "execution", cancel)
    assert c.auto.advance(c.execution_id)["status"] == "blocked"
    assert not c.effects.exists()
    assert c.validation.evidence(c.execution_id)["acceptance_status"] == "unknown"


@pytest.mark.parametrize("automatic", [1], indirect=True)
def test_automatic_round_budget_preserved_on_restart(automatic, monkeypatch):
    c = automatic
    admit_correction(monkeypatch)
    c.fix_script.write_text(c.fix_script.read_text().replace("VALUE = 'fixed'", "VALUE = 'still-wrong'"))
    result = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert result["reason"] == "correction_round_budget_exhausted", result
    reopened = AutomaticCandidateReviewService(c.project).advance(c.execution_id, acknowledge_external_cost=True)
    assert reopened["reason"] == "correction_round_budget_exhausted", reopened
    assert c.effects.read_text() == "call\n"
    assert len(c.runtime.records) == 2


def test_application_api_drives_and_projects_exact_execution(automatic, monkeypatch):
    from goodteam_os.execution.api import ExecutionAPI
    c = automatic
    admit_correction(monkeypatch)
    api = ExecutionAPI(c.project)
    prepared = api.start_automatic_review(c.execution_id, base_sha=c.base, receipt_id=c.first["receipt_id"],
        head_sha=c.head, reviewer_adapter="codex", reviewer_model="fixture-model", fix_seconds=30)
    assert prepared["status"] == "prepared"
    assert api.advance_automatic_review(c.execution_id, acknowledge_external_cost=True)["status"] == "reviewed"
    evidence = api.get_evidence(c.execution_id)
    assert evidence["candidate_validation"]["acceptance_status"] == "accepted_by_review_policy"
    assert evidence["correction_loop"]["status"] == "reviewed"
    assert evidence["correction_loop"]["automatic_provider_invocations"] is None
    assert evidence["correction_loop"]["validation_receipt_id"] == evidence["candidate_validation"]["receipt"]["receipt_id"]
    assert api.stop_automatic_review(c.execution_id)["reason"] == "correction_stopped_by_operator"


@pytest.mark.parametrize("proof", ["review", "committed"])
def test_partial_machine_proof_loss_fails_closed(automatic, proof):
    c = automatic
    passed_candidate(c)
    c.auto.review_current(c.execution_id)
    path = next(protected(c).glob("validation-machine-" + proof + "-*.json"))
    path.unlink()
    assert c.validation.evidence(c.execution_id)["available"] is False
    assert c.auto.advance(c.execution_id)["status"] == "blocked"
    assert len(c.runtime.records) == 1 and not path.exists()


@pytest.mark.parametrize("reason", ["stop", "deadline", "unknown"])
def test_preloop_blockers_survive_reopen_in_same_execution_projection(automatic, monkeypatch, reason):
    from goodteam_os.execution.api import ExecutionAPI
    from goodteam_os.execution.review_workbench_coverage import current_result_details
    c = automatic
    if reason == "stop":
        c.auto.stop(c.execution_id)
        expected = "correction_stopped_by_operator"
    elif reason == "deadline":
        start = _read(protected(c) / "automatic-review-start.json")
        c.auto.clock = lambda: start["started_at"] + 99999
        expected = "correction_time_budget_exhausted"
    else:
        c.runtime.transform = lambda _: None
        expected = "review_capture_incomplete"
    outcome = c.auto.advance(c.execution_id)
    assert outcome["status"] == "blocked"
    if reason == "unknown":
        expected = outcome["reason"]
    monkeypatch.setattr("goodteam_os.execution.automatic_review._write", lambda *_: pytest.fail("projection cannot write"))
    monkeypatch.setattr(WorkerJobStore, "run_job", lambda *_: pytest.fail("projection cannot dispatch"))
    evidence = ExecutionAPI(c.project).get_evidence(c.execution_id)
    rows = dict(current_result_details(evidence))
    assert rows["Correction state"] == "needs_human", rows
    assert rows["Correction limitation"] == expected, rows
    assert rows["Acceptance"].startswith("unknown")
    from goodteam_os.execution.review_workbench_renderer import render_review_workbench
    from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    assert expected in render_review_workbench(workbench)
    assert expected in render_review_workbench_html(workbench)


def test_stop_after_prepared_worker_projects_halt_without_mutation(automatic, monkeypatch):
    from goodteam_os.execution.api import ExecutionAPI
    from goodteam_os.execution.review_workbench_coverage import current_result_details
    c = automatic
    assert c.auto.advance(c.execution_id, acknowledge_external_cost=True)["reason"] == "correction_verified_runtime_unavailable"
    c.auto.stop(c.execution_id)
    monkeypatch.setattr("goodteam_os.execution.automatic_review._write", lambda *_: pytest.fail("projection cannot write"))
    monkeypatch.setattr("goodteam_os.execution.correction_loop._write", lambda *_: pytest.fail("halt overlay must be read-only"))
    rows = dict(current_result_details(ExecutionAPI(c.project).get_evidence(c.execution_id)))
    assert rows["Correction state"] == "needs_human"
    assert rows["Correction limitation"] == "correction_stopped_by_operator"


@pytest.mark.parametrize("automatic", [1], indirect=True)
def test_incompatible_existing_loop_cannot_expand_frozen_budget(automatic, monkeypatch):
    c = automatic
    admit_correction(monkeypatch)
    assert c.auto.review_current(c.execution_id)["decision"] == "request_changes"
    CorrectionLoopService(c.project).start(c.execution_id, receipt_id=c.first["receipt_id"], head_sha=c.head,
                                           max_rounds=3, wall_seconds=900)
    assert c.auto.advance(c.execution_id, acknowledge_external_cost=True)["reason"] == "automatic_review_loop_incompatible"
    assert not c.effects.exists()
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["reserved_rounds"] == 0


@pytest.mark.parametrize("control", ["stop", "halt"])
def test_automatic_stop_and_halt_block_existing_worker_dispatch(automatic, monkeypatch, control):
    from goodteam_os.execution.correction_worker import CorrectionWorkerService
    c = automatic
    assert c.auto.advance(c.execution_id, acknowledge_external_cost=True)["reason"] == "correction_verified_runtime_unavailable"
    job_id = CorrectionLoopService(c.project).evidence(c.execution_id)["worker_job_id"]
    if control == "stop":
        c.auto.stop(c.execution_id)
        reason = "correction_stopped_by_operator"
    else:
        start = _read(protected(c) / "automatic-review-start.json")
        c.auto.clock = lambda: start["started_at"] + 99999
        c.auto.advance(c.execution_id)
        reason = "correction_time_budget_exhausted"
    admit_correction(monkeypatch)
    with pytest.raises(TaskInputError, match=reason):
        CorrectionWorkerService(c.project).run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert not c.effects.exists()


@pytest.mark.parametrize("state", ["prepared", "reviewed"])
def test_no_correction_automatic_projection_supports_complete_workbench(automatic, state):
    from goodteam_os.execution.api import ExecutionAPI
    from goodteam_os.execution.review_workbench_renderer import render_review_workbench
    from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
    c = automatic
    if state == "reviewed":
        passed_candidate(c)
        assert c.auto.advance(c.execution_id)["status"] == "reviewed"
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    assert workbench["evidence"]["correction_loop"]["completed_rounds"] == 0
    assert "Feedback:" in render_review_workbench(workbench)
    assert "Feedback:" in render_review_workbench_html(workbench)
