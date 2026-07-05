from pathlib import Path

import goodteam_os.reviewer_dispatch as reviewer_dispatch
from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.reviewer_evidence_snapshot import build_reviewer_evidence_snapshot
from goodteam_os.state import ApprovalDecision, create_initial_state


def _target(*profile_ids: str, policy: dict[str, object] | None = None) -> dict[str, object]:
    target: dict[str, object] = {
        "reviewer_profiles": {
            "implementation": list(profile_ids),
        },
        "reviewer_dispatch": {
            "mode": "parallel",
            "rollout": {
                "enabled": True,
                "acknowledge": "parallel-reviewer-dispatch-is-advisory",
            },
        },
    }
    if policy is not None:
        target["review_evidence_policy"] = policy
    return target


def _state(tmp_path: Path) -> tuple[object, str, str]:
    artifact_path = tmp_path / "staging-report.md"
    artifact_path.write_text("# Staging\n\nReady.", encoding="utf-8")
    state = create_initial_state("run-1", "DEMO", "ship it")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact_path)
    return state, str(artifact_path), artifact_version_for_path(str(artifact_path))


def _status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
        "id": "job-correctness",
        "job_id": "job-correctness",
        "run_id": "run-1",
        "role": "code_review",
        "phase": "implementation",
        "approval": "staging",
        "gate": "staging",
        "adapter": "codex",
        "profile_id": "correctness-security",
        "status": "completed",
        "summary": "Reviewer completed.",
        "updated_at": "2026-07-05T10:00:00+00:00",
        "created_at": "2026-07-05T09:59:00+00:00",
        "result_path": "/tmp/job-correctness/result.md",
        "stdout_path": "/tmp/job-correctness/stdout.txt",
        "stderr_path": "/tmp/job-correctness/stderr.txt",
        "dispatch_group_id": "group-1",
        "idempotency_key": "key-correctness",
        "invocation_evidence": {
            "kind": "provider",
            "adapter": "codex",
            "provider": "openai",
            "external_invocation": True,
            "command_available": True,
            "reason": "Codex reviewer completed",
        },
    }
    status.update(overrides)
    return status


def _snapshot(tmp_path: Path, statuses: list[dict[str, object]]) -> dict[str, object]:
    state, artifact_path, artifact_version = _state(tmp_path)
    scoped_statuses = [
        {
            **status,
            "artifact_key": status.get("artifact_key", "staging_report"),
            "artifact_path": status.get("artifact_path", artifact_path),
            "artifact_version": status.get("artifact_version", artifact_version),
        }
        for status in statuses
    ]
    return build_reviewer_evidence_snapshot(
        state=state,
        approval="staging",
        workspace=tmp_path,
        worker_jobs=scoped_statuses,
        reviewer_statuses=scoped_statuses,
        reviewer_target=_target(
            "correctness-security",
            "architecture-maintainability",
            policy={"mode": "strict", "blockers": ["failed_invocation"]},
        ),
    )


def test_snapshot_includes_run_gate_artifact_and_configured_profiles(tmp_path: Path):
    snapshot = _snapshot(tmp_path, [_status()])

    assert snapshot["schema"] == "reviewer_evidence_snapshot.v1"
    assert snapshot["run_id"] == "run-1"
    assert snapshot["approval"] == "staging"
    assert snapshot["artifact"]["key"] == "staging_report"
    assert snapshot["artifact"]["path"].endswith("staging-report.md")
    assert snapshot["artifact"]["version"].startswith("sha256:")
    assert snapshot["reviewer_dispatch"] == {
        "mode": "parallel",
        "parallel_requested": True,
        "parallel_enabled": True,
        "advisory_only": True,
    }
    assert [reviewer["profile_id"] for reviewer in snapshot["reviewers"]] == [
        "correctness-security",
        "architecture-maintainability",
    ]


