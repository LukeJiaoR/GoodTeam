from __future__ import annotations

from pathlib import Path

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.models import ExecutionRun, LedgerEvent
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
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
        self.issues = {("lukejiaor/goodteam", 217): _snapshot()}

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
        return {"repo": repo, "branch_name": branch_name, "message": message, "files": files, "commit_sha": "commit-sha-217"}

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
        return GitHubBinding(repo=repo, issue_number=issue_number or 0, pr_number=301, branch_name=branch_name, execution_id=execution_id)

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
                "id": 7001, "state": "APPROVED",
                "body": "Approved with canonical evidence.",
                "submitted_at": "2026-07-06T03:00:00Z", "user": {"login": "reviewer-a"},
            }
        ]

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return [
            {
                "id": 8001, "body": "Manifest includes the ledger replay.",
                "created_at": "2026-07-06T03:01:00Z", "user": {"login": "reviewer-b"},
            }
        ]

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
        return {
            "number": pr_number, "merged": True,
            "merged_at": "2026-07-06T03:10:00Z",
            "merge_commit_sha": "merge-sha-301",
            "merged_by": {"login": "LukeJiaoR"},
        }


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=217,
        title="P0-3: Ledger & Evidence System",
        body="Build the append-only execution truth layer.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/217",
        labels=["p0", "ledger"],
    )


def _api(tmp_path: Path) -> ExecutionAPI:
    return ExecutionAPI(
        tmp_path,
        github_client=FakeGitHubClient(),
        clock=FakeClock(
            [
                "2026-07-06T02:30:00Z", "2026-07-06T02:31:00Z", "2026-07-06T02:32:00Z", "2026-07-06T02:33:00Z",
                "2026-07-06T02:34:00Z", "2026-07-06T02:35:00Z", "2026-07-06T02:36:00Z", "2026-07-06T02:37:00Z",
            ]
        ),
    )


def _full_lifecycle(tmp_path: Path) -> tuple[ExecutionAPI, str]:
    api = _api(tmp_path)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=217, adapter="fake")
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)
    api.create_or_reuse_pr(execution_id)
    api.observe_ci(execution_id)
    api.sync_reviews(execution_id)
    api.sync_merge(execution_id)
    return api, execution_id


def _assert_manifest_identity(manifest: dict[str, object], execution_id: str) -> None:
    assert manifest["schema_version"] == "execution_evidence_manifest.v1"
    assert manifest["execution_id"] == execution_id
    assert str(manifest["task_id"]).startswith("task_")
    assert manifest["source"] == {
        "type": "github_issue",
        "repo": "LukeJiaoR/GoodTeam",
        "issue_number": 217,
        "issue_ref": "LukeJiaoR/GoodTeam#217",
        "github_ref": "github:LukeJiaoR/GoodTeam#217",
    }
    assert manifest["source_issue"] == "LukeJiaoR/GoodTeam#217"
    assert manifest["github_ref"] == "github:LukeJiaoR/GoodTeam#217"


def _assert_manifest_observations(manifest: dict[str, object]) -> None:
    ledger_events = manifest["ledger_events"]
    assert isinstance(ledger_events, list)
    events_by_transition = {
        event["payload"]["transition"]: event
        for event in ledger_events
    }
    assert manifest["ci_observations"][0]["state"] == "success"
    assert manifest["ci_observations"][0]["summary"]["passed"] == 2
    assert manifest["review_observations"] == [
        {
            "event_id": events_by_transition["review_recorded"]["event_id"],
            "kind": "review",
            "github_id": "review:7001",
            "state": "APPROVED",
            "actor_login": "reviewer-a",
            "submitted_at": "2026-07-06T03:00:00Z",
            "body_excerpt": "Approved with canonical evidence.",
        },
        {
            "event_id": events_by_transition["pr_comment_recorded"]["event_id"],
            "kind": "comment",
            "github_id": "comment:8001",
            "state": "",
            "actor_login": "reviewer-b",
            "submitted_at": "2026-07-06T03:01:00Z",
            "body_excerpt": "Manifest includes the ledger replay.",
        },
    ]
    assert manifest["review_summary"] == {"reviews": 1, "comments": 1, "states": ["APPROVED"]}


