"""Read real local session files; no provider execution or paid Manager call."""
import json
import os

from goodteam_os.task_assistant import _manager_chat_context, worker_invocation_context
from goodteam_os.workers import WorkerJobStore
from goodteam_os.manager_runtime_observation import runtime_observation


def test_manager_observes_local_runtime_without_control_or_state_changes(tmp_path, monkeypatch):
    jobs = WorkerJobStore(tmp_path)
    job = jobs.create_job(run_id="dispatch", role="research", adapter="pi", task="Inspect task", context="", project_key="DEMO")
    root = jobs.root / job["id"]
    jobs._write_status(root, job, "running", "Local fixture", extra={"pid": os.getpid(), "heartbeat_at": jobs._now()})
    jobs._write_json(root / "pi_session.json", {"status": "running", "isStreaming": True,
        "pendingMessageCount": 2, "session_id": "PRIVATE_SESSION", "runtime_cwd": "PRIVATE_PATH", "provider": "PRIVATE_PROVIDER"})
    batch = {"run_id": "batch", "confirmed": True, "project_root": str(tmp_path.resolve()),
             "dispatch_run_id": "dispatch", "status": "running"}
    session = {"project_task_batches": {"batch": batch}}
    def no_action(*args, **kwargs):
        raise AssertionError("Observation must not control, wait for ack, or run a job")
    for name in ("session_control", "session_control_result", "run_job"):
        monkeypatch.setattr(WorkerJobStore, name, no_action)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    visible = _manager_chat_context({}, {}, session, root=tmp_path, project_key="DEMO")
    snapshot = json.loads(visible.split("Worker invocation records (fresh read-only state): ")[1])
    row = snapshot["current"][0]
    observation = row["runtime_observation"]
    assert row["status"] == "running" and observation["available"]
    assert observation["evidence"]["process_alive"] is True
    assert observation["evidence"]["isStreaming"] is True
    assert observation["evidence"]["pendingMessageCount"] == 2
    assert observation["observed_at"] and observation["read_only"]
    assert "PRIVATE_" not in visible
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert "runtime_observation" not in worker_invocation_context(tmp_path, "DEMO", session)["current"][0]

    jobs._write_status(root, job, "running", "Stale local fixture", extra={"pid": os.getpid(), "heartbeat_at": "2000-01-01T00:00:00+00:00"})
    stale = worker_invocation_context(tmp_path, "DEMO", session, observe_runtime=True)["current"][0]
    assert stale["status"] == "running" and stale["runtime_observation"]["evidence"]["process_alive"] is False
    # This remains an observation, not a failure transition or retry decision.
    assert jobs._read_json(root / "status.json")["status"] == "running"
    batch["current_dispatch_run_id"] = "recovered-dispatch"
    assert worker_invocation_context(tmp_path, "DEMO", session, observe_runtime=True)["current"][0]["runtime_observation"]["reason"] == "superseded_attempt"


def test_unavailable_or_unsupported_runtime_is_not_dead_or_a_reply():
    class Jobs:
        def session_status(self, job):
            raise ValueError("skill_runtime_not_connected PRIVATE_PATH")
    item = {"job_id": "job-a", "adapter": "pi"}
    denied = runtime_observation(Jobs(), item)
    assert not denied["available"] and "evidence" not in denied
    assert "PRIVATE_PATH" not in json.dumps(denied)
    assert runtime_observation(Jobs(), {**item, "adapter": "fake"})["reason"] == "runtime_observation_unsupported"
    class WrongIdentity:
        def session_status(self, job): return {"job_id": "other-job", "process_alive": True}
    assert runtime_observation(WrongIdentity(), item)["reason"] == "invocation_identity_mismatch"
