import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest


WORKFLOW = Path(".github/workflows/ci.yml")


def test_ci_is_pr_or_manual_only_with_read_only_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]

    assert "  pull_request:" in trigger
    assert "  workflow_dispatch:" in trigger
    assert "  push:" not in trigger
    assert "branches:" not in trigger  # Stacked PRs also need pre-merge checks.
    assert "permissions:\n  contents: read" in workflow


def test_every_job_keeps_the_self_hosted_runner_security_boundary():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    jobs = re.split(r"^  [a-z_]+:\n", workflow.split("\njobs:\n", 1)[1], flags=re.MULTILINE)[1:]
    guard = (
        "github.event_name != 'pull_request' "
        "|| github.event.pull_request.head.repo.full_name == github.repository"
    )

    assert jobs
    for job in jobs:
        assert "runs-on: [self-hosted, linux, x64, goodteam-ci]" in job
        assert guard in job.split("steps:", 1)[0]
    assert "pull_request_target:" not in workflow
    assert "secrets." not in workflow
    assert "environment:" not in workflow


@pytest.mark.parametrize(
    ("backend_mode", "frontend_mode", "backend_result", "browser_result"),
    [
        ("none", "none", "skipped", "skipped"),
        ("focused", "none", "success", "success"),
        ("full", "none", "success", "success"),
        ("none", "full", "skipped", "success"),
        ("full", "full", "success", "success"),
    ],
)
def test_gate_accepts_only_successful_planned_jobs(backend_mode, frontend_mode, backend_result, browser_result):
    # Execute the real gate: source-string checks cannot prove that failures,
    # cancellations, or unexpected skips actually block merging.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Validate planned jobs\n", 1)[1].split("\n      - name:", 1)[0]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    planned = {
        "CLASSIFY_RESULT": "success",
        "BACKEND_MODE": backend_mode,
        "FRONTEND_MODE": frontend_mode,
        "BACKEND_RESULT": backend_result,
        "BROWSER_RESULT": browser_result,
    }

    def accepts(overrides):
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            env={**os.environ, **planned, **overrides},
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0

    assert accepts({})
    for key in ("CLASSIFY_RESULT", "BACKEND_RESULT", "BROWSER_RESULT"):
        for result in ("success", "skipped", "failure", "cancelled", ""):
            if result != planned[key]:
                assert not accepts({key: result}), (key, result, planned)
    for key in ("BACKEND_MODE", "FRONTEND_MODE"):
        for mode in ("", "unknown"):
            assert not accepts({key: mode}), (key, mode, planned)
