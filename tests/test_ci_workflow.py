from pathlib import Path


WORKFLOW = Path(".github/workflows/ci.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_is_pr_or_manual_only_with_read_only_permissions():
    workflow = _workflow_text()

    assert "  pull_request:" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "  push:" not in workflow
    assert "permissions:\n  contents: read" in workflow


def test_ci_classifies_full_history_or_manual_full_mode():
    workflow = _workflow_text()

    assert "fetch-depth: 0" in workflow
    assert "--full" in workflow
    assert "--base" in workflow
    assert "--head" in workflow
    assert "github.event.pull_request.base.sha" in workflow
    assert "github.event.pull_request.head.sha" in workflow
    assert "--github-output \"$GITHUB_OUTPUT\"" in workflow
    assert "--summary \"$GITHUB_STEP_SUMMARY\"" in workflow


def test_ci_jobs_follow_classifier_outputs():
    workflow = _workflow_text()

    assert "needs.classify.outputs.backend != 'none'" in workflow
    assert "needs.classify.outputs.frontend != 'none'" in workflow
    assert "needs.classify.outputs.backend == 'focused'" in workflow
    assert "needs.classify.outputs.backend == 'full'" in workflow
    assert "needs.classify.outputs.backend_test_targets" in workflow
    assert "PYTHONPATH=src python -m pytest tests -q" in workflow
    assert "ruff check src/goodteam_os/ tests/ scripts/ci/" in workflow
    assert "npm ci" in workflow
    assert "npm run build" in workflow
    assert "npm run lint" in workflow


def test_ci_has_stable_aggregation_gate_that_validates_skips():
    workflow = _workflow_text()

    assert "name: CI / gate" in workflow
    assert "needs: [classify, backend, frontend]" in workflow
    assert "if: always()" in workflow
    assert "CLASSIFY_RESULT: ${{ needs.classify.result }}" in workflow
    assert "BACKEND_RESULT: ${{ needs.backend.result }}" in workflow
    assert "FRONTEND_RESULT: ${{ needs.frontend.result }}" in workflow
    assert '[[ "$BACKEND_MODE" == "none" && "$BACKEND_RESULT" == "skipped" ]]' in workflow
    assert '[[ "$FRONTEND_MODE" == "none" && "$FRONTEND_RESULT" == "skipped" ]]' in workflow
