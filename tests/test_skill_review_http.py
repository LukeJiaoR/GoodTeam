"""Real HTTP admission for locked retries; only the model process is a fixture."""
from contextlib import contextmanager
import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from goodteam_os import web
from goodteam_os.skills import review_execution
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore, create_initial_state
from test_skill_review_sources import GitFixture, ready
from test_task_skill_entrypoints import bind, method, ctx as ctx


@contextmanager
def server_url(ctx, monkeypatch, handler):
    if handler == "contract":
        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(ctx.workspace))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
    else:
        servers = queue.Queue()
        server_type = web.ThreadingHTTPServer

        class CapturingServer(server_type):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                servers.put(self)

        dist = ctx.root / "http-dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>Isolated fixture</title>")
        monkeypatch.setattr(web, "ThreadingHTTPServer", CapturingServer)
        thread = threading.Thread(target=web.serve, kwargs={
            "workspace": ctx.workspace, "host": "127.0.0.1", "port": 0, "frontend_dist": dist,
        }, daemon=True)
        thread.start()
        server = servers.get(timeout=5)
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "Owned HTTP fixture must terminate"


def post(base, path, body, *, origin=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=raw, method="POST", headers={
        "Content-Type": "application/json", "Origin": base if origin is None else origin,
    })
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        # Keep real HTTP 404 HTML observable rather than masking it with a JSON error.
        payload = json.loads(raw) if response.headers.get_content_type() == "application/json" else raw.decode()
        return response.status, payload


@pytest.mark.parametrize("handler", ["contract", "product"])
def test_actual_http_retry_preserves_scope_checks_source_and_new_context(ctx, monkeypatch, handler):
    job, context, selected, _request = ready(ctx)
    state = create_initial_state("git-review", "DEMO", "Review an exact change")
    state.task_skill_context = context
    states = StateStore(ctx.workspace)
    states.write_project_current(state)
    before = states.read_project_current("DEMO")
    runtime = GitFixture(ctx.root, malformed=True)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    assert ctx.workers.run_job(job["id"])["status"] == "failed"
    first = ReviewResultStore(ctx.workers, job["id"]).detail()
    new_method = method(ctx, "NEW_METHOD_NOT_FOR_RETRY", key="http-new-method")
    bind(ctx, new_method, subject="role:code_review", key="http-role-edit")
    (ctx.repo / "a.py").write_text("NEW WORKTREE MUST NOT REPLACE THE REVIEWED SOURCE")
    (ctx.repo / ".git").rename(ctx.repo / "removed-git")
    runtime.malformed = False
    path = "/api/projects/DEMO/runs/git-review/skill-reviews/" + job["id"] + "/retry"
    body = {"snapshot_digest": first["snapshot_ref"]["digest"], "context_digest": first["context_ref"]["digest"]}
    with server_url(ctx, monkeypatch, handler) as base:
        code, result = post(base, path, body, origin="https://untrusted.example")
        assert code == 403, result
        assert result["code"] == "same_origin_required"
        for malformed in (b"{", b"[]"):
            code, result = post(base, path, malformed)
            assert code == 400, result
            assert result["code"] == "invalid_request"
        code, result = post(base, path, {**body, "model": "unapproved"})
        assert code == 409, result
        code, result = post(base, path, {**body, "context_digest": "sha256:" + "f" * 64})
        assert code == 409, result
        assert len(runtime.records) == 1
        assert len(ctx.workers.list_jobs()) == 1
        code, result = post(base, path, body)
        assert code == 200, result
        assert result["created"] is True
        assert result["status"] == "review_received"
        assert result["job_id"] != job["id"]
        retry = ReviewResultStore(ctx.workers, result["job_id"]).detail(include_report=True)
        assert retry["methods"] == first["methods"]
        assert retry["methods"][0]["version_id"] == selected["version_id"]
        assert retry["target"] == first["target"]
        assert retry["context_ref"] != first["context_ref"]
        assert retry["runtime"]["session_id"] != first["runtime"]["session_id"]
        assert b"METHOD_LOCKED_V1" in runtime.prompts[-1]
        assert b"NEW_METHOD_NOT_FOR_RETRY" not in runtime.prompts[-1]
        code, replay = post(base, path, body)
        assert code == 200 and replay["job_id"] == result["job_id"], replay
        assert replay["created"] is False
        assert len(runtime.records) == 2
        assert states.read_project_current("DEMO") == before
        # Changing selected task cannot turn this route into a historical mutation.
        states.write_project_current(create_initial_state("later-task", "DEMO", "New work"))
        code, historical = post(base, path, body)
        assert code == 409, historical
        assert len(runtime.records) == 2


@pytest.mark.parametrize("suffix", ["report", "submit", "cancel/", "cancel/extra", "retry/", "retry/extra"])
def test_retry_admission_does_not_grant_other_report_writes(suffix):
    prefix = "/api/projects/DEMO/runs/git-review/skill-reviews/job-skill-" + "a" * 64
    assert not web._is_project_api_post_path(prefix + "/" + suffix)
    assert not web._is_project_api_post_path(prefix[:-1] + "/retry")
