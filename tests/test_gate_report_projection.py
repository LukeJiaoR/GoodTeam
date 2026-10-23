import hashlib
import json

import pytest

from goodteam_os.gate_report_projection import project_gate_reports


def fixture_report(tmp_path):
    status = dict(id="job-review-123", run_id="run-1", project_key="DEMO", profile_id="correctness-security",
                  artifact_key="release_notes", artifact_path="release.md", artifact_version="sha256:current",
                  status="completed")
    directory = tmp_path / ".goodteam/workers/jobs" / status["id"]
    directory.mkdir(parents=True)
    status["result_path"] = str(directory / "result.md")
    (directory / "job.json").write_text(json.dumps(status))
    (directory / "result.md").write_text("High: QA BLOCKED, exit 1, zero tests.\nAuthorization: Bearer private-token\n")
    return status, directory


def project(status, root):
    return project_gate_reports([status], roots=[root], run_id="run-1", project_key="DEMO")


def test_attributed_report_preserves_findings_without_claiming_test_success(tmp_path):
    status, _ = fixture_report(tmp_path)
    report, = project(status, tmp_path)
    assert report["report_status"] == "available"
    assert "High: QA BLOCKED, exit 1, zero tests." in report["content"]
    assert "private-token" not in report["content"]
    assert report["report_sha256"].startswith("sha256:")
    assert report["artifact_version"] == "sha256:current"
    assert report["advisory_only"] is True
    assert "qa_status" not in report


@pytest.mark.parametrize("key,value", [("run_id", "other"), ("project_key", "other"), ("id", "../other"),
                                       ("id", "job-skill-123")])
def test_foreign_or_protected_report_is_not_read(tmp_path, key, value):
    status, _ = fixture_report(tmp_path)
    status[key] = value
    assert project(status, tmp_path) == []


@pytest.mark.parametrize("mutation", ["missing", "symlink", "wrong_version", "wrong_job", "wrong_profile", "wrong_role", "foreign_path", "oversize"])
def test_missing_or_changed_report_does_not_borrow_another_result(tmp_path, mutation):
    status, directory = fixture_report(tmp_path)
    report = directory / "result.md"
    if mutation == "missing":
        report.unlink()
    elif mutation == "symlink":
        report.unlink()
        target = tmp_path / "other.md"
        target.write_text("must not read")
        report.symlink_to(target)
    elif mutation.startswith("wrong_"):
        job = json.loads((directory / "job.json").read_text())
        job[{"wrong_version": "artifact_version", "wrong_job": "id", "wrong_profile": "profile_id", "wrong_role": "role"}[mutation]] = "other"
        (directory / "job.json").write_text(json.dumps(job))
    elif mutation == "foreign_path":
        status["result_path"] = str(tmp_path / "other.md")
    else:
        report.write_text("x" * (128 * 1024 + 1))
    item, = project(status, tmp_path)
    assert item["report_status"] == "unavailable"
    assert item["content"] == item["report_sha256"] == ""


def test_current_exact_artifact_filter_excludes_stale_findings(tmp_path):
    from goodteam_os.multi_reviewer_projection import reviewer_statuses_for_artifact

    status, _ = fixture_report(tmp_path)
    selected = reviewer_statuses_for_artifact([status], artifact_key="release_notes", artifact_path="release.md",
                                             artifact_version="sha256:new", require_exact=True)
    assert selected == []
    assert project_gate_reports(selected, roots=[tmp_path], run_id="run-1", project_key="DEMO") == []


def test_actionable_gate_projects_only_its_current_report_to_shared_evidence(tmp_path, monkeypatch):
    import goodteam_os.web as web
    from goodteam_os.state import create_initial_state

    status, directory = fixture_report(tmp_path)
    artifact = tmp_path / "release.md"
    artifact.write_text("# Release candidate")
    status.update(artifact_path=str(artifact), artifact_version="sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest(),
                  role="release", job_id=status["id"])
    (directory / "job.json").write_text(json.dumps(status))
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda **kwargs: [status])
    monkeypatch.setattr(web, "_project_runtime_roots_for_state", lambda **kwargs: [tmp_path])
    state = create_initial_state("run-1", "DEMO", "test")
    state.artifacts["release_notes"] = str(artifact)
    state.next_node = "await_release_approval"
    review = next(r for r in web.build_approval_reviews(state, workspace=tmp_path) if r["approval"] == "release")
    assert review["reviewer_reports"][0]["report_status"] == "available"
    assert review["evidence_snapshot"]["reviewer_reports"] == review["reviewer_reports"]
    assert review["evidence_snapshot"]["qa_status"] == "unknown"
    artifact.write_text("# Changed candidate")
    review = next(r for r in web.build_approval_reviews(state, workspace=tmp_path) if r["approval"] == "release")
    assert review["reviewer_reports"] == []