def test_snapshot_represents_completed_failed_queued_and_missing_evidence(tmp_path: Path):
    state, artifact_path, artifact_version = _state(tmp_path)
    statuses = [
        _status(
            profile_id="correctness-security",
            status="completed",
            artifact_key="staging_report",
            artifact_path=artifact_path,
            artifact_version=artifact_version,
        ),
        _status(
            id="job-architecture",
            job_id="job-architecture",
            profile_id="architecture-maintainability",
            status="failed",
            summary="Architecture reviewer failed.",
            idempotency_key="key-architecture",
            artifact_key="staging_report",
            artifact_path=artifact_path,
            artifact_version=artifact_version,
            invocation_evidence={"kind": "provider", "provider": "anthropic", "adapter": "claude"},
        ),
        _status(
            id="job-queued",
            job_id="job-queued",
            profile_id="product-operator",
            status="queued",
            idempotency_key="key-queued",
            artifact_key="staging_report",
            artifact_path=artifact_path,
            artifact_version=artifact_version,
            invocation_evidence={},
        ),
    ]
    snapshot = build_reviewer_evidence_snapshot(
        state=state,
        approval="staging",
        workspace=tmp_path,
        worker_jobs=statuses,
        reviewer_statuses=statuses,
        reviewer_target=_target(
            "correctness-security",
            "architecture-maintainability",
            "product-operator",
            policy={"mode": "strict", "blockers": ["failed_invocation"]},
        ),
    )

    assert snapshot["reviewers"][0]["status"] == "completed"
    assert snapshot["reviewers"][1]["status"] == "failed"
    assert snapshot["reviewers"][1]["policy_state"] == "failed_invocation"
    assert snapshot["reviewers"][1]["policy_severity"] == "blocker"
    assert snapshot["reviewers"][2]["status"] == "queued"
    assert snapshot["jobs"]["queued_total"] == 1
    assert snapshot["jobs"]["failed_total"] == 1
    assert snapshot["jobs"]["queued_by_profile"] == {"product-operator": 1}
    assert snapshot["aggregate"]["completed"] == 1
    assert snapshot["aggregate"]["failed"] == 1
    assert snapshot["aggregate"]["queued"] == 1
    assert snapshot["aggregate"]["policy_blockers"] == 1


def test_snapshot_records_missing_evidence_explicitly(tmp_path: Path):
    snapshot = _snapshot(tmp_path, [])

    assert [reviewer["status"] for reviewer in snapshot["reviewers"]] == ["missing", "missing"]
    assert [reviewer["policy_state"] for reviewer in snapshot["reviewers"]] == [
        "missing_evidence",
        "missing_evidence",
    ]
    assert snapshot["aggregate"]["missing"] == 2


def test_snapshot_scopes_previous_failures_to_current_artifact_identity(tmp_path: Path):
    state, artifact_path, artifact_version = _state(tmp_path)
    current = _status(
        id="job-current-retry-success",
        job_id="job-current-retry-success",
        retry_attempt=2,
        original_failed_job_id="job-current-failure",
        artifact_key="staging_report",
        artifact_path=artifact_path,
        artifact_version=artifact_version,
    )
    previous_current = _status(
        id="job-current-failure",
        job_id="job-current-failure",
        status="failed",
        summary="Current artifact failed first.",
        updated_at="2026-07-05T09:00:00+00:00",
        artifact_key="staging_report",
        artifact_path=artifact_path,
        artifact_version=artifact_version,
    )
    stale = _status(
        id="job-stale-failure",
        job_id="job-stale-failure",
        status="failed",
        summary="Old artifact failed.",
        artifact_key="staging_report",
        artifact_path=artifact_path,
        artifact_version="sha256:old",
    )

    snapshot = build_reviewer_evidence_snapshot(
        state=state,
        approval="staging",
        workspace=tmp_path,
        worker_jobs=[current, previous_current, stale],
        reviewer_statuses=[current, previous_current, stale],
        reviewer_target=_target("correctness-security"),
    )

    reviewer = snapshot["reviewers"][0]
    assert reviewer["job_id"] == "job-current-retry-success"
    assert reviewer["retry_attempt"] == "2"
    assert reviewer["original_failed_job_id"] == "job-current-failure"
    assert reviewer["previous_failures"] == [
        {
            "job_id": "job-current-failure",
            "summary": "Current artifact failed first.",
            "updated_at": "2026-07-05T09:00:00+00:00",
            "result_path": "/tmp/job-correctness/result.md",
            "original_failed_job_id": "",
            "retry_attempt": "",
        }
    ]
    assert snapshot["jobs"]["failed_total"] == 0


def test_snapshot_does_not_dispatch_run_or_retry_reviewer_jobs(tmp_path: Path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("snapshot must not dispatch, run, or retry reviewer jobs")

    monkeypatch.setattr(reviewer_dispatch, "dispatch_reviewer_jobs_for_gate", fail)
    monkeypatch.setattr(reviewer_dispatch, "run_queued_reviewer_jobs_for_gate", fail)
    monkeypatch.setattr(reviewer_dispatch, "retry_failed_reviewer_jobs_for_gate", fail)

    snapshot = _snapshot(tmp_path, [_status()])

    assert snapshot["aggregate"]["completed"] == 1
