"""Continuation contention is deferred before its billable reservation."""
from copy import deepcopy

import pytest

from goodteam_os import manager_execution as manager
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_busy_continuation_defers_then_uses_one_slot_without_refunding_unknown_calls(tmp_path, monkeypatch):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    grant = dict(id="grant", status="active", authority="authority", task_ids=["task"],
                 manager_calls=0, max_manager_calls=2, batches_started=0, max_batches=2,
                 pending_batch=None)
    row = dict(id="task", status="todo")
    snapshot = dict(version="sha256:" + "a" * 64, candidates=[dict(task_id="task", eligible_to_plan=True)])
    monkeypatch.setattr(continuation, "authority_snapshot", lambda *a: "authority")
    monkeypatch.setattr(continuation, "require_no_active_project_goal", lambda *a: None)
    monkeypatch.setattr(continuation, "active_project_run", lambda *a: False)
    monkeypatch.setattr(continuation, "_scope_tasks", lambda *a: ({"task": row}, [row]))
    monkeypatch.setattr(continuation, "project_iteration_backlog", lambda *a, **k: snapshot)
    calls = []
    mode = ["success"]
    def plan(**kwargs):
        if mode[0] == "stop_before_reservation":
            store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
        kwargs["reserve_planning"]()
        assert store.read()["project_continuation"]["manager_calls"] == 1
        # The production runner can reuse ONLY the current active same-thread
        # lease; a second unrelated caller still cannot take the project slot.
        with manager._manager_turn_lock(tmp_path, "planning", resource_slot=kwargs["resource_slot"]):
            with pytest.raises(manager.ManagerResourceBusy):
                with manager.manager_resource_slot(tmp_path):
                    pass
            calls.append("entered")
            if mode[0] == "unknown":
                raise manager.ManagerExecutionError("unknown provider outcome", code="manager_resource_busy")
        return {"iteration_choice": {"selected_task_ids": ["task"]}}
    monkeypatch.setattr(continuation, "plan_next_iteration", plan)
    proposal = dict(id="p", revision=1, project_task_id="task")
    def queue(**kwargs):
        store._mutate(lambda s: {**s, "queued_task_proposals": [proposal]})
        return dict(queued_task_ids=["task"], queued_proposal_ids=["p"], task_assistant_session=store.read())
    monkeypatch.setattr(continuation, "queue_manager_iteration_choice", queue)
    def reset():
        store._mutate(lambda s: {**s, "project_continuation": deepcopy(grant)})
        calls.clear()
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=tmp_path,
            project_config=tmp_path / "project.yaml", batch_runner=lambda **kw: {"run_id": "batch"})
    reset()
    # Exercise the actual planner's conversation-lock entry, not the model seam:
    # chat preparation can own this lock without owning the Manager slot yet.
    from goodteam_os.assistant_requests import conversation_lock
    from goodteam_os.project_iteration_planner import plan_next_iteration
    with monkeypatch.context() as real_planner:
        real_planner.setattr(continuation, "plan_next_iteration", plan_next_iteration)
        with conversation_lock(store):
            deferred = tick()
    assert deferred["status"] == "active" and deferred["manager_calls"] == 0
    assert not deferred.get("planning_backlog_version") and not calls
    reset()
    with manager.manager_resource_slot(tmp_path):
        deferred = tick()
    assert deferred["status"] == "active" and deferred["reason"] == "manager_resource_busy"
    assert deferred["manager_calls"] == 0 and not deferred.get("planning_backlog_version") and not calls
    assert tick()["status"] == "active" and calls == ["entered"]
    assert store.read()["project_continuation"]["manager_calls"] == 1
    reset()
    mode[0] = "unknown"
    blocked = tick()
    assert blocked["status"] == "blocked" and blocked["manager_calls"] == 1
    tick()
    assert calls == ["entered"]  # unknown invocation cannot be retried/refunded
    for existing in (False, True):
        reset()
        mode[0] = "stop_before_reservation"
        if existing:
            store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
                "planning_backlog_version": snapshot["version"], "planning_input_version": 2, "manager_calls": 1}})
        stopped = tick()
        assert stopped["status"] == "stopped" and stopped["manager_calls"] == int(existing) and not calls
    for status, count, expected in [("stopped", 0, "stopped"), ("active", 2, "exhausted")]:
        reset()
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"],
            "status": status, "manager_calls": count}})
        assert tick()["status"] == expected and not calls


def test_resource_lease_is_only_reusable_while_active_on_its_owner(tmp_path):
    import threading
    with manager.manager_resource_slot(tmp_path) as lease:
        with manager._manager_turn_lock(tmp_path, "same", resource_slot=lease):
            pass
        with pytest.raises(manager.ManagerExecutionError):
            with manager._manager_turn_lock(tmp_path / "other", "other", resource_slot=lease):
                pass
        rejected = []
        def other_thread():
            try:
                with manager._manager_turn_lock(tmp_path, "thread", resource_slot=lease):
                    rejected.append(False)
            except manager.ManagerExecutionError:
                rejected.append(True)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=5)
        assert rejected == [True]
    with pytest.raises(manager.ManagerExecutionError):
        with manager._manager_turn_lock(tmp_path, "expired", resource_slot=lease):
            pass
