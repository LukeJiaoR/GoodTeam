"""Current-run exclusion over real HTTP; admission/engine are model-free fixtures."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from goodteam_os import web
from goodteam_os.skills import codex_container
from goodteam_os.state import StateStore
from test_codex_review_runtime import AdmissionFixture, ScriptedEngine
from test_codex_review_web import queued_review
from test_skill_review_http import post, server_url
from test_task_skill_entrypoints import ctx as ctx


@pytest.fixture(params=["run", "retry"])
def action(ctx, request):
    job, *_ = queued_review(ctx)
    if request.param == "retry":
        assert ctx.workers.run_job(job["id"])["status"] == "failed"
    return (
        "/api/reviewer-jobs/" + request.param,
        {"project_key": "DEMO", "run_id": "web-review", "approval": "security"},
        "ran_count" if request.param == "run" else "retried_count",
    )


def start_next(base, ctx):
    return post(base, "/api/start", {
        "project_key": "DEMO", "idea": "The next explicitly requested task",
        "project_config": str(ctx.config),
    })


def assert_busy(response):
    code, payload = response
    assert code in {400, 409} and "busy" in payload.get("message", payload.get("error", "")), response


def test_http_selection_keeps_run_current_until_review_or_retry_finishes(ctx, monkeypatch, action):
    path, body, count = action
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    selected, release = Event(), Event()
    roots = web._project_runtime_roots_for_state

    def paused(**kwargs):
        selected.set()  # The active run and approval gate have already been read.
        assert release.wait(timeout=10)
        return roots(**kwargs)

    monkeypatch.setattr(web, "_project_runtime_roots_for_state", paused)
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        with ThreadPoolExecutor(max_workers=1) as pool:
            reviewing = pool.submit(post, base, path, body)
            try:
                assert selected.wait(timeout=5)
                termination = post(base, "/api/terminate", {"project_key": "DEMO"})
                replacement = start_next(base, ctx)
                assert_busy(termination)
                assert_busy(replacement)
                assert StateStore(ctx.workspace).read_project_current("DEMO") == before
                assert admission.acquires == []
            finally:
                release.set()
            code, result = reviewing.result(timeout=10)
        assert code == 200 and result[count] == 1, result
        assert len(admission.acquires) == (1 if count == "ran_count" else 0)
        assert StateStore(ctx.workspace).read_project_current("DEMO") == before
        # The guard must release on completion; both mutation and a later run work.
        assert post(base, "/api/terminate", {"project_key": "DEMO"})[0] == 200
        code, replacement = start_next(base, ctx)
        assert code == 200 and replacement["run_id"] != before.run_id, replacement
        jobs = ctx.workers.list_jobs()
        acquires = list(admission.acquires)
        code, stale = post(base, path, body)
        assert code == 409 and "run_id" in stale["error"], stale
        assert ctx.workers.list_jobs() == jobs and admission.acquires == acquires
        assert StateStore(ctx.workspace).read_project_current("DEMO").run_id == replacement["run_id"]


def test_http_termination_wins_before_old_review_acquisition_or_retry_creation(ctx, monkeypatch, action):
    path, body, _count = action
    jobs = ctx.workers.list_jobs()
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    terminating, release = Event(), Event()
    archive = StateStore.archive_project_current

    def paused(store, key, **kwargs):
        terminating.set()  # Termination owns the guard, but the old state is still readable.
        assert release.wait(timeout=10)
        return archive(store, key, **kwargs)

    monkeypatch.setattr(StateStore, "archive_project_current", paused)
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        with ThreadPoolExecutor(max_workers=1) as pool:
            termination = pool.submit(post, base, "/api/terminate", {"project_key": "DEMO"})
            try:
                assert terminating.wait(timeout=5)
                code, refused = post(base, path, body)
                assert code == 409 and "busy" in refused["error"], refused
                assert admission.acquires == [] and ctx.workers.list_jobs() == jobs
            finally:
                release.set()
            assert termination.result(timeout=10)[0] == 200
        assert post(base, path, body)[0] == 404  # No current run remains.
        code, replacement = start_next(base, ctx)
        assert code == 200 and replacement["run_id"] != body["run_id"], replacement
        replacement_jobs = ctx.workers.list_jobs()
        assert [item for item in replacement_jobs if item["run_id"] == body["run_id"]] == jobs
        assert post(base, path, body)[0] == 409
        assert admission.acquires == [] and ctx.workers.list_jobs() == replacement_jobs
        assert engine.operations == []


def test_http_run_holds_current_run_guard_through_admission(ctx, monkeypatch):
    job, *_ = queued_review(ctx)
    acquiring, release = Event(), Event()

    class PausedAdmission(AdmissionFixture):
        def acquire(self, snapshot, *, context_id):
            lease = super().acquire(snapshot, context_id=context_id)
            acquiring.set()
            assert release.wait(timeout=10)
            return lease

    admission = PausedAdmission()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        with ThreadPoolExecutor(max_workers=1) as pool:
            reviewing = pool.submit(post, base, "/api/reviewer-jobs/run", {
                "project_key": "DEMO", "run_id": "web-review", "approval": "security",
            })
            try:
                assert acquiring.wait(timeout=5)
                assert_busy(post(base, "/api/terminate", {"project_key": "DEMO"}))
                assert_busy(start_next(base, ctx))
            finally:
                release.set()
            code, result = reviewing.result(timeout=10)
        assert code == 200 and result["results"][0]["status"] == "review_received", result
        assert result["results"][0]["job_id"] == job["id"]
        assert admission.finishes == [(admission.acquires[0], True)] and engine.removed
        assert post(base, "/api/terminate", {"project_key": "DEMO"})[0] == 200


def test_http_review_recovers_abandoned_transition_without_reentering_guard(ctx, monkeypatch, action):
    path, body, _count = action
    states = StateStore(ctx.workspace)
    state = states.read_project_current("DEMO")
    state.pending_transition = {"id": "abandoned", "status": "executing"}
    states.write_project_current(state)
    jobs = ctx.workers.list_jobs()
    admission = AdmissionFixture()
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("An interrupted workflow is not actionable"))
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        code, refused = post(base, path, body)
        assert code == 409 and "not currently actionable" in refused["error"], refused
        assert states.read_project_current("DEMO").pending_transition["status"] == "interrupted"
        assert admission.acquires == [] and ctx.workers.list_jobs() == jobs
        assert post(base, "/api/terminate", {"project_key": "DEMO"})[0] == 200
