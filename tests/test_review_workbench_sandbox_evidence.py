from __future__ import annotations

import json
import sys
from pathlib import Path

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun
from goodteam_os.execution.sandbox import SandboxService
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.workers import WorkerJobStore


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
    sandbox = api.create_sandbox(
        execution_id,
        claimed_paths=["src/goodteam_os/execution"],
        path_claims=[
            {
                "path": "src/goodteam_os/execution",
                "source": "area_mapping",
                "confidence": "medium",
                "reason": "matched sandbox area",
            }
        ],
        path_claim_warnings=["path_claims_broad_or_low_confidence"],
    )
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
    assert sandbox_surface["claimed_paths"] == ["src/goodteam_os/execution"]
    assert sandbox_surface["path_claim_warnings"] == ["path_claims_broad_or_low_confidence"]
    assert sandbox_surface["path_claims"][0]["confidence"] == "medium"
    assert sandbox_surface["conflict_risks"][0]["execution_id"] == first_id
    assert "sandbox_conflict_risk_detected" in [
        event["payload"]["transition"] for event in sandbox_surface["events"]
    ]


def test_sandbox_html_redacts_command_secrets(tmp_path):
    first_id = "exec_sandbox_html_conflict"
    execution_id = "exec_sandbox_html"
    _create_run(tmp_path, first_id)
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(first_id, claimed_paths=["src/goodteam_os/execution/api.py"])
    sandbox = api.create_sandbox(
        execution_id,
        claimed_paths=["src/goodteam_os/execution"],
        path_claims=[
            {
                "path": "src/goodteam_os/execution",
                "source": "area_mapping",
                "confidence": "medium",
                "reason": "matched sandbox area",
            }
        ],
        path_claim_warnings=["path_claims_broad_or_low_confidence"],
    )
    api.run_sandbox_command(execution_id, ["gh", "pr", "view"])
    api.run_sandbox_command(
        execution_id,
        [sys.executable, "-c", "import os\nprint(os.environ.get('ANTHROPIC_API_KEY'))"],
        cwd=Path(sandbox["workspace_path"]),
        env={"ANTHROPIC_API_KEY": "anthropic-secret"},
    )

    html = render_review_workbench_html(api.get_review_workbench(execution_id))

    assert "[REDACTED]" in html
    assert "anthropic-secret" not in html


def test_workbench_surfaces_runtime_evidence_and_cleanup_required_warning(tmp_path):
    execution_id = "exec_runtime_cleanup_surface"
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id=execution_id,
        role="implementation",
        adapter="codex",
        task="Implement runtime evidence.",
        context="Approved artifacts are available.",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    _create_run(tmp_path, execution_id, worker_job_id=job["id"], adapter="codex")
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "run_id": execution_id,
                "role": "implementation",
                "adapter": "codex",
                "status": "completed",
                "summary": "Codex CLI exited with code 0.",
                "updated_at": "2026-07-06T05:03:00Z",
                "stdout_path": str(job_root / "stdout.txt"),
                "stderr_path": str(job_root / "stderr.txt"),
                "result_path": str(job_root / "result.md"),
                "invocation_evidence": {
                    "runtime_adapter": "codex",
                    "runtime_binary": "/fake/bin/codex",
                    "runtime_version": "codex 0.61.0",
                    "runtime_model": "gpt-5.4",
                    "runtime_cwd": str(tmp_path / ".goodteam" / "sandboxes" / execution_id / "workspace"),
                    "runtime_workspace_mode": "workspace",
                    "runtime_workspace_status": "active",
                    "runtime_sandbox_mode": "unknown",
                    "runtime_approval_policy": "unknown",
                    "runtime_unsafe_mode": False,
                    "runtime_policy_warning": True,
                    "runtime_policy_warning_reason": "runtime_policy_unverified",
                    "runtime_transcript_path": "",
                    "stdout_path": str(job_root / "stdout.txt"),
                    "stderr_path": str(job_root / "stderr.txt"),
                    "result_path": str(job_root / "result.md"),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)
    SandboxService(tmp_path, clock=FakeClock()).retain_sandbox(execution_id, reason="pr_pending")

    sandbox = api.get_review_workbench(execution_id)["sandbox"]

    assert sandbox["status"] == "retained_for_pr"
    assert sandbox["cleanup_required"] is True
    assert sandbox["cleanup_warning_reason"] == "sandbox_retained_for_pr"
    assert sandbox["runtime"]["runtime_adapter"] == "codex"
    assert sandbox["runtime"]["runtime_version"] == "codex 0.61.0"
    assert sandbox["runtime"]["runtime_cwd"].endswith(f"{execution_id}/workspace")
    assert sandbox["runtime"]["runtime_policy_warning"] is True
    assert sandbox["runtime"]["runtime_policy_warning_reason"] == "runtime_policy_unverified"
    assert "sandbox_retained_for_pr" in [
        event["payload"]["transition"] for event in sandbox["events"]
    ]


