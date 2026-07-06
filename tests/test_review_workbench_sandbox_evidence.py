from __future__ import annotations

import sys
from pathlib import Path

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html


class FakeClock:
    def __init__(self):
        self.values = [f"2026-07-06T05:{minute:02d}:00Z" for minute in range(20)]
        self.index = 0

    def __call__(self) -> str:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


def test_workbench_returns_missing_sandbox_evidence_when_no_sandbox_events(tmp_path):
    execution_id = "exec_no_sandbox_evidence"
    _create_run(tmp_path, execution_id)

    sandbox = ExecutionAPI(tmp_path).get_review_workbench(execution_id)["sandbox"]

    assert sandbox == {
        "available": False,
        "reason": "sandbox_evidence_missing",
    }


def test_workbench_projects_sandbox_events_without_side_effects(tmp_path):
    first_id = "exec_sandbox_conflict_a"
    execution_id = "exec_sandbox_evidence"
    _create_run(tmp_path, first_id)
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(first_id, claimed_paths=["src/goodteam_os/execution/api.py"])
    sandbox = api.create_sandbox(execution_id, claimed_paths=["src/goodteam_os/execution"])
    api.run_sandbox_command(execution_id, ["gh", "pr", "view"])
    api.run_sandbox_command(
        execution_id,
        [
            sys.executable,
            "-c",
            "import os\nprint(os.environ.get('OPENAI_API_KEY'))",
        ],
        cwd=Path(sandbox["workspace_path"]),
        env={"OPENAI_API_KEY": "sk-real-secret"},
    )
    ledger = ExecutionLedger(tmp_path)
    before = ledger.read_events(execution_id)

    workbench = api.get_review_workbench(execution_id)
    second = api.get_review_workbench(execution_id)

    sandbox_surface = workbench["sandbox"]
    assert second["sandbox"] == sandbox_surface
    assert ledger.read_events(execution_id) == before
    assert sandbox_surface["available"] is True
    assert sandbox_surface["schema_version"] == "sandbox.v1"
    assert sandbox_surface["status"] == "active"
    assert sandbox_surface["mode"] == "workspace"
    assert sandbox_surface["workspace_path"] == sandbox["workspace_path"]
    assert sandbox_surface["commands"][0]["status"] == "rejected"
    assert sandbox_surface["commands"][0]["reason"] == "unknown_command"
    assert sandbox_surface["commands"][1]["status"] == "completed"
    assert "[REDACTED]" in sandbox_surface["commands"][1]["stdout_excerpt"]
    assert "sk-real-secret" not in str(sandbox_surface)
    assert sandbox_surface["conflict_risks"][0]["execution_id"] == first_id
    assert "sandbox_conflict_risk_detected" in [
        event["payload"]["transition"] for event in sandbox_surface["events"]
    ]


def test_browser_html_renders_sandbox_evidence(tmp_path):
    first_id = "exec_sandbox_html_conflict"
    execution_id = "exec_sandbox_html"
    _create_run(tmp_path, first_id)
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(first_id, claimed_paths=["src/goodteam_os/execution/api.py"])
    sandbox = api.create_sandbox(execution_id, claimed_paths=["src/goodteam_os/execution"])
    api.run_sandbox_command(execution_id, ["gh", "pr", "view"])
    api.run_sandbox_command(
        execution_id,
        [sys.executable, "-c", "import os\nprint(os.environ.get('ANTHROPIC_API_KEY'))"],
        cwd=Path(sandbox["workspace_path"]),
        env={"ANTHROPIC_API_KEY": "anthropic-secret"},
    )

    html = render_review_workbench_html(api.get_review_workbench(execution_id))

    assert "Sandbox evidence" in html
    assert "active" in html
    assert sandbox["workspace_path"] in html
    assert "Command history" in html
    assert "sandbox_command_rejected" in html
    assert "unknown_command" in html
    assert "Conflict risk" in html
    assert "[REDACTED]" in html
    assert "anthropic-secret" not in html


def _create_run(tmp_path: Path, execution_id: str) -> None:
    ExecutionLedger(tmp_path).create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id=f"task_{execution_id}",
            worker_job_id=f"worker_{execution_id}",
            adapter="fake",
            status="succeeded",
            branch_name=f"goodteam/{execution_id}",
            pr_number=None,
            created_at="2026-07-06T05:00:00Z",
            updated_at="2026-07-06T05:01:00Z",
        )
    )
