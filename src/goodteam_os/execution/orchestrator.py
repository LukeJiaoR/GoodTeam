from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from goodteam_os.execution.github import (
    GitHubClient,
    GitHubExecutionBindingStore,
    GitHubIssueRef,
    intake_github_issue as intake_github_issue_adapter,
)
from goodteam_os.execution.idempotency import (
    branch_name_from_execution_id,
    execution_id_from_task,
    task_id_from_github_issue,
)
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import (
    ExecutionActor,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionTask,
    GitHubBinding,
    LedgerEvent,
    LedgerEventType,
)
from goodteam_os.execution.sandbox import SandboxGitAdapter, SandboxService
from goodteam_os.execution.sandbox_surface import SANDBOX_SCHEMA_VERSION
from goodteam_os.workers import WorkerJobStore


Clock = Callable[[], str]


class ExecutionOrchestrator:
    def __init__(
        self,
        project_root: Path,
        github_client: GitHubClient | None = None,
        *,
        sandbox_enabled: bool = True,
        sandbox_mode: str = "auto",
        sandbox_git_adapter: SandboxGitAdapter | None = None,
    ):
        self.project_root = Path(project_root)
        self.github_client = github_client
        self.sandbox_enabled = sandbox_enabled
        self.sandbox_mode = sandbox_mode
        self.sandbox_git_adapter = sandbox_git_adapter
        self.ledger = ExecutionLedger(self.project_root)
        self.binding_store = GitHubExecutionBindingStore(self.project_root)
        self.worker_store = WorkerJobStore(self.project_root)
        self._clock: Clock = _utc_now

    def intake_github_issue(
        self,
        *,
        repo: str,
        issue_number: int,
        adapter: str = "codex",
        role: str = "implementation",
    ) -> ExecutionRun:
        if self.github_client is None:
            raise ValueError("A GitHubClient is required to intake a GitHub issue")

        issue_ref = GitHubIssueRef(repo=repo, issue_number=issue_number)
        intake_result = intake_github_issue_adapter(
            self.github_client,
            self.binding_store,
            issue_ref,
            clock=self._clock,
        )
        task = intake_result if isinstance(intake_result, ExecutionTask) else None
        execution_id = _execution_id_from_intake_result(intake_result)

        existing_run = self.ledger.read_run(execution_id)
        if existing_run is not None:
            return existing_run

        task_id = task.task_id if task is not None else task_id_from_github_issue(repo, issue_number)
        if task is not None:
            self.ledger.create_task(task)

        run = self._create_queued_run(
            task,
            execution_id=execution_id,
            task_id=task_id,
            repo=repo,
            issue_number=issue_number,
            adapter=adapter,
            role=role,
        )
        self.ledger.create_run(run)
        self._append_intake_events(run, repo=repo, issue_number=issue_number, role=role)
        return run

    def run_execution(self, execution_id: str) -> ExecutionRun:
        run = self.ledger.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")

        started_at = self._clock()
        self._append_event(
            run,
            event_type=LedgerEventType.STATUS_CHANGED,
            payload={
                "transition": "execution_started",
                "worker_job_id": run.worker_job_id,
                "adapter": run.adapter,
            },
            created_at=started_at,
        )
        sandbox_context = self._create_sandbox_context(run) if self.sandbox_enabled else None
        if sandbox_context is not None and sandbox_context.get("status") == "failed":
            return self._record_sandbox_create_failure(run, sandbox_context)

        try:
            if sandbox_context is None:
                worker_result = self.worker_store.run_job(run.worker_job_id)
            else:
                worker_result = self.worker_store.run_job(
                    run.worker_job_id,
                    sandbox_context=sandbox_context,
                )
            if worker_result.get("status") == "completed":
                return self._record_worker_success(run, worker_result)
            return self._record_worker_failure(run, worker_result)
        except Exception as exc:
            self._record_worker_exception(run, exc)
            raise
        finally:
            if sandbox_context is not None:
                self._destroy_sandbox(run.execution_id)

    def status(self, execution_id: str) -> dict[str, Any]:
        replay = self.ledger.replay(execution_id)
        run = replay["run"]
        worker_job = _find_worker_job(self.worker_store, run.worker_job_id) if run is not None else None
        return {
            "execution_id": replay["execution_id"],
            "task": _model_to_dict(replay["task"]),
            "run": _model_to_dict(run),
            "events": [_model_to_dict(event) for event in replay["events"]],
            "worker_job": worker_job,
        }

    def open_pull_request_for_execution(
        self,
        execution_id: str,
        *,
        files: dict[str, str] | None = None,
    ) -> GitHubBinding:
        if self.github_client is None:
            raise ValueError("A GitHubClient is required to open a pull request")

        from goodteam_os.execution.pr import PullRequestBindingService

        return PullRequestBindingService(
            self.project_root,
            self.github_client,
            clock=self._clock,
        ).create_or_reuse_pr(execution_id, files=files)

    def _append_event(
        self,
        run: ExecutionRun,
        *,
        event_type: LedgerEventType,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        event_number = len(self.ledger.read_events(run.execution_id)) + 1
        transition = str(payload.get("transition", event_type.value))
        event = LedgerEvent(
            event_id=f"{run.execution_id}:{event_number:04d}:{event_type.value}:{transition}",
            execution_id=run.execution_id,
            task_id=run.task_id,
            event_type=event_type,
            actor=ExecutionActor.ORCHESTRATOR,
            payload=payload,
            created_at=created_at,
        )
        self.ledger.append_event(event)

    def _create_queued_run(
        self,
        task: ExecutionTask | None,
        *,
        execution_id: str,
        task_id: str,
        repo: str,
        issue_number: int,
        adapter: str,
        role: str,
    ) -> ExecutionRun:
        worker_job = self.worker_store.create_job(
            run_id=execution_id,
            role=role,
            adapter=adapter,
            task=_worker_task(task, repo=repo, issue_number=issue_number),
            context=_worker_context(task, repo=repo, issue_number=issue_number),
            task_source=f"github:{repo.strip()}#{issue_number}",
            expected_artifacts=["worker result"],
        )
        timestamp = self._clock()
        return ExecutionRun(
            execution_id=execution_id,
            task_id=task_id,
            worker_job_id=str(worker_job["id"]),
            adapter=adapter,
            status=ExecutionRunStatus.QUEUED,
            branch_name=branch_name_from_execution_id(execution_id),
            pr_number=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _append_intake_events(
        self,
        run: ExecutionRun,
        *,
        repo: str,
        issue_number: int,
        role: str,
    ) -> None:
        payload = {
            "repo": repo.strip(),
            "issue_number": issue_number,
            "worker_job_id": run.worker_job_id,
            "adapter": run.adapter,
            "role": role,
            "branch_name": run.branch_name,
        }
        self._append_event(
            run,
            event_type=LedgerEventType.CREATED,
            payload={"transition": "intake_created", **payload},
            created_at=run.created_at,
        )
        self._append_event(
            run,
            event_type=LedgerEventType.STATUS_CHANGED,
            payload={"transition": "worker_job_created", **payload},
            created_at=run.created_at,
        )

    def _record_worker_exception(self, run: ExecutionRun, exc: Exception) -> ExecutionRun:
        failed_run = replace(run, status=ExecutionRunStatus.FAILED, updated_at=self._clock())
        self.ledger.create_run(failed_run)
        self._append_event(
            failed_run,
            event_type=LedgerEventType.FAILED,
            payload={
                "transition": "execution_failed",
                "worker_job_id": run.worker_job_id,
                "adapter": run.adapter,
                "failure_class": exc.__class__.__name__,
                "error": str(exc),
            },
            created_at=failed_run.updated_at,
        )
        return failed_run

    def _record_sandbox_create_failure(self, run: ExecutionRun, sandbox_result: dict[str, Any]) -> ExecutionRun:
        failed_run = replace(run, status=ExecutionRunStatus.FAILED, updated_at=self._clock())
        self.ledger.create_run(failed_run)
        reason = str(sandbox_result.get("reason", "sandbox_create_failed"))
        error = str(sandbox_result.get("error", ""))
        summary = f"Sandbox creation failed: {reason}"
        if error:
            summary = f"{summary}: {error}"
        self._append_event(
            failed_run,
            event_type=LedgerEventType.FAILED,
            payload={
                "transition": "execution_failed",
                "worker_job_id": run.worker_job_id,
                "adapter": run.adapter,
                "worker_status": "not_started",
                "summary": summary,
                "failure_class": "sandbox_create_failed",
                "sandbox_reason": reason,
                "sandbox_error": error,
            },
            created_at=failed_run.updated_at,
        )
        return failed_run

    def _record_worker_success(self, run: ExecutionRun, worker_result: dict[str, Any]) -> ExecutionRun:
        completed_run = replace(run, status=ExecutionRunStatus.SUCCEEDED, updated_at=self._clock())
        self.ledger.create_run(completed_run)
        self._append_event(
            completed_run,
            event_type=LedgerEventType.COMPLETED,
            payload={
                "transition": "execution_completed",
                "worker_job_id": run.worker_job_id,
                "adapter": run.adapter,
                "worker_status": worker_result.get("status"),
                "summary": worker_result.get("summary", ""),
                "result_path": worker_result.get("result_path", ""),
            },
            created_at=completed_run.updated_at,
        )
        return completed_run

    def _record_worker_failure(self, run: ExecutionRun, worker_result: dict[str, Any]) -> ExecutionRun:
        failed_run = replace(run, status=ExecutionRunStatus.FAILED, updated_at=self._clock())
        self.ledger.create_run(failed_run)
        self._append_event(
            failed_run,
            event_type=LedgerEventType.FAILED,
            payload={
                "transition": "execution_failed",
                "worker_job_id": run.worker_job_id,
                "adapter": run.adapter,
                "worker_status": worker_result.get("status"),
                "summary": worker_result.get("summary", ""),
                "failure_class": worker_result.get("failure_class", "worker_failed"),
            },
            created_at=failed_run.updated_at,
        )
        return failed_run

    def _create_sandbox_context(self, run: ExecutionRun) -> dict[str, Any]:
        return self._sandbox_service().create_sandbox(
            run.execution_id,
            claimed_paths=_sandbox_claimed_paths(run),
            mode=self._sandbox_mode_for_execution(),
        )

    def _destroy_sandbox(self, execution_id: str) -> None:
        try:
            self._sandbox_service().destroy_sandbox(execution_id)
        except Exception as exc:
            run = self.ledger.read_run(execution_id)
            if run is None:
                return
            self._append_event(
                run,
                event_type=LedgerEventType.STATUS_CHANGED,
                payload={
                    "transition": "sandbox_destroy_failed",
                    "source": "sandbox",
                    "schema_version": SANDBOX_SCHEMA_VERSION,
                    "execution_id": execution_id,
                    "status": "failed",
                    "reason": "sandbox_destroy_failed",
                    "error": str(exc),
                },
                created_at=self._clock(),
            )

    def _sandbox_mode_for_execution(self) -> str:
        mode = self.sandbox_mode.strip().lower()
        if mode != "auto":
            return mode
        return "worktree" if (self.project_root / ".git").exists() else "workspace"

    def _sandbox_service(self) -> SandboxService:
        return SandboxService(
            self.project_root,
            clock=self._clock,
            git_adapter=self.sandbox_git_adapter,
        )


def _execution_id_from_intake_result(result: ExecutionTask | GitHubBinding) -> str:
    if isinstance(result, ExecutionTask):
        return execution_id_from_task(result.task_id)
    return result.execution_id


def _worker_task(task: ExecutionTask | None, *, repo: str, issue_number: int) -> str:
    if task is None:
        return f"Implement GitHub issue {repo.strip()}#{issue_number}."
    body = task.body.strip()
    if body:
        return f"{task.title}\n\n{body}"
    return task.title


def _worker_context(task: ExecutionTask | None, *, repo: str, issue_number: int) -> str:
    title = task.title if task is not None else ""
    goal = task.goal if task is not None else ""
    body = task.body if task is not None else ""
    return (
        f"Repository: {repo.strip()}\n"
        f"Issue: #{issue_number}\n"
        f"Title: {title}\n"
        f"Goal: {goal}\n\n"
        f"{body}"
    ).strip()


def _find_worker_job(worker_store: WorkerJobStore, worker_job_id: str) -> dict[str, Any] | None:
    for job in worker_store.list_jobs():
        if job.get("id") == worker_job_id:
            return job
    return None


def _sandbox_claimed_paths(run: ExecutionRun) -> list[str]:
    return []


def _model_to_dict(value: Any) -> dict[str, Any] | None:
    return None if value is None else value.to_dict()

def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
