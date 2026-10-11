"""Current-result handoff uses real protected checks, not an old ledger decision."""
from copy import deepcopy

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.review_workbench import ReviewWorkbenchService
from goodteam_os.execution.review_workbench_browser import _summary_section, render_review_workbench_html
from goodteam_os.execution.review_workbench_coverage import current_result_details
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from test_correction_loop import begin, check, fix_and_commit, loop_context as loop_fixture, review  # noqa: F401
from test_automatic_candidate_review import automatic as automatic_fixture, admit_correction  # noqa: F401
from test_candidate_validation import candidate as candidate_fixture, ctx, protected, validate  # noqa: F401


@pytest.fixture(name="candidate")
def candidate_context(request):
    return request.getfixturevalue("candidate_fixture")


@pytest.fixture(name="correction")
def correction_context(request):
    return request.getfixturevalue("loop_fixture")


@pytest.fixture(name="automatic")
def automatic_context(request):
    return request.getfixturevalue("automatic_fixture")


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


def test_optional_human_approval_reopen_and_later_dirty_candidate_are_distinct(correction, tmp_path):
    c = correction
    begin(c)
    result = check(c, fix_and_commit(c, "fixed"), "round-1")
    review(c, result, "approve")
    ready = details(c)
    assert ready["Human record for this check"] == "approve"
    assert ready["Candidate checks"] == "passed"
    assert ready["Correction state"] == "reviewed"
    assert ready["Acceptance"] == "human_recorded"
    assert ready["Semantic verification"] == "judgment_not_independently_verified"
    assert "person must decide acceptance" not in ready["Next action"]
    (tmp_path / "current-result-optional-human-record.html").write_text(render_review_workbench_html(current(c)))
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
    assert output["Correction state"] == "needs_human"
    assert output["Candidate checks"] == "failed"
    assert output["Human record for this check"] == "approve"
    assert "failed check" in output["Next action"] and "unresolved" in output["Next action"]


def machine_evidence(c, *, decision="approve", check_status="passed", acceptance="accepted_by_review_policy"):
    """Projection fixture over a real check; this does not produce a trusted review."""
    validate(c)
    evidence = deepcopy(current(c)["evidence"])
    value = evidence["candidate_validation"]
    value["check_status"] = check_status
    value["acceptance_status"] = acceptance
    value["review_status"] = "machine_recorded"
    value["review"] = {
        "schema_version": "candidate_machine_review.v1", "producer": "automatic_candidate_review_controller",
        "receipt_id": "machine-review:example", "validation_receipt_id": value["receipt"]["receipt_id"],
        "candidate": deepcopy(value["candidate"]), "decision": decision,
        "snapshot": deepcopy(evidence["task_input"]["snapshot"]), "base_sha": c.head,
        "provenance": {"target": {"base_sha": c.head, "head_sha": value["candidate"]["head_sha"]},
                       "job_id": "synthetic-reviewer", "report_digest": "sha256:" + "1" * 64,
                       "runtime": {"adapter": "synthetic_read_only", "model": "synthetic"},
                       "runtime_verification": "simulated"},
        "reason": "Examined original task and exact candidate", "semantic_verification": "judgment_not_independently_verified",
    }
    return evidence


def test_machine_approval_projects_policy_acceptance_without_fabricating_human(candidate):
    c = candidate
    evidence = machine_evidence(c)
    before = deepcopy(evidence)
    output = dict(current_result_details(evidence))
    assert output["Candidate checks"] == "passed"
    assert output["Machine review for this check"] == "approve"
    assert output["Human record for this check"] == "missing"
    assert output["Acceptance"] == "accepted_by_review_policy"
    assert output["Review producer"] == "automatic_candidate_review_controller"
    assert output["Review receipt"] == "machine-review:example" and output["Review base"] == c.head
    assert output["Semantic verification"] == "judgment_not_independently_verified"
    assert "person must decide acceptance" not in output["Next action"]
    assert evidence == before


