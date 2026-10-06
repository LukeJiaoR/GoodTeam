"""Task-bound cancellation through real HTTP, without Workers or model calls."""
import json
from types import SimpleNamespace

import pytest

from goodteam_os import web
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_mutations import workflow_guard
from test_skill_review_http import post, server_url


def persisted_tasks(workspace):
    return {
        str(path.relative_to(workspace)): path.read_bytes()
        for path in (workspace / "state").rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }


def fixture_state(tmp_path, run_id="task-A", *, pending=False):
    ctx = SimpleNamespace(root=tmp_path, workspace=tmp_path / "workspace")
    state = create_initial_state(run_id, "DEMO", "Archive exactly this task")
    if pending:
        state.pending_transition = {"id": "owned-transition", "status": "executing"}
    StateStore(ctx.workspace).write_project_current(state)
    return ctx, state


@pytest.mark.parametrize("binding", ["missing", None, "", " ", 12, True, [], {}])
def test_http_requires_task_identity_before_cancellation(tmp_path, monkeypatch, binding):
    ctx, _state = fixture_state(tmp_path, pending=True)
    before = persisted_tasks(ctx.workspace)
    body = {"project_key": "DEMO"}
    if binding != "missing":
        body["expected_run_id"] = binding
    with server_url(ctx, monkeypatch, "product") as base:
        status, result = post(base, "/api/terminate", body)
    assert status == 400, result
    assert result["code"] == "run_binding_required"
    assert persisted_tasks(ctx.workspace) == before


@pytest.mark.parametrize("pending", [False, True])
def test_old_tab_cannot_cancel_or_interrupt_a_replacement_task(tmp_path, monkeypatch, pending):
    ctx, replacement = fixture_state(tmp_path, "task-B", pending=pending)
    before = persisted_tasks(ctx.workspace)
    with server_url(ctx, monkeypatch, "product") as base:
        status, result = post(base, "/api/terminate", {"project_key": "DEMO", "expected_run_id": "task-A"})
    assert status == 409, result
    assert result["code"] == "run_binding_mismatch"
    assert result["details"] == {"expected_run_id": "task-A", "current_run_id": "task-B"}
    assert StateStore(ctx.workspace).read_project_current("DEMO") == replacement
    assert persisted_tasks(ctx.workspace) == before


def test_http_archives_only_the_confirmed_task_and_rejects_replayed_cancel(tmp_path, monkeypatch):
    ctx, _state = fixture_state(tmp_path)
    other = create_initial_state("other-task", "OTHER", "Unrelated project")
    states = StateStore(ctx.workspace)
    states.write_project_current(other)
    body = {"project_key": "DEMO", "expected_run_id": "task-A"}
    with server_url(ctx, monkeypatch, "product") as base:
        status, result = post(base, "/api/terminate", body)
        assert status == 200 and result["run_id"] == "task-A", result
        assert states.read_project_current("DEMO") is None
        assert states.read_project_current("OTHER") == other
        before_replay = persisted_tasks(ctx.workspace)
        status, result = post(base, "/api/terminate", body)
    assert status == 409 and result["code"] == "run_binding_mismatch", result
    assert persisted_tasks(ctx.workspace) == before_replay
    ledger = ctx.workspace / "state" / "projects" / "DEMO" / "action-log.jsonl"
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [(event["action"], event["run_id"]) for event in events] == [("run_terminated", "task-A")]


def test_bound_cancel_uses_the_existing_project_workflow_lock(tmp_path, monkeypatch):
    ctx, _state = fixture_state(tmp_path)
    before = persisted_tasks(ctx.workspace)
    with server_url(ctx, monkeypatch, "product") as base, workflow_guard(ctx.workspace, "DEMO"):
        status, result = post(base, "/api/terminate", {"project_key": "DEMO", "expected_run_id": "task-A"})
    assert status == 400 and "busy" in result["message"], result
    assert persisted_tasks(ctx.workspace) == before


def test_trusted_python_caller_retains_explicit_project_cancellation_compatibility(tmp_path):
    ctx, _state = fixture_state(tmp_path)
    result = web.terminate_project_run(workspace=ctx.workspace, project_key="DEMO")
    assert result["terminated"] and result["run_id"] == "task-A"
