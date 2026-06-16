"""Regression: a fix-profile run must render approval reviews without crashing
on greenfield gate keys, and must show only the fix profile's gates.

This locks the bug surfaced live by the "Report Bug" flow: build_approval_reviews
iterated state.approvals (which still carries the greenfield keys from
create_initial_state) and called advisors_for_gate("fix_plan"), raising
"Unknown approval gate: fix_plan".
"""

from pathlib import Path

from goodteam_os.state import ApprovalDecision, create_initial_state
from goodteam_os.web import build_approval_reviews


def _fix_state():
    state = create_initial_state(run_id="r-fix", project_key="DEMO", idea="[BUG][p2] x")
    state.load_profile = "fix"
    # A fix run keeps the greenfield approval keys (prd/rfc/staging/security/release)
    # from create_initial_state AND adds the fix gate it is waiting on.
    state.approvals["fix_plan"] = ApprovalDecision.PENDING
    state.next_node = "await_fix_plan_approval"
    return state


def test_fix_run_approval_reviews_do_not_raise_on_greenfield_keys(tmp_path: Path):
    # Must not raise "Unknown approval gate: fix_plan".
    reviews = build_approval_reviews(_fix_state(), workspace=tmp_path)
    gates = {r["approval"] for r in reviews}
    # Only the fix profile's gates are shown; greenfield gates are filtered out.
    assert "fix_plan" in gates
    assert "rfc" not in gates
    assert "staging" not in gates
    assert "prd" not in gates


def test_fix_run_fix_plan_gate_is_actionable(tmp_path: Path):
    reviews = build_approval_reviews(_fix_state(), workspace=tmp_path)
    fix_plan = next(r for r in reviews if r["approval"] == "fix_plan")
    assert fix_plan["is_actionable"] is True


def test_greenfield_run_reviews_unchanged(tmp_path: Path):
    # Greenfield profile keeps showing all its gates (no filtering regression).
    state = create_initial_state(run_id="r-gf", project_key="DEMO", idea="build a thing")
    state.next_node = "await_prd_approval"
    reviews = build_approval_reviews(state, workspace=tmp_path)
    gates = {r["approval"] for r in reviews}
    assert {"prd", "rfc", "staging", "security", "release"}.issubset(gates)
