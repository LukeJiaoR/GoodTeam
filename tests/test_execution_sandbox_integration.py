from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.github import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
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
        (worktree_path / ".git").write_text("gitdir: fake\n", encoding="utf-8")
        (worktree_path / "src").mkdir(exist_ok=True)
        (worktree_path / "tests").mkdir(exist_ok=True)
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
    assert "src/goodteam_os/workers.py" in observed["sandbox_context"]["claimed_paths"]
    assert WorkerJobStore(tmp_path).list_jobs()[0]["sandbox_context"]["execution_id"] == queued.execution_id


def test_run_execution_passes_inferred_path_claims_into_sandbox_creation(tmp_path, monkeypatch):
    snapshot = GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=218,
        title="Worker runtime path claim inference",
        body=(
            "Update `src/goodteam_os/workers.py` and document docs/agent-sandbox-v0.md. "
            "Also improve sandbox cleanup notes."
        ),
        url="https://github.com/LukeJiaoR/GoodTeam/issues/218",
        labels=["p0", "sandbox"],
    )
    orchestrator = ExecutionOrchestrator(
        tmp_path,
        FakeGitHubClient({("lukejiaor/goodteam", 218): snapshot}),
        sandbox_mode="workspace",
    )
    orchestrator._clock = FakeClock()
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / queued.worker_job_id
    (job_root / "expected_artifacts.json").write_text(
        json.dumps([{"path": "tests/test_workers.py"}], indent=2),
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}
    original_run_job = WorkerJobStore.run_job

    def record_run_job(
        self: WorkerJobStore,
        job_id: str,
        *,
        sandbox_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed["sandbox_context"] = sandbox_context
        return original_run_job(self, job_id, sandbox_context=sandbox_context)

    monkeypatch.setattr(WorkerJobStore, "run_job", record_run_job)

    orchestrator.run_execution(queued.execution_id)

    metadata = json.loads(
        (tmp_path / ".goodteam" / "executions" / queued.execution_id / "sandbox.json").read_text(encoding="utf-8")
    )
    created_event = [
        event for event in ExecutionLedger(tmp_path).read_events(queued.execution_id)
        if event.payload.get("transition") == "sandbox_created"
    ][0]
    assert observed["sandbox_context"]["claimed_paths"] == [
        "src/goodteam_os/workers.py",
        "docs/agent-sandbox-v0.md",
        "tests/test_workers.py",
        "src/goodteam_os/execution/sandbox.py",
        "tests/test_execution_sandbox_integration.py",
    ]
    assert metadata["path_claims"][0] == {
        "path": "src/goodteam_os/workers.py",
        "source": "task_contract.path_scope",
        "confidence": "high",
        "reason": "explicit repo-relative path",
    }
    assert metadata["path_claim_warnings"] == []
    assert created_event.payload["path_claim_warnings"] == []
    assert created_event.payload["path_claims"][0]["path"] == "src/goodteam_os/workers.py"


def test_run_execution_prefers_contract_path_scope_for_sandbox_claims(tmp_path, monkeypatch):
    snapshot = GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=218,
        title="Typed contract path scope",
        body=(
            "Worker runtime mentions src/goodteam_os/workers.py.\n\n"
            "Acceptance:\n"
            "- Update `src/goodteam_os/execution/models.py`.\n"
            "- Cover `tests/test_execution_models.py`."
        ),
        url="https://github.com/LukeJiaoR/GoodTeam/issues/218",
        labels=["p0", "sandbox"],
    )
    orchestrator = ExecutionOrchestrator(
        tmp_path,
        FakeGitHubClient({("lukejiaor/goodteam", 218): snapshot}),
        sandbox_mode="workspace",
    )
    orchestrator._clock = FakeClock()
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
        return original_run_job(self, job_id, sandbox_context=sandbox_context)

    monkeypatch.setattr(WorkerJobStore, "run_job", record_run_job)

    orchestrator.run_execution(queued.execution_id)

    assert observed["sandbox_context"]["claimed_paths"][:2] == [
        "src/goodteam_os/execution/models.py",
        "tests/test_execution_models.py",
    ]
    assert [claim["source"] for claim in observed["sandbox_context"]["path_claims"][:2]] == [
        "task_contract.path_scope",
        "task_contract.path_scope",
    ]


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
            "summary": "Worker failed before PR handoff.",
            "failure_class": "test_failure",
        }

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(WorkerJobStore, "run_job", fail_job)
        failed = orchestrator.run_execution(queued.execution_id)

    transitions = _transitions(tmp_path, queued.execution_id)
    assert failed.status is ExecutionRunStatus.FAILED
    assert _ordered_subset(
        transitions,
        ["sandbox_created", "execution_failed", "sandbox_destroy_failed"],
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
    assert "src/goodteam_os/workers.py" in context["claimed_paths"]
    assert "ghp_context_secret" not in str(context)
    assert not ({"GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"} & set(context))


def test_cli_worker_process_launches_with_valid_workspace_cwd_and_evidence(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    workspace = _repo_like_workspace(tmp_path / "execution-worktree")
    monkeypatch.setenv("PYTHONPATH", "/existing/pythonpath")
    _install_fake_cli(monkeypatch, calls=calls)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-cli-workspace",
        role="implementation",
        adapter="codex",
        task="Implement in the execution worktree.",
        context="Use the provided workspace.",
    )

    result = store.run_job(job["id"], sandbox_context=_sandbox_context(workspace))

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    evidence = status["invocation_evidence"]
    assert result["status"] == "completed"
    assert calls[0]["cwd"] == workspace
    assert calls[0]["command"][calls[0]["command"].index("--cd") + 1] == str(workspace)
    assert calls[0]["env"]["PYTHONPATH"].split(":")[:2] == [
        str(workspace / "src"),
        "/existing/pythonpath",
    ]
    assert status["runtime_cwd"] == str(workspace)
    assert status["workspace_context_available"] is True
    assert status["workspace_cwd_fallback"] is False
    assert status["workspace_cwd_fallback_reason"] == ""
    assert status["pythonpath_workspace_src"] is True
    assert evidence["runtime_cwd"] == str(workspace)
    assert evidence["workspace_cwd_fallback"] is False
    assert evidence["pythonpath_workspace_src"] is True
    assert Path(status["result_path"]).is_absolute()
    assert Path(status["stdout_path"]).is_absolute()
    assert Path(status["stderr_path"]).is_absolute()
    assert job_root in Path(status["result_path"]).parents
    assert job_root in Path(status["stdout_path"]).parents
    assert job_root in Path(status["stderr_path"]).parents


def test_cli_worker_fallback_to_project_root_is_explicit_when_workspace_is_not_repo(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    _install_fake_cli(monkeypatch, calls=calls)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-cli-fallback",
        role="implementation",
        adapter="codex",
        task="Implement with fallback evidence.",
        context="Workspace is intentionally not repo-like.",
    )

    result = store.run_job(job["id"], sandbox_context=_sandbox_context(workspace))

    status = json.loads(
        (tmp_path / ".goodteam" / "workers" / "jobs" / job["id"] / "status.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "completed"
    assert calls[0]["cwd"] == tmp_path
    assert status["runtime_cwd"] == str(tmp_path)
    assert status["workspace_context_available"] is True
    assert status["workspace_cwd_fallback"] is True
    assert status["workspace_cwd_fallback_reason"] == "workspace_missing_or_not_repo"
    assert status["invocation_evidence"]["workspace_cwd_fallback"] is True


def test_worktree_execution_uses_worktree_cwd_preserves_shared_venv_and_pythonpath(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    shared_venv = tmp_path / ".venv"
    shared_venv.mkdir()
    (shared_venv / "marker").write_text("shared", encoding="utf-8")
    adapter = FakeSandboxGitAdapter()
    _install_fake_cli(monkeypatch, calls=calls)
    orchestrator = _orchestrator(
        tmp_path,
        sandbox_mode="worktree",
        sandbox_git_adapter=adapter,
    )
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="codex")

    completed = orchestrator.run_execution(queued.execution_id)

    worktree_path = Path(adapter.created[0]["worktree_path"])
    status = WorkerJobStore(tmp_path).list_jobs()[0]
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert calls[0]["cwd"] == worktree_path
    assert status["runtime_cwd"] == str(worktree_path)
    assert status["pythonpath_workspace_src"] is True
    assert calls[0]["env"]["PYTHONPATH"].split(":")[0] == str(worktree_path / "src")
    assert not (worktree_path / ".venv").exists()
    assert shared_venv.exists()
    assert adapter.removed == []
    assert _transitions(tmp_path, queued.execution_id)[-1] == "sandbox_retained_for_pr"
    assert ExecutionAPI(tmp_path).get_sandbox(queued.execution_id)["status"] == "retained_for_pr"


def test_worktree_execution_retains_workspace_until_pr_or_explicit_cleanup(tmp_path):
    adapter = FakeSandboxGitAdapter()
    client = FakePullRequestGitHubClient()
    orchestrator = ExecutionOrchestrator(
        tmp_path,
        client,
        sandbox_mode="worktree",
        sandbox_git_adapter=adapter,
    )
    orchestrator._clock = FakeClock()
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    completed = orchestrator.run_execution(queued.execution_id)
    binding = orchestrator.open_pull_request_for_execution(completed.execution_id)
    after_pr_transitions = _transitions(tmp_path, completed.execution_id)

    worktree_path = Path(adapter.created[0]["worktree_path"])
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert binding.pr_number == 218
    assert worktree_path.exists()
    assert adapter.removed == []
    assert _ordered_subset(
        after_pr_transitions,
        ["sandbox_created", "execution_completed", "sandbox_retained_for_pr", "pr_opened"],
    )
    assert "sandbox_destroyed" not in after_pr_transitions[: after_pr_transitions.index("pr_opened")]

    destroyed = ExecutionAPI(tmp_path, sandbox_git_adapter=adapter).destroy_sandbox(completed.execution_id)

    assert destroyed["status"] == "destroyed"
    assert adapter.removed == [{"repo_root": tmp_path, "worktree_path": worktree_path}]
    assert _transitions(tmp_path, completed.execution_id)[-2:] == [
        "sandbox_cleanup_requested",
        "sandbox_destroyed",
    ]


def test_destroy_retained_worktree_failure_marks_cleanup_failed_and_allows_retry(tmp_path):
    adapter = FakeSandboxGitAdapter(fail_remove=True)
    orchestrator = _orchestrator(
        tmp_path,
        sandbox_mode="worktree",
        sandbox_git_adapter=adapter,
    )
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")
    completed = orchestrator.run_execution(queued.execution_id)

    first = ExecutionAPI(tmp_path, sandbox_git_adapter=adapter).destroy_sandbox(completed.execution_id)

    assert first["status"] == "cleanup_failed"
    assert first["reason"] == "sandbox_destroy_failed"
    assert ExecutionAPI(tmp_path).get_sandbox(completed.execution_id)["status"] == "cleanup_failed"
    assert _transitions(tmp_path, completed.execution_id)[-2:] == [
        "sandbox_cleanup_requested",
        "sandbox_destroy_failed",
    ]

    adapter.fail_remove = False
    second = ExecutionAPI(tmp_path, sandbox_git_adapter=adapter).destroy_sandbox(completed.execution_id)

    assert second["status"] == "destroyed"
    assert ExecutionAPI(tmp_path).get_sandbox(completed.execution_id)["status"] == "destroyed"
    assert _transitions(tmp_path, completed.execution_id)[-2:] == [
        "sandbox_cleanup_requested",
        "sandbox_destroyed",
    ]


def test_destroy_sandbox_is_idempotent_after_destroyed(tmp_path):
    adapter = FakeSandboxGitAdapter()
    orchestrator = _orchestrator(
        tmp_path,
        sandbox_mode="worktree",
        sandbox_git_adapter=adapter,
    )
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")
    completed = orchestrator.run_execution(queued.execution_id)
    api = ExecutionAPI(tmp_path, sandbox_git_adapter=adapter)
    api.destroy_sandbox(completed.execution_id)
    transitions_before = _transitions(tmp_path, completed.execution_id)

    again = api.destroy_sandbox(completed.execution_id)

    assert again["status"] == "already_destroyed"
    assert _transitions(tmp_path, completed.execution_id) == transitions_before


def test_cli_worker_context_status_and_prompt_do_not_expose_secret_values(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []
    workspace = _repo_like_workspace(tmp_path / "secret-worktree")
    monkeypatch.setenv("GH_TOKEN", "ghp_runtime_secret")
    _install_fake_cli(monkeypatch, calls=calls)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-secret",
        role="implementation",
        adapter="codex",
        task="Check secret handling.",
        context="No credentials should be persisted.",
    )

    store.run_job(job["id"], sandbox_context=_sandbox_context(workspace))

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status_text = (job_root / "status.json").read_text(encoding="utf-8")
    context_text = (job_root / "sandbox_context.json").read_text(encoding="utf-8")
    assert "ghp_runtime_secret" not in status_text
    assert "ghp_runtime_secret" not in context_text
    assert "ghp_runtime_secret" not in calls[0]["input"]
    assert "GH_TOKEN" not in context_text


def test_destroy_sandbox_exception_records_destroy_failed_without_rewriting_worker_outcome(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=218, adapter="fake")

    def explode_destroy(self, execution_id: str) -> dict[str, Any]:
        raise RuntimeError(f"destroy exploded for {execution_id}")

    monkeypatch.setattr("goodteam_os.execution.sandbox.SandboxService.destroy_sandbox", explode_destroy)

    completed = orchestrator.run_execution(queued.execution_id)

    events = ExecutionLedger(tmp_path).read_events(queued.execution_id)
    transitions = [str(event.payload.get("transition", "")) for event in events]
    destroy_event = events[-1]
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert transitions[-2:] == ["execution_completed", "sandbox_destroy_failed"]
    assert destroy_event.payload["source"] == "sandbox"
    assert destroy_event.payload["reason"] == "sandbox_destroy_failed"
    assert "destroy exploded" in destroy_event.payload["error"]


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
    sandbox_mode: str = "auto",
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


def _repo_like_workspace(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: fake\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "tests").mkdir()
    return path


def _sandbox_context(workspace: Path) -> dict[str, Any]:
    return {
        "available": True,
        "schema_version": "sandbox.v1",
        "execution_id": "exec-runtime-cwd",
        "sandbox_id": "sandbox_exec-runtime-cwd",
        "workspace_path": str(workspace),
        "worktree_path": str(workspace),
        "mode": "worktree",
        "branch_name": "sandbox/exec-runtime-cwd",
        "claimed_paths": [],
    }


class _FakePopen:
    pid = 4343

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str] | None, calls: list[dict[str, Any]]):
        self.command = command
        self.cwd = cwd
        self.env = env or {}
        self.calls = calls
        self.returncode = 0

    def communicate(self, input=None, timeout=None):
        self.calls.append(
            {
                "command": self.command,
                "cwd": self.cwd,
                "env": self.env,
                "input": input,
                "timeout": timeout,
            }
        )
        if "--output-last-message" in self.command:
            result_path = Path(self.command[self.command.index("--output-last-message") + 1])
            result_path.write_text("# Worker Result\n\nCompleted.\n", encoding="utf-8")
        return ("worker stdout", "worker stderr")

    def kill(self) -> None:
        self.returncode = -9


def _install_fake_cli(monkeypatch, *, calls: list[dict[str, Any]]) -> None:
    def fake_popen(command, *, cwd, env, stdin, stdout, stderr, text):
        return _FakePopen(list(command), cwd=Path(cwd), env=env, calls=calls)

    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", fake_popen)


class FakePullRequestGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 218): _snapshot()}
        self.branches: list[dict[str, object]] = []
        self.commits: list[dict[str, object]] = []
        self.prs: list[dict[str, object]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_or_update_branch(
        self,
        *,
        repo: str,
        branch_name: str,
        base_ref: str = "main",
    ) -> dict[str, object]:
        payload = {"repo": repo, "branch_name": branch_name, "base_ref": base_ref}
        self.branches.append(payload)
        return payload

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, object]:
        payload = {
            "repo": repo,
            "branch_name": branch_name,
            "message": message,
            "files": files,
            "commit_sha": f"sha-{len(self.commits) + 1}",
        }
        self.commits.append(payload)
        return payload

    def create_pr(
        self,
        *,
        repo: str,
        issue_number: int | None,
        branch_name: str,
        execution_id: str,
        title: str,
        body: str,
    ) -> GitHubBinding:
        self.prs.append(
            {
                "repo": repo,
                "issue_number": issue_number,
                "branch_name": branch_name,
                "execution_id": execution_id,
                "title": title,
                "body": body,
            }
        )
        return GitHubBinding(
            repo=repo,
            issue_number=issue_number or 0,
            pr_number=218,
            branch_name=branch_name,
            execution_id=execution_id,
        )


def _transitions(tmp_path: Path, execution_id: str) -> list[str]:
    return [
        str(event.payload.get("transition", ""))
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
    ]


def _ordered_subset(values: list[str], expected: list[str]) -> bool:
    remaining = iter(values)
    return all(any(value == wanted for value in remaining) for wanted in expected)
