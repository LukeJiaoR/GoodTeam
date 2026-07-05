from __future__ import annotations

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 230): _snapshot()}
        self.issue_fetch_count = 0
        self.created_branches: list[dict[str, object]] = []
        self.committed_files: list[dict[str, object]] = []
        self.created_prs: list[dict[str, object]] = []
        self.fetched_checks: list[dict[str, object]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        self.issue_fetch_count += 1
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
        self.created_branches.append(payload)
        return payload

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, object]:
        payload = {"repo": repo, "branch_name": branch_name, "message": message, "files": files}
        self.committed_files.append(payload)
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
            pr_number=230,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        self.fetched_checks.append({"repo": repo, "ref": ref})
        return {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ]
        }


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=230,
        title="P0-1F: Execution CLI/API Surface and E2E Test Contract",
        body="Expose the execution loop through stable CLI and local API surfaces.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/230",
        labels=["p0", "execution"],
    )


def test_execution_fixture_e2e_contract_replays_full_p0_1_loop_without_duplicates(tmp_path):
    client = FakeGitHubClient()
    api = ExecutionAPI(tmp_path, github_client=client)

    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=230, adapter="fake")
    execution_id = intake["execution_id"]
    run = api.run_execution(execution_id)
    pr = api.create_or_reuse_pr(execution_id)
    ci = api.observe_ci(execution_id)

    replay = ExecutionAPI(tmp_path, github_client=client).replay(execution_id)
    transitions = [event["payload"]["transition"] for event in replay["events"]]
    assert transitions == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "execution_completed",
        "pr_created",
        "ci_observed",
    ]
    assert run["worker_job"]["status"] == "completed"
    assert pr["binding"]["pr_number"] == 230
    assert ci["ci"]["state"] == "success"
    assert ci["ci"]["passed"] == 2

    second_intake = api.intake_github_issue(repo=" lukejiaor/goodteam ", issue_number=230, adapter="fake")
    second_run = api.run_execution(execution_id)
    second_pr = api.create_or_reuse_pr(execution_id)
    second_ci = api.observe_ci(execution_id)
    second_replay = ExecutionAPI(tmp_path, github_client=client).replay(execution_id)

    assert second_intake["execution_id"] == execution_id
    assert second_run["run"]["status"] == "succeeded"
    assert second_pr["binding"] == pr["binding"]
    assert second_ci["ci"]["state"] == "success"
    assert client.issue_fetch_count == 1
    assert len(client.created_branches) == 1
    assert len(client.committed_files) == 1
    assert len(client.created_prs) == 1
    assert len(second_replay["worker_jobs"]) == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("pr_created") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("execution_completed") == 1
