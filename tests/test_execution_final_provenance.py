from __future__ import annotations

from pathlib import Path

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, LedgerEvent, LedgerEventType
from goodteam_os.memory import RoleMemoryStore


class FakeClock:
    def __init__(self, timestamps: list[str]):
        self.timestamps = list(timestamps)
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
        return GitHubBinding(
            repo=repo,
            issue_number=issue_number or 0,
            pr_number=296,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        return {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ]
        }

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return [
            {
                "id": 7001,
                "state": "APPROVED",
                "body": "Approved with provenance evidence.",
                "submitted_at": "2026-07-06T03:00:00Z",
                "user": {"login": "reviewer-a"},
            }
        ]

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return [
            {
                "id": 8001,
                "body": "Final ledger trace is present.",
                "created_at": "2026-07-06T03:01:00Z",
                "user": {"login": "reviewer-b"},
            }
        ]

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
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
        issue_number=219,
        title="P0-5: GitHub Integration Loop",
        body="Turn GitHub lifecycle telemetry into durable provenance memory.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/219",
        labels=["p0", "github"],
    )


def _api(tmp_path: Path) -> ExecutionAPI:
    return ExecutionAPI(
        tmp_path,
        github_client=FakeGitHubClient(),
        clock=FakeClock(
            [
                "2026-07-06T02:30:00Z",
                "2026-07-06T02:31:00Z",
                "2026-07-06T02:32:00Z",
                "2026-07-06T02:33:00Z",
                "2026-07-06T02:34:00Z",
                "2026-07-06T02:35:00Z",
                "2026-07-06T02:36:00Z",
                "2026-07-06T02:37:00Z",
            ]
        ),
    )


def _full_lifecycle(tmp_path: Path) -> tuple[ExecutionAPI, str]:
    api = _api(tmp_path)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=219, adapter="fake")
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)
    api.create_or_reuse_pr(execution_id)
    api.observe_ci(execution_id)
    api.sync_reviews(execution_id)
    api.sync_merge(execution_id)
    return api, execution_id


def test_full_replay_creates_final_provenance_evidence(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    replay_before = api.replay(execution_id)

    result = api.finalize(execution_id)

    evidence = result["evidence"]
    assert result["execution_id"] == execution_id
    assert result["memory_written"] is True
    assert evidence["execution_id"] == execution_id
    assert evidence["task_id"] == replay_before["run"]["task_id"]
    assert evidence["issue"] == "LukeJiaoR/GoodTeam#219"
    assert evidence["pr"] == "LukeJiaoR/GoodTeam#296"
    assert evidence["ci_summary"]["state"] == "success"
    assert evidence["review_summary"] == {"reviews": 1, "comments": 1, "states": ["APPROVED"]}
    assert evidence["merge_summary"]["merge_commit_sha"] == "merge-sha-296"
    assert evidence["final_status"] == "completed"
    assert [event["event_id"] for event in evidence["events"]] == [
        event["event_id"] for event in replay_before["events"]
    ]


def test_finalize_writes_one_memory_record_and_one_ledger_closure_event(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    result = api.finalize(execution_id)

    memory_events = RoleMemoryStore(tmp_path).list_events("orchestrator")
    ledger_events = ExecutionLedger(tmp_path).read_events(execution_id)
    closure_events = [
        event for event in ledger_events if event.payload.get("transition") == "provenance_written"
    ]
    assert len(memory_events) == 1
    assert memory_events[0]["source"] == f"execution:{execution_id}:final_provenance"
    assert memory_events[0]["id"] == result["memory_event_id"]
    assert len(closure_events) == 1
    assert result["ledger_event_id"] == closure_events[0].event_id
    assert closure_events[0].event_type is LedgerEventType.STATUS_CHANGED
    assert closure_events[0].payload["memory_event_id"] == result["memory_event_id"]
    assert closure_events[0].payload["pr_number"] == 296
    assert closure_events[0].payload["merge_commit_sha"] == "merge-sha-296"
    assert closure_events[0].payload["evidence_hash"]


def test_finalize_is_idempotent_across_reruns(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    first = api.finalize(execution_id)
    second = api.finalize(execution_id)

    memory_events = RoleMemoryStore(tmp_path).list_events("orchestrator")
    closure_events = [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "provenance_written"
    ]
    assert first["memory_written"] is True
    assert second["memory_written"] is False
    assert second["memory_event_id"] == first["memory_event_id"]
    assert second["ledger_event_id"] == first["ledger_event_id"]
    assert second["evidence"] == first["evidence"]
    assert len(memory_events) == 1
    assert len(closure_events) == 1


def test_missing_pr_returns_pending_without_memory_or_ledger_closure(tmp_path):
    api = _api(tmp_path)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=219, adapter="fake")
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)

    result = api.finalize(execution_id)

    assert result["memory_written"] is False
    assert result["memory_event_id"] is None
    assert result["ledger_event_id"] is None
    assert result["evidence"]["final_status"] == "pending"
    assert RoleMemoryStore(tmp_path).list_events("orchestrator") == []
    assert [
        event.payload.get("transition")
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
    ].count("provenance_written") == 0


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
