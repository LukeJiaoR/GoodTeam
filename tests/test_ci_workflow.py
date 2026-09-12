from pathlib import Path
import re


WORKFLOW = Path(".github/workflows/ci.yml")
TESTING_STRATEGY = Path("docs/testing-strategy.md")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_is_pr_or_manual_only_with_read_only_permissions():
    workflow = _workflow_text()

    assert "  pull_request:" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "  push:" not in workflow
    assert "permissions:\n  contents: read" in workflow


def test_all_routine_jobs_use_guarded_goodteam_runner_without_hosted_fallback():
    workflow = _workflow_text()
    selector = "runs-on: [self-hosted, linux, x64, goodteam-ci]"
    same_repository_guard = (
        "github.event_name != 'pull_request' "
        "|| github.event.pull_request.head.repo.full_name == github.repository"
    )

    assert workflow.count(selector) == 4
    assert workflow.count(same_repository_guard) == 4
    assert "runs-on: ubuntu-latest" not in workflow
    assert "pull_request_target:" not in workflow
    assert "secrets." not in workflow
    assert "environment:" not in workflow


def test_persistent_runner_uses_local_caches_with_reproducible_installs():
    workflow = _workflow_text()

    assert "python3.12 -m venv" in workflow
    assert 'echo "$RUNNER_TEMP/goodteam-venv/bin" >> "$GITHUB_PATH"' in workflow
    assert 'pip install -e ".[dev]"' in workflow
    assert "npm ci" in workflow
    assert "cache: pip" not in workflow
    assert "cache: npm" not in workflow


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
    assert "npm test" in workflow
    assert "npm run lint" in workflow


def test_ci_has_stable_aggregation_gate_that_validates_skips():
    workflow = _workflow_text()

    assert "name: CI / gate" in workflow
    assert "needs: [classify, backend, frontend]" in workflow
    assert "always()" in workflow
    assert "CLASSIFY_RESULT: ${{ needs.classify.result }}" in workflow
    assert "BACKEND_RESULT: ${{ needs.backend.result }}" in workflow
    assert "FRONTEND_RESULT: ${{ needs.frontend.result }}" in workflow
    assert '[[ "$BACKEND_MODE" == "none" && "$BACKEND_RESULT" == "skipped" ]]' in workflow
    assert '[[ "$FRONTEND_MODE" == "none" && "$FRONTEND_RESULT" == "skipped" ]]' in workflow


def test_testing_strategy_documents_risk_tiered_ci_gate():
    policy = TESTING_STRATEGY.read_text(encoding="utf-8")

    assert "`CI / gate`" in policy
    assert "`workflow_dispatch`" in policy
    assert "fail closed" in policy
    assert re.search(r"does not rerun after merge\s+to `main`", policy)
    assert re.search(r"requires pull requests\s+for `main`", policy)


def test_openwiki_remains_manual_only_and_github_hosted():
    workflow = Path(".github/workflows/openwiki-update.yml").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow
    assert "  schedule:" not in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "OPENROUTER_API_KEY" in workflow
    assert "contents: write" in workflow
