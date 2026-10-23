"""In-process GET integration with real stores and synthetic retained evidence.

No worker is executed and no Git/model/browser/task acceptance is asserted.
Only the HTTP transport is substituted; discovery, persistence and projections
are production code. Inputs stand in for bytes already captured by the host.
"""
import io
import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from goodteam_os import web
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.resources import register_markdown_artifact
from goodteam_os.source_preview import build_source_preview, register_source_preview
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("This projection integration must not execute a subprocess or worker")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(WorkerJobStore, "run_job", forbidden)
    root = tmp_path / "project"
    root.mkdir()
    config = root / "project.yaml"
    config.write_text(f"name: Integration\nkey: DEMO\nrepos:\n  app: {root}\nreviewer_profiles:\n  release:\n    - correctness-security\n")
    ProjectRegistry().register_project(config, root=root, key="DEMO")
    state = create_initial_state("run-integration", "DEMO", "Synthetic retained evidence")
    state.source_ref = str(config)
    state.next_node = "await_release_approval"
    release = tmp_path / "artifacts/runs/run-integration/release/release.md"
    release.parent.mkdir(parents=True)
    release.write_text("# Candidate\nSynthetic fixture, not a successful task.\n")
    register_markdown_artifact(state, "release_notes", release)
    files = {"src/main.py": b"print('retained revision')\n", "src/config.py": b'API_KEY="private-fixture-key"',
             ".env": b"PRIVATE_FIXTURE_VALUE"}
    result = dict(job_id="workflow-integration", baseline_commit="a" * 40,
                  result_commit="b" * 40, changed_files=list(files), project_key="DEMO", run_id=state.run_id)
    result["source_preview"] = build_source_preview(
        capture={"files": {name: {"mode": "100644", "data": data} for name, data in files.items()}},
        result=result, project_key=state.project_key, run_id=state.run_id)
    register_source_preview(tmp_path, state, result)
    state.release_candidate["repository_result"] = result
    StateStore(tmp_path).write_project_current(state)
    store = WorkerJobStore(root)
    job = store.create_job(run_id=state.run_id, project_key="DEMO", role="release", adapter="manual",
                           task="Inspect retained fixture", context="No execution requested",
                           profile_id="correctness-security", approval="release", phase="release",
                           artifact_key="release_notes", artifact_path=str(release),
                           artifact_version="sha256:" + sha256(release.read_bytes()).hexdigest())
    job_root = store.root / job["id"]
    (job_root / "result.md").write_text("High: QA BLOCKED; exit 1, zero tests.\nAuthorization: Bearer fixture-private-token\n")
    # Synthetic retained result only, explicitly not a worker execution receipt.
    (job_root / "status.json").write_text(json.dumps({"status": "completed", "result_path": str(job_root / "result.md")}))
    captured = []
    class TransportCaptured(Exception):
        pass
    def capture_handler(address, handler):
        captured.append(handler)
        raise TransportCaptured
    monkeypatch.setattr(web, "ThreadingHTTPServer", capture_handler)
    with pytest.raises(TransportCaptured):
        web.serve(tmp_path)
    def get(path):
        handler = object.__new__(captured[0])
        handler.path = path
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.do_GET()
        head, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(body)
    def detail():
        status, payload = get(f"/api/projects/DEMO/runs/{state.run_id}")
        assert status == 200, payload
        return payload
    return tmp_path, state, release, job_root, get, detail


def release_review(payload):
    return next(item for item in payload["approval_reviews"] if item["approval"] == "release")


