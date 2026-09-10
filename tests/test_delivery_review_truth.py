import pytest

from goodteam_os.state import create_initial_state
from goodteam_os.web import build_approval_recommendation


@pytest.mark.parametrize("kind", ["missing", "builtin", "failed", "fallback"])
def test_unproven_reviewer_never_recommends_approval(tmp_path, kind):
    recommendation = build_approval_recommendation(
        state=create_initial_state("run", "DEMO", "Work"),
        workspace=tmp_path,
        review={"approval": "takeover", "content": "# Brief", "reviewer_provenance": {"badge": {"kind": kind}}},
        evidence_snapshot={"blocker_count": 0},
    )
    assert "caution" == recommendation["verdict"]
    assert 0 == recommendation["blocking_count"]


def test_non_release_gate_does_not_require_ci_for_real_review(tmp_path):
    result = build_approval_recommendation(
        state=create_initial_state("run", "DEMO", "Work"),
        workspace=tmp_path,
        review={"approval": "prd", "content": "# PRD", "reviewer_provenance": {"badge": {"kind": "provider"}}},
        evidence_snapshot={"blocker_count": 0},
    )
    assert "approve" == result["verdict"]


def test_incomplete_provider_invocation_respects_existing_policy_warning(tmp_path):
    result = build_approval_recommendation(
        state=create_initial_state("run", "DEMO", "Work"),
        workspace=tmp_path,
        review={
            "approval": "prd",
            "content": "# PRD",
            "reviewer_provenance": {"badge": {"kind": "provider"}},
            "review_evidence_policy": {"state": "fallback_evidence"},
        },
        evidence_snapshot={"blocker_count": 0},
    )
    assert "caution" == result["verdict"]
