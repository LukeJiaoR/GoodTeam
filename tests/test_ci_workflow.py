import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest
import yaml


WORKFLOW = Path(".github/workflows/ci.yml")


def _assert_read_only_runner_boundary(workflow):
    permissions = workflow.get("permissions")
    assert isinstance(permissions, dict), "Declare explicit read-only workflow permissions"
    assert set(permissions.values()) <= {"read", "none"}, "No workflow write permissions on goodteam-ci"
    assert "pull_request_target" not in workflow.get("on", {}), "No privileged PR trigger on goodteam-ci"
    assert not re.search(
        r"\$\{\{(?:(?!\}\}).)*\bsecrets\b", yaml.safe_dump(workflow), flags=re.DOTALL,
    ), "No secret context on goodteam-ci"
    assert workflow.get("jobs"), "A workflow must declare jobs"
    for name, job in workflow["jobs"].items():
        runner = job.get("runs-on")
        assert isinstance(runner, list), f"{name}: use fixed goodteam-ci labels, not hosted or dynamic routing"
        assert {label.lower() for label in runner} == {"self-hosted", "linux", "x64", "goodteam-ci"}, name
        assert "uses" not in job, f"{name}: reusable workflow routing needs explicit review"
        assert "environment" not in job, f"{name}: no GitHub Environment on goodteam-ci"
        assert "secrets" not in job, f"{name}: no inherited secrets on goodteam-ci"
        effective = job.get("permissions", permissions)
        assert isinstance(effective, dict) and set(effective.values()) <= {"read", "none"}, name


@pytest.mark.parametrize("path", sorted([*WORKFLOW.parent.glob("*.yml"), *WORKFLOW.parent.glob("*.yaml")]))
def test_all_workflows_follow_runner_policy(path):
    # BaseLoader keeps GitHub's `on` key as a string (YAML 1.1 treats it as True).
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if path.name == "openwiki-update.yml":
        assert set(workflow["on"]) == {"workflow_dispatch"}
        assert workflow["jobs"]
        for job in workflow["jobs"].values():
            assert job["runs-on"] == "ubuntu-latest", "Keep OpenWiki credentials off goodteam-ci"
    else:
        _assert_read_only_runner_boundary(workflow)


@pytest.mark.parametrize(
    "override",
    [
        {"runs-on": "ubuntu-latest"},
        {"runs-on": "${{ inputs.runner }}"},
        {"runs-on": ["self-hosted", "linux", "x64"]},
        {"permissions": {"contents": "write"}},
        {"permissions": "write-all"},
        {"environment": "production"},
        {"env": {"KEY": "${{ secrets.API_KEY }}"}},
        {"env": {"KEY": "${{ secrets['API_KEY'] }}"}},
        {"env": {"KEY": "${{ toJSON(secrets) }}"}},
        {"env": {"KEY": "${{ secrets }}"}},
        {"secrets": "inherit"},
        {"uses": "./.github/workflows/other.yml"},
    ],
)
def test_runner_policy_rejects_hosted_fallback_and_privilege_escalation(override):
    workflow = {
        "on": {"workflow_dispatch": {}},
        "permissions": {"contents": "read"},
        "jobs": {"diagnostic": {"runs-on": ["self-hosted", "linux", "x64", "goodteam-ci"]}},
    }
    _assert_read_only_runner_boundary(workflow)
    workflow["jobs"]["diagnostic"].update(override)
    with pytest.raises(AssertionError):
        _assert_read_only_runner_boundary(workflow)


@pytest.mark.parametrize("permissions", [None, "read-all", "write-all", {"contents": "write"}])
def test_runner_policy_requires_explicit_read_only_workflow_permissions(permissions):
    with pytest.raises(AssertionError):
        _assert_read_only_runner_boundary({
            "permissions": permissions,
            "jobs": {"diagnostic": {"runs-on": ["self-hosted", "linux", "x64", "goodteam-ci"]}},
        })


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
