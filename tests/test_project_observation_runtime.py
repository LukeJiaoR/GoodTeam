"""Free production-consumer tests; transport is simulated, not live acceptance."""
from copy import deepcopy
import json

import pytest

from goodteam_os import graph, projects, manager_execution as manager
from goodteam_os.project_observation_runtime import judge_with_production_runtime
from goodteam_os.project_running_observation import _binding
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore


def test_production_consumer_reserves_single_job_and_preserves_stop_and_uncertainty(tmp_path, monkeypatch):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0,
        "max_manager_calls": 4, "discovery": {"allow_running_observation": True},
        "pending_batch": {"run_id": "batch", "task_ids": ["task"]}}
    from goodteam_os.project_outcome_discovery import authorization_digest
    value.update(seed_task_ids=["task"], seed_task_intents={"task": "intent"},
        approved_backlog_version="version", max_batches=3)
    value["authorization_digest"] = authorization_digest(value)
    batch = {"confirmed": True, "expected_continuation_id": "grant", "project_root": str(tmp_path.resolve()),
        "status": "running", "dispatch_run_id": "attempt"}
    store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    packet = {"binding": _binding(store.read(), tmp_path), "workers": [{"job_id": "worker", "status": "running"}]}
    def candidate(key):
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
            "running_observation": {"digest": key, "packet": deepcopy(packet)}}})
    monkeypatch.setattr(projects, "load_project_target", lambda p: {})
    monkeypatch.setattr(graph, "resolve_project_runtime_root", lambda r: tmp_path)
    monkeypatch.setattr(graph, "resolve_role_adapter", lambda *a: "fake")
    monkeypatch.setattr(manager, "_configured_manager_model", lambda t: "fixture")
    monkeypatch.setattr(manager, "_manager_settings", lambda t: {})
    calls = []
    def transport(workers, job_id):
        record = store.read()["project_continuation"]["running_judgments"][calls_key[0]]
        assert record["manager_invocation"]["job_id"] == job_id
        assert record["status"] == "reserved"
        calls.append(job_id)
        if calls_key[0] == "two":
            raise TimeoutError("unknown external result")
        output = {"assessment": "wait", "rationale": "Worker process is still running.", "observed_job_ids": ["worker"]}
        (workers.root / job_id / "result.md").write_text(json.dumps(output))
        return {"status": "completed"}
    monkeypatch.setattr(WorkerJobStore, "run_job", transport)
    calls_key = ["one"]
    def judge():
        return judge_with_production_runtime(store=store, root=tmp_path, project_config=tmp_path / "project.yaml",
            project_key="DEMO", value=store.read()["project_continuation"], authority_check=lambda: True)
    candidate("one")
    with manager.manager_resource_slot(tmp_path):
        assert judge()["manager_calls"] == 0  # Busy is not billable.
    saved = judge()
    assert saved["manager_calls"] == 1 and saved["running_judgments"]["one"]["status"] == "recorded"
    assert len(calls) == 1
    judge()
    assert len(calls) == 1
    calls_key[0] = "two"
    candidate("two")
    assert judge()["running_judgments"]["two"]["status"] == "unconfirmed"
    candidate("three")
    assert judge()["manager_calls"] == 2 and len(calls) == 2
    store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
    with pytest.raises(ValueError, match="stopped"):
        judge()
    assert len(calls) == 2


def test_existing_manager_paths_share_nonblocking_slot_and_do_not_retry(tmp_path):
    with manager._manager_turn_lock(tmp_path, "chat"):
        with pytest.raises(manager.ManagerExecutionError, match="busy"):
            with manager._manager_turn_lock(tmp_path, "observation"):
                pytest.fail("parallel Manager")
    with manager._manager_turn_lock(tmp_path, "next"):
        pass
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(run_id="test", role="orchestrator", adapter="codex", model="fixture", task="Read only", context="",
        execution_policy="read_only", phase="project_running_observation")
    with pytest.raises(ValueError, match="launch authority"):
        workers.run_job(job["id"])
    assert job["max_attempts"] == 1


def test_new_permission_does_not_expand_old_grants(tmp_path):
    from goodteam_os.project_outcome_discovery import normalize_discovery
    grant = dict(objective="Inspect running Worker evidence safely.", allowed_write_domains=["src"], max_new_tasks=1, max_planning_calls=2)
    assert "allow_running_observation" not in normalize_discovery(grant)
    assert normalize_discovery({**grant, "allow_running_observation": True})["allow_running_observation"]
    with pytest.raises(ValueError):
        normalize_discovery({**grant, "allow_running_observation": "true"})
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"discovery": grant, "manager_calls": 0}
    assert judge_with_production_runtime(store=store, root=tmp_path, project_config=tmp_path / "unused",
        project_key="DEMO", value=value, authority_check=lambda: False) is value


