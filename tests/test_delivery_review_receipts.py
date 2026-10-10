"""Real protected reports plus model-free child execution, never model acceptance."""
import pytest

from goodteam_os.delivery_outcomes import build_delivery_summary
from goodteam_os.skills import review_execution
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore
from test_skill_review_execution import ProcessFixture, ready_job
from test_task_skill_entrypoints import ctx as ctx


@pytest.fixture
def received(ctx, monkeypatch):
    job, _context, _method, source = ready_job(ctx)
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    assert ctx.workers.run_job(job["id"])["status"] == "review_received"
    detail = ReviewResultStore(ctx.workers, job["id"]).detail()
    assert detail["status"] == "received" and detail["report_available"] is True
    assert detail["runtime_verification"] == "contract_fixture"
    return job, source, runtime


@pytest.mark.parametrize("next_node,expected", [
    ("product_requirement", "working"), ("await_code_review_approval", "awaiting_review"),
    ("", "needs_verification"),
])
def test_received_advisory_is_not_failure_or_verified_delivery(ctx, received, next_node, expected):
    _job, _source, runtime = received
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    state.next_node = next_node
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    summary = build_delivery_summary(ctx.workspace, state=state, jobs=ctx.workers.list_jobs(), worker_root=ctx.repo)
    assert summary["status"] == expected
    assert summary["verification"] == "unverified"
    assert summary["attempts"][0]["status"] == "review_received"
    assert runtime.executions == 1
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before


def test_received_projection_survives_store_reconstruction_without_new_execution(ctx, received):
    _job, _source, runtime = received
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    first = build_delivery_summary(ctx.workspace, state=state, jobs=ctx.workers.list_jobs(), worker_root=ctx.repo)
    restored = WorkerJobStore(ctx.repo)
    second = build_delivery_summary(ctx.workspace, state=StateStore(ctx.workspace).read_project_current("DEMO"),
                                    jobs=restored.list_jobs(), worker_root=ctx.repo)
    assert first == second and second["status"] == "working"
    assert runtime.executions == 1


@pytest.mark.parametrize("other_status,expected", [("failed", "blocked"), ("cancelled", "stopped"), ("unknown", "blocked")])
def test_received_report_cannot_hide_another_attempt(ctx, received, other_status, expected):
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    jobs = ctx.workers.list_jobs()
    jobs.append({"id": "independent-attempt", "run_id": state.run_id, "role": "code_review", "status": other_status})
    summary = build_delivery_summary(ctx.workspace, state=state, jobs=jobs, worker_root=ctx.repo)
    assert summary["status"] == expected
    assert summary["verification"] == "unverified" and len(summary["attempts"]) == 2


@pytest.mark.parametrize("change", ["source", "unregistered_id", "foreign_root", "missing_root", "state_blocker"])
def test_unavailable_or_foreign_receipt_never_clears_failure(ctx, received, change):
    _job, source, runtime = received
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    jobs = ctx.workers.list_jobs()
    root = ctx.repo
    if change == "source":
        source.write_text("The reviewed bytes changed.\n")
    elif change == "unregistered_id":
        jobs[0]["id"] = "job-skill-" + "f" * 64
    elif change == "foreign_root":
        root = ctx.root / "other-repo"
        root.mkdir()
    elif change == "missing_root":
        root = None
    else:
        state.blockers.append("Independent unresolved blocker")
    summary = build_delivery_summary(ctx.workspace, state=state, jobs=jobs, worker_root=root)
    assert summary["status"] == "blocked" and summary["verification"] == "unverified"
    assert runtime.executions == 1


def test_worker_received_flag_cannot_replace_protected_invalid_result(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, output=b"invalid review output\n")
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    assert ctx.workers.run_job(job["id"])["status"] == "failed"
    jobs = ctx.workers.list_jobs()
    jobs[0]["status"] = "review_received"
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    summary = build_delivery_summary(ctx.workspace, state=state, jobs=jobs, worker_root=ctx.repo)
    assert summary["status"] == "blocked"
    assert summary["verification"] == "unverified"
    assert runtime.executions == 1


@pytest.mark.parametrize("handler", ["contract", "product"])
def test_http_current_and_archived_delivery_use_protected_receipt(ctx, received, monkeypatch, handler):
    import json
    import urllib.request

    from goodteam_os.state import create_initial_state
    from test_skill_review_http import server_url

    _job, _source, runtime = received
    states = StateStore(ctx.workspace)
    before = states.read_project_current("DEMO")

    def get(base, path):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            assert response.status == 200
            return json.loads(response.read())

    def current_path(run_id):
        # The narrow contract handler exposes project routes; /api/state belongs
        # to the full product server. Exercise both supported entrypoints.
        return "/api/state?project=DEMO" if handler == "product" else f"/api/projects/DEMO/runs/{run_id}"

    with server_url(ctx, monkeypatch, handler) as base:
        current = get(base, current_path(before.run_id))["delivery_summary"]
        assert current["status"] == "working" and current["verification"] == "unverified"
        assert states.read_project_current("DEMO") == before
        states.archive_project_current("DEMO", reason="cancelled")
        states.write_project_current(create_initial_state("next-run", "DEMO", "Separate current task"))
        active_before = states.read_project_current("DEMO")
        historical = get(base, "/api/projects/DEMO/runs/review-worker")["delivery_summary"]
        assert historical["run_id"] == before.run_id
        assert historical["status"] == "stopped" and historical["verification"] == "unverified"
        assert get(base, current_path("next-run"))["delivery_summary"]["attempts"] == []
        assert states.read_project_current("DEMO") == active_before
        assert runtime.executions == 1