def test_full_execution_replay_builds_canonical_evidence_manifest(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    manifest = api.get_evidence(execution_id)

    transitions = [event["payload"]["transition"] for event in manifest["ledger_events"]]
    _assert_manifest_identity(manifest, execution_id)
    assert manifest["pr"]["ref"] == "LukeJiaoR/GoodTeam#301"
    assert manifest["pr"]["commit_sha"] == "commit-sha-217"
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
    _assert_manifest_observations(manifest)
    assert manifest["merge_summary"] == {
        "merged": True,
        "merged_at": "2026-07-06T03:10:00Z",
        "merge_commit_sha": "merge-sha-301",
        "merged_by": "LukeJiaoR",
    }
    assert manifest["memory_provenance_refs"] == []
    assert manifest["final_status"] == "completed"
    assert manifest["incomplete_reason"] == ""


def test_evidence_query_is_deterministic_idempotent_and_read_only(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    ledger = ExecutionLedger(tmp_path)
    memory = RoleMemoryStore(tmp_path)
    events_before = ledger.read_events(execution_id)
    memory_before = memory._read_events("orchestrator")

    first = api.get_evidence(execution_id)
    second = api.get_evidence(execution_id)

    assert second == first
    assert ledger.read_events(execution_id) == events_before
    assert memory._read_events("orchestrator") == memory_before
    assert [event["event_id"] for event in first["ledger_events"]] == [
        event.event_id for event in events_before
    ]


def test_incomplete_execution_returns_pending_manifest(tmp_path):
    api = _api(tmp_path)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=217, adapter="fake")
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)

    manifest = api.get_evidence(execution_id)

    assert manifest["execution_id"] == execution_id
    assert manifest["pr"]["ref"] == ""
    assert manifest["ci_observations"] == []
    assert manifest["review_observations"] == []
    assert manifest["merge_summary"]["merged"] is False
    assert manifest["final_status"] == "pending"
    assert manifest["incomplete_reason"] == "pr_missing"


def test_failed_execution_without_pr_returns_failed_manifest(tmp_path):
    execution_id = "exec_failed_no_pr"
    ledger = ExecutionLedger(tmp_path)
    run = ExecutionRun(
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
    ledger.create_run(run)
    ledger.append_event(_failed_event(execution_id))

    manifest = ExecutionAPI(tmp_path).get_evidence(execution_id)

    assert manifest["pr"]["ref"] == ""
    assert manifest["final_status"] == "failed"
    assert manifest["incomplete_reason"] == "execution_failed:worker_failed"


def test_memory_provenance_refs_are_read_without_query_side_effects(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    finalized = api.finalize(execution_id)
    ledger = ExecutionLedger(tmp_path)
    memory = RoleMemoryStore(tmp_path)
    events_before = ledger.read_events(execution_id)
    memory_before = memory._read_events("orchestrator")

    manifest = api.get_evidence(execution_id)
    second = api.get_evidence(execution_id)

    assert second == manifest
    assert ledger.read_events(execution_id) == events_before
    assert memory._read_events("orchestrator") == memory_before
    assert manifest["memory_provenance_integrity"]["complete"] is True
    assert len(manifest["memory_provenance_refs"]) == 1
    ref = manifest["memory_provenance_refs"][0]
    assert ref["status"] == "complete"
    assert ref["execution_id"] == execution_id
    assert ref["memory_event_id"] == finalized["memory_event_id"]
    assert ref["ledger_event_id"] == finalized["ledger_event_id"]
    assert ref["evidence_hash"]


def test_missing_run_returns_incomplete_manifest_without_unexplained_error(tmp_path):
    execution_id = "exec_missing_run"
    ledger = ExecutionLedger(tmp_path)
    ledger_event = _manual_event(execution_id)
    ledger.append_event(ledger_event)

    manifest = ExecutionAPI(tmp_path).get_evidence(execution_id)

    assert manifest["execution_id"] == execution_id
    assert manifest["task_id"] == ledger_event.task_id
    assert manifest["final_status"] == "incomplete"
    assert manifest["incomplete_reason"] == "execution_run_missing"


def _manual_event(execution_id: str) -> LedgerEvent:
    return LedgerEvent(
        event_id=f"{execution_id}:0001:created:intake_created",
        execution_id=execution_id,
        task_id="github:LukeJiaoR/GoodTeam#217",
        event_type="created",
        actor="orchestrator",
        payload={"transition": "intake_created", "repo": "LukeJiaoR/GoodTeam", "issue_number": 217},
        created_at="2026-07-06T02:30:00Z",
    )


def _failed_event(execution_id: str) -> LedgerEvent:
    return LedgerEvent(
        event_id=f"{execution_id}:0001:failed:execution_failed",
        execution_id=execution_id,
        task_id="task_failed",
        event_type="failed",
        actor="orchestrator",
        payload={"transition": "execution_failed", "failure_class": "worker_failed"},
        created_at="2026-07-06T02:31:00Z",
    )
