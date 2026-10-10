"""Server-owned rationale contract; no origin or client-verdict bypass."""
from copy import deepcopy

import pytest

from goodteam_os.approval_rationale import approval_confirmation_error


def context():
    return dict(current_run_id="run-1", current_node="await_prd_approval", review={
        "approval": "prd", "artifact": "prd", "artifact_version": "sha256:v1",
        "is_actionable": True, "recommendation": {"verdict": "caution"},
    }, action={
        "action": "approval_decided", "decision": "approved", "run_id": "run-1",
        "gate": "prd", "before_node": "await_prd_approval", "artifact": "prd",
        "artifact_version": "sha256:v1", "note": "",
    })


@pytest.mark.parametrize("verdict", [None, "", "caution", "request_changes", "reject", "unknown"])
@pytest.mark.parametrize("note", [None, "", " \n\t", 123, {}])
def test_every_nonpositive_server_review_requires_a_text_reason(verdict, note):
    value = context()
    value["review"]["recommendation"] = {"verdict": verdict}
    value["action"]["note"] = note
    assert approval_confirmation_error(**value) == "approval_reason_required"


@pytest.mark.parametrize("origin", ["web", "telegram", "cli", "manager_intent_commit", "fake", "test"])
def test_origin_and_client_claims_never_supply_authority(origin):
    value = context()
    value["action"].update(decision_origin=origin, recommendation={"verdict": "approve"},
                            client_context={"verdict": "approve", "reason_required": False})
    assert approval_confirmation_error(**value) == "approval_reason_required"


def test_unknown_recommendation_is_not_a_positive_review():
    value = context()
    del value["review"]["recommendation"]
    assert approval_confirmation_error(**value) == "approval_reason_required"


def test_reason_is_preserved_without_turning_manual_choice_into_reviewer_pass():
    value = context()
    value["action"]["note"] = "  I inspected this planning document manually.\n"
    before = deepcopy(value)
    assert approval_confirmation_error(**value) is None
    assert value == before
    assert value["review"]["recommendation"]["verdict"] == "caution"


def test_positive_planning_review_does_not_gain_an_unrelated_ci_or_reason_requirement():
    value = context()
    value["review"]["recommendation"]["verdict"] = "approve"
    assert approval_confirmation_error(**value) is None


@pytest.mark.parametrize("field,replacement", [
    ("run_id", "run-2"), ("gate", "security"), ("before_node", "complete"),
    ("artifact", "rfc"), ("artifact_version", "sha256:old"),
])
def test_typed_reason_does_not_override_changed_approval_identity(field, replacement):
    value = context()
    value["action"].update(note="I accept the gap.", **{field: replacement})
    assert approval_confirmation_error(**value) == "stale_approval"


@pytest.mark.parametrize("field,replacement", [
    ("artifact_version", "missing"), ("artifact_version", ""),
    ("artifact", ""), ("is_actionable", False),
])
def test_missing_or_historical_material_cannot_be_approved(field, replacement):
    value = context()
    value["action"]["note"] = "I accept the gap."
    value["review"][field] = replacement
    assert approval_confirmation_error(**value) == "stale_approval"
