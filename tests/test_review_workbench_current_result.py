"""Current-result handoff uses real protected checks, not an old ledger decision."""
from copy import deepcopy

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.review_workbench import ReviewWorkbenchService
from goodteam_os.execution.review_workbench_browser import _summary_section, render_review_workbench_html
from goodteam_os.execution.review_workbench_coverage import current_result_details
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from test_correction_loop import begin, check, fix_and_commit, loop_context as loop_fixture, review  # noqa: F401
from test_candidate_validation import candidate as candidate_fixture, ctx, protected, validate  # noqa: F401


@pytest.fixture(name="candidate")
def candidate_context(request):
    return request.getfixturevalue("candidate_fixture")


@pytest.fixture(name="correction")
def correction_context(request):
    return request.getfixturevalue("loop_fixture")


def current(c):
    return ExecutionAPI(c.project).get_review_workbench(c.execution_id)


def test_current_result_does_not_reuse_old_approval_after_real_fix(correction, tmp_path):
    c = correction
    ReviewWorkbenchService(c.project).record_decision(c.execution_id, "approve", "legacy-reviewer", "Old ledger note")
    begin(c)
    head = fix_and_commit(c, "fixed")
    receipt = check(c, head, "round-1")
    value = current(c)
    assert value["status"] == "pending" and value["reviewer_decision"]["decision"] == "approve"
    summary = _summary_section(value)
    assert head in summary and receipt["receipt_id"] in summary
    assert "Human record for this check</dt><dd>missing" in summary
    assert "Review this exact candidate and its fresh check receipt." in summary
    text = render_review_workbench(value).split("Supervised correction loop:")[0]
    assert head in text and receipt["receipt_id"] in text
    assert "Human record for this check: missing" in text
    assert "Acceptance: unknown" in text
    (tmp_path / "current-result-before-review.html").write_text(render_review_workbench_html(value))
    # Reopening through a fresh service reconstructs the same bound summary.
    assert _summary_section(current(c)) == summary


def details(c):
    return dict(current_result_details(current(c)["evidence"]))


def test_no_check_does_not_treat_finished_worker_as_delivery(candidate):
    value = current(candidate)
    value["status"] = "completed"
    result = dict(current_result_details(value["evidence"]))
    assert result["Candidate checks"] == "unknown"
    assert result["Check receipt"] == "unavailable"
    assert "no check is recorded" in result["Next action"]
    assert result["Acceptance"].startswith("unknown")
    html = render_review_workbench_html(value)
    assert "Recorded execution: completed" in html


def test_approval_reopen_and_later_dirty_candidate_are_distinct(correction, tmp_path):
    c = correction
    begin(c)
    result = check(c, fix_and_commit(c, "fixed"), "round-1")
    review(c, result, "approve")
    ready = details(c)
    assert ready["Human record for this check"] == "approve"
    assert ready["Candidate checks"] == "passed"
    assert ready["Correction state"] == "awaiting_human_acceptance"
    assert ready["Acceptance"].startswith("unknown")
    assert "person must decide acceptance" in ready["Next action"]
    (tmp_path / "current-result-human-handoff.html").write_text(render_review_workbench_html(current(c)))
    (c.project / "feature.py").write_text("VALUE = 'later unreviewed edit'\n")
    dirty = details(c)
    assert dirty["Candidate checks"] == "unknown"
    assert dirty["Human record for this check"] == "approve (historical; current applicability unknown)"
    assert dirty["Correction state"] == "needs_human"
    assert "Inspect the preserved evidence" in dirty["Next action"]
    (tmp_path / "current-result-dirty.html").write_text(render_review_workbench_html(current(c)))


def test_new_receipt_does_not_inherit_previous_human_record(candidate):
    c = candidate
    old = validate(c)
    c.validation.attest_review(c.execution_id, receipt_id=old["receipt_id"], head_sha=c.head,
                              reviewer="human", decision="approve", reason="Earlier check reviewed")
    new = validate(c, operation_id="new-check", criteria=[1])
    result = details(c)
    assert result["Check receipt"] == new["receipt_id"]
    assert result["Human record for this check"] == "missing"
    assert "Review this exact candidate" in result["Next action"]


def test_stopped_loop_keeps_its_stop_and_does_not_suggest_another_worker(correction):
    c = correction
    opened = begin(c)
    c.loop.stop(c.execution_id, loop_id=opened["loop_id"])
    result = details(c)
    assert result["Correction state"] == "needs_human"
    assert result["Correction limitation"] == "correction_stopped_by_operator"
    assert "no automatic retry" in result["Next action"]
    assert result["Acceptance"].startswith("unknown")


