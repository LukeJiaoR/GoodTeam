from __future__ import annotations

import pytest

from goodteam_os.execution.github import (
    GitHubExecutionBindingStore,
    GitHubIssueNotFound,
    GitHubIssueRef,
    GitHubIssueSnapshot,
    execution_task_from_github_issue,
    intake_github_issue,
)
from goodteam_os.execution.idempotency import (
    branch_name_from_execution_id,
    execution_id_from_task,
    task_id_from_github_issue,
)
from goodteam_os.execution.models import ExecutionTaskStatus, GitHubBinding


CREATED_AT = "2026-07-04T03:16:40Z"


def fixed_clock() -> str:
    return CREATED_AT


class FakeGitHubClient:
    def __init__(self, issues: dict[tuple[str, int], GitHubIssueSnapshot]):
        self.issues = issues
        self.fetches: list[tuple[str, int]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        self.fetches.append((repo, issue_number))
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_pr(self, **_kwargs) -> GitHubBinding:
        raise AssertionError("fake client should not create pull requests in these tests")


def _snapshot(body: str | None = "Implement the GitHub intake boundary.") -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=227,
        title="P0-1C: GitHub Issue Intake and Binding Adapter",
        body=body,
        url="https://github.com/LukeJiaoR/GoodTeam/issues/227",
        labels=["p0", "execution", "github"],
    )


def test_fixture_github_issue_becomes_execution_task():
    snapshot = _snapshot()

    task = execution_task_from_github_issue(
        snapshot,
        clock=fixed_clock,
    )

    assert task.task_id == task_id_from_github_issue("LukeJiaoR/GoodTeam", 227)
    assert task.source_type == "github_issue"
    assert task.repo == "LukeJiaoR/GoodTeam"
    assert task.issue_number == 227
    assert task.title == "P0-1C: GitHub Issue Intake and Binding Adapter"
    assert task.body == "Implement the GitHub intake boundary."
    assert task.goal == "P0-1C: GitHub Issue Intake and Binding Adapter"
    assert task.status is ExecutionTaskStatus.QUEUED
    assert task.created_at == CREATED_AT
    assert task.updated_at == CREATED_AT
    assert task.task_contract is not None
    assert task.task_contract.schema_version == "task_contract.v1"
    assert task.task_contract.summary == "P0-1C: GitHub Issue Intake and Binding Adapter"
    assert task.task_contract.task_identity["task_id"] == task.task_id
    assert task.task_contract.source_intent["source_type"] == "github_issue"


def test_empty_body_issue_becomes_empty_task_body():
    snapshot = _snapshot(body=None)

    task = execution_task_from_github_issue(
        snapshot,
        clock=fixed_clock,
    )

    assert snapshot.body == ""
    assert task.body == ""
    assert task.task_contract is not None
    assert task.task_contract.instructions == ""


def test_github_issue_body_populates_contract_sections():
    snapshot = _snapshot(
        body=(
            "Goal: typed execution contract.\n\n"
            "Acceptance:\n"
            "- Persist `task_contract.v1` in task.json.\n"
            "- Surface acceptance criteria in Review Workbench.\n\n"
            "Verification:\n"
            "- PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution_models.py -q\n\n"
            "Review:\n"
            "- Reviewer checks contract summary.\n\n"
            "Out of scope:\n"
            "- cleanup UX\n"
            "Touch `src/goodteam_os/execution/models.py` and `tests/test_execution_models.py`."
        )
    )

    task = execution_task_from_github_issue(snapshot, clock=fixed_clock)

    assert task.task_contract is not None
    assert task.task_contract.acceptance_criteria == [
        "Persist `task_contract.v1` in task.json.",
        "Surface acceptance criteria in Review Workbench.",
    ]
    assert task.task_contract.verification_expectations == [
        "PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution_models.py -q"
    ]
    assert task.task_contract.review_expectations == ["Reviewer checks contract summary."]
    assert task.task_contract.constraints == ["cleanup UX"]
    assert task.task_contract.path_scope["focus_paths"] == [
        "tests/test_execution_models.py",
        "src/goodteam_os/execution/models.py",
    ]
    assert task.task_contract.expected_artifacts == [
        {"name": "worker result", "kind": "worker_result", "path": "", "required": True}
    ]


