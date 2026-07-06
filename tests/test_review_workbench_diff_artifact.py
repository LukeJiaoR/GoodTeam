from __future__ import annotations

import json

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.models import ExecutionRun
from goodteam_os.execution.review_workbench import render_review_workbench
from tests.review_workbench_helpers import (
    FakeClock,
    FakeGitHubClient,
    api as _api,
    failed_event as _failed_event,
    full_lifecycle as _full_lifecycle,
    warning_codes as _warning_codes,
)


def test_completed_pr_execution_can_persist_diff_artifact(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    result = api.persist_pr_diff(execution_id)

    artifact_path = tmp_path / ".goodteam" / "executions" / execution_id / "artifacts" / "pr-diff.json"
    patch_path = tmp_path / ".goodteam" / "executions" / execution_id / "artifacts" / "pr-diff.patch"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert result["status"] == "persisted"
    assert result["artifact_path"] == str(artifact_path)
    assert result["patch_path"] == str(patch_path)
    assert artifact["schema_version"] == "execution_pr_diff.v1"
    assert artifact["execution_id"] == execution_id
    assert artifact["repo"] == "LukeJiaoR/GoodTeam"
    assert artifact["pr_number"] == 2160
    assert artifact["branch_name"]
    assert artifact["base_ref"] == "main"
    assert artifact["head_sha"] == "commit-sha-216"
    assert artifact["patch_path"] == f".goodteam/executions/{execution_id}/artifacts/pr-diff.patch"
    assert artifact["changed_files"][0] == {
        "path": "src/goodteam_os/execution/api.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "patch_excerpt": "+def persist_pr_diff():",
    }
    assert "diff --git" in patch_path.read_text(encoding="utf-8")


def test_persisting_diff_artifact_records_ledger_event_once(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    first = api.persist_pr_diff(execution_id)
    second = api.persist_pr_diff(execution_id)

    events = [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "diff_artifact_persisted"
    ]
    assert first["status"] == "persisted"
    assert second["status"] == "deduped"
    assert len(events) == 1
    assert events[0].event_type == "status_changed"
    assert events[0].payload["execution_id"] == execution_id
    assert events[0].payload["source"] == "review_workbench"
    assert events[0].payload["schema_version"] == "execution_pr_diff.v1"
    assert events[0].payload["changed_file_count"] == 2
    assert events[0].payload["artifact_path"].endswith("/pr-diff.json")
    assert events[0].payload["patch_path"].endswith("/pr-diff.patch")


def test_workbench_reads_persisted_diff_artifact(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)

    diff = api.get_review_workbench(execution_id)["diff"]

    assert diff["available"] is True
    assert diff["reason"] == ""
    assert diff["artifact_path"].endswith("/pr-diff.json")
    assert diff["patch_path"].endswith("/pr-diff.patch")
    assert diff["changed_files"][1]["path"] == "tests/test_review_workbench_diff_artifact.py"
    assert diff["summary"] == "2 files changed (+130/-2)"


def test_renderer_includes_changed_files_from_diff_artifact(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)

    output = render_review_workbench(api.get_review_workbench(execution_id))

    assert "Diff: 2 files changed (+130/-2)" in output
    assert "- src/goodteam_os/execution/api.py modified +10 -2" in output
    assert "- tests/test_review_workbench_diff_artifact.py added +120 -0" in output


def test_missing_diff_artifact_empty_state_is_preserved(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    diff = api.get_review_workbench(execution_id)["diff"]

    assert diff == {
        "available": False,
        "reason": "diff_artifact_missing",
        "changed_files": [],
        "artifact_path": "",
        "summary": "",
    }


def test_workbench_diff_read_is_read_only(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    ledger = ExecutionLedger(tmp_path)
    before = ledger.read_events(execution_id)

    api.get_review_workbench(execution_id)
    api.get_review_workbench(execution_id)

    assert ledger.read_events(execution_id) == before


def test_missing_diff_source_does_not_create_fake_changed_files(tmp_path):
    client = FakeGitHubClient()
    client.diff_payload = None
    api = ExecutionAPI(tmp_path, github_client=client, clock=FakeClock())
    lifecycle_api, execution_id = _full_lifecycle(tmp_path)
    lifecycle_api.github_client = client

    result = api.persist_pr_diff(execution_id)

    assert result == {
        "status": "unavailable",
        "reason": "diff_source_unavailable",
        "changed_files": [],
    }
    assert not (tmp_path / ".goodteam" / "executions" / execution_id / "artifacts").exists()


def test_client_without_diff_method_returns_unavailable_without_crashing(tmp_path):
    class NoDiffClient:
        pass

    api, execution_id = _full_lifecycle(tmp_path)
    no_diff_api = ExecutionAPI(tmp_path, github_client=NoDiffClient(), clock=FakeClock())

    result = no_diff_api.persist_pr_diff(execution_id)

    assert result == {
        "status": "unavailable",
        "reason": "diff_source_unavailable",
        "changed_files": [],
    }


def test_pending_execution_returns_unavailable_without_crashing(tmp_path):
    api = _api(tmp_path)
    intake = api.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=216,
        adapter="fake",
    )
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)

    result = api.persist_pr_diff(execution_id)

    assert result == {
        "status": "unavailable",
        "reason": "pr_binding_missing",
        "changed_files": [],
    }
    assert _warning_codes(api.get_review_workbench(execution_id)) >= {"diff_artifact_missing"}


def test_failed_execution_returns_unavailable_without_crashing(tmp_path):
    execution_id = "exec_failed_diff"
    ledger = ExecutionLedger(tmp_path)
    ledger.create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_diff",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )
    ledger.append_event(_failed_event(execution_id))

    result = ExecutionAPI(tmp_path, github_client=FakeGitHubClient()).persist_pr_diff(execution_id)

    assert result == {
        "status": "unavailable",
        "reason": "execution_not_completed",
        "changed_files": [],
    }
