"""Ordinary HTTP Reviewer controls with startup-owned admission.

The real server, task/source/method locks, Worker, child and saved report are
used. Admission and Docker remain labelled model-free fixtures; no selector is
patched, no provider is called, and no live conformance is implied.
"""
import json
import urllib.request

import pytest

from goodteam_os import web
from goodteam_os.skills import codex_container
from goodteam_os.skills.models import content_digest
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from test_codex_review_dispatch import source_report
from test_codex_review_runtime import AdmissionFixture, ScriptedEngine
from test_skill_review_http import post, server_url
from test_skill_review_protocol import direct_request
from test_skill_review_results import SOURCE, report
from test_skill_review_sources import ready
from test_task_skill_entrypoints import bind, capture, method, ctx as ctx


def queued_review(ctx):
    item = method(ctx, "A user method for this bounded Web review.")
    bind(ctx, item, subject="role:code_review")
    context = capture(ctx, "web-review", subjects=("role:code_review",))
    candidate = ctx.root / "candidate.py"
    candidate.write_bytes(SOURCE)
    job = ctx.workers.create_job(**direct_request(context,
        artifact_key="rfc", artifact_path=str(candidate), artifact_version=content_digest(SOURCE),
        profile_id="correctness-security", phase="implementation", approval="security",
        dispatch_group_id="web-review-group"))
    state = create_initial_state("web-review", "DEMO", "Review the exact candidate")
    state.task_skill_context = context
    state.current_node, state.next_node = "security_review_gate", "await_security_approval"
    state.artifacts["rfc"] = str(candidate)
    StateStore(ctx.workspace).write_project_current(state)
    return job, context, item


def get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)


def test_product_http_runs_queued_review_with_startup_authority_without_approval(ctx, monkeypatch):
    job, _context, item = queued_review(ctx)
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    request = {"project_key": "DEMO", "run_id": "web-review", "approval": "security"}
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        assert admission.acquires == [], "Server construction does not grant or acquire a route"
        code, result = post(base, "/api/reviewer-jobs/run", request, origin="https://untrusted.example")
        assert code == 403, result
        code, result = post(base, "/api/reviewer-jobs/run", {**request, "run_id": "other-task"})
        assert code == 409, result
        assert admission.acquires == []
        code, result = post(base, "/api/reviewer-jobs/run", request)
        assert code == 200 and result["ran_count"] == 1, result
        assert result["results"][0]["status"] == "review_received"
        assert result["advisory_only"] and result["approval_unchanged"]
        saved = get_json(base, f"/api/projects/DEMO/runs/web-review/skill-reviews/{job['id']}/report")
        assert saved["report_available"] and saved["report"]["advisory"]
        assert saved["runtime_verification"] == "contract_fixture"
        assert saved["methods"][0]["version_id"] == item["version_id"]
        code, replay = post(base, "/api/reviewer-jobs/run", request)
        assert code == 200 and replay["ran_count"] == 0, replay
        assert get_json(base, f"/api/projects/DEMO/runs/web-review/skill-reviews/{job['id']}/report") == saved
    assert engine.attaches == 1 and engine.removed
    assert len(admission.acquires) == 1 and admission.finishes == [(admission.acquires[0], True)]
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before


def test_request_json_cannot_register_admission_in_an_ordinary_server(ctx, monkeypatch):
    job, *_ = queued_review(ctx)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No startup authority was supplied"))
    with server_url(ctx, monkeypatch, "product") as base:
        code, result = post(base, "/api/reviewer-jobs/run", {
            "project_key": "DEMO", "run_id": "web-review", "approval": "security",
            "codex_review_admission": {"verified": True}, "provider_url": "http://unapproved/v1",
        })
        assert code == 200 and result["ran_count"] == 1, result
        assert result["results"][0]["status"] == "failed"
    status = next(item for item in ctx.workers.list_jobs() if item["id"] == job["id"])
    assert status["skill_error_code"] == "skill_runtime_not_connected"
    assert status["invocation_evidence"]["external_invocation"] is False


