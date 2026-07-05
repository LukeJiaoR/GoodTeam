from __future__ import annotations

import json

import pytest

from goodteam_os.execution import (
    ExecutionLedger,
    ExecutionOrchestrator,
    ExecutionRunStatus,
    GitHubBinding,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
    LedgerEventType,
)
from goodteam_os.execution.pr import PullRequestBindingService


CREATED_AT = "2026-07-04T03:16:40Z"
STARTED_AT = "2026-07-04T03:20:00Z"
FINISHED_AT = "2026-07-04T03:25:00Z"
PR_AT = "2026-07-04T03:30:00Z"
RETRY_AT = "2026-07-04T03:35:00Z"


class FakeClock:
    def __init__(self, timestamps: list[str] | None = None):
        self.timestamps = list(timestamps or [CREATED_AT])
        self.index = 0

    def __call__(self) -> str:
        if self.index >= len(self.timestamps):
            return self.timestamps[-1]
        value = self.timestamps[self.index]
        self.index += 1
        return value


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 229): _snapshot()}
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
            pr_number=123,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        raise AssertionError(f"PR tests should not fetch checks for {repo}@{ref}")


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        title="P0-1E: PR and CI Binding Loop",
        body="Bind a completed execution to a PR and observe checks.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/229",
        labels=["p0", "execution"],
    )


def _completed_execution(tmp_path, client: FakeGitHubClient):
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT])
    queued = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        adapter="fake",
    )
    return orchestrator.run_execution(queued.execution_id)


def test_completed_execution_creates_pr_once_and_persists_binding(tmp_path):
    client = FakeGitHubClient()
    run = _completed_execution(tmp_path, client)
    service = PullRequestBindingService(tmp_path, client, clock=FakeClock([PR_AT]))

    binding = service.create_or_reuse_pr(run.execution_id)

    binding_path = tmp_path / ".goodteam" / "executions" / run.execution_id / "bindings.json"
    persisted = GitHubBinding.from_json(binding_path.read_text(encoding="utf-8"))
    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert binding == GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        pr_number=123,
        branch_name=run.branch_name,
        execution_id=run.execution_id,
    )
    assert persisted == binding
    assert len(client.branches) == 1
    assert len(client.commits) == 1
    assert len(client.prs) == 1
    assert f"docs/dogfood/generated/{run.execution_id}.md" in client.commits[0]["files"]
    assert events[-1].event_type is LedgerEventType.PR_OPENED
    assert events[-1].payload["transition"] == "pr_opened"
    assert events[-1].payload["pr_number"] == 123
    assert events[-1].payload["worker_job_id"] == run.worker_job_id


def test_retry_reuses_existing_pr_binding_without_second_create(tmp_path):
    client = FakeGitHubClient()
    run = _completed_execution(tmp_path, client)
    service = PullRequestBindingService(tmp_path, client, clock=FakeClock([PR_AT, RETRY_AT]))

    first = service.create_or_reuse_pr(run.execution_id)
    second = service.create_or_reuse_pr(run.execution_id)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert second == first
    assert len(client.prs) == 1
    assert len(client.branches) == 1
    assert len(client.commits) == 1
    assert [event.payload["transition"] for event in events[-2:]] == [
        "execution_completed",
        "pr_opened",
    ]
    assert [event.event_type for event in events].count(LedgerEventType.PR_OPENED) == 1


def test_pr_body_includes_execution_issue_worker_and_event_summary(tmp_path):
    client = FakeGitHubClient()
    run = _completed_execution(tmp_path, client)

    PullRequestBindingService(tmp_path, client, clock=FakeClock([PR_AT])).create_or_reuse_pr(run.execution_id)

    [created_pr] = client.prs
    body = str(created_pr["body"])
    assert run.execution_id in body
    assert run.worker_job_id in body
    assert "LukeJiaoR/GoodTeam#229" in body
    assert "execution_completed" in body
    assert "worker_job_created" in body


def test_missing_execution_raises_clear_value_error(tmp_path):
    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        PullRequestBindingService(tmp_path, FakeGitHubClient()).create_or_reuse_pr("exec_missing")


def test_non_completed_execution_raises_clear_value_error(tmp_path):
    client = FakeGitHubClient()
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = FakeClock([CREATED_AT])
    run = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        adapter="fake",
    )

    with pytest.raises(ValueError, match=f"Execution is not completed: {run.execution_id}"):
        PullRequestBindingService(tmp_path, client).create_or_reuse_pr(run.execution_id)

    assert ExecutionLedger(tmp_path).read_run(run.execution_id).status is ExecutionRunStatus.QUEUED


def test_read_pr_binding_returns_existing_binding(tmp_path):
    client = FakeGitHubClient()
    run = _completed_execution(tmp_path, client)
    service = PullRequestBindingService(tmp_path, client, clock=FakeClock([PR_AT]))
    created = service.create_or_reuse_pr(run.execution_id)

    assert service.read_pr_binding(run.execution_id) == created
    raw = json.loads(
        (tmp_path / ".goodteam" / "executions" / run.execution_id / "bindings.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["pr_number"] == 123
