from __future__ import annotations

import pytest

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, LedgerEvent, LedgerEventType
from goodteam_os.memory import RoleMemoryStore


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 230): _snapshot()}
        self.issue_fetch_count = 0
        self.created_branches: list[dict[str, object]] = []
        self.committed_files: list[dict[str, object]] = []
        self.created_prs: list[dict[str, object]] = []
        self.fetched_checks: list[dict[str, object]] = []
        self.fetched_reviews: list[dict[str, object]] = []
        self.fetched_comments: list[dict[str, object]] = []
        self.fetched_prs: list[dict[str, object]] = []
        self.checks = {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ]
        }

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
            pr_number=296,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        self.fetched_checks.append({"repo": repo, "ref": ref})
        return self.checks

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        self.fetched_reviews.append({"repo": repo, "pr_number": pr_number})
        return [
            {
                "id": 7001,
                "state": "APPROVED",
                "body": "Approved with ledger trace.",
                "submitted_at": "2026-07-06T03:00:00Z",
                "user": {"login": "reviewer-a"},
            }
        ]

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        self.fetched_comments.append({"repo": repo, "pr_number": pr_number})
        return [
            {
                "id": 8001,
                "body": "Please keep the CI observation in the replay.",
                "created_at": "2026-07-06T03:01:00Z",
                "user": {"login": "reviewer-b"},
            }
        ]

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
        self.fetched_prs.append({"repo": repo, "pr_number": pr_number})
        return {
            "number": pr_number,
            "merged": True,
            "merged_at": "2026-07-06T03:10:00Z",
            "merge_commit_sha": "merge-sha-296",
            "merged_by": {"login": "LukeJiaoR"},
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


@pytest.fixture
def execution(tmp_path):
    client = FakeGitHubClient()
    api = ExecutionAPI(tmp_path, github_client=client)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=230, adapter="fake")
    execution_id = intake["execution_id"]
    return api, client, execution_id


def test_execution_lifecycle_gate_replays_without_duplicate_side_effects(tmp_path, execution):
    api, client, execution_id = execution
    run = api.run_execution(execution_id)
    pr = api.create_or_reuse_pr(execution_id)
    ci = api.observe_ci(execution_id)
    reviews = api.sync_reviews(execution_id)
    merge = api.sync_merge(execution_id)

    replay = ExecutionAPI(tmp_path, github_client=client).replay(execution_id)
    transitions = [event["payload"]["transition"] for event in replay["events"]]
    assert transitions == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "sandbox_created",
        "execution_completed",
        "sandbox_cleanup_requested",
        "sandbox_destroyed",
        "pr_opened",
        "ci_observed",
        "review_recorded",
        "pr_comment_recorded",
        "merge_completed",
    ]
    assert run["worker_job"]["status"] == "completed"
    assert pr["binding"]["pr_number"] == 296
    assert ci["ci"]["state"] == "success"
    assert ci["ci"]["passed"] == 2
    assert reviews["reviews"] == {"reviews_recorded": 1, "comments_recorded": 1}
    assert merge["merge"] == {"merged": True, "recorded": True}

    second_intake = api.intake_github_issue(repo=" lukejiaor/goodteam ", issue_number=230, adapter="fake")
    second_run = api.run_execution(execution_id)
    second_pr = api.create_or_reuse_pr(execution_id)
    second_ci = api.observe_ci(execution_id)
    second_reviews = api.sync_reviews(execution_id)
    second_merge = api.sync_merge(execution_id)
    second_replay = ExecutionAPI(tmp_path, github_client=client).replay(execution_id)

    assert second_intake["execution_id"] == execution_id
    assert second_run["run"]["status"] == "succeeded"
    assert second_pr["binding"] == pr["binding"]
    assert second_ci["ci"]["state"] == "success"
    assert second_reviews["reviews"] == {"reviews_recorded": 0, "comments_recorded": 0}
    assert second_merge["merge"] == {"merged": True, "recorded": False}
    assert client.issue_fetch_count == 1
    assert len(client.created_branches) == 1
    assert len(client.committed_files) == 1
    assert len(client.created_prs) == 1
    assert len(second_replay["worker_jobs"]) == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("pr_opened") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("execution_completed") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("ci_observed") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("review_recorded") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("pr_comment_recorded") == 1
    assert [event["payload"]["transition"] for event in second_replay["events"]].count("merge_completed") == 1

    ledger = ExecutionLedger(tmp_path)
    review_events = [
        event for event in ledger.read_events(execution_id)
        if event.event_type is LedgerEventType.REVIEW_RECORDED
    ]
    assert {event.payload["github_id"] for event in review_events} == {"review:7001", "comment:8001"}
    assert {event.payload["actor_login"] for event in review_events} == {"reviewer-a", "reviewer-b"}
    merge_event = next(
        event for event in ledger.read_events(execution_id)
        if event.payload.get("transition") == "merge_completed"
    )
    assert merge_event.event_type is LedgerEventType.COMPLETED
    assert merge_event.payload["merge_commit_sha"] == "merge-sha-296"
    assert merge_event.payload["merged_by"] == "LukeJiaoR"
    assert ledger.read_run(execution_id).pr_number == 296

    # Finalize through a fresh API instance: the gate covers persisted evidence,
    # the memory/ledger link, and repeat safety rather than a second full setup.
    finalized = ExecutionAPI(tmp_path, github_client=client).finalize(execution_id)
    evidence = finalized["evidence"]
    assert finalized["execution_id"] == execution_id
    assert finalized["memory_written"] is True
    assert evidence["execution_id"] == execution_id
    assert evidence["task_id"] == second_replay["run"]["task_id"]
    assert evidence["issue"] == "LukeJiaoR/GoodTeam#230"
    assert evidence["pr"] == "LukeJiaoR/GoodTeam#296"
    assert evidence["ci_summary"]["state"] == "success"
    assert evidence["review_summary"] == {"reviews": 1, "comments": 1, "states": ["APPROVED"]}
    assert evidence["merge_summary"]["merge_commit_sha"] == "merge-sha-296"
    assert evidence["final_status"] == "completed"
    assert [event["event_id"] for event in evidence["events"]] == [
        event["event_id"] for event in second_replay["events"]
    ]

    repeated = ExecutionAPI(tmp_path, github_client=client).finalize(execution_id)
    assert repeated["memory_written"] is False
    assert repeated["memory_event_id"] == finalized["memory_event_id"]
    assert repeated["ledger_event_id"] == finalized["ledger_event_id"]
    assert repeated["evidence"] == evidence

    memory_events = RoleMemoryStore(tmp_path).list_events("orchestrator")
    closure_events = [
        event for event in ledger.read_events(execution_id)
        if event.payload.get("transition") == "provenance_written"
    ]
    assert len(memory_events) == len(closure_events) == 1
    assert memory_events[0]["source"] == f"execution:{execution_id}:final_provenance"
    assert memory_events[0]["id"] == finalized["memory_event_id"]
    assert closure_events[0].event_id == finalized["ledger_event_id"]
    assert closure_events[0].event_type is LedgerEventType.STATUS_CHANGED
    assert closure_events[0].payload["memory_event_id"] == finalized["memory_event_id"]
    assert closure_events[0].payload["pr_number"] == 296
    assert closure_events[0].payload["merge_commit_sha"] == "merge-sha-296"
    assert closure_events[0].payload["evidence_hash"]