def _stop_in_another_process(root, ready, go, stopped):
    store = TaskAssistantSessionStore(root, "DEMO")
    ready.set()
    if go.wait(5):
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
        stopped.set()


def test_stop_and_transport_spawn_are_linearized_without_holding_session_for_wait(tmp_path, monkeypatch):
    import threading
    from goodteam_os.project_observation_runtime import _launch_under_authority
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed",
        "pending_batch": {"run_id": "batch", "task_ids": []},
        "running_judgments": {"obs": {"status": "reserved", "manager_invocation": {"job_id": "manager"}}}}
    batch = {"confirmed": True, "expected_continuation_id": "grant", "project_root": str(tmp_path.resolve()),
        "status": "running", "dispatch_run_id": "attempt"}
    def reset():
        store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    reset()
    packet = {"binding": _binding(store.read(), tmp_path)}
    launch_job = ["manager"]
    def call(launch):
        return _launch_under_authority(store=store, root=tmp_path, observation=packet, observation_id="obs",
            continuation_id="grant", job_id=launch_job[0], authority_check=lambda: True, launch=launch)
    def stop():
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
    stop()
    with pytest.raises(manager.ManagerExecutionError):
        call(lambda: pytest.fail("Stop won; must not spawn"))
    # Exercise the production pipe entry, not only the helper: Stop arriving
    # during final transport preparation must prevent the actual Popen boundary.
    reset()
    workers = WorkerJobStore(tmp_path, launch_authorizer=lambda job, launch: call(launch))
    job = workers.create_job(run_id="observation", role="orchestrator", adapter="codex",
        model="fixture", task="Read only", context="", execution_policy="read_only",
        phase="project_running_observation")
    launch_job[0] = job["id"]
    store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
        "running_judgments": {"obs": {"status": "reserved",
            "manager_invocation": {"job_id": job["id"]}}}}})
    monkeypatch.setattr(workers, "_memory_transport", lambda *args: stop())
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Stop must prevent Popen"))
    with pytest.raises(manager.ManagerExecutionError, match="before transport launch"):
        workers._run_cli_worker(workers.root / job["id"], job, command=["unused-fixture"],
            model="fixture", preflight={}, label="fixture", capture="stdout", summarize=lambda value: value)
    reset()
    launch_job[0] = "manager"
    import multiprocessing
    context = multiprocessing.get_context("fork")
    ready, go, stopped = context.Event(), context.Event(), context.Event()
    stopper = context.Process(target=_stop_in_another_process, args=(tmp_path, ready, go, stopped))
    stopper.start()  # Fork before the launcher owns a thread lock.
    assert ready.wait(2)
    spawned, release = threading.Event(), threading.Event()
    process = object()
    def launch():
        spawned.set()
        assert release.wait(2)
        return process
    launcher = threading.Thread(target=lambda: call(launch))
    launcher.start()
    assert spawned.wait(2)
    go.set()
    assert not stopped.wait(0.05)
    release.set()
    launcher.join(2)
    assert stopped.wait(2)  # no communicate/wait held the session lock
    stopper.join(2)
    assert stopper.exitcode == 0
    assert store.read()["project_continuation"]["status"] == "stopped"


def test_spawn_is_owned_and_reaped_when_session_save_fails(tmp_path, monkeypatch):
    from goodteam_os.project_observation_runtime import _launch_under_authority
    from goodteam_os.skills import review_process
    from types import SimpleNamespace
    import io
    session = {"project_continuation": {"id": "grant", "status": "active", "authority": "fixed",
        "pending_batch": {"run_id": "batch", "task_ids": []},
        "running_judgments": {"obs": {"status": "reserved", "manager_invocation": {"job_id": "manager"}}}},
        "project_task_batches": {"batch": {"confirmed": True, "expected_continuation_id": "grant",
            "project_root": str(tmp_path.resolve()), "status": "running", "dispatch_run_id": "attempt"}}}
    packet = {"binding": _binding(session, tmp_path)}
    def mutate(operation):
        operation(session)
        raise OSError("disk full after spawn")
    process = SimpleNamespace(pid=12345, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    terminated = []
    monkeypatch.setattr(review_process, "_terminate", lambda child: terminated.append(child))
    with pytest.raises(OSError, match="disk full"):
        _launch_under_authority(store=SimpleNamespace(_mutate=mutate), root=tmp_path, observation=packet,
            observation_id="obs", continuation_id="grant", job_id="manager", authority_check=lambda: True,
            launch=lambda: process)
    assert terminated == [process]
    assert all(getattr(process, name).closed for name in ("stdin", "stdout", "stderr"))
