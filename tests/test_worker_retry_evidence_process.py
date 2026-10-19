"""Actual offline CLI boundaries: retry evidence, host timeout, and artifact copy."""
import json
from pathlib import Path
import sys

import pytest

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.graph import render_downstream_artifact_markdown
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


@pytest.mark.parametrize("host_timeout", [False, True])
def test_offline_cli_attempt_evidence_and_sanitized_artifact_copy(tmp_path, monkeypatch, host_timeout):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "2")
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda _seconds: None)
    project = tmp_path / "project"
    project.mkdir()
    counter = tmp_path / "invocations"
    script = '''import pathlib,sys,time
counter=pathlib.Path(sys.argv[1])
attempt=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(attempt))
sys.stdin.read()
if sys.argv[2]=='host':
    print('partial synthetic output',flush=True)
    time.sleep(5)
if attempt==1:
    print('first synthetic output',flush=True)
    sys.stderr.write('Provider request timeout')
    sys.exit(1)
print('# Synthetic report')
print('Changed src/example.py.')
print('Authorization: Bearer SYNTHETIC_PROCESS_CREDENTIAL')
print('Verification: isolated checks passed.')
'''

    def run_offline(self, job_root, job, _spec):
        return self._run_cli_worker(
            job_root, job,
            command=[sys.executable, "-c", script, str(counter), "host" if host_timeout else "provider"],
            model="synthetic", preflight={"claude_available": True}, label="Synthetic CLI", capture="stdout",
            summarize=lambda code, stderr: "Synthetic complete" if code == 0 else stderr,
            timeout_seconds=0.3 if host_timeout else 10,
        )

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", run_offline)
    workers = WorkerJobStore(project)
    job = workers.create_job(run_id="run-synthetic", project_key="SYNTHETIC", role="implementation",
                             adapter="claude", task="Validate synthetic evidence", context="Offline fixtures only")

    status = workers.run_job(job["id"])

    assert int(counter.read_text()) == status["attempts"] == (1 if host_timeout else 2)
    assert status["status"] == ("failed" if host_timeout else "completed")
    assert status["disposition"] == ("human_handoff" if host_timeout else "recovered")
    archives = [json.loads(Path(item["status_path"]).read_text()) for item in status["attempt_history"]]
    assert archives[0]["attempts"] == 1
    assert archives[0]["failure_class"] == "timeout"
    assert archives[0]["disposition"] == ("human_handoff" if host_timeout else "retrying")
    for archive, entry in zip(archives, status["attempt_history"]):
        for field in ("stdout_path", "stderr_path", "result_path"):
            assert archive["invocation_evidence"][field] == archive[field]
            assert Path(archive[field]).parent == Path(entry["status_path"]).parent
    if host_timeout:
        return
    assert Path(archives[0]["stdout_path"]).read_text().strip() == "first synthetic output"
    assert Path(archives[0]["stderr_path"]).read_text() == "Provider request timeout"
    assert archives[1]["disposition"] == "completed"
    assert "failure_class" not in archives[1]
    state = create_initial_state("run-synthetic", "SYNTHETIC", "Validate synthetic evidence")
    for artifact_key in ("implementation_handoff", "qa_report"):
        content = render_downstream_artifact_markdown(artifact_key, state, worker_results=[status])
        saved = ArtifactStore(tmp_path / "workspace", mirror_root=project).write_markdown(
            state.run_id, "delivery", f"{artifact_key}.md", content,
        )
        assert "SYNTHETIC_PROCESS_CREDENTIAL" not in saved.read_text()
        assert "Changed src/example.py." in content
        assert "Verification: isolated checks passed." in content
        assert saved.read_text() == (project / "docs" / f"{artifact_key}.md").read_text()