def test_check_sync_is_idempotent_until_summary_changes(tmp_path, execution):
    api, client, execution_id = execution
    api.run_execution(execution_id)
    api.create_or_reuse_pr(execution_id)

    first = api.observe_ci(execution_id)
    second = api.observe_ci(execution_id)
    client.checks = {
        "check_runs": [
            {"name": "unit", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ]
    }
    third = api.observe_ci(execution_id)

    ci_events = [
        event for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "ci_observed"
    ]
    assert first["ci"]["state"] == second["ci"]["state"] == "success"
    assert third["ci"]["state"] == "failure"
    assert [event.payload["state"] for event in ci_events] == ["success", "failure"]


def test_sync_methods_fail_clearly_without_pr_binding(execution):
    api, _, execution_id = execution
    with pytest.raises(ValueError, match=f"Execution PR binding not found: {execution_id}"):
        api.sync_reviews(execution_id)
    with pytest.raises(ValueError, match=f"Execution PR binding not found: {execution_id}"):
        api.sync_merge(execution_id)


def test_missing_pr_returns_pending_without_memory_or_ledger_closure(tmp_path, execution):
    api, _, execution_id = execution
    api.run_execution(execution_id)
    result = api.finalize(execution_id)

    assert result["memory_written"] is False
    assert result["memory_event_id"] is None
    assert result["ledger_event_id"] is None
    assert result["evidence"]["final_status"] == "pending"
    assert RoleMemoryStore(tmp_path).list_events("orchestrator") == []
    assert not any(
        event.payload.get("transition") == "provenance_written"
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
    )


def test_failed_execution_without_pr_builds_failed_provenance(tmp_path):
    execution_id = "exec_failed_no_pr"
    ledger = ExecutionLedger(tmp_path)
    ledger.create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_no_pr",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )
    ledger.append_event(
        LedgerEvent(
            event_id=f"{execution_id}:0001:failed:execution_failed",
            execution_id=execution_id,
            task_id="task_failed",
            event_type="failed",
            actor="orchestrator",
            payload={"transition": "execution_failed", "failure_class": "worker_failed"},
            created_at="2026-07-06T02:31:00Z",
        )
    )
    evidence = ExecutionEvidenceService(tmp_path).build_provenance(execution_id)
    assert evidence["pr"] == ""
    assert evidence["final_status"] == "failed"
