"""Release corrections travel through the current CLI result, not shared files."""
import json
from pathlib import Path
import sys

import pytest

from goodteam_os.release_artifacts import (
    ReleaseCandidateError, bind_release_candidate, capture_release_candidate, validated_release_candidate,
)
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


DOCUMENT = """# Release Notes

## Release Identity
Synthetic candidate; no deployment was performed.

## Verification
Offline fixture only. Downstream QA and review are pending.

## Risks and Limitations
No business acceptance is claimed.

## Rollback
Not exercised.
"""


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "1")
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    root = tmp_path / "project"
    root.mkdir()
    config = tmp_path / "project.yaml"
    config.write_text(f"name: Release\nkey: RELEASE\nrepos:\n  root: {root}\n")
    state = create_initial_state("release-run", "RELEASE", "Complete release correction")
    state.change_requests["release"] = "Correct the release evidence."
    store = WorkerJobStore(root)
    job = store.create_job(run_id=state.run_id, project_key=state.project_key,
                           role="implementation", adapter="claude", task="Correct release notes",
                           context="Offline fixture", expected_artifacts=["release-notes.md"])
    return root, config, state, store, job


def run_result(monkeypatch, store, job, output):
    script = """import pathlib,sys
sys.stdin.read()
output=sys.argv[2]
if output != 'NO_OUTPUT':
    pathlib.Path(sys.argv[1]).write_text(output)
"""

    def offline(self, job_root, current, _spec):
        return self._run_cli_worker(
            job_root, current,
            command=[sys.executable, "-c", script, str(job_root / "result.md"), output],
            model="synthetic", preflight={}, label="Offline release", capture="output_file",
            summarize=lambda code, _stderr: f"Offline release exit {code}", timeout_seconds=10,
        )

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    result = store.run_job(job["id"])
    assert result["status"] == "completed"
    return result


def test_release_correction_captures_cli_result_and_preserves_qa_binding(release, tmp_path, monkeypatch):
    root, config, state, store, job = release
    (root / "release-notes.md").write_text("Stale shared notes must not be used.")
    result = run_result(monkeypatch, store, job, DOCUMENT)
    assert not (store.root / job["id"] / "release-notes.md").exists()
    capture_release_candidate(state, tmp_path / "workspace", config, [result])
    assert Path(state.release_candidate["path"]).read_text() == DOCUMENT
    assert "Return the complete corrected release document as your final assistant response" in store._worker_prompt(
        store.root / job["id"], job,
    )
    qa = tmp_path / "qa.md"
    qa.write_text("Status: passed\n")
    state.artifacts["qa_report"] = str(qa)
    bind_release_candidate(state)
    assert validated_release_candidate(state, tmp_path / "workspace") == DOCUMENT
    qa.write_text("Status: failed\n")
    with pytest.raises(ReleaseCandidateError):
        validated_release_candidate(state, tmp_path / "workspace")


@pytest.mark.parametrize("output", ["Changed files: shared release-notes.md", "NO_OUTPUT"])
def test_release_correction_rejects_summary_and_missing_current_result(release, tmp_path, monkeypatch, output):
    root, config, state, store, job = release
    (root / "release-notes.md").write_text(DOCUMENT)
    # A previous result cannot satisfy an attempt that returns no final document.
    (store.root / job["id"] / "result.md").write_text(DOCUMENT)
    result = run_result(monkeypatch, store, job, output)
    if output == "NO_OUTPUT":
        assert (store.root / job["id"] / "result.md").read_text().startswith("# Worker Result\n")
    with pytest.raises(ReleaseCandidateError):
        capture_release_candidate(state, tmp_path / "workspace", config, [result])
    assert state.release_candidate == {}


def test_existing_exact_job_local_release_document_remains_supported(release, tmp_path):
    _root, config, state, store, job = release
    path = store.root / job["id"]
    current = json.loads((path / "job.json").read_text())
    current["status"] = "completed"
    (path / "job.json").write_text(json.dumps(current))
    (path / "result.md").write_text("Changed files: release-notes.md")
    (path / "release-notes.md").write_text(DOCUMENT)
    capture_release_candidate(state, tmp_path / "workspace", config,
                              [{"role": "implementation", "job_id": job["id"], "status": "completed"}])
    assert Path(state.release_candidate["path"]).read_text() == DOCUMENT
