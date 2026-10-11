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
from goodteam_os.execution.review_workbench_browser import (
    export_review_workbench_html_artifacts,
)
from goodteam_os.execution.sandbox import SandboxGitAdapter, SandboxService
from goodteam_os.workers import WorkerJobStore


Clock = Callable[[], str]


class ExecutionAPI:
    def __init__(
        self,
        project_root: Path,
        *,
        github_client: GitHubClient | None = None,
        clock: Clock | None = None,
        sandbox_git_adapter: SandboxGitAdapter | None = None,
        codex_review_admission=None,
    ):
        from goodteam_os.skills.codex_runtime import review_worker_kwargs

        self.review_runtime_kwargs = review_worker_kwargs(codex_review_admission)
        self.project_root = Path(project_root)
        self.github_client = github_client
        self.clock = clock
        self.sandbox_git_adapter = sandbox_git_adapter
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

    def validate_candidate(self, execution_id: str, **request) -> dict[str, Any]:
        """Explicit local Operator action; deliberately not exposed by HTTP POST."""
        from goodteam_os.execution.candidate_validation import CandidateValidationService
        return CandidateValidationService(self.project_root).validate(execution_id, **request)

    def validate_and_review_candidate(self, execution_id: str, *, review_base="",
                                      acknowledge_external_cost=False, **request) -> dict[str, Any]:
        """Local CLI entry: reuse its exact check receipt without an approval handoff.

        The startup authority supplies selection, never task/issue/model text.
        Resumption preserves the first base, Reviewer and budgets.
        """
        import os
        import shutil
        from .automatic_review import AutomaticCandidateReviewService
        from .task_snapshot import TaskInputError

        service = AutomaticCandidateReviewService(self.project_root, **self.review_runtime_kwargs)
        saved = service.configuration(execution_id)
        selected = None
        if saved is not None:
            if review_base and review_base != saved["base_sha"]:
                raise TaskInputError("automatic_review_already_started")
            command = list(request["argv"])
            if command:
                command[0] = command[0] if Path(command[0]).is_absolute() else shutil.which(command[0], path=os.defpath)
            contract = {"argv": command, "criteria": sorted(request["criteria"]), "seconds": request.get("seconds", 30)}
            if contract != saved["validation_contract"]:
                raise TaskInputError("correction_check_changed")
        elif review_base or self.review_runtime_kwargs:
            if not review_base:
                raise ValueError("--review-base must name the original exact base for the first automatic review")
            from goodteam_os.skills.codex_local import LocalCodexReviewAdmission
            from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission

            admission = self.review_runtime_kwargs.get("codex_review_admission")
            if not isinstance(admission, (LocalCodexReviewAdmission, TrustedCodexReviewAdmission)):
                raise ValueError("Automatic CLI start requires an already composed local or trusted Codex Reviewer policy")
            selected = {"reviewer_adapter": "codex", "reviewer_model": admission.policy["model"]}
        result = self.validate_candidate(execution_id, **request)
        if saved is None and selected is None:
            return result
        if saved is not None:
            # Idempotently complete a start interrupted before its task binding.
            service.start(execution_id, **{key: saved[key] for key in
                ("base_sha", "reviewer_adapter", "reviewer_model", "max_rounds", "wall_seconds", "fix_seconds")},
                receipt_id=saved["initial_receipt"], head_sha=saved["initial_head"])
        else:
            service.start(execution_id, base_sha=review_base, receipt_id=result["receipt_id"],
                          head_sha=result["candidate"]["head_sha"], **selected)
        return {**result, "automatic_review": service.advance(execution_id,
            acknowledge_external_cost=acknowledge_external_cost)}

    def attest_candidate_review(self, execution_id: str, **request) -> dict[str, Any]:
        """Record an explicit Operator attestation, not authenticated independence."""
        from goodteam_os.execution.candidate_validation import CandidateValidationService
        return CandidateValidationService(self.project_root).attest_review(execution_id, **request)

    def start_automatic_review(self, execution_id: str, **request) -> dict[str, Any]:
        """Trusted composition only; captures exact base and original policy."""
        from .automatic_review import AutomaticCandidateReviewService
        return AutomaticCandidateReviewService(self.project_root, **self.review_runtime_kwargs).start(execution_id, **request)

    def advance_automatic_review(self, execution_id: str, *, acknowledge_external_cost=False) -> dict[str, Any]:
        from .automatic_review import AutomaticCandidateReviewService
        return AutomaticCandidateReviewService(self.project_root, **self.review_runtime_kwargs).advance(
            execution_id, acknowledge_external_cost=acknowledge_external_cost)

    def stop_automatic_review(self, execution_id: str) -> dict[str, Any]:
        from .automatic_review import AutomaticCandidateReviewService
        return AutomaticCandidateReviewService(self.project_root, **self.review_runtime_kwargs).stop(execution_id)

    def start_correction_loop(self, execution_id: str, **request) -> dict[str, Any]:
        from goodteam_os.execution.correction_loop import CorrectionLoopService
        return CorrectionLoopService(self.project_root).start(execution_id, **request)

    def prepare_correction_worker(self, execution_id: str, **request) -> dict[str, Any]:
        from .correction_worker import CorrectionWorkerService
        return CorrectionWorkerService(self.project_root).prepare(execution_id, **request)

    def run_correction_worker(self, execution_id: str, job_id: str, *, acknowledge_external_cost=False) -> dict[str, Any]:
        from .correction_worker import CorrectionWorkerService
        return CorrectionWorkerService(self.project_root).run(execution_id, job_id,
            acknowledge_external_cost=acknowledge_external_cost)

    def stop_correction_loop(self, execution_id: str, *, loop_id: str) -> dict[str, Any]:
        from goodteam_os.execution.correction_loop import CorrectionLoopService
        return CorrectionLoopService(self.project_root).stop(execution_id, loop_id=loop_id)

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

    def export_review_workbench_html(self, execution_id: str) -> dict[str, str]:
        workbench = self.get_review_workbench(execution_id)
        return export_review_workbench_html_artifacts(self.project_root, workbench)

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

    def create_sandbox(
        self,
        execution_id: str,
        *,
        claimed_paths: list[str] | None = None,
        path_claims: list[dict[str, Any]] | None = None,
        path_claim_warnings: list[str] | None = None,
        mode: str = "workspace",
    ) -> dict[str, Any]:
        return self._sandbox_service().create_sandbox(
            execution_id,
            claimed_paths=claimed_paths or [],
            path_claims=path_claims or [],
            path_claim_warnings=path_claim_warnings or [],
            mode=mode,
        )

    def get_sandbox(self, execution_id: str) -> dict[str, Any]:
        return self._sandbox_service().get_sandbox(execution_id)

    def destroy_sandbox(self, execution_id: str) -> dict[str, Any]:
        return self._sandbox_service().destroy_sandbox(execution_id)

    def run_sandbox_command(
        self,
        execution_id: str,
        argv: list[str] | str,
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._sandbox_service().run_command(
            execution_id,
            argv,
            cwd=cwd,
            env=env,
        )

    def replay(self, execution_id: str) -> dict[str, Any]:
        run = self.ledger_store.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        return self._status_payload(execution_id)

    def ledger(self, execution_id: str) -> dict[str, Any]:
        return self.replay(execution_id)

    def _orchestrator(self) -> ExecutionOrchestrator:
        orchestrator = ExecutionOrchestrator(
            self.project_root,
            self.github_client,
            sandbox_git_adapter=self.sandbox_git_adapter,
        )
        if self.clock is not None:
            orchestrator._clock = self.clock
        return orchestrator

    def _sandbox_service(self) -> SandboxService:
        return SandboxService(
            self.project_root,
            clock=self.clock,
            git_adapter=self.sandbox_git_adapter,
        )

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
