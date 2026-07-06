from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRunStatus
from goodteam_os.execution.orchestrator import ExecutionOrchestrator
from goodteam_os.workers import WorkerJobStore

from tests.test_execution_orchestrator import FakeClock, FakeGitHubClient, _snapshot


class FakeSandboxGitAdapter:
    def __init__(self, *, fail_create: bool = False, fail_remove: bool = False):
        self.fail_create = fail_create
        self.fail_remove = fail_remove
        self.created: list[dict[str, object]] = []
        self.removed: list[dict[str, object]] = []

    def create_worktree(
        self,
        *,
        repo_root: Path,
        worktree_path: Path,
        branch_name: str,
        base_ref: str,
    ) -> dict[str, object]:
        self.created.append(
            {
                "repo_root": repo_root,
                "worktree_path": worktree_path,
                "branch_name": branch_name,
                "base_ref": base_ref,
            }
        )
        if self.fail_create:
            raise RuntimeError("worktree create exploded")
        worktree_path.mkdir(parents=True, exist_ok=True)
        return {"status": "created"}

    def remove_worktree(self, *, repo_root: Path, worktree_path: Path) -> dict[str, object]:
        self.removed.append({"repo_root": repo_root, "worktree_path": worktree_path})
        if self.fail_remove:
            raise RuntimeError("worktree remove exploded")
        return {"status": "removed"}


def test_run_execution_creates_sandbox_before_worker_and_destroys_after_success(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")
    observed: dict[str, Any] = {}
    original_run_job = WorkerJobStore.run_job

    def record_run_job(
        self: WorkerJobStore,
        job_id: str,
        *,
        sandbox_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed["sandbox_context"] = sandbox_context
        observed["transitions_before_worker"] = _transitions(tmp_path, queued.execution_id)
        return original_run_job(self, job_id, sandbox_context=sandbox_context)

    monkeypatch.setattr(WorkerJobStore, "run_job", record_run_job)

    completed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert observed["transitions_before_worker"][-1] == "sandbox_created"
    assert _ordered_subset(
        transitions,
        ["execution_started", "sandbox_created", "execution_completed", "sandbox_destroyed"],
    )
    assert observed["sandbox_context"]["available"] is True
    assert observed["sandbox_context"]["workspace_path"]
    assert observed["sandbox_context"]["claimed_paths"] == []
    assert WorkerJobStore(tmp_path).list_jobs()[0]["sandbox_context"]["execution_id"] == queued.execution_id


def test_run_execution_records_failed_worker_result_and_destroys_sandbox(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    def fail_job(
        self: WorkerJobStore,
        job_id: str,
        *,
        sandbox_context: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        assert sandbox_context is not None
        return {
            "job_id": job_id,
            "run_id": queued.execution_id,
            "status": "failed",
            "summary": "Worker failed inside sandbox.",
            "failure_class": "test_failure",
        }

    monkeypatch.setattr(WorkerJobStore, "run_job", fail_job)

    failed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert failed.status is ExecutionRunStatus.FAILED
    assert _ordered_subset(
        transitions,
        ["execution_started", "sandbox_created", "execution_failed", "sandbox_destroyed"],
    )


def test_worker_exception_records_failure_then_destroys_sandbox(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    def raise_job(
        self: WorkerJobStore,
        job_id: str,
        *,
        sandbox_context: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        assert sandbox_context is not None
        raise RuntimeError(f"boom while running {job_id}")

    monkeypatch.setattr(WorkerJobStore, "run_job", raise_job)

    with pytest.raises(RuntimeError, match="boom while running"):
        orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert ExecutionLedger(tmp_path).read_run(queued.execution_id).status is ExecutionRunStatus.FAILED
    assert _ordered_subset(
        transitions,
        ["execution_started", "sandbox_created", "execution_failed", "sandbox_destroyed"],
    )


def test_sandbox_create_failure_prevents_worker_run_and_records_execution_failure(tmp_path, monkeypatch):
    orchestrator = _orchestrator(
        tmp_path,
        sandbox_mode="worktree",
        sandbox_git_adapter=FakeSandboxGitAdapter(fail_create=True),
    )
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    def run_job(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("worker must not run when sandbox creation fails")

    monkeypatch.setattr(WorkerJobStore, "run_job", run_job)

    failed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert failed.status is ExecutionRunStatus.FAILED
    assert "sandbox_create_failed" in transitions
    assert "execution_failed" in transitions
    assert "sandbox_created" not in transitions
    assert not (tmp_path / ".goodteam" / "executions" / queued.execution_id / "sandbox.json").exists()


def test_sandbox_destroy_failure_does_not_hide_worker_success(tmp_path):
    orchestrator = _orchestrator(
        tmp_path,
        sandbox_mode="worktree",
        sandbox_git_adapter=FakeSandboxGitAdapter(fail_remove=True),
    )
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    completed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert _ordered_subset(
        transitions,
        ["sandbox_created", "execution_completed", "sandbox_destroy_failed"],
    )


def test_worker_sandbox_context_has_no_host_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_context_secret")
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    orchestrator.run_execution(queued.execution_id)

    job = WorkerJobStore(tmp_path).list_jobs()[0]
    context = job["sandbox_context"]
    assert context["schema_version"] == "sandbox.v1"
    assert context["mode"] == "workspace"
    assert context["workspace_path"]
    assert context["claimed_paths"] == []
    assert "ghp_context_secret" not in str(context)
    assert not ({"GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"} & set(context))


def test_run_execution_can_disable_sandbox_for_legacy_compatibility(tmp_path):
    orchestrator = _orchestrator(tmp_path, sandbox_enabled=False)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    completed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert transitions == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "execution_completed",
    ]
    assert "sandbox_context" not in WorkerJobStore(tmp_path).list_jobs()[0]


def test_missing_execution_does_not_create_sandbox_artifacts(tmp_path):
    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        ExecutionOrchestrator(tmp_path).run_execution("exec_missing")

    assert not (tmp_path / ".goodteam" / "executions" / "exec_missing").exists()
    assert not (tmp_path / ".goodteam" / "sandboxes" / "exec_missing").exists()


def test_review_workbench_shows_real_run_sandbox_lifecycle(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    orchestrator.run_execution(queued.execution_id)

    sandbox = ExecutionAPI(tmp_path).get_review_workbench(queued.execution_id)["sandbox"]
    transitions = [event["payload"]["transition"] for event in sandbox["events"]]
    assert sandbox["available"] is True
    assert sandbox["schema_version"] == "sandbox.v1"
    assert sandbox["status"] == "destroyed"
    assert sandbox["commands"] == []
    assert "sandbox_created" in transitions
    assert "sandbox_destroyed" in transitions


def _orchestrator(
    tmp_path: Path,
    *,
    sandbox_enabled: bool = True,
    sandbox_mode: str = "workspace",
    sandbox_git_adapter: FakeSandboxGitAdapter | None = None,
) -> ExecutionOrchestrator:
    orchestrator = ExecutionOrchestrator(
        tmp_path,
        FakeGitHubClient({("lukejiaor/goodteam", 218): _snapshot()}),
        sandbox_enabled=sandbox_enabled,
        sandbox_mode=sandbox_mode,
        sandbox_git_adapter=sandbox_git_adapter,
    )
    orchestrator._clock = FakeClock()
    return orchestrator


def _transitions(tmp_path: Path, execution_id: str) -> list[str]:
    return [
        str(event.payload.get("transition", ""))
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
    ]


def _ordered_subset(values: list[str], expected: list[str]) -> bool:
    remaining = iter(values)
    return all(any(value == wanted for value in remaining) for wanted in expected)