def test_snapshot_copies_labels_without_aliasing_input():
    labels = ["p0", "execution", "github"]
    snapshot = GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam", issue_number=227, title="Intake",
        body="", url="https://github.com/LukeJiaoR/GoodTeam/issues/227",
        labels=labels,
    )
    labels.append("changed-after-capture")

    assert snapshot.labels == ["p0", "execution", "github"]


def test_write_binding_then_existing_for_issue_returns_same_binding(tmp_path):
    store = GitHubExecutionBindingStore(tmp_path)
    binding = GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=227,
        pr_number=99,
        branch_name="goodteam/exec_123",
        execution_id="exec_123",
    )

    path = store.write_binding(binding)

    assert path == tmp_path / ".goodteam" / "executions" / "exec_123" / "bindings.json"
    assert store.existing_for_issue(" lukejiaor/goodteam ", 227) == binding


def test_first_intake_writes_durable_issue_binding(tmp_path):
    store = GitHubExecutionBindingStore(tmp_path)
    client = FakeGitHubClient({("lukejiaor/goodteam", 227): _snapshot()})
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 227)
    execution_id = execution_id_from_task(task_id)

    result = intake_github_issue(
        client,
        store,
        GitHubIssueRef(repo="LukeJiaoR/GoodTeam", issue_number=227),
        clock=fixed_clock,
    )

    assert result.task_id == task_id
    assert store.existing_for_issue(" lukejiaor/goodteam ", 227) == GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=227,
        pr_number=None,
        branch_name=branch_name_from_execution_id(execution_id),
        execution_id=execution_id,
    )


def test_duplicate_intake_returns_existing_binding_without_second_binding(tmp_path):
    store = GitHubExecutionBindingStore(tmp_path)
    task_id = task_id_from_github_issue("LukeJiaoR/GoodTeam", 227)
    execution_id = execution_id_from_task(task_id)
    expected_binding = GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=227,
        pr_number=None,
        branch_name=branch_name_from_execution_id(execution_id),
        execution_id=execution_id,
    )
    client = FakeGitHubClient({("lukejiaor/goodteam", 227): _snapshot()})

    first = intake_github_issue(
        client,
        store,
        GitHubIssueRef(repo="LukeJiaoR/GoodTeam", issue_number=227),
        clock=fixed_clock,
    )
    result = intake_github_issue(
        client,
        store,
        GitHubIssueRef(repo=" lukejiaor/goodteam ", issue_number=227),
        clock=fixed_clock,
    )

    assert first.task_id == task_id
    assert result == expected_binding
    assert client.fetches == [("LukeJiaoR/GoodTeam", 227)]
    assert len(list((tmp_path / ".goodteam" / "executions").glob("*/bindings.json"))) == 1


def test_missing_issue_raises_clear_exception(tmp_path):
    client = FakeGitHubClient({})

    with pytest.raises(GitHubIssueNotFound, match="GitHub issue not found: LukeJiaoR/GoodTeam#404"):
        intake_github_issue(
            client,
            GitHubExecutionBindingStore(tmp_path),
            GitHubIssueRef(repo="LukeJiaoR/GoodTeam", issue_number=404),
            clock=fixed_clock,
        )


def test_malformed_bindings_json_raises_value_error_with_path(tmp_path):
    path = tmp_path / ".goodteam" / "executions" / "exec_bad" / "bindings.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        GitHubExecutionBindingStore(tmp_path).existing_for_issue("LukeJiaoR/GoodTeam", 227)

    message = str(exc_info.value)
    assert str(path) in message
    assert "Malformed GitHub binding" in message