@pytest.mark.parametrize("check_status", ["failed", "unknown"])
def test_machine_approve_cannot_override_failed_or_unknown_checks(candidate, check_status):
    evidence = machine_evidence(candidate, check_status=check_status)
    output = dict(current_result_details(evidence))
    assert output["Candidate checks"] == check_status
    assert output["Acceptance"].startswith("unknown")
    assert output["Human record for this check"] == "missing"
    assert output["Machine review for this check"].startswith("approve")


@pytest.mark.parametrize("decision", ["request_changes", "inconclusive"])
def test_machine_nonapproval_cannot_inherit_policy_acceptance(candidate, decision):
    evidence = machine_evidence(candidate, decision=decision)
    output = dict(current_result_details(evidence))
    assert output["Machine review for this check"] == decision
    assert output["Acceptance"].startswith("unknown")
    if decision == "inconclusive":
        assert "did not establish" in output["Next action"]
    else:
        assert "recorded feedback" in output["Next action"]


@pytest.mark.parametrize("change", ["receipt", "candidate", "producer", "schema", "review_status", "snapshot", "base", "target", "acceptance"])
def test_machine_receipt_binding_and_producer_are_required(candidate, change):
    evidence = machine_evidence(candidate)
    value = evidence["candidate_validation"]
    if change == "receipt":
        value["review"]["validation_receipt_id"] = "validation:old"
    elif change == "candidate":
        value["review"]["candidate"]["head_sha"] = "f" * 40
    elif change == "producer":
        value["review"]["producer"] = "explicit_operator_cli"
    elif change == "schema":
        value["review"]["schema_version"] = "candidate_review_attestation.v1"
    elif change == "review_status":
        value["review_status"] = "operator_recorded"
    elif change == "snapshot":
        value["review"]["snapshot"]["execution_id"] = "other-execution"
    elif change == "base":
        value["review"]["base_sha"] = "f" * 40
    elif change == "target":
        value["review"]["provenance"]["target"]["head_sha"] = "f" * 40
    else:
        value["acceptance_status"] = "unknown"
    output = dict(current_result_details(evidence))
    assert output["Acceptance"].startswith("unknown")
    assert output["Human record for this check"] == "missing"
    if change != "acceptance":
        assert output["Machine review for this check"] == "missing"


def test_reviewed_loop_cannot_join_another_receipt_at_the_same_head(candidate):
    evidence = machine_evidence(candidate)
    evidence["correction_loop"] = {
        "available": True, "status": "reviewed", "candidate": deepcopy(evidence["candidate_validation"]["candidate"]),
        "validation_receipt_id": "validation:other-check", "acceptance_status": "accepted_by_review_policy",
        "next_action": "Candidate review policy is satisfied.",
    }
    output = dict(current_result_details(evidence))
    assert output["Acceptance"].startswith("unknown")
    assert "observations do not match" in output["Next action"]


def test_machine_receipt_rendering_is_read_only_and_does_not_fill_human_form(candidate):
    value = current(candidate)
    value["evidence"] = machine_evidence(candidate)
    html = render_review_workbench_html(value, decision_actions_enabled=True)
    text = render_review_workbench(value)
    assert "Machine decision: approve" in text and "Human decision: approve" not in text
    assert "Review producer: automatic_candidate_review_controller" in text
    assert "Acceptance: accepted_by_review_policy" in text
    assert "judgment_not_independently_verified" in html
    form = html.split('<form id="review-decision-form"', 1)[1].split('</form>', 1)[0]
    assert 'name="reviewer" value=""' in form
    assert "automatic_candidate_review_controller" not in form
    assert "Examined original task" not in form
    static = render_review_workbench_html(value)
    assert "fetch(" not in static


