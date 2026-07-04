from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from goodteam_os.execution.idempotency import task_id_from_github_issue
from goodteam_os.execution.models import ExecutionTask, ExecutionTaskStatus, GitHubBinding


Clock = Callable[[], str]


@dataclass(frozen=True)
class GitHubIssueRef:
    repo: str
    issue_number: int

    def __post_init__(self) -> None:
        _normalize_repo(self.repo)
        _validate_issue_number(self.issue_number)


@dataclass(frozen=True)
class GitHubIssueSnapshot:
    repo: str
    issue_number: int
    title: str
    body: str | None
    url: str
    labels: list[str]

    def __post_init__(self) -> None:
        _normalize_repo(self.repo)
        _validate_issue_number(self.issue_number)
        _validate_non_empty_string(self.title, "title")
        _validate_non_empty_string(self.url, "url")
        if self.body is not None and not isinstance(self.body, str):
            raise ValueError("body must be a string or None")
        if not isinstance(self.labels, list) or not all(isinstance(label, str) for label in self.labels):
            raise ValueError("labels must be a list[str]")

        object.__setattr__(self, "body", self.body or "")
        object.__setattr__(self, "labels", list(self.labels))


class GitHubClient(Protocol):
    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        raise NotImplementedError

    def create_pr(
        self,
        *,
        repo: str,
        issue_number: int,
        branch_name: str,
        execution_id: str,
        title: str,
        body: str,
    ) -> GitHubBinding:
        raise NotImplementedError


class GitHubIssueError(RuntimeError):
    pass


class GitHubIssueNotFound(GitHubIssueError):
    def __init__(self, repo: str, issue_number: int):
        super().__init__(f"GitHub issue not found: {repo}#{issue_number}")


class GitHubIssueClosed(GitHubIssueError):
    def __init__(self, repo: str, issue_number: int):
        super().__init__(f"GitHub issue is closed: {repo}#{issue_number}")


class GitHubExecutionBindingStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.executions_root = self.project_root / ".goodteam" / "executions"

    def existing_for_issue(self, repo: str, issue_number: int) -> GitHubBinding | None:
        normalized_repo = _normalize_repo(repo)
        normalized_issue_number = _validate_issue_number(issue_number)
        if not self.executions_root.exists():
            return None

        for path in sorted(self.executions_root.glob("*/bindings.json")):
            binding = self._read_binding(path)
            if (
                _normalize_repo(binding.repo) == normalized_repo
                and binding.issue_number == normalized_issue_number
            ):
                return binding
        return None

    def write_binding(self, binding: GitHubBinding) -> Path:
        _validate_binding(binding)
        path = self.executions_root / binding.execution_id / "bindings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(binding.to_json(), encoding="utf-8")
        return path

    @staticmethod
    def _read_binding(path: Path) -> GitHubBinding:
        try:
            binding = GitHubBinding.from_json(path.read_text(encoding="utf-8"))
            _validate_binding(binding)
            return binding
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Malformed GitHub binding at {path}: {exc}") from exc


def execution_task_from_github_issue(
    snapshot: GitHubIssueSnapshot,
    *,
    clock: Clock,
) -> ExecutionTask:
    timestamp = clock()
    return ExecutionTask(
        task_id=task_id_from_github_issue(snapshot.repo, snapshot.issue_number),
        source_type="github_issue",
        repo=snapshot.repo,
        issue_number=snapshot.issue_number,
        title=snapshot.title,
        body=snapshot.body or "",
        goal=snapshot.title,
        status=ExecutionTaskStatus.QUEUED,
        created_at=timestamp,
        updated_at=timestamp,
    )


def intake_github_issue(
    client: GitHubClient,
    binding_store: GitHubExecutionBindingStore,
    issue_ref: GitHubIssueRef,
    *,
    clock: Clock,
) -> ExecutionTask | GitHubBinding:
    existing = binding_store.existing_for_issue(issue_ref.repo, issue_ref.issue_number)
    if existing is not None:
        return existing

    snapshot = client.fetch_issue(issue_ref.repo, issue_ref.issue_number)
    return execution_task_from_github_issue(snapshot, clock=clock)


def _validate_binding(binding: GitHubBinding) -> None:
    _normalize_repo(binding.repo)
    _validate_issue_number(binding.issue_number)
    if binding.pr_number is not None:
        _validate_issue_number(binding.pr_number)
    _validate_non_empty_string(binding.branch_name, "branch_name")
    _validate_non_empty_string(binding.execution_id, "execution_id")


def _normalize_repo(repo: str) -> str:
    return _validate_non_empty_string(repo, "repo").lower()


def _validate_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validate_issue_number(issue_number: int) -> int:
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number <= 0:
        raise ValueError("issue_number must be a positive int")
    return issue_number
