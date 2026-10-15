"""Actual offline report/Worker handoff, without paid or admitted live runtimes."""
from copy import deepcopy
import json

import pytest

from goodteam_os.execution.correction_feedback import render_correction_input
from goodteam_os.execution.correction_input import CorrectionAttemptInput
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes
from goodteam_os.skills.review_results import ReviewResultStore
from test_automatic_candidate_review import automatic as automatic_fixture, admit_correction  # noqa: F401
from test_candidate_validation import protected
from test_task_input_snapshot import ctx  # noqa: F401


def complete_report(value):
    value["checks"][0]["detail"] = "CHECK_RATIONALE_ONLY_IN_REPORT"
    value["evidence"][0]["observation"] = "SOURCE_OBSERVATION_ONLY_IN_REPORT"
    if value["findings"]:
        value["findings"][0].update(detail="FINDING_DETAIL_ONLY_IN_REPORT",
                                     recommendation="Preserve OLD_CONDITION. " + "Long remedy. " * 340)
        value["findings"].append({"id": "F2", "severity": "critical", "title": "LATE_CRITICAL_FINDING",
            "detail": "SECOND_DETAIL_REQUIRED", "recommendation": "SECOND_REMEDY_REQUIRED", "evidence_ids": ["E1"]})
    return value


def pending(c):
    c.runtime.transform = complete_report
    outcome = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert outcome["reason"] == "correction_verified_runtime_unavailable", outcome
    state = CorrectionLoopService(c.project)._load(c.store.load(c.execution_id))
    authority = CorrectionAttemptStore(c.project, state["rounds"][0]["worker_job_id"])
    return authority, authority.load()


def test_full_feedback_reaches_actual_fix_process_and_stays_pinned_after_recheck(request, monkeypatch):
    c = request.getfixturevalue("automatic_fixture")
    c.runtime.transform = complete_report
    tokens = ["FINDING_DETAIL_ONLY_IN_REPORT", "CHECK_RATIONALE_ONLY_IN_REPORT",
              "SOURCE_OBSERVATION_ONLY_IN_REPORT", "LATE_CRITICAL_FINDING", "SECOND_DETAIL_REQUIRED",
              "SECOND_REMEDY_REQUIRED", "head/feature.py", "VALUE = 'candidate'", "host_check_observation"]
    body = c.fix_script.read_text()
    c.fix_script.write_text(body.replace("prompt=sys.stdin.read()", "prompt=sys.stdin.read()\n"
        + "assert all(text in prompt for text in " + repr(tokens) + ")"))
    admit_correction(monkeypatch)
    result = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert result["status"] == "reviewed", result
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2
    state = CorrectionLoopService(c.project)._load(c.store.load(c.execution_id))
    authority = CorrectionAttemptStore(c.project, state["rounds"][0]["worker_job_id"])
    value = authority.load()
    assert "LATE_CRITICAL_FINDING" not in value.to_dict()["review"]["reason"]
    assert result["review"]["candidate"] != value.to_dict()["candidate"]
    before = {p: p.read_bytes() for p in c.operator.rglob('*') if p.is_file()}
    rendered = render_correction_input(c.project, value)
    assert all(token in rendered for token in tokens)
    assert before == {p: p.read_bytes() for p in c.operator.rglob('*') if p.is_file()}
    # Both references and original input stay byte-for-byte unchanged.
    assert authority.load().data == value.data
    assert authority.load().original.data == value.original.data
    assert not list(protected(c).glob("validation-review-*"))


@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_lost_retained_report_cannot_fall_back_to_summary(request, mutation):
    c = request.getfixturevalue("automatic_fixture")
    _authority, value = pending(c)
    reviewer = value.to_dict()["review"]["reviewer"]
    path = c.operator / "skills" / "review-results" / reviewer / "outcome.json"
    if mutation == "missing":
        path.unlink()
    else:
        content = json.loads(path.read_text())
        content["report"]["findings"][0]["detail"] = "Forged detail"
        path.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        render_correction_input(c.project, value)
    assert not c.effects.exists() and len(c.runtime.records) == 1


@pytest.mark.parametrize("field", ["validation_receipt_id", "receipt_id"])
def test_other_receipt_cannot_select_feedback(request, field):
    c = request.getfixturevalue("automatic_fixture")
    _authority, value = pending(c)
    changed = value.to_dict()
    changed["review"][field] = "not-the-captured-receipt"
    with pytest.raises(TaskInputError, match="correction_feedback_mismatch"):
        render_correction_input(c.project, CorrectionAttemptInput(value.original, canonical_bytes(changed)))
    assert not c.effects.exists()


def test_report_movement_during_second_read_is_rejected(request, monkeypatch):
    c = request.getfixturevalue("automatic_fixture")
    _authority, value = pending(c)
    original = ReviewResultStore.detail
    reads = []
    def moved(self, **kwargs):
        detail = original(self, **kwargs)
        reads.append(self.job_id)
        # The first read verifies the immutable machine receipt; the second is
        # the report being handed off. Neither may select different bytes.
        if len(reads) == 2:
            detail = deepcopy(detail)
            detail["report_digest"] = "sha256:" + "0" * 64
        return detail
    monkeypatch.setattr(ReviewResultStore, "detail", moved)
    with pytest.raises(TaskInputError, match="correction_feedback_mismatch"):
        render_correction_input(c.project, value)
    assert len(reads) == 2 and not c.effects.exists()


def test_oversize_report_stops_before_round_reservation_without_truncation(request, monkeypatch):
    c = request.getfixturevalue("automatic_fixture")
    c.runtime.transform = complete_report
    monkeypatch.setattr("goodteam_os.execution.correction_feedback.MAX_TASK_CONTEXT_BYTES", 8000)
    outcome = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert outcome["reason"] == "correction_context_size_limit", outcome
    state = CorrectionLoopService(c.project)._load(c.store.load(c.execution_id))
    assert state["rounds"] == []
    assert not list(protected(c).glob("correction-worker-*"))
    assert not list((c.project / ".goodteam" / "sandboxes").glob("job-correction-*"))
    assert not c.effects.exists() and len(c.runtime.records) == 1


def test_prompt_assembly_rechecks_context_budget(request, monkeypatch):
    c = request.getfixturevalue("automatic_fixture")
    _authority, value = pending(c)
    monkeypatch.setattr("goodteam_os.execution.correction_feedback.MAX_TASK_CONTEXT_BYTES", 8000)
    workers = c.auto.workers
    root = workers.root / value.job_id
    job = workers._read_json(root / "job.json")
    with pytest.raises(TaskInputError, match="correction_context_size_limit"):
        workers._worker_prompt(root, job)
    assert not c.effects.exists()