@pytest.mark.parametrize("lost", ["snapshot.json", "receipts.json"])
def test_missing_protected_evidence_never_promotes_legacy_metadata(candidate, lost):
    c = candidate
    validate(c)
    (protected(c) / lost).unlink()
    value = current(c)
    value["status"] = "completed"
    value["reviewer_decision"] = {"decision": "approve", "reviewer": "legacy"}
    result = dict(current_result_details(value["evidence"]))
    assert result["Latest checked head"] == "unavailable"
    assert result["Candidate checks"] == "unknown"
    assert result["Human record for this check"] == "missing"
    assert result["Acceptance"].startswith("unknown")


@pytest.mark.parametrize("change", ["snapshot", "receipt_candidate", "loop_candidate", "human_receipt", "human_candidate"])
def test_mixed_version_observations_cannot_create_current_handoff(correction, change):
    c = correction
    begin(c)
    result = check(c, fix_and_commit(c, "fixed"), "round-1")
    review(c, result, "approve")
    evidence = deepcopy(current(c)["evidence"])
    validation = evidence["candidate_validation"]
    if change == "snapshot":
        validation["receipt"]["snapshot_digest"] = "f" * 64
    elif change == "receipt_candidate":
        validation["receipt"]["candidate"]["head_sha"] = "f" * 40
    elif change == "loop_candidate":
        evidence["correction_loop"]["candidate"]["head_sha"] = "f" * 40
    elif change == "human_receipt":
        validation["review"]["validation_receipt_id"] = "validation:old"
    else:
        validation["review"]["candidate"]["head_sha"] = "f" * 40
    before = deepcopy(evidence)
    output = dict(current_result_details(evidence))
    assert evidence == before
    assert "observations do not match" in output["Next action"]
    if change in {"human_receipt", "human_candidate"}:
        assert output["Human record for this check"] == "missing"
    assert output["Acceptance"].startswith("unknown")


def test_failed_check_approval_is_unresolved_not_accepted(candidate):
    c = candidate
    import sys
    result = validate(c, argv=[sys.executable, "-c", "raise SystemExit(3)"])
    c.validation.attest_review(c.execution_id, receipt_id=result["receipt_id"], head_sha=c.head,
                              reviewer="human", decision="approve", reason="Recorded despite check failure")
    output = details(c)
    assert output["Candidate checks"] == "failed"
    assert output["Human record for this check"] == "approve"
    assert "failed check" in output["Next action"] and "unresolved" in output["Next action"]


def test_unavailable_loop_cannot_suggest_starting_again(candidate):
    c = candidate
    validate(c)
    evidence = current(c)["evidence"]
    evidence["correction_loop"] = {"available": False, "status": "needs_human",
                                   "reason": "correction_state_unavailable"}
    output = dict(current_result_details(evidence))
    assert output["Correction limitation"] == "correction_state_unavailable"
    assert "do not repeat" in output["Next action"]


def test_current_result_escapes_source_feedback_without_creating_actions(candidate):
    value = current(candidate)
    value["evidence"]["correction_loop"] = {"available": False, "status": "needs_human",
        "reason": '<img src=x onerror="UNTRUSTED_FEEDBACK">'}
    section = _summary_section(value)
    assert '<img' not in section and '&lt;img' in section
    assert '<button' not in section and '<form' not in section


def test_manual_committed_fix_preserves_explicit_recheck_handoff(correction):
    c = correction
    begin(c)
    fix_and_commit(c, "fixed")
    result = details(c)
    assert result["Candidate checks"] == "unknown"
    assert result["Human record for this check"].startswith("request_changes (historical;")
    assert result["Correction state"] == "awaiting_fix"
    assert "explicitly rerun the same check" in result["Next action"]
    assert "observations do not match" not in result["Next action"]
    assert details(c) == result


def test_failed_recheck_with_bound_approval_still_requires_resolution(correction):
    c = correction
    begin(c)
    result = check(c, fix_and_commit(c, "still failing"), "failed-round")
    review(c, result, "approve")
    output = details(c)
    assert output["Correction state"] == "awaiting_human_acceptance"
    assert output["Candidate checks"] == "failed"
    assert output["Human record for this check"] == "approve"
    assert "failed check" in output["Next action"] and "unresolved" in output["Next action"]
