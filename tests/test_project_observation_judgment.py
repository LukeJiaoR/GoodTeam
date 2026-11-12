"""Offline judgment contract; no live Manager Runtime or Worker reply evidence."""
from copy import deepcopy

from goodteam_os.project_observation_judgment import judge_running_observation
from goodteam_os.project_running_observation import _binding, running_observation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_budgeted_advice_is_once_only_and_never_an_execution_or_worker_reply(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0, "max_manager_calls": 2,
        "pending_batch": {"run_id": "batch", "task_ids": ["task"]}}
    batch = {"confirmed": True, "expected_continuation_id": "grant", "project_root": str(tmp_path.resolve()),
        "status": "running", "dispatch_run_id": "attempt"}
    store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    binding = _binding(store.read(), tmp_path)
    packet = {"binding": binding, "workers": [{"job_id": "job", "status": "running"}]}
    def candidate(key):
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
            "running_observation": {"digest": key, "packet": deepcopy(packet)}}})
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        # Reservation is persisted before the call; duplicate/reentrant attempts cannot spend.
        assert store.read()["project_continuation"]["manager_calls"] == len(calls)
        duplicate = judge_running_observation(store=store, root=tmp_path, value=value, runner=runner, authority_check=lambda: True)
        assert duplicate["manager_calls"] == len(calls)
        return {"assessment": "wait", "rationale": "Only local process evidence is available.", "observed_job_ids": ["job"]}
    def judge(r=runner, allowed=True):
        return judge_running_observation(store=store, root=tmp_path, value=value, runner=r, authority_check=lambda: allowed)
    candidate("one")
    assert judge(None)["manager_calls"] == 0
    assert judge(allowed=False)["manager_calls"] == 0
    first = judge()
    record = first["running_judgments"]["one"]
    assert len(calls) == 1 and record["status"] == "recorded" and record["advice_only"]
    assert not record["actions_executed"] and not record["worker_reply_attributed"]
    assert judge()["manager_calls"] == 1 and len(calls) == 1
    assert running_observation_context(store.read())["judgment"]["advice"]["assessment"] == "wait"
    candidate("two")
    assert judge()["manager_calls"] == 2
    candidate("three")
    assert judge()["manager_calls"] == 2 and len(calls) == 2
    assert store.read()["project_continuation"]["status"] == "active"


def test_uncertain_invalid_and_late_judgments_cannot_resend_or_revive(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    base = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0, "max_manager_calls": 3,
            "pending_batch": {"run_id": "batch", "task_ids": []}}
    batch = {"confirmed": True, "expected_continuation_id": "grant", "project_root": str(tmp_path.resolve()),
             "status": "running", "dispatch_run_id": "attempt"}
    def reset():
        store._mutate(lambda s: {**s, "project_continuation": deepcopy(base), "project_task_batches": {"batch": deepcopy(batch)}})
        packet = {"binding": _binding(store.read(), tmp_path), "workers": [{"job_id": "job"}]}
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "running_observation": {"digest": "one", "packet": packet}}})
    def judge(r): return judge_running_observation(store=store, root=tmp_path, value=base, runner=r, authority_check=lambda: True)
    def failed(**kwargs): raise TimeoutError("provider may have completed; PRIVATE")
    reset()
    assert judge(failed)["running_judgments"]["one"]["status"] == "unconfirmed"
    store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "running_observation": {**s["project_continuation"]["running_observation"], "digest": "two"}}})
    assert judge(failed)["manager_calls"] == 1
    reset()
    def drift_then_timeout(**kwargs):
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
            "running_observation": {**s["project_continuation"]["running_observation"], "digest": "new"}}})
        raise TimeoutError("Unknown outcome despite newer evidence")
    drifted = judge(drift_then_timeout)
    assert drifted["running_judgments"]["one"]["status"] == "unconfirmed"
    assert not drifted["running_judgments"]["one"]["current_binding"]
    assert judge(failed)["manager_calls"] == 1
    reset()
    invalid = judge(lambda **k: {"assessment": "retry", "rationale": "Restart now", "observed_job_ids": ["job"]})
    assert invalid["running_judgments"]["one"]["status"] == "invalid_response"
    assert invalid["status"] == "active"
    reset()
    def stop(**kwargs):
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
        return {"assessment": "needs_attention", "rationale": "Inspect the uncertain local evidence.", "observed_job_ids": ["job"]}
    late = judge(stop)
    assert late["status"] == "stopped" and late["running_judgments"]["one"]["status"] == "stale_result"
    assert not running_observation_context(store.read())["current_binding"]
