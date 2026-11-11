"""Offline original Runtime entry -> existing Manager observation contract."""
from copy import deepcopy
import json
from types import SimpleNamespace

from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionRuntime
from goodteam_os.workers import WorkerJobStore
from goodteam_os.task_assistant import worker_invocation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.project_running_observation import observe_running_batch
from goodteam_os.project_observation_judgment import judge_running_observation


def test_stable_invocation_output_reaches_manager_without_inventing_control_reply(tmp_path):
    jobs = WorkerJobStore(tmp_path)
    job = jobs.create_job(run_id="attempt", role="research", adapter="pi", task="Inspect", context="", project_key="DEMO")
    root = jobs.root / job["id"]
    jobs._write_status(root, job, "running", "fixture")
    runtime = PiSessionRuntime(project_root=tmp_path, job_root=root, job=job, runtime_cwd=tmp_path,
        config=PiRuntimeConfig("unused", "", "", "", tmp_path / "sessions"), stderr_path=root / "stderr")
    runtime.config.session_dir.mkdir()
    session_file = runtime.config.session_dir / "session.jsonl"
    runtime.metadata = {"job_id": job["id"], "project_key": "DEMO", "execution_id": "attempt",
                        "session_id": "PRIVATE_SESSION", "session_file": str(session_file), "status": "running", "active_operation": {"state": "settled"}}
    runtime.store.write_metadata(runtime.metadata)
    entry = {"id": "entry-a", "type": "message", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "PRIVATE_THOUGHT"}, {"type": "text", "text": "Existing interface is missing a consumer."}]}}
    session_file.write_text(json.dumps({"type": "session", "version": 3, "id": "PRIVATE_SESSION"}) + "\n" + json.dumps(entry) + "\n")
    requests = []
    def request(command, **kwargs):
        requests.append(command)
        assert command["type"] == "get_entries"
        return {"data": {"entries": [entry], "leafId": "entry-a"}}
    runtime.rpc = SimpleNamespace(request=request, wait_settled=lambda timeout: True)
    runtime._on_record({"type": "turn_end"})
    assert requests == []  # The reader never blocks on its own RPC stream.
    runtime.wait_until_settled(1)
    assert requests == []  # No runtime RPC, hence no late-response poisoning or slow ack wait.
    original_write = runtime.store.write_metadata
    def locked_write(payload):
        assert runtime._metadata_lock._is_owned()
        original_write(payload)
    runtime.store.write_metadata = locked_write
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0, "max_manager_calls": 3,
             "pending_batch": {"run_id": "batch", "task_ids": ["task"]}}
    batch = {"run_id": "batch", "confirmed": True, "project_root": str(tmp_path), "dispatch_run_id": "attempt",
             "status": "running", "expected_continuation_id": "grant"}
    store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    def observe():
        return observe_running_batch(store=store, root=tmp_path, project_key="DEMO", value=value)
    first = observe()["running_observation"]
    output = first["packet"]["workers"][0]["worker_output"]
    assert output["available"] and output["binding"]["job_id"] == job["id"]
    assert output["entries"] == [{"entry_id": "entry-a", "text": "Existing interface is missing a consumer.", "truncated": False}]
    assert not output["control_reply_attributed"] and "PRIVATE_" not in json.dumps(first)
    calls = []
    def manager(**kwargs):
        calls.append(kwargs)
        assert kwargs["observation"]["workers"][0]["worker_output"] == output
        return {"assessment": "needs_attention", "rationale": "Worker reports a missing consumer.", "observed_job_ids": [job["id"]]}
    judged = judge_running_observation(store=store, root=tmp_path, value=value, runner=manager, authority_check=lambda: True)
    assert judged["manager_calls"] == 1 and len(calls) == 1
    runtime.get_entries()  # Duplicate stable entry: no new Manager wakeup.
    assert observe()["running_observation"]["digest"] == first["digest"]
    # Unbound old output and foreign-session output must not leak into the packet.
    with runtime.store.entries_path.open("a") as f:
        f.write(json.dumps({**entry, "id": "legacy", "message": {"role": "assistant", "content": "PRIVATE_LEGACY"}}) + "\n")
        f.write(json.dumps({**entry, "id": "foreign", "_goodteam_source": {**output["binding"], "session_digest": "other"}}) + "\n")
    assert observe()["running_observation"]["digest"] == first["digest"]
    # Later output is retained for history, but cannot restart a stopped grant.
    store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
    entry["id"] = "entry-b"
    runtime.get_entries()
    assert observe()["running_observation"] == first
    jobs._write_status(root, job, "completed", "returned, not accepted")
    history = worker_invocation_context(tmp_path, "DEMO", store.read(), observe_runtime=True)["recent_history"][0]
    assert len(history["worker_output"]["entries"]) == 2
    # A replaced session cannot inherit old entries, even under the same job.
    runtime.metadata["session_id"] = "replacement"
    with runtime._metadata_lock:
        runtime.store.write_metadata(runtime.metadata)
    assert worker_invocation_context(tmp_path, "DEMO", store.read(), observe_runtime=True)["recent_history"][0]["worker_output"]["entries"] == []


def test_local_output_snapshot_rejects_foreign_header_partial_tail_and_outside_root(tmp_path):
    import pytest
    job = {"id": "job", "run_id": "attempt"}
    runtime = PiSessionRuntime(project_root=tmp_path, job_root=tmp_path, job=job, runtime_cwd=tmp_path,
        config=PiRuntimeConfig("unused", "", "", "", tmp_path / "sessions"), stderr_path=tmp_path / "stderr")
    runtime.config.session_dir.mkdir()
    path = runtime.config.session_dir / "session.jsonl"
    runtime.metadata = {"session_id": "expected", "session_file": str(path)}
    path.write_text(json.dumps({"type": "session", "id": "foreign"}) + "\n")
    with pytest.raises(ValueError, match="header identity"):
        runtime._capture_persisted_entries()
    assert not runtime.store.entries_path.exists()
    path.write_text(json.dumps({"type": "session", "id": "expected"}) + "\n" + '{"id":"partial"')
    runtime._capture_persisted_entries()
    assert not runtime.store.entries_path.exists()
    runtime.metadata["session_file"] = str(tmp_path / "outside.jsonl")
    with pytest.raises(ValueError, match="outside"):
        runtime._capture_persisted_entries()
