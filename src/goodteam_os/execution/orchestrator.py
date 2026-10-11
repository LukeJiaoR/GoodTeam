from __future__ import annotations

import json
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
from goodteam_os.execution.path_claims import PathClaimInference, infer_execution_path_claims
from goodteam_os.execution.pr_diff_contract import artifact_json_path
from goodteam_os.execution.runtime_policy import build_runtime_policy
from goodteam_os.execution.sandbox import SandboxGitAdapter, SandboxService
from goodteam_os.execution.sandbox_surface import SANDBOX_SCHEMA_VERSION
from goodteam_os.execution.task_snapshot import TaskExecutionSnapshot, TaskInputError
from goodteam_os.execution.task_snapshot_store import store_for_project
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
        input_store = store_for_project(self.project_root)
        expected_id = execution_id_from_task(task_id_from_github_issue(repo, issue_number))
        captured = input_store.load(expected_id, allow_pending=True) if input_store else None
        if captured is not None:
            request = captured.to_dict()
            if request["role"] != role or request["adapter"] != adapter:
                raise TaskInputError("task_input_operation_conflict")
            captured = input_store.commit(captured)

        def capture_before_binding(task: ExecutionTask) -> ExecutionTask:
            nonlocal captured
            if input_store is None:
                return task
            # Default task input and custom Skill method authority stay distinct.
            # A custom task keeps its original closed runtime protocol.
            context, _key = self._issue_skill_context(task, execution_id=expected_id, task_id=task.task_id,
                                                      repo=repo, issue_number=issue_number, role=role, adapter=adapter)
            if context:
                if captured is not None:
                    raise TaskInputError("task_input_protocol_conflict")
                return task
            proposed = captured or TaskExecutionSnapshot.capture(task, scope=input_store.scope, role=role, adapter=adapter)
            captured = input_store.commit(proposed)
            return captured.task

        intake_result = intake_github_issue_adapter(
            self.github_client,
            self.binding_store,
            issue_ref,
            clock=self._clock,
            before_binding=capture_before_binding,
            recovered_task=captured.task if captured else None,
        )
        task = captured.task if captured else (intake_result if isinstance(intake_result, ExecutionTask) else None)
        execution_id = _execution_id_from_intake_result(intake_result)
        bound_input = input_store.load(execution_id) if input_store else None
        if bound_input is not None:
            if bound_input.execution_id != expected_id:
                raise TaskInputError("task_input_job_mismatch")
            captured, task = bound_input, bound_input.task

        existing_run = self.ledger.read_run(execution_id)
        if existing_run is not None:
            original_input = self.worker_store.admit_task_input(existing_run.worker_job_id)
            if original_input is not None:
                _require_snapshot_run(original_input, existing_run, expected_id)
                request = original_input.to_dict()
                if request["role"] != role or request["adapter"] != adapter:
                    raise TaskInputError("task_input_operation_conflict")
            if captured is not None and (original_input is None or original_input.data != captured.data):
                raise TaskInputError("task_input_job_mismatch")
            from goodteam_os.skills.worker_requests import is_skill_job_id, original_worker_request
            from goodteam_os.skills.models import SkillConflict, SkillError

            if is_skill_job_id(existing_run.worker_job_id):
                job = _find_worker_job(self.worker_store, existing_run.worker_job_id)
                if job is None or not job.get("task_skill_context"):
                    raise SkillError("task_context_missing", "The original issue execution lost its method reference.")
                original = original_worker_request(self.worker_store, command_key=str(job.get("idempotency_key", "")),
                                                   context=job["task_skill_context"])
                if original is None or original["role"] != role or original["adapter"] != adapter:
                    raise SkillConflict("operation_conflict", "Issue execution already owns another Worker request.")
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
            task_input=captured,
        )
        self.ledger.create_run(run)
        self._append_intake_events(run, task=task, repo=repo, issue_number=issue_number, role=role)
        return run

    def run_execution(self, execution_id: str) -> ExecutionRun:
        run = self.ledger.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")

        # Admission precedes sandbox creation and every adapter, including fake.
        input_authority = store_for_project(self.project_root, existing_only=True)
        original_input = input_authority.load(execution_id) if input_authority else None
        admitted_input = self.worker_store.admit_task_input(run.worker_job_id)
        for snapshot in (original_input, admitted_input):
            if snapshot is not None:
                _require_snapshot_run(snapshot, run, execution_id)
        if original_input is not None and (admitted_input is None or admitted_input.data != original_input.data):
            raise TaskInputError("task_input_job_mismatch")
        if admitted_input is not None and run.status is ExecutionRunStatus.SUCCEEDED:
            return run

        from goodteam_os.skills.worker_requests import is_skill_job_id

        if is_skill_job_id(run.worker_job_id):
            # S5 remains closed; validate and record the blocked attempt before
            # creating a sandbox, probing a CLI, or touching a target checkout.
            result = self.worker_store.run_job(run.worker_job_id)
            return self._record_worker_failure(run, result)
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

        retain_sandbox = False
        try:
            if sandbox_context is None:
                worker_result = self.worker_store.run_job(run.worker_job_id)
            else:
                worker_result = self.worker_store.run_job(
                    run.worker_job_id,
                    sandbox_context=sandbox_context,
                )
            if worker_result.get("status") == "completed":
                retain_sandbox = _should_retain_sandbox_for_pr(sandbox_context)
                return self._record_worker_success(run, worker_result)
            return self._record_worker_failure(run, worker_result)
        except Exception as exc:
            self._record_worker_exception(run, exc)
            raise
        finally:
            if sandbox_context is not None:
                if retain_sandbox:
                    self._retain_sandbox(run.execution_id)
                else:
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
        task_input: TaskExecutionSnapshot | None = None,
    ) -> ExecutionRun:
        from goodteam_os.skills.worker_requests import WORKER_METADATA_LOADER, subject_for_role

        # Intake already persisted the issue task. Recovery uses that original
        # body instead of manufacturing a shorter task from an existing binding.
        if task is None:
            task = self.ledger.read_task(execution_id)
        context, key = self._issue_skill_context(task, execution_id=execution_id, task_id=task_id,
                                                  repo=repo, issue_number=issue_number, role=role, adapter=adapter)
        skill_fields = {}
        if context:
            if task_input is not None:
                raise TaskInputError("task_input_protocol_conflict")
            skill_fields = {"task_skill_context": context, "skill_subject": subject_for_role(role),
                            "skill_loader_version": WORKER_METADATA_LOADER,
                            "project_key": key, "execution_id": execution_id,
                            "idempotency_key": f"issue-worker:{key}:{execution_id}"}
        elif task_input is not None:
            skill_fields = {"task_input": task_input, "execution_id": execution_id,
                            "idempotency_key": task_input.to_dict()["operation_id"]}
        input_fields = {"task": _worker_task(task, repo=repo, issue_number=issue_number),
                        "context": _worker_context(task, repo=repo, issue_number=issue_number),
                        "expected_artifacts": ["worker result"]}
        if task_input is not None:
            from goodteam_os.execution.task_context import task_worker_fields
            input_fields = task_worker_fields(task_input)
        worker_job, _created = self.worker_store.create_or_reuse_job(
            **skill_fields,
            **input_fields,
            run_id=execution_id,
            role=role,
            adapter=adapter,
            task_source=f"github:{task_input.task.repo if task_input else repo.strip()}#{issue_number}",
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

    def _issue_skill_context(
        self, task: ExecutionTask | None, *, execution_id: str, task_id: str,
        repo: str, issue_number: int, role: str, adapter: str,
    ) -> tuple[dict[str, Any], str]:
        from goodteam_os.skills.task_locks import TaskSkillIdentity
        from goodteam_os.skills.task_start import capture_task_start, registered_project_scope

        scope = registered_project_scope(self.project_root)
        if scope is None:
            return {}, ""
        workspace, key = scope
        context = capture_task_start(
            workspace=workspace, project_key=key, worker_root=self.project_root,
            task=TaskSkillIdentity("issue_execution", key, execution_id),
            command_key=f"issue-start:{key}:{execution_id}",
            confirmed_input={"task_id": task_id, "source": f"github:{repo.strip().lower()}#{issue_number}",
                             "task": _worker_task(task, repo=repo, issue_number=issue_number),
                             "context": _worker_context(task, repo=task.repo if task is not None else repo, issue_number=issue_number),
                             "role": role, "adapter": adapter},
        )
        return context, key

    def _append_intake_events(
        self,
        run: ExecutionRun,
        *,
        task: ExecutionTask | None,
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
        original_job = _find_worker_job(self.worker_store, run.worker_job_id)
        if original_job:
            payload.update({key: original_job[key] for key in ("task_skill_context", "skill_execution_snapshot_ref")
                            if key in original_job})
        self._append_event(
            run,
            event_type=LedgerEventType.CREATED,
            payload={"transition": "intake_created", **payload},
            created_at=run.created_at,
        )
        runtime_policy = build_runtime_policy(
            project_root=self.project_root,
            task=task,
            run=run,
            sandbox_enabled=self.sandbox_enabled,
            sandbox_mode=self._sandbox_mode_for_execution(),
        )
        self._append_event(
            run,
            event_type=LedgerEventType.STATUS_CHANGED,
            payload={
                "transition": "worker_job_created",
                **payload,
                "runtime_policy": runtime_policy.to_dict(),
            },
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
        inference = _sandbox_claim_inference(run, self.project_root, self.worker_store)
        return self._sandbox_service().create_sandbox(
            run.execution_id,
            claimed_paths=inference.claimed_paths,
            path_claims=[claim.to_dict() for claim in inference.claims],
            path_claim_warnings=inference.warnings,
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

    def _retain_sandbox(self, execution_id: str) -> None:
        try:
            self._sandbox_service().retain_sandbox(execution_id, reason="pr_pending")
        except Exception as exc:
            run = self.ledger.read_run(execution_id)
            if run is None:
                return
            self._append_event(
                run,
                event_type=LedgerEventType.STATUS_CHANGED,
                payload={
                    "transition": "sandbox_retain_failed",
                    "source": "sandbox",
                    "schema_version": SANDBOX_SCHEMA_VERSION,
                    "execution_id": execution_id,
                    "status": "failed",
                    "reason": "sandbox_retain_failed",
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


def _require_snapshot_run(snapshot: TaskExecutionSnapshot, run: ExecutionRun, caller_execution_id: str) -> None:
    # Both directions matter: a captured task cannot route to a legacy job, and
    # an uncaptured caller cannot borrow somebody else's captured job.
    if (snapshot.execution_id != caller_execution_id or run.execution_id != caller_execution_id
            or run.task_id != snapshot.task.task_id or run.worker_job_id != snapshot.job_id
            or run.adapter != snapshot.to_dict()["adapter"]):
        raise TaskInputError("task_input_job_mismatch")


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


def _sandbox_claim_inference(
    run: ExecutionRun,
    project_root: Path,
    worker_store: WorkerJobStore,
) -> PathClaimInference:
    job_root = worker_store.root / run.worker_job_id
    worker_job = _find_worker_job(worker_store, run.worker_job_id) or {}
    task = ExecutionLedger(project_root).read_task(run.execution_id)
    snapshot = worker_store.admit_task_input(run.worker_job_id)
    if snapshot is not None:
        task = snapshot.task
        return infer_execution_path_claims(
            task_text=task.body, context_text=task.goal,
            expected_artifacts=task.task_contract.expected_artifacts,
            existing_changed_files=_existing_changed_files(project_root, run.execution_id, worker_job),
            task_contract=task.task_contract.to_dict(),
        )
    return infer_execution_path_claims(
        task_text=_read_text(job_root / "task.md"),
        context_text=_read_text(job_root / "context.md"),
        expected_artifacts=_read_json(job_root / "expected_artifacts.json")
        or worker_job.get("expected_artifacts"),
        existing_changed_files=_existing_changed_files(project_root, run.execution_id, worker_job),
        task_contract=task.task_contract.to_dict() if task and task.task_contract else None,
    )


def _existing_changed_files(project_root: Path, execution_id: str, worker_job: dict[str, Any]) -> list[Any]:
    changed_files: list[Any] = []
    worker_files = worker_job.get("changed_files")
    if isinstance(worker_files, list):
        changed_files.extend(worker_files)
    diff_payload = _read_json(artifact_json_path(project_root, execution_id))
    diff_files = diff_payload.get("changed_files") if isinstance(diff_payload, dict) else None
    if isinstance(diff_files, list):
        changed_files.extend(diff_files)
    return changed_files


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def _should_retain_sandbox_for_pr(sandbox_context: dict[str, Any] | None) -> bool:
    if not isinstance(sandbox_context, dict):
        return False
    return sandbox_context.get("mode") == "worktree"


def _model_to_dict(value: Any) -> dict[str, Any] | None:
    return None if value is None else value.to_dict()

def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
