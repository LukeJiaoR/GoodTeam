from scripts.ci.classify_changes import Change, classify_changes


def test_docs_only_is_tier_zero_without_product_jobs():
    plan = classify_changes([Change("M", ("docs/testing-strategy.md",))])

    assert plan.tier == 0
    assert plan.backend == "none"
    assert plan.frontend == "none"


def test_mixed_changes_take_highest_tier_and_union_stack_impact():
    plan = classify_changes(
        [
            Change("M", ("src/goodteam_os/execution/ledger.py",)),
            Change("M", ("frontend/src/components/ApprovalBanner.jsx",)),
        ]
    )

    assert plan.tier == 4
    assert plan.backend == "full"
    assert plan.frontend == "full"
    assert plan.live_smoke_evidence_required is True
