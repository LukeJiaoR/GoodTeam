"""Release corrections travel through the current CLI result, not shared files."""
import json
from pathlib import Path
import subprocess
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


def test_collection_failure_retains_worker_and_retries_before_downstream_qa(tmp_path, monkeypatch):
    from goodteam_os import graph
    from goodteam_os.workflow_workspace import prepare_workflow_workspace, workspace_for_workflow

    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "1")
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    root = tmp_path / "project"
    root.mkdir()
    for args in [("init", "-q"), ("config", "user.name", "Collection fixture"),
                 ("config", "user.email", "collection@example.invalid")]:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "README.md").write_text("Original source\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "commit.gpgSign=false", "commit", "-qm", "Baseline"], check=True)
    config = tmp_path / "project.yaml"
    config.write_text(f"name: Collection\nkey: COLLECTION\nrepos:\n  root: {root}\n")
    state = create_initial_state("collection-run", "COLLECTION", "Deliver a reviewed project iteration")
    state.change_requests["release"] = "Complete the delivery evidence."
    state.repository_baseline = prepare_workflow_workspace(root, project_key=state.project_key, run_id=state.run_id)
    state.pending_transition = {"id": "first-delivery"}
    specs = [{"run_id": state.run_id, "role": role, "adapter": "claude", "model": "synthetic", "task": "Offline collection fixture",
              "context": "", "expected_artifacts": ["release-notes.md"] if role == "implementation" else []}
             for role in ("implementation", "qa")]
    monkeypatch.setattr(graph, "implementation_worker_job_specs", lambda *_args, **_kwargs: specs)
    calls = tmp_path / "calls"
    script = """import pathlib,shutil,subprocess,sys
sys.stdin.read()
role=sys.argv[1]
calls=pathlib.Path(sys.argv[2])
previous=calls.read_text() if calls.exists() else ''
calls.write_text(previous+role+'\\n')
if role=='implementation':
    nested=pathlib.Path('.release-checkout')
    if not previous:
        nested.mkdir()
        subprocess.run(['git','init','-q',str(nested)],check=True)
        pathlib.Path('worker.txt').write_text('Retained first output\\n')
    else:
        assert pathlib.Path('worker.txt').read_text()=='Retained first output\\n'
        shutil.rmtree(nested)
pathlib.Path(sys.argv[3]).write_text(sys.argv[4])
"""

    def offline(self, job_root, current, _spec):
        return self._run_cli_worker(job_root, current,
            command=[sys.executable, "-c", script, current["role"], str(calls), str(job_root / "result.md"),
                     DOCUMENT if current["role"] == "implementation" else "Status: passed\nOffline checks passed.\n"],
            model="synthetic", preflight={}, label="Offline collection", capture="output_file",
            summarize=lambda code, _stderr: f"Offline collection exit {code}", timeout_seconds=10)

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    graph.run_delivery_until_release_gate(state, tmp_path / "workspace", config)

    assert state.current_node == "implementation"
    assert any(".release-checkout/" in blocker for blocker in state.blockers)
    assert state.release_candidate == {}
    assert calls.read_text() == "implementation\n"
    store = WorkerJobStore(root)
    first = store.list_jobs()[0]
    assert first["status"] == "completed"
    result_path = store.root / first["id"] / "result.md"
    status_path = store.root / first["id"] / "status.json"
    original = (result_path.read_bytes(), status_path.read_bytes())
    execution_root = workspace_for_workflow(root, state.repository_baseline,
                                           project_key=state.project_key, run_id=state.run_id)
    assert (execution_root / ".release-checkout/.git").is_dir()
    assert not (root / "worker.txt").exists()

    state.pending_transition = {"id": "explicit-retry"}
    state.blockers = []
    graph.run_delivery_until_release_gate(state, tmp_path / "workspace", config)

    assert state.next_node == "await_release_approval"
    assert state.blockers == []
    assert calls.read_text() == "implementation\nimplementation\nqa\n"
    assert original == (result_path.read_bytes(), status_path.read_bytes())
    assert len(store.list_jobs()) == 3
    assert (execution_root / "worker.txt").read_text() == "Retained first output\n"
    assert not (root / "worker.txt").exists()


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


def test_retry_archives_legacy_document_but_cannot_reuse_it(release, tmp_path, monkeypatch):
    _root, config, state, store, _job = release
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "2")
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda _seconds: None)
    job = store.create_job(run_id=state.run_id, project_key=state.project_key,
                           role="implementation", adapter="claude", task="Correct release notes",
                           context="Offline retry fixture", expected_artifacts=["release-notes.md"])
    counter = tmp_path / "attempt-counter"
    script = """import pathlib,sys
sys.stdin.read()
counter=pathlib.Path(sys.argv[1])
attempt=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(attempt))
if attempt==1:
    pathlib.Path(sys.argv[2]).write_text(sys.argv[3])
    sys.stderr.write('ERROR: unexpected status 503 Service Unavailable')
    sys.exit(1)
"""

    def offline(self, job_root, current, _spec):
        return self._run_cli_worker(
            job_root, current,
            command=[sys.executable, "-c", script, str(counter), str(job_root / "release-notes.md"), DOCUMENT],
            model="synthetic", preflight={}, label="Offline release", capture="output_file",
            summarize=lambda code, stderr: "Synthetic complete" if code == 0 else stderr, timeout_seconds=10,
        )

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    result = store.run_job(job["id"])
    assert result["status"] == "completed" and result["attempts"] == 2
    with pytest.raises(ReleaseCandidateError):
        capture_release_candidate(state, tmp_path / "workspace", config, [result])
    first_attempt = Path(result["attempt_history"][0]["status_path"]).parent
    assert (first_attempt / "release-notes.md").read_text() == DOCUMENT
    assert not (store.root / job["id"] / "release-notes.md").exists()
