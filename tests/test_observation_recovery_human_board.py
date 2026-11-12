"""Free production-consumer tests; transport is simulated, not live acceptance."""
from copy import deepcopy
import json

import pytest

from goodteam_os import graph, projects, manager_execution as manager
from goodteam_os.project_observation_runtime import judge_with_production_runtime
from goodteam_os.project_running_observation import _binding
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore


@pytest.mark.parametrize("mode", ["current", "old_grant", "stopped", "stale"])
def test_original_completed_observation_recovers_human_question_once(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('GOODTEAM_STATE_DIR', str(tmp_path.parent / (tmp_path.name+'-state')))
    monkeypatch.setenv('GOODTEAM_HOME', str(tmp_path.parent / (tmp_path.name+'-home')))
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0,
        "max_manager_calls": 4, "discovery": {"allow_running_observation": True},
        "pending_batch": {"run_id": "batch", "task_ids": ["task"]}}
    if mode != "old_grant":
        value["discovery"]["allow_running_human_question"] = True
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
    workers = WorkerJobStore(tmp_path)
    job_id = calls[-1]
    job = json.loads((workers.root / job_id / 'job.json').read_text())
    output = {'assessment': 'needs_attention', 'rationale': 'Only the owner can settle the audience decision without stopping other work.',
              'observed_job_ids': ['worker'], 'human_question': {'question': 'Should this API serve internal or external consumers?',
                  'why_human': 'Only the owner can choose the intended product audience.'}}
    (workers.root / job_id / 'result.md').write_text(json.dumps(output))
    workers._write_status(workers.root / job_id, job, 'completed', 'Original result recovered locally')
    from goodteam_os.project_observation_runtime import reconcile_completed_observations
    from goodteam_os.project_human_feedback import question_board
    if mode == 'stopped':
        store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'], 'status': 'stopped'}})
    if mode == 'stale':
        candidate('newer-observation')
    def reconcile():
        with manager.manager_resource_slot(tmp_path):
            return reconcile_completed_observations(store=store, root=tmp_path, project_key='DEMO',
                value=store.read()['project_continuation'], authority_check=lambda: True)
    recovered = judge() if mode in {'current', 'old_grant'} else reconcile()
    expected = 'recorded' if mode == 'current' else 'invalid_response' if mode == 'old_grant' else 'stale_result'
    assert recovered['running_judgments']['two']['status'] == expected
    assert recovered['manager_calls'] == 2 and len(calls) == 2
    board = question_board(store.read())['items']
    assert len(board) == (1 if mode == 'current' else 0)
    if mode == 'current':
        assert board[0]['can_answer']
        assert recovered['running_judgments']['two']['human_question_id'] == board[0]['id']
        judge()
        assert len(question_board(store.read())['items']) == 1
    reconcile()
    assert len(calls) == 2 and store.read()['project_continuation']['manager_calls'] == 2
    assert store.read()['project_task_batches']['batch'] == batch