def test_workbench_surfaces_runtime_evidence_even_when_sandbox_events_are_missing(tmp_path):
    execution_id = "exec_runtime_without_sandbox_events"
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id=execution_id,
        role="implementation",
        adapter="codex",
        task="Implement runtime evidence.",
        context="Approved artifacts are available.",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    _create_run(tmp_path, execution_id, worker_job_id=job["id"], adapter="codex")
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "run_id": execution_id,
                "role": "implementation",
                "adapter": "codex",
                "status": "completed",
                "summary": "Codex CLI exited with code 0.",
                "updated_at": "2026-07-06T05:03:00Z",
                "invocation_evidence": {
                    "runtime_adapter": "codex",
                    "runtime_binary": "/fake/bin/codex",
                    "runtime_version": "codex 0.61.0",
                    "runtime_model": "gpt-5.4",
                    "runtime_cwd": str(tmp_path),
                    "runtime_workspace_mode": "project_root",
                    "runtime_workspace_status": "none",
                    "runtime_sandbox_mode": "disabled",
                    "runtime_approval_policy": "bypass",
                    "runtime_unsafe_mode": True,
                    "runtime_policy_warning": True,
                    "runtime_policy_warning_reason": "runtime_policy_unsafe",
                    "runtime_transcript_path": "",
                    "stdout_path": str(job_root / "stdout.txt"),
                    "stderr_path": str(job_root / "stderr.txt"),
                    "result_path": str(job_root / "result.md"),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    sandbox = ExecutionAPI(tmp_path).get_review_workbench(execution_id)["sandbox"]

    assert sandbox["available"] is True
    assert sandbox["sandbox_available"] is False
    assert sandbox["reason"] == "sandbox_evidence_missing"
    assert sandbox["runtime"]["runtime_adapter"] == "codex"
    assert sandbox["runtime"]["runtime_policy_warning_reason"] == "runtime_policy_unsafe"
    assert sandbox["events"] == []


def test_workbench_surfaces_cleanup_failed_as_retryable_recovery(tmp_path):
    execution_id = "exec_cleanup_failed_surface"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)
    sandbox_path = tmp_path / ".goodteam" / "executions" / execution_id / "sandbox.json"
    sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
    sandbox.update(
        {
            "status": "cleanup_failed",
            "cleanup_failure_reason": "sandbox_destroy_failed",
            "cleanup_failure_error": "worktree remove exploded",
        }
    )
    sandbox_path.write_text(json.dumps(sandbox, indent=2), encoding="utf-8")

    workbench = api.get_review_workbench(execution_id)

    assert workbench["sandbox"]["status"] == "cleanup_failed"
    assert workbench["sandbox"]["cleanup_required"] is True
    assert workbench["sandbox"]["cleanup_recovery_required"] is True
    assert workbench["sandbox"]["recovery_state"] == "sandbox_cleanup_recovery_required"
    assert workbench["sandbox"]["cleanup_failure_reason"] == "sandbox_destroy_failed"


def test_workbench_projects_failed_sandbox_commands(tmp_path, monkeypatch):
    execution_id = "exec_sandbox_failed_command_surface"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    sandbox = api.create_sandbox(execution_id)

    def fail_spawn(*args, **kwargs):
        raise OSError("spawn failed with sk-workbench-secret")

    monkeypatch.setattr("goodteam_os.execution.sandbox.subprocess.run", fail_spawn)

    api.run_sandbox_command(
        execution_id,
        [sys.executable, "-V"],
        cwd=Path(sandbox["workspace_path"]),
        env={"OPENAI_API_KEY": "sk-workbench-secret"},
    )

    command = api.get_review_workbench(execution_id)["sandbox"]["commands"][0]

    assert command["transition"] == "sandbox_command_failed"
    assert command["status"] == "failed"
    assert command["reason"] == "spawn_failed"
    assert "[REDACTED]" in command["error_excerpt"]
    assert "sk-workbench-secret" not in str(command)


def _create_run(
    tmp_path: Path,
    execution_id: str,
    *,
    worker_job_id: str | None = None,
    adapter: str = "fake",
) -> None:
    ExecutionLedger(tmp_path).create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id=f"task_{execution_id}",
            worker_job_id=worker_job_id or f"worker_{execution_id}",
            adapter=adapter,
            status="succeeded",
            branch_name=f"goodteam/{execution_id}",
            pr_number=None,
            created_at="2026-07-06T05:00:00Z",
            updated_at="2026-07-06T05:01:00Z",
        )
    )
