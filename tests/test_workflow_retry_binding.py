"""Retry admission uses real persisted state/jobs and actual HTTP handlers."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from urllib.request import urlopen

import pytest

from goodteam_os import web
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.workflow_mutations import INTERRUPTED_TRANSITION, workflow_guard, WorkflowBusyError
from test_skill_review_http import server_url, post
from test_task_skill_entrypoints import ctx as ctx


def interrupted(ctx, run="run-A", status="interrupted"):
    state = create_initial_state(run, "DEMO", f"Task {run}")
    state.current_node, state.next_node = "release_review_gate", "blocked"
    state.approvals.update({key: ApprovalDecision.APPROVED for key in state.approvals})
    state.blockers = [INTERRUPTED_TRANSITION]
    state.pending_transition = {
        "id": f"{run}-transition", "status": status,
        "current_node": "release_review_gate", "next_node": "retro_memory",
        "action": {"run_id": run, "project_key": "DEMO", "actor": "human", "action": "approval_decided",
                   "gate": "release", "decision": "approved", "note": "Isolated model-free recovery fixture.",
                   "before_node": "await_release_approval", "after_node": "retro_memory"},
    }
    StateStore(ctx.workspace).write_project_current(state)
    return state


def body(ctx, state=None):
    state = state or StateStore(ctx.workspace).read_project_current("DEMO")
    target = web.workflow_retry_target(ctx.workspace, state)
    return {"project_key": "DEMO", "expected_run_id": state.run_id, "expected_retry_version": target["version"]}


def evidence(ctx):
    state_dir = StateStore(ctx.workspace).project_state_dir("DEMO")
    return {str(path): path.read_bytes() for root in (state_dir, ctx.workers.root)
            if root.exists() for path in root.rglob("*") if path.is_file()}


def job(ctx, *, identifier="attempt-1", status="failed", attempt="1", run="run-A"):
    root = ctx.workers.root / identifier
    root.mkdir(parents=True, exist_ok=True)
    value = {"id": identifier, "project_key": "DEMO", "run_id": run, "role": "implementation",
             "adapter": "fake", "status": status, "attempt": attempt, "updated_at": "2026-01-01T00:00:00Z"}
    (root / "job.json").write_text(json.dumps(value))
    return value


@pytest.mark.parametrize("path", ["/api/retry", "/retry"])
def test_real_http_stale_page_cannot_resume_replacement_task(ctx, monkeypatch, path):
    old = interrupted(ctx)
    request = body(ctx, old)
    interrupted(ctx, "run-B", "executing")
    before = evidence(ctx)
    with server_url(ctx, monkeypatch, "product") as base:
        code, result = post(base, path, request)
    assert code == 409 and result["code"] == "run_binding_mismatch", result
    assert evidence(ctx) == before  # No interrupt_transition side effect on B.


@pytest.mark.parametrize("path", ["/api/retry", "/retry"])
def test_real_http_bound_resume_then_duplicate_is_rejected(ctx, monkeypatch, path):
    interrupted(ctx)
    request = body(ctx)
    with server_url(ctx, monkeypatch, "product") as base:
        with urlopen(base + "/api/state?project=DEMO", timeout=5) as response:
            displayed = json.load(response)["retry_target"]
        assert displayed["schema_version"] == "workflow-retry-target.v1"
        assert displayed["available"] and displayed["mode"] == "resume_transition"
        request = {"project_key": displayed["project_key"], "expected_run_id": displayed["run_id"],
                   "expected_retry_version": displayed["version"]}
        code, result = post(base, path, request)
        assert code == 200 and result["retried"] is True and result["current_node"] == "complete", result
        after = evidence(ctx)
        code, result = post(base, path, request)
        assert code == 409 and result["code"] == "retry_target_changed", result
        assert evidence(ctx) == after
    starts = [item for item in web.build_action_items(ctx.workspace, "DEMO") if item["action"] == "blocked_workflow_retry_started"]
    assert len(starts) == 1
    assert ctx.workers.list_jobs() == []


@pytest.mark.parametrize("change", ["new_attempt", "same_job_attempt", "active_attempt", "stage", "config"])
def test_same_run_changed_attempt_or_stage_rejects_before_mutation(ctx, change):
    state = interrupted(ctx)
    job(ctx)
    request = body(ctx)
    if change == "new_attempt":
        job(ctx, identifier="attempt-2", attempt="2")
    elif change == "same_job_attempt":
        job(ctx, attempt="2")
    elif change == "active_attempt":
        job(ctx, identifier="attempt-2", attempt="2", status="running")
    elif change == "config":
        config, _jobs, _context = web._workflow_retry_context(ctx.workspace, state)
        config.write_text(config.read_text() + "\n# changed execution configuration\n")
    else:
        state.pending_transition["id"] = "different-stage-attempt"
        StateStore(ctx.workspace).write_project_current(state)
    before = evidence(ctx)
    with pytest.raises(web.ProjectApiError) as error:
        web.retry_blocked_workflow(workspace=ctx.workspace, **request)
    assert error.value.code == "retry_target_changed"
    assert evidence(ctx) == before


@pytest.mark.parametrize("status,code", [("running", "retry_attempt_active"), ("queued", "retry_attempt_active"),
                                         ("possibly_stuck", "retry_attempt_unknown"), ("mystery", "retry_attempt_unknown")])
def test_live_or_unknown_attempt_cannot_be_retried_even_with_fresh_binding(ctx, status, code):
    state = interrupted(ctx)
    job(ctx, status=status)
    target = web.workflow_retry_target(ctx.workspace, state)
    assert not target["available"]
    before = evidence(ctx)
    with pytest.raises(web.ProjectApiError) as error:
        web.retry_blocked_workflow(workspace=ctx.workspace, **body(ctx))
    assert error.value.code == code and evidence(ctx) == before


def test_other_task_jobs_do_not_change_retry_binding(ctx):
    state = interrupted(ctx)
    before = body(ctx)
    job(ctx, run="unrelated-run", status="running")
    assert web.workflow_retry_target(ctx.workspace, state)["available"]
    assert body(ctx) == before


@pytest.mark.parametrize("missing", ["expected_run_id", "expected_retry_version"])
def test_missing_binding_is_rejected_before_locks_and_writes(ctx, missing):
    interrupted(ctx)
    request = body(ctx)
    del request[missing]
    before = evidence(ctx)
    with pytest.raises(web.ProjectApiError) as error:
        web.retry_blocked_workflow(workspace=ctx.workspace, **request)
    assert error.value.code == "retry_binding_required" and evidence(ctx) == before
    assert not (ctx.workspace / "state/workflow-locks").exists()


def test_rejected_binding_never_invokes_transition_recovery(ctx, monkeypatch):
    state = interrupted(ctx, status="executing")
    request = body(ctx, state)
    request["expected_retry_version"] = "sha256:" + "0" * 64
    monkeypatch.setattr(web, "interrupt_transition", lambda *_: pytest.fail("rejection must not interrupt"))
    with pytest.raises(web.ProjectApiError, match="attempt changed"):
        web.retry_blocked_workflow(workspace=ctx.workspace, **request)


def test_failed_retry_consumes_snapshot_even_if_state_returns_unchanged(ctx, monkeypatch):
    state = interrupted(ctx)
    request = body(ctx, state)
    calls = []
    def fail(**_kwargs):
        calls.append(True)
        raise RuntimeError("controlled failure before transition execution")
    monkeypatch.setattr(web, "_retry_bound_workflow", fail)
    with pytest.raises(RuntimeError):
        web.retry_blocked_workflow(workspace=ctx.workspace, **request)
    assert StateStore(ctx.workspace).read_project_current("DEMO") == state
    after = evidence(ctx)
    with pytest.raises(web.ProjectApiError) as error:
        web.retry_blocked_workflow(workspace=ctx.workspace, **request)
    assert error.value.code == "retry_target_changed" and calls == [True] and evidence(ctx) == after


def test_retry_validation_and_execution_share_existing_project_guard(ctx, monkeypatch):
    interrupted(ctx)
    request = body(ctx)
    entered, release = Event(), Event()
    original = web._retry_bound_workflow
    def paused(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(web, "_retry_bound_workflow", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(web.retry_blocked_workflow, workspace=ctx.workspace, **request)
        try:
            assert entered.wait(5)
            with pytest.raises(WorkflowBusyError):
                web.retry_blocked_workflow(workspace=ctx.workspace, **request)
            with pytest.raises(WorkflowBusyError), workflow_guard(ctx.workspace, "DEMO"):
                pytest.fail("cannot replace the task during retry")
        finally:
            release.set()
        assert running.result(timeout=5)["current_node"] == "complete"


@pytest.mark.parametrize("path", ["/api/retry", "/retry"])
def test_real_http_missing_binding_cannot_dispatch(ctx, monkeypatch, path):
    interrupted(ctx)
    before = evidence(ctx)
    with server_url(ctx, monkeypatch, "product") as base:
        code, result = post(base, path, {"project_key": "DEMO"})
    assert code == 400 and result["code"] == "retry_binding_required"
    assert evidence(ctx) == before


def test_normalized_same_project_active_attempt_is_not_excluded(ctx):
    state = interrupted(ctx)
    item = job(ctx, status="running")
    item["project_key"] = "demo"
    (ctx.workers.root / item["id"] / "job.json").write_text(json.dumps(item))
    target = web.workflow_retry_target(ctx.workspace, state)
    assert not target["available"] and target["active_attempt_ids"] == [item["id"]]


@pytest.mark.parametrize("status", ["recording", "executing"])
def test_live_transition_never_advertises_a_second_retry(ctx, status):
    state = interrupted(ctx, status=status)
    target = web.workflow_retry_target(ctx.workspace, state)
    assert not target["available"] and target["reason"] == "active_attempt"
    before = evidence(ctx)
    with pytest.raises(web.ProjectApiError) as error:
        web.retry_blocked_workflow(workspace=ctx.workspace, **body(ctx))
    assert error.value.code == "retry_attempt_active" and evidence(ctx) == before


def test_recorded_zero_retry_count_is_preserved(ctx):
    state = interrupted(ctx)
    item = job(ctx)
    item["retry_attempt"] = 0
    (ctx.workers.root / item["id"] / "job.json").write_text(json.dumps(item))
    assert web.workflow_retry_target(ctx.workspace, state)["attempts"][0]["retry_attempt"] == "0"


def test_http_legacy_revision_display_hint_does_not_invalidate_persisted_binding(ctx, monkeypatch):
    state = create_initial_state("revision-run", "DEMO", "Apply the recorded revision")
    state.current_node = "release_review_gate"
    state.next_node = "revise_release"
    state.change_requests["release"] = "Regenerate the recorded release revision."
    state.blockers = []
    StateStore(ctx.workspace).write_project_current(state)
    with server_url(ctx, monkeypatch, "product") as base:
        with urlopen(base + "/api/state?project=DEMO", timeout=5) as response:
            displayed = json.load(response)
        assert displayed["state"]["blockers"] == ["Changes requested; retry to apply the recorded revision."]
        assert StateStore(ctx.workspace).read_project_current("DEMO").blockers == []
        target = displayed["retry_target"]
        assert target["available"] and target["mode"] == "retry_revision"
        code, result = post(base, "/api/retry", {"project_key": "DEMO", "expected_run_id": target["run_id"],
                                               "expected_retry_version": target["version"]})
        # The bound retry was accepted, but a fake implementation did not emit
        # the newly required owned release correction. Fail closed with a
        # retryable blocked result, not a stale-binding error or template success.
        assert code == 200, result
        assert result["next_node"] == "blocked"
        assert "Release correction is missing or stale" in str(result["blockers"])
        assert all(item["adapter"] == "fake" for item in ctx.workers.list_jobs())


def test_first_http_view_versions_configuration_after_existing_team_migration(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from goodteam_os.projects import write_project_config
    from goodteam_os.project_registry import ProjectRegistry

    workspace, repo = tmp_path / "workspace", tmp_path / "repo"
    repo.mkdir()
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(repo), worker_mode="fake")
    ProjectRegistry().register_project(config, root=repo, key="DEMO")
    fresh = SimpleNamespace(root=tmp_path, workspace=workspace)
    interrupted(fresh)
    before_config = config.read_bytes()
    with server_url(fresh, monkeypatch, "product") as base:
        with urlopen(base + "/api/state?project=DEMO", timeout=5) as response:
            displayed = json.load(response)
        assert config.read_bytes() != before_config  # Existing first-use migration really ran.
        target = displayed["retry_target"]
        assert target["version"] == body(fresh)["expected_retry_version"]
        code, result = post(base, "/api/retry", {"project_key": "DEMO", "expected_run_id": target["run_id"],
                                               "expected_retry_version": target["version"]})
        assert code == 200 and result["current_node"] == "complete", result
