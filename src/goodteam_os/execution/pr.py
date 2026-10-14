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

    def sync_reviews_for_execution(self, execution_id: str) -> dict[str, int]:
        run, binding = self._run_and_pr_binding(execution_id)
        reviews = self.github_client.fetch_pr_reviews(binding.repo, binding.pr_number or 0)
        comments = self.github_client.fetch_pr_comments(binding.repo, binding.pr_number or 0)
        reviews_recorded = 0
        comments_recorded = 0

        for review in reviews:
            if self._append_review_event(run, binding, review, source="review"):
                reviews_recorded += 1
        for comment in comments:
            if self._append_review_event(run, binding, comment, source="comment"):
                comments_recorded += 1
        return {"reviews_recorded": reviews_recorded, "comments_recorded": comments_recorded}

    def create_for_published_candidate(self, execution_id: str, *, head_sha: str) -> GitHubBinding:
        """Bind a PR to an already published, checked head without writing source.

        The local Operator publishes the exact candidate first. The ordinary
        summary-file transport would create a new, unreviewed commit here.
        """
        from .candidate_validation import CandidateValidationService
        from .candidate_probe import probe_candidate
        from goodteam_os.memory_storage import role_lock

        validation = CandidateValidationService(self.project_root)
        original = validation._snapshot(execution_id)
        with role_lock(validation._root(execution_id), create=False):
            current = validation._current(original)
            if (current is None or current[1]["candidate"]["head_sha"] != head_sha
                    or current[1]["observation"]["status"] not in {"passed", "failed"}):
                raise ValueError("Published candidate lacks its exact completed check receipt")
            probe_candidate(validation.source_root(original, current[0]["request"]), head_sha)
        run = self._completed_run(execution_id)
        binding = self.read_pr_binding(execution_id)
        binding = self._binding_from_existing_or_task(execution_id, binding, run)
        if (binding.repo.casefold() != original.task.repo.casefold()
                or binding.issue_number != original.task.issue_number or binding.execution_id != execution_id
                or binding.branch_name != branch_name_from_execution_id(execution_id)):
            raise ValueError("Published candidate PR binding does not match the original execution")
        selected = binding
        if binding.pr_number is None:
            binding = self.github_client.create_pr(repo=binding.repo, issue_number=binding.issue_number,
                branch_name=binding.branch_name, execution_id=execution_id, title=self._pr_title(binding),
                body=self._pr_body(run, binding))
            # Preserve the real external result even if its head observation
            # subsequently fails; recovery must not create a duplicate PR.
            self.binding_store.write_binding(binding)
        if (binding.repo != selected.repo or binding.issue_number != selected.issue_number
                or binding.execution_id != selected.execution_id or binding.branch_name != selected.branch_name
                or binding.pr_number is None):
            raise ValueError("Published candidate PR binding does not match the original execution")
        pr = self.github_client.fetch_pr(binding.repo, binding.pr_number or 0)
        if pr.get("head_sha") != head_sha or pr.get("headRefName") != binding.branch_name:
            raise ValueError("Published PR no longer matches the reviewed candidate")
        if run.pr_number != binding.pr_number:
            self.ledger.create_run(replace(run, pr_number=binding.pr_number, branch_name=binding.branch_name,
                                           updated_at=self._clock()))
        if not any(event.payload.get("transition") == "pr_opened"
                   and event.payload.get("pr_number") == binding.pr_number
                   and event.payload.get("commit_sha") == head_sha for event in self.ledger.read_events(execution_id)):
            self._append_pr_event(run, binding, commit_sha=head_sha)
        return binding

    def sync_merge_for_execution(self, execution_id: str) -> dict[str, bool]:
        run, binding = self._run_and_pr_binding(execution_id)
        pr = self.github_client.fetch_pr(binding.repo, binding.pr_number or 0)
        if not bool(pr.get("merged")):
            return {"merged": False, "recorded": False}

        merge_key = _merge_key(pr)
        if self._has_event(run.execution_id, transition="merge_completed", key=merge_key):
            return {"merged": True, "recorded": False}

        payload = {
            "transition": "merge_completed",
            "repo": binding.repo,
            "issue_number": binding.issue_number,
            "pr_number": binding.pr_number,
            "branch_name": binding.branch_name,
            "execution_id": run.execution_id,
            "worker_job_id": run.worker_job_id,
            "merged_at": _string_or_empty(pr.get("merged_at")),
            "merge_commit_sha": _string_or_empty(pr.get("merge_commit_sha")),
            "merged_by": _actor_login(pr.get("merged_by")),
            "github_id": merge_key,
        }
        self.ledger.create_run(
            replace(run, pr_number=binding.pr_number, branch_name=binding.branch_name)
        )
        self.ledger.append_event(
            _ledger_event(
                self.ledger,
                run,
                event_type=LedgerEventType.COMPLETED,
                actor=ExecutionActor.GITHUB,
                payload=payload,
                created_at=self._clock(),
            )
        )
        return {"merged": True, "recorded": True}

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

    def _run_and_pr_binding(self, execution_id: str) -> tuple[ExecutionRun, GitHubBinding]:
        run = self.ledger.read_run(execution_id)
        binding = self.read_pr_binding(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        if binding is None or binding.pr_number is None:
            raise ValueError(f"Execution PR binding not found: {execution_id}")
        return run, binding

    def _append_review_event(
        self,
        run: ExecutionRun,
        binding: GitHubBinding,
        item: dict[str, Any],
        *,
        source: str,
    ) -> bool:
        github_id = f"{source}:{_string_or_empty(item.get('id'))}"
        if github_id == f"{source}:" or self._has_event(
            run.execution_id,
            transition=_review_transition(source),
            key=github_id,
        ):
            return False

        payload = _review_payload(run, binding, item, source=source, github_id=github_id)
        self.ledger.append_event(
            _ledger_event(
                self.ledger,
                run,
                event_type=LedgerEventType.REVIEW_RECORDED,
                actor=ExecutionActor.GITHUB,
                payload=payload,
                created_at=self._clock(),
            )
        )
        return True

    def _has_event(self, execution_id: str, *, transition: str, key: str) -> bool:
        return any(
            event.payload.get("transition") == transition
            and event.payload.get("github_id") == key
            for event in self.ledger.read_events(execution_id)
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


def _review_payload(
    run: ExecutionRun,
    binding: GitHubBinding,
    item: dict[str, Any],
    *,
    source: str,
    github_id: str,
) -> dict[str, Any]:
    return {
        "transition": _review_transition(source),
        "repo": binding.repo,
        "issue_number": binding.issue_number,
        "pr_number": binding.pr_number,
        "branch_name": binding.branch_name,
        "execution_id": run.execution_id,
        "worker_job_id": run.worker_job_id,
        "github_id": github_id,
        "review_state": _string_or_empty(item.get("state")),
        "actor_login": _actor_login(item.get("user")),
        "submitted_at": _string_or_empty(item.get("submitted_at") or item.get("created_at")),
        "body_excerpt": _body_excerpt(item.get("body")),
    }


def _review_transition(source: str) -> str:
    if source == "comment":
        return "pr_comment_recorded"
    return "review_recorded"


def _merge_key(pr: dict[str, Any]) -> str:
    sha = _string_or_empty(pr.get("merge_commit_sha"))
    merged_at = _string_or_empty(pr.get("merged_at"))
    number = _string_or_empty(pr.get("number"))
    return f"merge:{number}:{sha}:{merged_at}"


def _actor_login(value: object) -> str:
    if isinstance(value, dict):
        login = value.get("login")
        if isinstance(login, str):
            return login.strip()
    return ""


def _body_excerpt(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    return normalized[:limit]


def _string_or_empty(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
