from __future__ import annotations

import json
from pathlib import Path

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.models import ExecutionRun
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


def test_changed_diff_source_does_not_overwrite_artifact_or_ledger(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    first = api.persist_pr_diff(execution_id)
    artifact_path = Path(first["artifact_path"])
    patch_path = Path(first["patch_path"])
    original_artifact = artifact_path.read_text(encoding="utf-8")
    original_patch = patch_path.read_text(encoding="utf-8")
    client = api.github_client
    assert isinstance(client, FakeGitHubClient)
    client.diff_payload = {
        "base_ref": "main",
        "head_sha": "new-head-sha",
        "patch": "diff --git a/src/new.py b/src/new.py\n+changed\n",
        "changed_files": [
            {
                "path": "src/new.py",
                "status": "added",
                "additions": 1,
                "deletions": 0,
                "patch_excerpt": "+changed",
            }
        ],
    }

    result = api.persist_pr_diff(execution_id)

    events = [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "diff_artifact_persisted"
    ]
    assert result == {
        "status": "unavailable",
        "reason": "diff_artifact_changed",
        "changed_files": [
            {
                "path": "src/new.py",
                "status": "added",
                "additions": 1,
                "deletions": 0,
                "patch_excerpt": "+changed",
            }
        ],
        "artifact_path": str(artifact_path),
    }
    assert artifact_path.read_text(encoding="utf-8") == original_artifact
    assert patch_path.read_text(encoding="utf-8") == original_patch
    assert len(events) == 1


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