def test_machine_and_optional_human_records_remain_distinct(candidate):
    c = candidate
    evidence = machine_evidence(c)
    receipt = evidence["candidate_validation"]["receipt"]
    human = c.validation.attest_review(c.execution_id, receipt_id=receipt["receipt_id"], head_sha=c.head,
                                      reviewer="optional-human", decision="request_changes", reason="Human feedback")
    evidence["candidate_validation"]["human_review"] = human
    output = dict(current_result_details(evidence))
    assert output["Machine review for this check"] == "approve"
    assert output["Human record for this check"] == "request_changes"
    assert output["Acceptance"] == "accepted_by_review_policy"
    value = current(c)
    value["evidence"] = evidence
    html = render_review_workbench_html(value, decision_actions_enabled=True)
    form = html.split('<form id="review-decision-form"', 1)[1].split('</form>', 1)[0]
    assert 'name="reviewer" value="optional-human"' in form
    assert 'type="submit" disabled' in form
    assert "Optional human record receipt:" in render_review_workbench(value)


def test_inconclusive_review_is_coherent_with_awaiting_review_loop(candidate):
    evidence = machine_evidence(candidate, decision="inconclusive", acceptance="unknown")
    value = evidence["candidate_validation"]
    evidence["correction_loop"] = {
        "available": True, "status": "awaiting_review", "candidate": deepcopy(value["candidate"]),
        "validation_receipt_id": value["receipt"]["receipt_id"], "acceptance_status": "unknown",
        "next_action": "Review this exact candidate and its fresh check receipt.",
    }
    output = dict(current_result_details(evidence))
    assert output["Acceptance"].startswith("unknown")
    assert "observations do not match" not in output["Next action"]
    assert "did not establish" in output["Next action"]


def test_actual_machine_correction_result_reopens_with_producer_provenance(automatic, monkeypatch, tmp_path):
    c = automatic
    admit_correction(monkeypatch)
    result = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert result["status"] == "reviewed", result
    value = current(c)
    validation = value["evidence"]["candidate_validation"]
    output = dict(current_result_details(value["evidence"]))
    assert output["Candidate checks"] == "passed"
    assert output["Correction state"] == "reviewed"
    assert output["Acceptance"] == "accepted_by_review_policy"
    assert output["Machine review for this check"] == "approve"
    assert output["Human record for this check"] == "missing"
    assert output["Latest checked head"] == validation["candidate"]["head_sha"]
    assert output["Check receipt"] == validation["receipt"]["receipt_id"]
    assert output["Review base"] == c.base
    assert output["Review receipt"] == validation["review"]["receipt_id"]
    assert output["Semantic verification"] == "judgment_not_independently_verified"
    assert "observations do not match" not in output["Next action"]
    text = render_review_workbench(value)
    assert "Reviewer runtime verification: contract_fixture" in text
    assert validation["review"]["provenance"]["job_id"] in text
    assert validation["review"]["provenance"]["report_digest"] in text
    assert "Human decision: approve" not in text
    html = render_review_workbench_html(value, decision_actions_enabled=True)
    assert 'data-action="stop"' not in html
    form = html.split('<form id="review-decision-form"', 1)[1].split('</form>', 1)[0]
    assert 'name="reviewer" value=""' in form
    assert 'type="submit" disabled' not in form
    (tmp_path / "actual-automatic-review-result.html").write_text(render_review_workbench_html(value))
    assert details(c) == output
    assert len(c.runtime.records) == 2 and c.effects.read_text() == "call\n"


def test_machine_detail_section_cannot_promote_another_original_snapshot(candidate):
    value = current(candidate)
    value["evidence"] = machine_evidence(candidate)
    value["evidence"]["task_input"]["snapshot"]["payload_digest"] = "f" * 64
    text = render_review_workbench(value)
    details_text = text.split("Candidate checks and review judgment:", 1)[1].split("Original acceptance coverage:", 1)[0]
    assert "Acceptance: unknown" in details_text
    assert "Machine review: missing" in details_text
    assert "Machine decision: approve" not in details_text
    html = render_review_workbench_html(value)
    details_html = html.split("<h2>Candidate validation</h2>", 1)[1].split("</section>", 1)[0]
    assert "Acceptance: unknown" in details_html
    assert "accepted_by_review_policy" not in details_html
