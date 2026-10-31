from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.github import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.models import ExecutionRunStatus
from goodteam_os.execution.orchestrator import ExecutionOrchestrator
from goodteam_os.workers import WorkerJobStore

from execution_fixture import FakeClock, FakeGitHubClient, _snapshot as _orchestrator_snapshot


def _snapshot() -> GitHubIssueSnapshot:
    # This suite requests #218; the shared orchestrator fixture represents #228.
    # A source adapter must never silently return another issue's task.
    return replace(_orchestrator_snapshot(), issue_number=218,
                   url="https://github.com/LukeJiaoR/GoodTeam/issues/218")


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
