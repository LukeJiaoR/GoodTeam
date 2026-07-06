from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from goodteam_os.execution.ci import CIStatusService
from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.github import GitHubClient, GitHubExecutionBindingStore
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRunStatus
from goodteam_os.execution.orchestrator import ExecutionOrchestrator
from goodteam_os.execution.pr import PullRequestBindingService
from goodteam_os.execution.pr_diff import PullRequestDiffArtifactService
from goodteam_os.execution.review_workbench import ReviewWorkbenchService
from goodteam_os.workers import WorkerJobStore


Clock = Callable[[], str]


class ExecutionAPI:
    def __init__(
        self,
        project_root: Path,
        *,
        github_client: GitHubClient | None = None,
        clock: Clock | None = None,
    ):
        self.project_root = Path(project_root)
        self.github_client = github_client
        self.clock = clock
        self.ledger_store = ExecutionLedger(self.project_root)
        self.binding_store = GitHubExecutionBindingStore(self.project_root)
        self.worker_store = WorkerJobStore(self.project_root)

    def intake_github_issue(
        self,
        *,
        repo: str,
        issue_number: int,
        adapter: str = "fake",
        role: str = "implementation",
    ) -> dict[str, Any]:
        self._require_github_client()
        orchestrator = self._orchestrator()
        run = orchestrator.intake_github_issue(
            repo=repo,
            issue_number=issue_number,
            adapter=adapter,
            role=role,
        )
        return self._status_payload(run.execution_id)

    def run_execution(self, execution_id: str) -> dict[str, Any]:
        run = self.ledger_store.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        if run.status is not ExecutionRunStatus.SUCCEEDED:
            run = self._orchestrator().run_execution(execution_id)
        return self._status_payload(run.execution_id)

    def create_or_reuse_pr(self, execution_id: str) -> dict[str, Any]:
        self._require_github_client()
        service = PullRequestBindingService(
            self.project_root,
            self.github_client,
            clock=self.clock,
        )
        binding = service.create_or_reuse_pr(execution_id)
        return self._status_payload(execution_id, binding=binding.to_dict())

    def observe_ci(self, execution_id: str) -> dict[str, Any]:
        self._require_github_client()
        summary = CIStatusService(
            self.project_root,
            self.github_client,
            clock=self.clock,
        ).observe_execution_checks(execution_id)
        return self._status_payload(execution_id, ci=summary)

    def sync_reviews(self, execution_id: str) -> dict[str, Any]:
        self._require_github_client()
        result = PullRequestBindingService(
            self.project_root,
            self.github_client,
            clock=self.clock,
        ).sync_reviews_for_execution(execution_id)
        return self._status_payload(execution_id, reviews=result)

    def sync_merge(self, execution_id: str) -> dict[str, Any]:
        self._require_github_client()
        result = PullRequestBindingService(
            self.project_root,
            self.github_client,
            clock=self.clock,
        ).sync_merge_for_execution(execution_id)
        return self._status_payload(execution_id, merge=result)

    def finalize(self, execution_id: str) -> dict[str, Any]:
        return ExecutionEvidenceService(
            self.project_root,
            clock=self.clock,
        ).finalize_provenance(execution_id)

    def get_evidence(self, execution_id: str) -> dict[str, Any]:
        return ExecutionEvidenceService(
            self.project_root,
            clock=self.clock,
        ).get_evidence(execution_id)

    def audit_memory_provenance(self, execution_id: str) -> dict[str, Any]:
        return ExecutionEvidenceService(
            self.project_root,
            clock=self.clock,
        ).audit_memory_provenance(execution_id)

    def get_review_workbench(self, execution_id: str) -> dict[str, Any]:
        return ReviewWorkbenchService(
            self.project_root,
            clock=self.clock,
        ).get_review(execution_id)

    def persist_pr_diff(self, execution_id: str) -> dict[str, Any]:
        return PullRequestDiffArtifactService(
            self.project_root,
            self.github_client,
            clock=self.clock,
        ).persist(execution_id)

    def record_review_decision(
        self,
        execution_id: str,
        decision: str,
        reviewer: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return ReviewWorkbenchService(
            self.project_root,
            clock=self.clock,
        ).record_decision(execution_id, decision, reviewer, reason)

    def replay(self, execution_id: str) -> dict[str, Any]:
        run = self.ledger_store.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        return self._status_payload(execution_id)

    def ledger(self, execution_id: str) -> dict[str, Any]:
        return self.replay(execution_id)

    def _orchestrator(self) -> ExecutionOrchestrator:
        orchestrator = ExecutionOrchestrator(self.project_root, self.github_client)
        if self.clock is not None:
            orchestrator._clock = self.clock
        return orchestrator

    def _status_payload(
        self,
        execution_id: str,
        *,
        binding: dict[str, Any] | None = None,
        ci: dict[str, Any] | None = None,
        reviews: dict[str, Any] | None = None,
        merge: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        replay = self.ledger_store.replay(execution_id)
        run = replay["run"]
        worker_job = self._worker_job(run.worker_job_id) if run is not None else None
        stored_binding = binding if binding is not None else self._binding(execution_id)
        payload: dict[str, Any] = {
            "status": "ok",
            "execution_id": execution_id,
            "task": self._json_ready(replay["task"]),
            "run": self._json_ready(run),
            "events": [self._json_ready(event) for event in replay["events"]],
            "worker_job": self._json_ready(worker_job),
            "worker_jobs": self._json_ready(self._worker_jobs_for_execution(execution_id)),
        }
        if stored_binding is not None:
            payload["binding"] = self._json_ready(stored_binding)
        if ci is not None:
            payload["ci"] = self._json_ready(ci)
        if reviews is not None:
            payload["reviews"] = self._json_ready(reviews)
        if merge is not None:
            payload["merge"] = self._json_ready(merge)
        return payload

    def _worker_job(self, worker_job_id: str) -> dict[str, Any] | None:
        for job in self.worker_store.list_jobs():
            if job.get("id") == worker_job_id:
                return job
        return None

    def _worker_jobs_for_execution(self, execution_id: str) -> list[dict[str, Any]]:
        return [job for job in self.worker_store.list_jobs() if job.get("run_id") == execution_id]

    def _binding(self, execution_id: str) -> dict[str, Any] | None:
        binding = self.binding_store.read_binding(execution_id)
        return binding.to_dict() if binding is not None else None

    def _require_github_client(self) -> None:
        if self.github_client is None:
            raise ValueError("GitHubClient is required")

    @staticmethod
    def _json_ready(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return json.loads(json.dumps(value))
