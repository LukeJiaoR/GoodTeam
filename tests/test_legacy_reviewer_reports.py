import json

import pytest

from goodteam_os import web
from goodteam_os.delivery_outcomes import DeliveryArtifactError
from goodteam_os.legacy_reviewer_reports import build_legacy_reviewer_reports, read_legacy_reviewer_report
from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def legacy(tmp_path):
    state = create_initial_state("run-one", "DEMO", "Review exact delivery")
    state.current_node = "release_review_gate"
    state.next_node = "await_release_approval"
    path = tmp_path / "release.md"
    path.write_text("# Release\nQA remains unknown.\n")
    state.artifacts["release_notes"] = str(path)
    store = WorkerJobStore(tmp_path)
    job_id = "job-7c5932f6-f2bb-495c-b2e9-472826dc3bfb"
    job_root = store.root / job_id
    job_root.mkdir(parents=True)
    job = {"id": job_id, "project_key": "DEMO", "run_id": state.run_id, "role": "release", "phase": "release",
           "approval": "release", "gate": "release", "profile_id": "correctness-security", "status": "completed",
           "artifact_key": "release_notes", "artifact_path": str(path), "artifact_version": artifact_version_for_path(str(path)),
           "dispatch_group_id": "exact-delivery", "idempotency_key": "exact-review"}
    (job_root / "job.json").write_text(json.dumps(job))
    (job_root / "status.json").write_text(json.dumps({"status": "completed", "result_path": "/arbitrary/path.md"}))
    (job_root / "result.md").write_text("## Findings\n\n**High** — QA blocked.\n\n**Medium** — patch binding missing.\n")
    return state, store, job_root


def read(state, store, item, **overrides):
    return read_legacy_reviewer_report(state, [store.project_root], **{
        "job_id": item["job_id"], "profile_id": item["profile_id"], "approval": item["approval"],
        "artifact_key": item["artifact_key"], "artifact_version": item["artifact_version"],
        "version": item["report_version"], **overrides})


def test_original_legacy_findings_are_readable_without_skill_receipt_or_gate_decision(legacy):
    state, store, job_root = legacy
    item, = build_legacy_reviewer_reports(state, [store.project_root])
    value = read(state, store, item)
    assert value["content"] == (job_root / "result.md").read_text()
    assert value["advisory_only"] is True
    assert "schema_version" not in value
    assert state.approvals["release"] == "pending"
    with pytest.raises(DeliveryArtifactError):
        read(state, store, item, profile_id="different-profile")
    with pytest.raises(DeliveryArtifactError):
        read(state, store, item, artifact_version="sha256:stale")
    (job_root / "result.md").write_text("Different report\n")
    with pytest.raises(DeliveryArtifactError) as error:
        read(state, store, item)
    assert error.value.status == 409


def test_legacy_report_never_follows_result_symlink_or_cross_run_job(legacy):
    state, store, job_root = legacy
    item, = build_legacy_reviewer_reports(state, [store.project_root])
    (job_root / "result.md").unlink()
    (job_root / "result.md").symlink_to(state.artifacts["release_notes"])
    assert build_legacy_reviewer_reports(state, [store.project_root])[0]["href"] == ""
    with pytest.raises(DeliveryArtifactError):
        read(state, store, item)
    state.run_id = "different-run"
    assert build_legacy_reviewer_reports(state, [store.project_root]) == []


def test_reviewer_start_rejects_stale_artifact_before_dispatch(tmp_path, monkeypatch, legacy):
    state, _, _ = legacy
    monkeypatch.setattr(web, "interrupt_transition", lambda *_: state)
    monkeypatch.setattr(web, "WorkerJobStore", lambda *_args, **_kwargs: pytest.fail("Must not dispatch stale review"))
    status, payload = web._run_queued_reviewer_jobs_for_current_run(tmp_path,
        {"project_key": "DEMO", "run_id": state.run_id, "approval": "release", "artifact_key": "release_notes",
         "artifact_version": "sha256:stale"}, "DEMO", {})
    assert status == 409
    assert "artifact_version" in payload["error"]
