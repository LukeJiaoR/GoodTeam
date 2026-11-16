"""Real offline subprocess evidence, not paid Codex/model acceptance."""
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time

import pytest

from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionMismatch, PiSessionRuntime
from goodteam_os.workers import WorkerJobStore


def test_native_role_context_streams_before_completion_and_stop_owns_exact_job(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "3")
    root = tmp_path / "disposable-project"
    root.mkdir()
    workers = WorkerJobStore(root)
    release = tmp_path / "release"
    script = '''import json,pathlib,sys,time
prompt=sys.stdin.read()
assert 'Role: implementation' in prompt
assert 'Current bounded task' in prompt and 'Captured context for this task' in prompt
print(json.dumps({'type':'thread.started','thread_id':'offline-native-thread'}),flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'progress-1','type':'agent_message','text':'Inspecting the requested change'}}),flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'reasoning-1','type':'reasoning','text':'Not a public report'}}),flush=True)
while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.02)
pathlib.Path(sys.argv[2]).write_text('Offline native result, pending review.')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':5}}),flush=True)
'''

    def native_fixture(self, job_root, job, _spec):
        return self._run_cli_worker(job_root, job,
            command=[sys.executable, "-c", script, str(release), str(job_root / "result.md"), "--json"],
            model="offline-fixture", preflight={}, label="Offline native CLI", capture="output_file",
            summarize=lambda code, stderr: "Offline native CLI complete", timeout_seconds=8)

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", native_fixture)

    def create():
        return workers.create_job(run_id="original-dispatch", role="implementation", adapter="codex",
            task="Current bounded task", context="Captured context for this task", project_key="DEMO",
            project_task_id="DEMO-1", track_id="DEMO-1", provider_connection=None)

    def wait_report(job_id):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            progress = workers.native_events(job_id)
            if any(row["event"] == "worker_report" for row in progress["events"]):
                return progress
            time.sleep(0.02)
        pytest.fail("Native progress did not arrive while the subprocess was working.")

    with ThreadPoolExecutor(max_workers=1) as executor:
        job = create()
        running = executor.submit(workers.run_job, job["id"])
        try:
            progress = wait_report(job["id"])
            assert not running.done()
            assert progress["capabilities"] == dict(event_stream=True, abort=True, steer=False,
                                                     follow_up=False, process_owned=True)
            report = next(row for row in progress["events"] if row["event"] == "worker_report")
            assert report["job_id"] == job["id"] and report["run_id"] == "original-dispatch"
            assert report["project_task_id"] == "DEMO-1"
            assert "Inspecting" in (workers.root / job["id"] / "stdout.txt").read_text()
            assert "Not a public report" not in json.dumps(progress)
            release.touch()
            result = running.result(timeout=5)
            assert result["status"] == "completed" and result["token_usage"]["total_tokens"] == 15
            later = workers.native_events(job["id"], after=progress["cursor"])
            assert [row["event"] for row in later["events"]] == ["turn_completed"]
            assert not later["capabilities"]["abort"]
        finally:
            release.touch()
            workers.abort_native_job(job["id"])

        release.unlink()
        other = create()
        stopped = executor.submit(workers.run_job, other["id"])
        try:
            wait_report(other["id"])
            # A terminal job cannot be used as an alias to signal the next Worker.
            assert workers.abort_native_job(job["id"])["accepted"] is False
            control = workers.abort_native_job(other["id"], wait_for_ack=False)
            assert control["accepted"] and control["control_state"] in {"requested", "stopped"}
            outcome = stopped.result(timeout=5)
            assert outcome["status"] == "aborted" and outcome["attempts"] == 1
            assert outcome["disposition"] == "stopped" and outcome["termination_confirmed"] is True
        finally:
            release.touch()
            workers.abort_native_job(other["id"])


def test_retained_manager_pi_session_cannot_restart(tmp_path, monkeypatch):
    job_root = tmp_path / "job-historical"
    job_root.mkdir()
    original = '{"status":"running","session_id":"historical"}'
    (job_root / "pi_session.json").write_text(original)
    monkeypatch.setattr("goodteam_os.pi_rpc.PiRpcProcess.start",
                        lambda _: pytest.fail("Retired Manager Pi process must not start"))
    runtime = PiSessionRuntime(project_root=tmp_path, job_root=job_root,
        job={"id": job_root.name, "role": "orchestrator", "adapter": "pi"}, runtime_cwd=tmp_path,
        config=PiRuntimeConfig(sys.executable, "", "", "", tmp_path / "sessions"),
        stderr_path=tmp_path / "stderr")
    with pytest.raises(PiSessionMismatch, match="moved to Codex app-server"):
        runtime.start_or_resume()
    assert (job_root / "pi_session.json").read_text() == original
