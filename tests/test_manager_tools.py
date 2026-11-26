"""[INPUT]: Manager 工具、任务账与项目会话。
[OUTPUT]: 委派校验、重放与控制额度回归。
[POS]: 验证原始任务与验收标准进入既有队列。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from goodteam_os.assistant_requests import update_request
from goodteam_os.manager_tools import ManagerTools
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.tasks import LocalTaskStore


def _tools(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    ensure_team_state(tmp_path, "DEMO", config)
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("request", "user", "Research the repository and cite the current implementation.")
    update_request(store, "request", attempt=1, status="running", phase="model")
    runtime = SimpleNamespace(store=store, workspace=tmp_path, root=root, key="DEMO", config=config)
    return ManagerTools(runtime, logical_id="request", background=False), store, root


def test_concurrent_tool_delivery_reuses_one_original_assignment(tmp_path):
    tools, store, root = _tools(tmp_path)
    criterion = "Verify each source reference against the retained project files. " * 70 + "Record omissions."
    args = {"operation_id": "research", "role": "research", "source_message_ids": ["request"],
            "execution_policy": "read_only", "write_domains": [], "acceptance_criteria": [criterion],
            "delegation": {"purpose": "Understand the project", "context": "Current repository", "scope": "Read only"}}
    with pytest.raises(ValueError, match="Provide 1 to 8 nonempty acceptance criteria"):
        tools.call("goodteam_delegate", {**args, "acceptance_criteria": ["Verify one item."] * 9},
                   call_id="invalid-criteria", logical_ids=["request"])
    assert not LocalTaskStore(root, "DEMO").list_tasks()
    assert not store.read().get("manager_app_server", {}).get("tool_receipts")
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(tools.call, "goodteam_delegate", args, call_id=str(index), logical_ids=["request"])
                 for index in range(2)]
        results = [call.result(timeout=5) for call in calls]
    assert results[0] == results[1]
    project = tools.call("goodteam_project", {}, call_id="inspect", logical_ids=["request"])
    assert project["backlog"]["candidates"][0]["acceptance_criteria"] == [criterion]
    assert project["queue"][0]["assignment"]["acceptance_criteria"] == [criterion]
    assert len(LocalTaskStore(root, "DEMO").list_tasks()) == len(store.read()["queued_task_proposals"]) == 1
    # Recovery returns the original queue's latest fact, not a synthetic new pending task.
    receipt_key = next(iter(store.read()["manager_app_server"]["tool_receipts"]))
    def after_confirmation(session):
        proposal = session["queued_task_proposals"][0]
        proposal.update(confirmed=True, run_id="original-batch", status="running")
        session["manager_app_server"]["tool_receipts"][receipt_key]["status"] = "started"
        return session
    store._mutate(after_confirmation)
    recovered = tools.call("goodteam_delegate", args, call_id="recovery", logical_ids=["request"])
    assert recovered["run_id"] == "original-batch" and recovered["confirmed"]
    update_request(store, "request", stop_requested=True)
    with pytest.raises(ValueError, match="stopped"):
        tools.call("goodteam_delegate", {**args, "operation_id": "after-stop"}, call_id="late", logical_ids=["request"])
    assert len(LocalTaskStore(root, "DEMO").list_tasks()) == 1


def test_concurrent_controls_share_original_followup_allowance(tmp_path, monkeypatch):
    from goodteam_os import task_assistant, manager_worker_control

    from goodteam_os.workers import WorkerJobStore
    tools, store, _root = _tools(tmp_path)
    rows = [{"job_id": name, "batch_run_id": "batch", "run_id": "dispatch", "adapter": "pi"}
            for name in ("job-one", "job-two")]
    monkeypatch.setattr(task_assistant, "worker_invocation_context", lambda *_a, **_k: {"current": rows})
    monkeypatch.setattr(manager_worker_control, "_target", lambda *_a, **_k: rows[0])
    sent = []
    def enqueue(_self, job_id, action, **kwargs):
        assert kwargs["actor"] == "manager" and kwargs["wait_for_ack"] is False
        sent.append(job_id)
        return {"control_state": "queued", "accepted": False}
    monkeypatch.setattr(WorkerJobStore, "session_control", enqueue)
    store._mutate(lambda session: {**session,
        "project_continuation": {"id": "grant", "status": "active", "discovery": {"worker_follow_up_limit": 1}},
        "project_task_batches": {"batch": {"expected_continuation_id": "grant"}}})
    with pytest.raises(ValueError, match="declared operation"):
        tools.call("goodteam_worker_control", {"operation_id": "outside-schema", "job_id": "job-one",
            "action": "steer", "message": "This must not bypass the follow-up allowance."},
            call_id="outside-schema", logical_ids=["request"])
    assert not sent and not store.read()["project_continuation"].get("manager_native_controls")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(tools.call, "goodteam_worker_control",
            {"operation_id": row["job_id"], "job_id": row["job_id"], "action": "follow_up", "message": "Report current progress."},
            call_id=row["job_id"], logical_ids=["request"]) for row in rows]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=5))
            except ValueError as exc:
                outcomes.append(str(exc))
    assert len(sent) == 1
    assert len(store.read()["project_continuation"]["manager_native_controls"]) == 1
    assert any(isinstance(item, str) and "allowance" in item for item in outcomes)
