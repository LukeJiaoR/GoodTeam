from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from goodteam_os.execution.idempotency import (
    branch_name_from_execution_id,
    execution_id_from_task,
    task_id_from_github_issue,
)
from goodteam_os.execution.models import (
    ExecutionTask,
    ExecutionTaskStatus,
    GitHubBinding,
    TaskContract,
)
from goodteam_os.execution.path_claims import normalize_repo_path


Clock = Callable[[], str]
_PATH_TOKEN_RE = re.compile(r"`([^`]+)`|(?:^|\s)([A-Za-z0-9_.-]+/[^\s`<>\[\]{}\"']+)")


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

    def create_or_update_branch(
        self,
        *,
        repo: str,
        branch_name: str,
        base_ref: str = "main",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, Any]:
        raise NotImplementedError

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
        raise NotImplementedError

    def fetch_checks(self, repo: str, ref: str) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
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

    def read_binding(self, execution_id: str) -> GitHubBinding | None:
        _validate_non_empty_string(execution_id, "execution_id")
        path = self.executions_root / execution_id / "bindings.json"
        if not path.exists():
            return None
        return self._read_binding(path)

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
    task_id = task_id_from_github_issue(snapshot.repo, snapshot.issue_number)
    return ExecutionTask(
        task_id=task_id,
        source_type="github_issue",
        repo=snapshot.repo,
        issue_number=snapshot.issue_number,
        title=snapshot.title,
        body=snapshot.body or "",
        goal=snapshot.title,
        status=ExecutionTaskStatus.QUEUED,
        created_at=timestamp,
        updated_at=timestamp,
        task_contract=_task_contract_from_github_issue(snapshot, task_id=task_id),
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
    task = execution_task_from_github_issue(snapshot, clock=clock)
    execution_id = execution_id_from_task(task.task_id)
    binding_store.write_binding(
        GitHubBinding(
            repo=task.repo,
            issue_number=task.issue_number,
            pr_number=None,
            branch_name=branch_name_from_execution_id(execution_id),
            execution_id=execution_id,
        )
    )
    return task


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


def _task_contract_from_github_issue(snapshot: GitHubIssueSnapshot, *, task_id: str) -> TaskContract:
    body = snapshot.body or ""
    sections = _body_sections(body)
    paths = _contract_focus_paths(snapshot.title, body, sections)
    return TaskContract(
        source_intent={
            "source_type": "github_issue",
            "repo": snapshot.repo,
            "issue_number": snapshot.issue_number,
            "url": snapshot.url,
            "title": snapshot.title,
            "body": body,
        },
        task_identity={
            "task_id": task_id,
            "repo": snapshot.repo,
            "issue_number": snapshot.issue_number,
            "title": snapshot.title,
        },
        summary=snapshot.title,
        instructions=body,
        constraints=sections.get("out_of_scope", []),
        expected_artifacts=[
            {"name": "worker result", "kind": "worker_result", "path": "", "required": True}
        ],
        path_scope={"focus_paths": paths, "allowed_paths": [], "excluded_paths": []},
        acceptance_criteria=sections.get("acceptance", []),
        verification_expectations=sections.get("verification", []),
        review_expectations=sections.get("review", []),
        pr_metadata={"title": snapshot.title, "body": f"Refs #{snapshot.issue_number}"},
        metadata={"labels": list(snapshot.labels)},
    )


def _body_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    active = ""
    for raw_line in body.splitlines():
        heading = _section_heading(raw_line)
        if heading:
            active = heading
            sections.setdefault(active, [])
            continue
        if active:
            item = _section_item(raw_line)
            if item:
                sections[active].append(item)
    return sections


def _section_heading(line: str) -> str:
    normalized = line.strip().rstrip(":").lower()
    normalized = normalized.replace("-", " ").replace("_", " ")
    aliases = {
        "acceptance": "acceptance",
        "acceptance criteria": "acceptance",
        "verification": "verification",
        "validation": "verification",
        "review": "review",
        "review expectations": "review",
        "out of scope": "out_of_scope",
        "constraints": "out_of_scope",
        "do not do": "out_of_scope",
    }
    return aliases.get(normalized, "")


def _section_item(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    return text[2:].strip() if text.startswith(("- ", "* ")) else ""


def _contract_focus_paths(title: str, body: str, sections: dict[str, list[str]]) -> list[str]:
    prioritized_items: list[str] = []
    for name in ("acceptance", "verification", "review"):
        prioritized_items.extend(sections.get(name, []))
    prioritized = "\n".join(prioritized_items)
    structured = sorted(_explicit_repo_paths(prioritized), key=_focus_path_sort_key)
    fallback = sorted(
        _explicit_repo_paths(f"{title}\n{body}"),
        key=_focus_path_sort_key,
    )
    return _dedupe_paths([*structured, *fallback])


def _explicit_repo_paths(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(text):
        path = normalize_repo_path(match.group(1) or match.group(2) or "")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _dedupe_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _focus_path_sort_key(path: str) -> tuple[int, str]:
    for index, prefix in enumerate(("src/", "tests/", "docs/", "scripts/", ".github/")):
        if path.startswith(prefix):
            return (index, path)
    return (99, path)