@pytest.mark.parametrize("handler", ["contract", "product"])
def test_http_retry_uses_startup_authority_and_keeps_exact_original_inputs(ctx, monkeypatch, handler):
    job, context, selected, original = ready(ctx)
    state = create_initial_state("git-review", "DEMO", "Review this exact change")
    state.task_skill_context = context
    StateStore(ctx.workspace).write_project_current(state)
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    admission = AdmissionFixture()
    engines = []

    def make_engine():
        output = report(summary="") if not engines else source_report()
        engine = ScriptedEngine(admission.selected, output_report=output)
        engines.append(engine)
        return engine

    monkeypatch.setattr(codex_container, "DockerEngine", make_engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    assert workers.run_job(job["id"])["status"] == "failed"
    first = ReviewResultStore(workers, job["id"]).detail()
    assert first["status"] == "invalid" and first["retry_available"]
    bind(ctx, method(ctx, "NEW_METHOD_NOT_FOR_THIS_RETRY", key="new-method"),
         subject="role:code_review", key="new-binding")
    (ctx.repo / "a.py").write_text("New worktree bytes must not replace the original source")
    (ctx.repo / ".git").rename(ctx.repo / "removed-git")
    path = f"/api/projects/DEMO/runs/git-review/skill-reviews/{job['id']}/retry"
    body = {"snapshot_digest": first["snapshot_ref"]["digest"], "context_digest": first["context_ref"]["digest"]}
    with server_url(ctx, monkeypatch, handler, codex_review_admission=admission) as base:
        code, injected = post(base, path, {**body, "codex_review_admission": {"verified": True}})
        assert code == 409, injected
        assert len(admission.acquires) == 1
        code, result = post(base, path, body)
        assert code == 200 and result["status"] == "review_received", result
        assert result["created"] and result["approval_changed"] is False
        saved = get_json(base, path.rsplit("/", 2)[0] + "/" + result["job_id"] + "/report")
        assert saved["methods"] == first["methods"]
        assert saved["methods"][0]["version_id"] == selected["version_id"]
        assert saved["target"]["head_sha"] == original["review_target"]["head_sha"]
        assert saved["context_ref"] != first["context_ref"]
        assert saved["runtime"]["session_id"] != first["runtime"]["session_id"]
        assert saved["runtime_verification"] == "contract_fixture"
        code, replay = post(base, path, body)
        assert code == 200 and replay["job_id"] == result["job_id"] and not replay["created"], replay
        assert StateStore(ctx.workspace).read_project_current("DEMO") == before
        StateStore(ctx.workspace).write_project_current(create_initial_state("later-task", "DEMO", "Later work"))
        assert post(base, path, body)[0] == 409
    assert len(admission.acquires) == 2 and len(set(admission.acquires)) == 2
    assert len(engines) == 2 and all(engine.attaches == 1 and engine.removed for engine in engines)


def test_product_http_queues_retry_then_runs_original_locked_review(ctx, monkeypatch):
    job, _context, selected = queued_review(ctx)
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    assert ctx.workers.run_job(job["id"])["status"] == "failed"  # Default runtime remains blocked.
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    bind(ctx, method(ctx, "New binding must not change queued retries", key="new"),
         subject="role:code_review", key="new-bind")
    request = {"project_key": "DEMO", "run_id": "web-review", "approval": "security"}
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        code, queued = post(base, "/api/reviewer-jobs/retry", request)
        assert code == 200 and queued["retried_count"] == 1, queued
        retry_id = queued["results"][0]["job_id"]
        assert queued["results"][0]["status"] == "queued"
        assert admission.acquires == []
        code, replay = post(base, "/api/reviewer-jobs/retry", request)
        assert code == 200 and replay["retried_count"] == 0, replay
        code, run = post(base, "/api/reviewer-jobs/run", request)
        assert code == 200 and run["ran_count"] == 1, run
        assert run["results"][0]["status"] == "review_received"
        saved = get_json(base, f"/api/projects/DEMO/runs/web-review/skill-reviews/{retry_id}/report")
        assert saved["methods"][0]["version_id"] == selected["version_id"]
        assert saved["runtime_verification"] == "contract_fixture" and saved["report_available"]
    assert len(admission.acquires) == 1 and engine.attaches == 1 and engine.removed
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before


@pytest.mark.parametrize("startup", ["serve", "handler", "cli"])
@pytest.mark.parametrize("invalid", [{"verified": True}, "provider-module:factory", True])
def test_invalid_startup_authority_is_rejected_before_server_creation(tmp_path, monkeypatch, startup, invalid):
    monkeypatch.setattr(web, "ThreadingHTTPServer", lambda *_: pytest.fail("Invalid authority must fail before listening"))
    with pytest.raises(ValueError, match="control-plane admission authority"):
        if startup == "handler":
            web.build_dashboard_handler(tmp_path, codex_review_admission=invalid)
        elif startup == "serve":
            web.serve(tmp_path, codex_review_admission=invalid)
        else:
            from goodteam_os.cli import main
            main(["serve", "--workspace", str(tmp_path)], codex_review_admission=invalid)
    assert list(tmp_path.iterdir()) == []


def test_cli_serve_passes_the_dependency_without_acquisition_or_global_registration(tmp_path, monkeypatch):
    from goodteam_os import cli

    calls = []
    monkeypatch.setattr(cli, "serve", lambda **kwargs: calls.append(kwargs))
    admission = AdmissionFixture()
    argv = ["serve", "--workspace", str(tmp_path)]
    assert cli.main(argv, codex_review_admission=admission) == 0
    assert calls[0]["codex_review_admission"] is admission
    assert cli.main(argv) == 0
    assert "codex_review_admission" not in calls[1]
    assert admission.acquires == []
    with pytest.raises(ValueError, match="direct dispatch and serve"):
        cli.main(["status"], codex_review_admission=admission)
