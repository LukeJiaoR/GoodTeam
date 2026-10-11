from __future__ import annotations

from pathlib import Path

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun, LedgerEvent, LedgerEventType
from goodteam_os.memory import RoleMemoryStore


_CLOCK_TIMESTAMPS = [f"2026-07-06T02:{minute}:00Z" for minute in range(30, 38)]


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
            pr_number=302,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        return {"check_runs": [{"name": "unit", "status": "completed", "conclusion": "success"}]}

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return []

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return []

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
        return {
            "number": pr_number,
            "merged": True,
            "merged_at": "2026-07-06T03:10:00Z",
            "merge_commit_sha": "merge-sha-302",
            "merged_by": {"login": "LukeJiaoR"},
        }


def test_finalize_exposes_ledger_linked_memory_provenance_ref(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    finalized = api.finalize(execution_id)
    manifest = api.get_evidence(execution_id)
    audit = api.audit_memory_provenance(execution_id)

    assert manifest["memory_provenance_integrity"] == audit
    assert audit["complete"] is True
    assert audit["pending"] is False
    assert audit["missing_memory_event_ids"] == []
    assert audit["orphan_memory_event_ids"] == []
    assert audit["duplicate_ledger_event_ids"] == []
    assert len(manifest["memory_provenance_refs"]) == 1
    ref = manifest["memory_provenance_refs"][0]
    assert ref["status"] == "complete"
    assert ref["execution_id"] == execution_id
    assert ref["memory_event_id"] == finalized["memory_event_id"]
    assert ref["ledger_event_id"] == finalized["ledger_event_id"]
    assert ref["source"] == f"execution:{execution_id}:final_provenance"
    assert ref["role"] == "orchestrator"
    assert ref["horizon"] == "long-term"
    assert ref["evidence_hash"]
    assert ref["created_at"] == "2026-07-06T02:37:00Z"
    assert ref["timestamp"]
    assert ref["supersedes"] == ""
    assert ref["is_superseded"] is False


def test_repeated_finalize_reuses_existing_memory_and_ledger_refs(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    first = api.finalize(execution_id)
    second = api.finalize(execution_id)

    memory_events = RoleMemoryStore(tmp_path)._read_events("orchestrator")
    ledger_closures = _provenance_written_events(tmp_path, execution_id)
    assert second["memory_written"] is False
    assert second["memory_event_id"] == first["memory_event_id"]
    assert second["ledger_event_id"] == first["ledger_event_id"]
    assert len(memory_events) == 1
    assert len(ledger_closures) == 1


def test_repeated_evidence_query_is_read_only_for_memory_provenance_refs(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.finalize(execution_id)
    ledger = ExecutionLedger(tmp_path)
    memory = RoleMemoryStore(tmp_path)
    events_before = ledger.read_events(execution_id)
    memory_before = memory._read_events("orchestrator")

    first = api.get_evidence(execution_id)
    second = api.get_evidence(execution_id)

    assert second == first
    assert ledger.read_events(execution_id) == events_before
    assert memory._read_events("orchestrator") == memory_before


def test_missing_memory_event_is_returned_as_broken_ref(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    finalized = api.finalize(execution_id)
    RoleMemoryStore(tmp_path).event_path("orchestrator").write_text("", encoding="utf-8")

    manifest = api.get_evidence(execution_id)

    ref = manifest["memory_provenance_refs"][0]
    assert ref["status"] == "missing_memory_event"
    assert ref["issues"] == ["missing_memory_event"]
    assert manifest["memory_provenance_integrity"]["memory_context_status"] == "unavailable"
    assert manifest["memory_provenance_integrity"]["memory_context_reasons"] == ["wal_unreadable"]
    assert ref["memory_event_id"] == finalized["memory_event_id"]
    assert ref["ledger_event_id"] == finalized["ledger_event_id"]
    assert manifest["memory_provenance_integrity"]["complete"] is False
    assert manifest["memory_provenance_integrity"]["missing_memory_event_ids"] == [
        finalized["memory_event_id"]
    ]


def test_matching_memory_event_without_ledger_closure_is_returned_as_orphan(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    event = RoleMemoryStore(tmp_path).append_memory_event(
        "orchestrator",
        horizon="long-term",
        text="Execution provenance was written without its closure.",
        source=f"execution:{execution_id}:final_provenance",
    )
    memory_event_id = RoleMemoryStore(tmp_path)._event_id(event)

    manifest = api.get_evidence(execution_id)

    assert manifest["memory_provenance_integrity"]["complete"] is False
    assert manifest["memory_provenance_integrity"]["orphan_memory_event_ids"] == [memory_event_id]
    ref = manifest["memory_provenance_refs"][0]
    assert ref["status"] == "orphan_memory_event"
    assert ref["issues"] == ["missing_ledger_link"]
    assert ref["memory_event_id"] == memory_event_id
    assert ref["ledger_event_id"] == ""
    assert ref["timestamp"] == event["timestamp"]


def test_duplicate_provenance_closure_is_reported_in_integrity(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    finalized = api.finalize(execution_id)
    first_closure = _provenance_written_events(tmp_path, execution_id)[0]
    ExecutionLedger(tmp_path).append_event(
        LedgerEvent(
            event_id=f"{execution_id}:9999:status_changed:provenance_written",
            execution_id=execution_id,
            task_id=first_closure.task_id,
            event_type=LedgerEventType.STATUS_CHANGED,
            actor=first_closure.actor,
            payload=dict(first_closure.payload),
            created_at="2026-07-06T03:30:00Z",
        )
    )

    manifest = api.get_evidence(execution_id)

    assert manifest["memory_provenance_integrity"]["complete"] is False
    assert manifest["memory_provenance_integrity"]["duplicate_ledger_event_ids"] == [
        f"{execution_id}:9999:status_changed:provenance_written"
    ]
    assert [ref["ledger_event_id"] for ref in manifest["memory_provenance_refs"]] == [
        finalized["ledger_event_id"],
        f"{execution_id}:9999:status_changed:provenance_written",
    ]
    assert manifest["memory_provenance_refs"][1]["issues"] == ["duplicate_ledger_closure"]


def test_failed_and_pending_executions_do_not_write_memory_provenance(tmp_path):
    pending_api = _api(tmp_path)
    intake = pending_api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=217, adapter="fake")
    pending_id = intake["execution_id"]
    pending_api.run_execution(pending_id)

    pending_result = pending_api.finalize(pending_id)
    pending_manifest = pending_api.get_evidence(pending_id)
    failed_api = ExecutionAPI(tmp_path)
    failed_id = "exec_failed_no_provenance"
    ExecutionLedger(tmp_path).create_run(
        ExecutionRun(
            execution_id=failed_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_no_provenance",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )

    failed_result = failed_api.finalize(failed_id)
    failed_manifest = failed_api.get_evidence(failed_id)

    assert pending_result["memory_event_id"] is None
    assert pending_result["ledger_event_id"] is None
    assert failed_result["memory_event_id"] is None
    assert failed_result["ledger_event_id"] is None
    assert _provenance_written_events(tmp_path, pending_id) == []
    assert _provenance_written_events(tmp_path, failed_id) == []
    assert pending_manifest["memory_provenance_integrity"]["pending"] is True
    assert pending_manifest["memory_provenance_integrity"]["pending_reason"] == "pr_missing"
    assert failed_manifest["memory_provenance_integrity"]["pending"] is True
    assert failed_manifest["memory_provenance_integrity"]["pending_reason"] == "execution_failed"


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
        clock=FakeClock(_CLOCK_TIMESTAMPS),
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


def _provenance_written_events(tmp_path: Path, execution_id: str) -> list[LedgerEvent]:
    return [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "provenance_written"
    ]
