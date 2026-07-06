from __future__ import annotations

from pathlib import Path

import pytest

from goodteam_os.execution import (
    ExecutionLedger,
    ExecutionOrchestrator,
    GitHubBinding,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
    LedgerEventType,
)
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.pr import PullRequestBindingService


CREATED_AT = "2026-07-06T02:30:00Z"
STARTED_AT = "2026-07-06T02:31:00Z"
FINISHED_AT = "2026-07-06T02:32:00Z"
PR_AT = "2026-07-06T02:33:00Z"
SYNC_AT = "2026-07-06T02:34:00Z"


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
        self.issues = {("lukejiaor/goodteam", 219): _snapshot()}
        self.checks: dict[str, object] = {
            "check_runs": [
                {
                    "id": 9001,
                    "name": "backend",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
        self.reviews: list[dict[str, object]] = [
            {
                "id": 7001,
                "state": "APPROVED",
                "body": "Looks good.",
                "submitted_at": "2026-07-06T02:40:00Z",
                "user": {"login": "reviewer-a"},
            }
        ]
        self.comments: list[dict[str, object]] = [
            {
                "id": 8001,
                "body": "Please keep the ledger trace explicit.",
                "created_at": "2026-07-06T02:41:00Z",
                "user": {"login": "reviewer-b"},
            }
        ]
        self.pull: dict[str, object] = {
            "number": 219,
            "merged": True,
            "merged_at": "2026-07-06T02:50:00Z",
            "merge_commit_sha": "merge-sha-1",
            "user": {"login": "LukeJiaoR"},
            "merged_by": {"login": "LukeJiaoR"},
        }
        self.created_prs: list[dict[str, object]] = []
        self.fetch_checks_calls: list[dict[str, object]] = []
        self.fetch_reviews_calls: list[dict[str, object]] = []
        self.fetch_comments_calls: list[dict[str, object]] = []
        self.fetch_pr_calls: list[dict[str, object]] = []

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
        return {"repo": repo, "branch_name": branch_name, "base_ref": base_ref}

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, object]:
        return {"repo": repo, "branch_name": branch_name, "message": message, "files": files}

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
        self.created_prs.append(
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
            pr_number=219,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        self.fetch_checks_calls.append({"repo": repo, "ref": ref})
        return self.checks

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        self.fetch_reviews_calls.append({"repo": repo, "pr_number": pr_number})
        return self.reviews

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        self.fetch_comments_calls.append({"repo": repo, "pr_number": pr_number})
        return self.comments

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
        self.fetch_pr_calls.append({"repo": repo, "pr_number": pr_number})
        return self.pull


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        title="P0-5: GitHub Integration Loop",
        body="Run the GitHub-native lifecycle loop.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/219",
        labels=["p0", "github"],
    )


def _completed_pr_execution(tmp_path: Path, client: FakeGitHubClient):
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT])
    queued = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        adapter="fake",
    )
    run = orchestrator.run_execution(queued.execution_id)
    PullRequestBindingService(
        tmp_path,
        client,
        clock=FakeClock([PR_AT]),
    ).create_or_reuse_pr(run.execution_id)
    return run


def test_check_sync_is_idempotent_until_summary_changes(tmp_path):
    client = FakeGitHubClient()
    run = _completed_pr_execution(tmp_path, client)
    api = ExecutionAPI(tmp_path, github_client=client, clock=FakeClock([SYNC_AT]))

    first = api.observe_ci(run.execution_id)
    second = api.observe_ci(run.execution_id)
    client.checks = {
        "check_runs": [
            {
                "id": 9001,
                "name": "backend",
                "status": "completed",
                "conclusion": "failure",
            }
        ]
    }
    third = api.observe_ci(run.execution_id)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    ci_events = [event for event in events if event.payload.get("transition") == "ci_observed"]
    assert first["ci"]["state"] == "success"
    assert second["ci"]["state"] == "success"
    assert third["ci"]["state"] == "failure"
    assert len(ci_events) == 2
    assert [event.payload["state"] for event in ci_events] == ["success", "failure"]


def test_review_sync_records_reviews_and_comments_once_by_github_id(tmp_path):
    client = FakeGitHubClient()
    run = _completed_pr_execution(tmp_path, client)
    service = PullRequestBindingService(tmp_path, client, clock=FakeClock([SYNC_AT]))

    first = service.sync_reviews_for_execution(run.execution_id)
    second = service.sync_reviews_for_execution(run.execution_id)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    review_events = [event for event in events if event.event_type is LedgerEventType.REVIEW_RECORDED]
    assert first == {"reviews_recorded": 1, "comments_recorded": 1}
    assert second == {"reviews_recorded": 0, "comments_recorded": 0}
    assert len(review_events) == 2
    assert {event.payload["github_id"] for event in review_events} == {"review:7001", "comment:8001"}
    assert {event.payload["actor_login"] for event in review_events} == {"reviewer-a", "reviewer-b"}


def test_merge_sync_records_merge_once_and_preserves_completed_run(tmp_path):
    client = FakeGitHubClient()
    run = _completed_pr_execution(tmp_path, client)
    service = PullRequestBindingService(tmp_path, client, clock=FakeClock([SYNC_AT]))

    first = service.sync_merge_for_execution(run.execution_id)
    second = service.sync_merge_for_execution(run.execution_id)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    merge_events = [
        event for event in events if event.payload.get("transition") == "merge_completed"
    ]
    stored_run = ExecutionLedger(tmp_path).read_run(run.execution_id)
    assert first == {"merged": True, "recorded": True}
    assert second == {"merged": True, "recorded": False}
    assert len(merge_events) == 1
    assert merge_events[0].event_type is LedgerEventType.COMPLETED
    assert merge_events[0].payload["merge_commit_sha"] == "merge-sha-1"
    assert merge_events[0].payload["merged_by"] == "LukeJiaoR"
    assert stored_run is not None
    assert stored_run.pr_number == 219


def test_sync_methods_fail_clearly_without_pr_binding(tmp_path):
    client = FakeGitHubClient()
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT])
    run = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=219)

    service = PullRequestBindingService(tmp_path, client)

    with pytest.raises(ValueError, match=f"Execution PR binding not found: {run.execution_id}"):
        service.sync_reviews_for_execution(run.execution_id)
    with pytest.raises(ValueError, match=f"Execution PR binding not found: {run.execution_id}"):
        service.sync_merge_for_execution(run.execution_id)
