"""Offline coverage contract; source-backed fixture judgments are not live acceptance."""
from copy import deepcopy
import json

import pytest

from goodteam_os import cli
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService, review_policy, policy_decision
from goodteam_os.execution.candidate_validation import CandidateValidationService, _read
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills.review_results import ReviewResultStore
from test_approved_plan_binding import register
from test_automatic_review_entry import entry as entry_fixture, admitted, validate_args  # noqa: F401
from test_candidate_validation import protected
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.mark.parametrize("profile", ["greenfield", "fix"])
def test_bound_policy_pins_semantic_assessments_to_exact_intent(request, profile):
    c = request.getfixturevalue("entry_fixture")
    binding = register(c, profile=profile)
    snapshot = c.store.load(c.execution_id)
    old = review_policy(snapshot)
    current = review_policy(snapshot, binding)
    assert old["schema_version"] == "candidate_review_policy.v1" and "plan_checks" not in old
    assert current["schema_version"] == "candidate_review_policy.v2"
    assert current["approved_plan_binding_id"] == binding["binding_id"]
    assert current["criterion_ids"] == old["criterion_ids"]
    assert current["plan_checks"] == [
        {"name": f"{binding['binding_id']}:artifact:{row['artifact_key']}",
         "artifact_key": row["artifact_key"], "artifact_version": row["artifact_version"]}
        for row in binding["approved_artifacts"]
    ] + [{"name": f"{binding['binding_id']}:derived-task",
          "artifact_key": binding["derived_task"]["artifact_key"],
          "artifact_version": binding["derived_task"]["artifact_version"]}]
    report = {"findings": [], "checks": [{"name": name, "status": "checked", "evidence_ids": ["E1"]}
                                         for name in old["criterion_ids"]]}
    receipt = {"observation": {"status": "passed"}}
    # Regression: original task checks alone previously approved, with no record
    # that the independent reviewer assessed the separately approved intent.
    assert policy_decision(old, receipt, report)[0] == "approve"
    assert policy_decision(current, receipt, report)[0] == "inconclusive"
    report["checks"] += [{"name": row["name"], "status": "checked", "evidence_ids": ["E1"]}
                         for row in current["plan_checks"]]
    assert policy_decision(current, receipt, report)[0] == "approve"
    assert policy_decision({**current, "criterion_ids": []}, receipt, report)[0] == "inconclusive"
    for change in ("missing", "duplicate", "inconclusive", "not_run", "no_evidence", "other_binding"):
        changed = deepcopy(report)
        row = changed["checks"][-1]
        if change == "missing":
            changed["checks"].pop()
        elif change == "duplicate":
            changed["checks"].append(deepcopy(row))
        elif change == "no_evidence":
            row["evidence_ids"] = []
        elif change == "other_binding":
            row["name"] = "approved-plan:" + "0" * 64 + ":derived-task"
        else:
            row["status"] = change
        assert policy_decision(current, receipt, changed)[0] == "inconclusive", change
    report["findings"] = [{"severity": "medium", "title": "Outside approved scope",
                           "recommendation": "Remove the unrelated behavior."}]
    assert policy_decision(current, receipt, report)[0] == "request_changes"


@pytest.mark.parametrize("covered", [False, True])
def test_real_controller_retains_plan_judgment_or_stops_without_it(request, capsys, covered):
    c = request.getfixturevalue("entry_fixture")
    c.approved_plan = register(c)
    admission = admitted(c, plan_coverage=covered)
    assert cli.main(validate_args(c), codex_review_admission=admission) == 0
    output = json.loads(capsys.readouterr().out)
    result = output["automatic_review"]
    assert result["status"] == ("reviewed" if covered else "inconclusive"), result
    evidence = CandidateValidationService(c.project).evidence(c.execution_id)
    assert evidence["acceptance_status"] == ("accepted_by_review_policy" if covered else "unknown")
    review = evidence["review"]
    assert review["policy"]["approved_plan_binding_id"] == c.approved_plan["binding_id"]
    assert review["candidate"]["head_sha"] == c.head
    service = AutomaticCandidateReviewService(c.project)
    detail = ReviewResultStore(service.workers, review["reviewer"]).detail(
        expected_task_context=review["provenance"]["task_skill_context"], include_report=True)
    names = {row["name"] for row in detail["report"]["checks"]}
    for check in review["policy"]["plan_checks"]:
        assert (check["name"] in names) is covered
    before = c.calls.read_text()
    replay = service.advance(c.execution_id)
    assert replay["status"] == result["status"] and c.calls.read_text() == before
    assert evidence["review"]["semantic_verification"] == "judgment_not_independently_verified"


@pytest.mark.parametrize("interrupted", [False, True])
def test_bound_legacy_policy_is_preserved_and_requires_explicit_upgrade(request, monkeypatch, interrupted):
    c = request.getfixturevalue("entry_fixture")
    c.approved_plan = register(c)
    validation = CandidateValidationService(c.project)
    receipt = validation.validate(c.execution_id, operation_id="legacy", head_sha=c.head, argv=c.argv, criteria=[0, 1])
    service = AutomaticCandidateReviewService(c.project)
    # Construct a genuine old frozen start/lock, not a forged modern receipt.
    import goodteam_os.execution.automatic_review as module
    original = module.review_policy
    with monkeypatch.context() as patch:
        patch.setattr(module, "review_policy", lambda snapshot, approved_plan=None: original(snapshot))
        if interrupted:
            patch.setattr(service, "_capture", lambda *_: (_ for _ in ()).throw(RuntimeError("interrupted")))
            with pytest.raises(RuntimeError, match="interrupted"):
                service.start(c.execution_id, base_sha=c.base, receipt_id=receipt["receipt_id"], head_sha=c.head,
                              reviewer_adapter="codex", reviewer_model="fixture")
        else:
            service.start(c.execution_id, base_sha=c.base, receipt_id=receipt["receipt_id"], head_sha=c.head,
                          reviewer_adapter="codex", reviewer_model="fixture")
    before = {p.name: p.read_bytes() for p in protected(c).iterdir() if p.is_file()}
    with pytest.raises(TaskInputError, match="automatic_review_plan_coverage_upgrade_required"):
        service.configuration(c.execution_id)
    with pytest.raises(TaskInputError, match="automatic_review_plan_coverage_upgrade_required"):
        service.start(c.execution_id, base_sha=c.base, receipt_id=receipt["receipt_id"], head_sha=c.head,
                      reviewer_adapter="codex", reviewer_model="fixture")
    result = service.advance(c.execution_id)
    assert result["reason"] == ("automatic_review_binding_missing" if interrupted
                                else "automatic_review_plan_coverage_upgrade_required")
    assert service.projection(c.execution_id)["acceptance_status"] == "unknown"
    assert {p.name: p.read_bytes() for p in protected(c).iterdir() if p.is_file()} == before
    assert _read(protected(c) / "automatic-review-start.json")["policy"]["schema_version"] == "candidate_review_policy.v1"
    assert not c.calls.exists()