def test_registered_candidate_routes_bind_source_and_discovered_findings(scenario):
    workspace, state, release, job_root, get, detail = scenario
    payload = detail()
    artifact = next(item for item in payload["delivery_summary"]["artifacts"] if item["key"] == "source_preview")
    status, body = get(artifact["href"])
    assert status == 200
    assert body["version"] == state.release_candidate["repository_result"]["source_preview_version"]
    assert "retained revision" in body["content"]
    assert "private-fixture-key" not in body["content"]
    assert "PRIVATE_FIXTURE_VALUE" not in body["content"]
    assert "Excluded or unavailable changed files: 2" in body["content"]
    review = release_review(payload)
    report, = review["reviewer_reports"]
    assert report["report_status"] == "available"
    assert report["job_id"] == job_root.name
    assert report["artifact_version"] == review["artifact_version"]
    assert "QA BLOCKED; exit 1, zero tests" in report["content"]
    assert "fixture-private-token" not in report["content"]
    assert report["advisory_only"] is True
    assert review["evidence_snapshot"]["reviewer_reports"] == [report]
    assert review["evidence_snapshot"]["qa_status"] == "unknown"
    assert review["is_pending"] is True
    assert get(artifact["href"].replace("project=DEMO", "project=OTHER"))[0] == 404
    assert get(artifact["href"].replace("run-integration", "run-other"))[0] == 404
    assert get(artifact["href"].replace(artifact["version"][7:], "0" * 64))[0] == 409
    # Rereads cannot turn the synthetic completed reviewer into QA success.
    assert release_review(detail())["evidence_snapshot"]["qa_status"] == "unknown"


@pytest.mark.parametrize("mutation", ["content", "candidate", "symlink"])
def test_retained_source_replacement_fails_at_actual_get_route(scenario, mutation):
    workspace, state, release, job_root, get, detail = scenario
    artifact = next(item for item in detail()["delivery_summary"]["artifacts"] if item["key"] == "source_preview")
    source = Path(state.artifacts["source_preview"])
    if mutation == "content":
        source.write_text("replacement must not leak")
    elif mutation == "candidate":
        state.release_candidate["repository_result"]["result_commit"] = "c" * 40
        StateStore(workspace).write_project_current(state)
    else:
        private = workspace / "private.md"
        private.write_text("replacement must not leak")
        source.unlink()
        source.symlink_to(private)
    status, body = get(artifact["href"])
    assert status in {404, 409}
    assert "replacement must not leak" not in json.dumps(body)


@pytest.mark.parametrize("mutation", ["artifact", "status_binding", "report_symlink", "foreign_project", "foreign_run"])
def test_discovered_reports_fail_closed_for_mismatches(scenario, mutation):
    workspace, state, release, job_root, get, detail = scenario
    assert release_review(detail())["reviewer_reports"][0]["report_status"] == "available"
    if mutation == "artifact":
        release.write_text("# New candidate")
    elif mutation == "report_symlink":
        (job_root / "result.md").unlink()
        (job_root / "result.md").symlink_to(release)
    elif mutation == "status_binding":
        path = job_root / "status.json"
        status = json.loads(path.read_text())
        status["profile_id"] = "product-operator"
        path.write_text(json.dumps(status))
    else:
        path = job_root / "job.json"
        job = json.loads(path.read_text())
        job["project_key" if mutation == "foreign_project" else "run_id"] = "OTHER"
        path.write_text(json.dumps(job))
    review = release_review(detail())
    assert not any(report["report_status"] == "available" for report in review["reviewer_reports"])
    assert review["evidence_snapshot"]["qa_status"] == "unknown"
    assert review["is_pending"] is True


def test_archived_run_retains_its_preview_and_reports_after_successor(scenario):
    workspace, state, release, job_root, get, detail = scenario
    old = detail()
    artifact = next(item for item in old["delivery_summary"]["artifacts"] if item["key"] == "source_preview")
    before = get(artifact["href"])
    store = StateStore(workspace)
    store.archive_project_current("DEMO")
    successor = create_initial_state("successor-run", "DEMO", "Unrelated successor")
    successor.source_ref = state.source_ref
    successor.next_node = "await_release_approval"
    store.write_project_current(successor)
    assert get(artifact["href"]) == before
    archived = detail()
    assert archived["run"]["status"] == "archived"
    assert release_review(archived)["reviewer_reports"] == release_review(old)["reviewer_reports"]
    status, current = get("/api/projects/DEMO/runs/successor-run")
    assert status == 200
    assert release_review(current)["reviewer_reports"] == []
    assert not any(item["key"] == "source_preview" for item in current["delivery_summary"]["artifacts"])
    assert get(artifact["href"].replace("run-integration", "successor-run"))[0] == 404
    assert store.read_project_current("DEMO").approvals == successor.approvals
