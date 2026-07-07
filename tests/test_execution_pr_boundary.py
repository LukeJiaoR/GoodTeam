from __future__ import annotations

import pytest

from goodteam_os.execution import (
    ExecutionActor,
    ExecutionLedger,
    ExecutionOrchestrator,
    GitHubBinding,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
    LedgerEventType,
)


CREATED_AT = "2026-07-06T01:00:00Z"
STARTED_AT = "2026-07-06T01:05:00Z"
FINISHED_AT = "2026-07-06T01:10:00Z"
PR_AT = "2026-07-06T01:15:00Z"


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
            "commit_sha": f"sha-{len(self.commits) + 1}",
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
            pr_number=219,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        raise AssertionError(f"PR boundary tests should not fetch checks for {repo}@{ref}")


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        title="P0: GitHub Integration Loop",
        body="Turn completed GoodTeam executions into GitHub-native pull requests.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/219",
        labels=["p0", "github"],
    )


def _orchestrator(tmp_path, client: FakeGitHubClient, clock: FakeClock) -> ExecutionOrchestrator:
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = clock
    return orchestrator


def _completed_execution(tmp_path, client: FakeGitHubClient):
    orchestrator = _orchestrator(
        tmp_path,
        client,
        FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT, PR_AT]),
    )
    queued = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        adapter="fake",
    )
    return orchestrator, orchestrator.run_execution(queued.execution_id)


def test_completed_execution_opens_pr_and_records_single_pr_event(tmp_path):
    client = FakeGitHubClient()
    orchestrator, run = _completed_execution(tmp_path, client)
    files = {"docs/dogfood/generated/custom.md": "# Custom execution output\n"}

    binding = orchestrator.open_pull_request_for_execution(run.execution_id, files=files)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert binding == GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        pr_number=219,
        branch_name=run.branch_name,
        execution_id=run.execution_id,
    )
    assert client.branches == [
        {"repo": "LukeJiaoR/GoodTeam", "branch_name": run.branch_name, "base_ref": "main"}
    ]
    assert client.commits[0]["message"] == f"GoodTeam execution {run.execution_id}"
    assert client.commits[0]["files"] == files
    assert client.prs[0]["title"] == "GoodTeam execution for LukeJiaoR/GoodTeam#219"
    assert run.execution_id in str(client.prs[0]["body"])
    assert run.task_id in str(client.prs[0]["body"])
    assert run.worker_job_id in str(client.prs[0]["body"])
    assert "adapter: fake" in str(client.prs[0]["body"])
    assert "generated by GoodTeam" in str(client.prs[0]["body"])
    assert [event.payload["transition"] for event in events] == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "sandbox_created",
        "execution_completed",
        "sandbox_cleanup_requested",
        "sandbox_destroyed",
        "pr_opened",
    ]
    assert events[-1].event_type is LedgerEventType.PR_OPENED
    assert events[-1].actor is ExecutionActor.GITHUB
    assert events[-1].payload["commit_sha"] == "sha-1"
    assert events[-1].payload["idempotent"] is False


def test_reopening_execution_pr_reuses_binding_without_duplicate_operations_or_events(tmp_path):
    client = FakeGitHubClient()
    orchestrator, run = _completed_execution(tmp_path, client)

    first = orchestrator.open_pull_request_for_execution(run.execution_id)
    second = orchestrator.open_pull_request_for_execution(run.execution_id)

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert second == first
    assert len(client.branches) == 1
    assert len(client.commits) == 1
    assert len(client.prs) == 1
    assert [event.event_type for event in events].count(LedgerEventType.PR_OPENED) == 1
    assert [event.payload["transition"] for event in events].count("pr_opened") == 1


def test_default_pr_file_contains_execution_summary_and_worker_result(tmp_path):
    client = FakeGitHubClient()
    orchestrator, run = _completed_execution(tmp_path, client)

    orchestrator.open_pull_request_for_execution(run.execution_id)

    committed_files = client.commits[0]["files"]
    assert list(committed_files) == [f"docs/dogfood/generated/{run.execution_id}.md"]
    content = committed_files[f"docs/dogfood/generated/{run.execution_id}.md"]
    assert f"# GoodTeam execution {run.execution_id}" in content
    assert f"- Task id: {run.task_id}" in content
    assert "Completed by fake smoke adapter" in content
    assert "execution_completed" in content


def test_open_pull_request_for_missing_execution_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        ExecutionOrchestrator(tmp_path, FakeGitHubClient()).open_pull_request_for_execution(
            "exec_missing"
        )


def test_open_pull_request_for_queued_execution_is_rejected(tmp_path):
    client = FakeGitHubClient()
    orchestrator = _orchestrator(tmp_path, client, FakeClock([CREATED_AT]))
    run = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=219,
        adapter="fake",
    )

    with pytest.raises(ValueError, match=f"Execution is not completed: {run.execution_id}"):
        orchestrator.open_pull_request_for_execution(run.execution_id)

    assert client.branches == []
    assert client.commits == []
    assert client.prs == []


def test_open_pull_request_requires_github_client(tmp_path):
    with pytest.raises(ValueError, match="A GitHubClient is required to open a pull request"):
        ExecutionOrchestrator(tmp_path).open_pull_request_for_execution("exec_missing")
