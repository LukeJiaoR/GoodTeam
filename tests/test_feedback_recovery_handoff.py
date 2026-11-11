"""Explicit recovery rejoins released feedback ownership; execution is a fixture."""
from contextlib import nullcontext
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from goodteam_os import project_iteration_recovery as recovery
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_execution as execution
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os import assistant_requests, tasks
from goodteam_os.project_batch_feedback import _digest, _SCHEMA


def test_explicit_recovery_restores_original_pointer_once_and_preserves_stop(tmp_path, monkeypatch):
    proposals, revisions = ["failed-proposal"], {"failed-proposal": 1}
    run_id, dispatch_id = execution._identity(proposals, revisions)
    task = dict(id="DEMO-1", title="Original task", description="Original bounded task",
        acceptance_criteria=["Original acceptance"], dependencies=[], write_domains=["src"], role="implementation",
        execution_policy="workspace_write", status="blocked", run_id=run_id, delivery_verified=False)
    grant = dict(id="grant", status="blocked", authority={"scope": "unchanged"}, task_ids=[task["id"]],
        task_intents={task["id"]: continuation._intent(task)}, seed_task_ids=[task["id"]],
        seed_task_intents={task["id"]: continuation._intent(task)}, approved_backlog_version="approved",
        discovery={"objective": "original scope"}, max_batches=3, max_manager_calls=4, pending_batch=None)
    grant["authorization_digest"] = discovery.authorization_digest(grant)
    batch = dict(run_id=run_id, dispatch_run_id=dispatch_id, task_ids=[task["id"]], proposal_ids=proposals,
        expected_revisions=revisions, expected_continuation_id=grant["id"], expected_authority=grant["authority"],
        confirmed=True, status="blocked", project_root=str(tmp_path), project_config="project.yaml",
        task_bindings=[dict(task_id=task["id"], task_intent=tasks.project_task_intent(task))],
        tracks=[dict(project_task_id=task["id"], role="implementation", context="original instructions")])
    report = dict(run_id=dispatch_id, project_root=str(tmp_path), jobs=[dict(
        project_task_id=task["id"], job_id="original-job", outcome="needs_human")])
    raw = json.dumps(report).encode()
    report_digest = "sha256:" + sha256(raw).hexdigest()
    key = run_id + ":" + dispatch_id
    receipt = dict(schema_version=_SCHEMA, continuation_id=grant["id"], authorization_digest=grant["authorization_digest"],
        observation_key=key, batch_run_id=run_id, dispatch_run_id=dispatch_id, dispatch_report_digest=report_digest,
        tasks=[dict(task_id=task["id"], task_intent_digest=continuation._intent(task))])
    receipt["receipt_id"] = _digest(receipt)
    grant["batch_observations"] = {key: receipt}
    state = dict(project_continuation=deepcopy(grant), project_task_batches={run_id: deepcopy(batch)}, queued_task_proposals=[])
    ledger = {"tasks": [deepcopy(task)]}

    class Store:
        def __init__(self, *args): pass
        def read(self): return deepcopy(state)
        def _mutate(self, operation):
            updated = operation(deepcopy(state))
            state.clear()
            state.update(updated)
            return self.read()

    class Ledger:
        def __init__(self, *args): pass
        def _lock(self): return nullcontext()
        def _read(self): return deepcopy(ledger)
        def _write(self, value): ledger.update(deepcopy(value))
        def _now(self): return "fixture-now"

    monkeypatch.setattr(recovery, "TaskAssistantSessionStore", Store)
    monkeypatch.setattr(recovery, "LocalTaskStore", Ledger)
    monkeypatch.setattr(recovery, "goal_guard", lambda *args: nullcontext())
    monkeypatch.setattr(recovery, "workflow_guard", lambda *args: nullcontext())
    monkeypatch.setattr(assistant_requests, "conversation_lock", lambda *args: nullcontext())
    monkeypatch.setattr(recovery, "dispatch_artifact_root", lambda *args: tmp_path)
    monkeypatch.setattr(recovery, "read_bytes", lambda path: raw if path.name == "report.json" else b'{"idempotency_key":"old-call"}')
    monkeypatch.setattr(recovery, "dispatch_job_terminal", lambda *args: ({"status": "failed", "summary": "known failure"}, {"proof": "fixture"}))
    monkeypatch.setattr(recovery, "_quality_quiescent", lambda *args: True)
    monkeypatch.setattr(recovery, "read_dispatch_recovery", lambda *args: {})
    monkeypatch.setattr(execution, "_require_execution_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution, "_require_authority", lambda *args, **kwargs: None)
    starts = []
    def resume(workspace, project_key, root, saved, command_id):
        command = state["project_task_batches"][run_id]["recovery_commands"][command_id]
        if not command["started"]:
            starts.append(command["attempt_id"])
            command["started"] = True
    monkeypatch.setattr(recovery, "_resume_command", resume)
    expected_pending = dict(run_id=run_id, task_ids=[task["id"]], proposal_ids=proposals, expected_revisions=revisions)
    assert recovery._continuation_pending_for_recovery(grant, batch, report_digest) == expected_pending
    # No pending judgment, other task, tampered source or stopped authority can be stolen.
    for field, value in [("status", "stopped"), ("pending_batch", {"run_id": "other"}),
                         ("discovery_pending", {"reserved": True}), ("investigation_pending", "diagnostic"),
                         ("batch_feedback_pending", key), ("planning_backlog_version", "reserved")]:
        assert recovery._continuation_pending_for_recovery({**grant, field: value}, batch, report_digest) is None
    assert recovery._continuation_pending_for_recovery(grant, batch, "sha256:stale") is None
    assert recovery._continuation_pending_for_recovery({**grant, "authority": {"scope": "changed"}}, batch, report_digest) is None
    assert recovery._continuation_pending_for_recovery({**grant, "authorization_digest": "changed"}, batch, report_digest) is None
    changed = deepcopy(grant)
    changed["task_intents"][task["id"]] = "changed"
    assert recovery._continuation_pending_for_recovery(changed, batch, report_digest) is None
    assert recovery._continuation_pending_for_recovery(grant, {**batch, "expected_revisions": {proposals[0]: 2}}, report_digest) is None

    evidence = recovery.project_batch_recovery_evidence(workspace=tmp_path, project_key="DEMO", root=tmp_path, run_id=run_id)
    assert evidence["can_resume_continuation"] is True
    request = dict(action="retry_project_batch", confirmed=True, billing_acknowledged=True, idempotency_key="explicit-recovery",
        run_id=run_id, expected_recovery_version=evidence["version"], retry_task_ids=[task["id"]],
        resume_continuation=True, continuation_id=grant["id"])
    with pytest.raises(ValueError, match="charges"):
        recovery.recover_project_batch(workspace=tmp_path, project_key="DEMO", root=tmp_path,
                                       request={**request, "billing_acknowledged": False})
    assert not starts
    recovery.recover_project_batch(workspace=tmp_path, project_key="DEMO", root=tmp_path, request=request)
    assert state["project_continuation"]["status"] == "active"
    assert state["project_continuation"]["pending_batch"] == expected_pending
    assert state["project_continuation"]["batch_observations"] == grant["batch_observations"]
    assert len(starts) == 1 and ledger["tasks"][0]["status"] == "in_progress"
    state["project_continuation"]["status"] = "stopped"
    recovery.recover_project_batch(workspace=tmp_path, project_key="DEMO", root=tmp_path, request=request)
    assert len(starts) == 1 and state["project_continuation"]["status"] == "stopped"
    # Retry without explicit continuation consent leaves its released pointer alone.
    state.update(project_continuation=deepcopy(grant), project_task_batches={run_id: deepcopy(batch)})
    ledger["tasks"] = [deepcopy(task)]
    recovery.recover_project_batch(workspace=tmp_path, project_key="DEMO", root=tmp_path,
        request={**request, "idempotency_key": "retry-without-resume", "resume_continuation": False})
    assert state["project_continuation"]["status"] == "blocked"
    assert state["project_continuation"]["pending_batch"] is None
    assert len(starts) == 2
