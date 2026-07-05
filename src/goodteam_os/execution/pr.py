from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goodteam_os.execution.github import GitHubClient, GitHubExecutionBindingStore
from goodteam_os.execution.idempotency import branch_name_from_execution_id
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import (
    ExecutionActor,
    ExecutionRun,
    ExecutionRunStatus,
    GitHubBinding,
    LedgerEvent,
    LedgerEventType,
)
from goodteam_os.workers import WorkerJobStore


Clock = Callable[[], str]


class PullRequestBindingService:
    def __init__(
        self,
        project_root: Path,
        github_client: GitHubClient,
        *,
        base_ref: str = "main",
        clock: Clock | None = None,
    ):
        self.project_root = Path(project_root)
        self.github_client = github_client
        self.base_ref = base_ref
        self._clock = clock or _utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.binding_store = GitHubExecutionBindingStore(self.project_root)
        self.worker_store = WorkerJobStore(self.project_root)

    def create_or_reuse_pr(
        self,
        execution_id: str,
        *,
        files: dict[str, str] | None = None,
    ) -> GitHubBinding:
        run = self._completed_run(execution_id)
        binding = self.read_pr_binding(execution_id)
        if binding is not None and binding.pr_number is not None:
            return binding

        binding = self._binding_from_existing_or_task(execution_id, binding, run)
        branch_name = binding.branch_name or run.branch_name or branch_name_from_execution_id(execution_id)
        self.github_client.create_or_update_branch(
            repo=binding.repo,
            branch_name=branch_name,
            base_ref=self.base_ref,
        )
        commit_result = self.github_client.commit_files(
            repo=binding.repo,
            branch_name=branch_name,
            message=f"GoodTeam execution {execution_id}",
            files=files or self._default_files(run, binding),
        )
        created = self.github_client.create_pr(
            repo=binding.repo,
            issue_number=binding.issue_number,
            branch_name=branch_name,
            execution_id=execution_id,
            title=self._pr_title(binding),
            body=self._pr_body(run, binding),
        )
        self.binding_store.write_binding(created)
        self.ledger.create_run(
            replace(
                run,
                branch_name=created.branch_name,
                pr_number=created.pr_number,
                updated_at=self._clock(),
            )
        )
        self._append_pr_event(run, created, commit_sha=_commit_sha(commit_result))
        return created

    def read_pr_binding(self, execution_id: str) -> GitHubBinding | None:
        return self.binding_store.read_binding(execution_id)

    def _completed_run(self, execution_id: str) -> ExecutionRun:
        run = self.ledger.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        if run.status is not ExecutionRunStatus.SUCCEEDED:
            raise ValueError(f"Execution is not completed: {execution_id}")
        return run

    def _binding_from_existing_or_task(
        self,
        execution_id: str,
        binding: GitHubBinding | None,
        run: ExecutionRun,
    ) -> GitHubBinding:
        if binding is not None:
            return binding
        task = self.ledger.read_task(execution_id)
        if task is None:
            raise ValueError(f"Execution binding source not found: {execution_id}")
        return GitHubBinding(
            repo=task.repo,
            issue_number=task.issue_number,
            pr_number=None,
            branch_name=run.branch_name or branch_name_from_execution_id(execution_id),
            execution_id=execution_id,
        )

    def _pr_title(self, binding: GitHubBinding) -> str:
        return f"GoodTeam execution for {binding.repo}#{binding.issue_number}"

    def _default_files(self, run: ExecutionRun, binding: GitHubBinding) -> dict[str, str]:
        return {_summary_path(run.execution_id): self._summary_markdown(run, binding)}

    def _summary_markdown(self, run: ExecutionRun, binding: GitHubBinding) -> str:
        worker_result = self._worker_result(run.worker_job_id)
        return "\n".join(
            [
                f"# GoodTeam execution {run.execution_id}",
                "",
                f"- Source issue: {binding.repo}#{binding.issue_number}",
                f"- Task id: {run.task_id}",
                f"- Worker job: {run.worker_job_id}",
                f"- Adapter: {run.adapter}",
                f"- Status: {run.status.value}",
                "",
                "## Ledger events",
                self._event_summary(run.execution_id),
                "",
                "## Worker result",
                worker_result or "No worker result was available.",
                "",
            ]
        )

    def _pr_body(self, run: ExecutionRun, binding: GitHubBinding) -> str:
        return "\n".join(
            [
                "This pull request was generated by GoodTeam from a completed execution run.",
                "",
                f"- execution id: {run.execution_id}",
                f"- task id: {run.task_id}",
                f"- worker job id: {run.worker_job_id}",
                f"- adapter: {run.adapter}",
                f"- source issue: {binding.repo}#{binding.issue_number}",
                f"- branch: {binding.branch_name}",
                "",
                "## Ledger summary",
                self._event_summary(run.execution_id),
                "",
            ]
        )

    def _event_summary(self, execution_id: str) -> str:
        events = self.ledger.read_events(execution_id)
        if not events:
            return "- No ledger events recorded."
        return "\n".join(
            f"- {event.event_type.value}: {event.payload.get('transition', event.event_type.value)}"
            for event in events
        )

    def _worker_result(self, worker_job_id: str) -> str:
        result_path = self.worker_store.root / worker_job_id / "result.md"
        if result_path.is_file():
            return result_path.read_text(encoding="utf-8").strip()

        for job in self.worker_store.list_jobs():
            if job.get("id") != worker_job_id:
                continue
            job_result_path = job.get("result_path")
            if isinstance(job_result_path, str) and job_result_path:
                path = Path(job_result_path)
                if path.is_file():
                    return path.read_text(encoding="utf-8").strip()
            return str(job.get("summary", "")).strip()
        return ""

    def _append_pr_event(
        self,
        run: ExecutionRun,
        binding: GitHubBinding,
        *,
        commit_sha: str | None,
    ) -> None:
        payload = {
            "transition": "pr_opened",
            "repo": binding.repo,
            "issue_number": binding.issue_number,
            "execution_id": run.execution_id,
            "branch_name": binding.branch_name,
            "pr_number": binding.pr_number,
            "worker_job_id": run.worker_job_id,
            "idempotent": False,
        }
        if commit_sha:
            payload["commit_sha"] = commit_sha

        self.ledger.append_event(
            _ledger_event(
                self.ledger,
                run,
                event_type=LedgerEventType.PR_OPENED,
                actor=ExecutionActor.GITHUB,
                payload=payload,
                created_at=self._clock(),
            )
        )


def _ledger_event(
    ledger: ExecutionLedger,
    run: ExecutionRun,
    *,
    event_type: LedgerEventType,
    actor: ExecutionActor,
    payload: dict[str, Any],
    created_at: str,
) -> LedgerEvent:
    event_number = len(ledger.read_events(run.execution_id)) + 1
    transition = str(payload.get("transition", event_type.value))
    return LedgerEvent(
        event_id=f"{run.execution_id}:{event_number:04d}:{event_type.value}:{transition}",
        execution_id=run.execution_id,
        task_id=run.task_id,
        event_type=event_type,
        actor=actor,
        payload=payload,
        created_at=created_at,
    )


def _summary_path(execution_id: str) -> str:
    return f"docs/dogfood/generated/{execution_id}.md"


def _commit_sha(payload: dict[str, Any]) -> str | None:
    for key in ("commit_sha", "sha", "oid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    commit = payload.get("commit")
    if isinstance(commit, dict):
        value = commit.get("sha")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
