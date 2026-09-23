"""Operator cancellation is a durable request, not an assertion of termination."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import threading
import time

from goodteam_os.skills import review_cancel
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_api import handle_review_get, handle_review_post, task_review_details
from goodteam_os.skills.review_capture import capture_final_turn
from goodteam_os.skills.review_cancel import request_cancellation
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_mutations import workflow_guard
from test_skill_review_http import post, server_url
from test_skill_review_results import prepared, channel


import os
import signal

import pytest

from goodteam_os.skills import review_execution, review_process
from goodteam_os.skills.review_results import ReviewResultStore
from test_skill_review_execution import ProcessFixture, ready_job
from test_task_skill_entrypoints import ctx as ctx


@pytest.mark.parametrize("boundary", ["signal", "probe"])
def test_unconfirmed_transport_cleanup_cannot_become_a_retryable_failure(ctx, monkeypatch, boundary):
    job, _context, *_rest = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, body="import time\ntime.sleep(20)", deadline=0.3)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    original_popen, original_killpg = review_process.subprocess.Popen, os.killpg
    children = []

    def child(*args, **kwargs):
        result = original_popen(*args, **kwargs)
        children.append(result)
        return result

    def denied(pid, sig):
        if (boundary == "signal" and sig != 0) or (boundary == "probe" and sig == 0):
            raise PermissionError("Owned fixture cannot confirm termination")
        return original_killpg(pid, sig)

    monkeypatch.setattr(review_process.subprocess, "Popen", child)
    monkeypatch.setattr(os, "killpg", denied)
    try:
        result = ctx.workers.run_job(job["id"])
        assert result["skill_error_code"] == "review_termination_unknown", result
        detail = ReviewResultStore(ctx.workers, job["id"]).detail()
        assert detail["status"] == "stopping"
        assert not detail["report_available"] and not detail["retry_available"]
        assert len(children) == 1
    finally:
        # Only the exact child/group created by this test is cleaned up.
        for process in children:
            try:
                original_killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()



def request_for(detail):
    return {"snapshot_digest": detail["snapshot_ref"]["digest"], "context_digest": detail["context_ref"]["digest"]}


def cancel_path(job):
    return "/api/projects/DEMO/runs/review-worker/skill-reviews/" + job["id"] + "/cancel"


@contextmanager
def running(ctx, monkeypatch, *, closed_streams=False, guarded=False):
    job, context, selected, _candidate = ready_job(ctx)
    body = ("from pathlib import Path\nimport time\n"
            + ("os.close(0);os.close(1);os.close(2)\n" if closed_streams else "")
            + "Path('started-pid').write_text(str(os.getpid()))\n"
            + "while not Path('release').exists(): time.sleep(0.01)\n")
    runtime = ProcessFixture(ctx.root, body=body, deadline=8)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    results, failures = [], []

    def work():
        try:
            with workflow_guard(ctx.workspace, "DEMO") if guarded else nullcontext():
                results.append(ctx.workers.run_job(job["id"]))
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    marker = ctx.root / "execution-0" / "started-pid"
    try:
        end = time.monotonic() + 6
        while not marker.exists() and thread.is_alive() and time.monotonic() < end:
            time.sleep(0.01)
        assert marker.exists(), (results, failures)
        store = ReviewResultStore(ctx.workers, job["id"])
        detail = store.detail()
        assert detail["status"] == "running"
        yield job, context, selected, store, detail, int(marker.read_text()), thread, results, runtime
    finally:
        if marker.parent.exists():
            (marker.parent / "release").touch()
        thread.join(timeout=10)
        assert not thread.is_alive(), "The test-owned execution must be stopped"
        assert not failures, failures


@pytest.mark.parametrize("handler", ["contract", "product"])
@pytest.mark.parametrize("closed_streams", [False, True])
def test_real_http_cancel_stops_owner_even_with_workflow_guard(ctx, monkeypatch, handler, closed_streams):
    with running(ctx, monkeypatch, closed_streams=closed_streams, guarded=True) as data:
        job, context, _selected, store, detail, pid, thread, results, runtime = data
        before = StateStore(ctx.workspace).read_project_current("DEMO")
        with server_url(ctx, monkeypatch, handler) as base:
            # The actual long-running workflow guard is still held by the owner.
            # A cancellation endpoint taking that guard would fail this test.
            code, rejected = post(base, cancel_path(job), request_for(detail), origin="https://outside.invalid")
            assert code == 403, rejected
            code, rejected = post(base, cancel_path(job), {**request_for(detail), "pid": pid})
            assert code == 409, rejected
            assert thread.is_alive()
            start = time.monotonic()
            code, accepted = post(base, cancel_path(job), request_for(detail))
            assert time.monotonic() - start < 2
            assert code == 202 and accepted["status"] == "stopping", accepted
            assert accepted["termination_confirmed"] is False
            thread.join(timeout=3)
            assert not thread.is_alive(), "A stop request must reach a real child, not just a UI field"
            assert results[0]["skill_error_code"] == "review_cancelled", results
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            final = store.detail()
            assert final["status"] == "cancelled"
            assert final["retry_available"] is True and not final["report_available"]
            code, duplicate = post(base, cancel_path(job), request_for(detail))
            assert code == 200 and duplicate["termination_confirmed"] is True, duplicate
            assert duplicate["status"] == "cancelled"
            assert store.detail() == final
            assert runtime.executions == 1
            assert StateStore(ctx.workspace).read_project_current("DEMO") == before
            report_code, _ = task_review_details(ctx.workers, context, job_id=job["id"], include_report=True)
            assert report_code == 409


@pytest.mark.parametrize("change", [{"context_digest": "sha256:" + "f" * 64},
    {"snapshot_digest": "sha256:" + "e" * 64}, {"model": "other"}, {"job_id": "other"}])
def test_stale_or_extended_cancel_request_cannot_change_attempt(ctx, change):
    store, _job, _context, *_rest = prepared(ctx)
    detail = store.detail()
    with pytest.raises(SkillError):
        request_cancellation(store, {**request_for(detail), **change}, check_task=lambda: None)
    assert store.detail() == detail


def test_received_result_wins_atomic_race_against_late_cancellation(ctx):
    store, *_rest = prepared(ctx)
    store.mark_running()
    detail = store.receive(capture_final_turn(channel(), returncode=0))
    with pytest.raises(SkillError) as error:
        request_cancellation(store, request_for(detail), check_task=lambda: None)
    assert error.value.code == "review_cancel_finished"
    assert store.detail() == detail and detail["report_available"]


def test_cancel_receipt_wins_before_late_report_and_never_claims_a_stop(ctx):
    store, *_rest = prepared(ctx)
    store.mark_running()
    body = request_for(store.detail())
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: request_cancellation(store, body, check_task=lambda: None), range(6)))
    assert all(item == results[0] for item in results)
    assert results[0]["termination_confirmed"] is False
    assert store.detail()["status"] == "stopping"
    with pytest.raises(SkillError) as error:
        store.receive(capture_final_turn(channel(), returncode=0))
    assert error.value.code == "review_cancel_requested"
    with pytest.raises(SkillError):
        store.assert_retryable()
    with pytest.raises(SkillError):
        store.recover()
    assert not store.detail()["report_available"]


def test_crash_after_stop_receipt_does_not_erase_request_or_permit_launch(ctx, monkeypatch):
    store, *_rest = prepared(ctx)
    body = request_for(store.detail())
    original = review_cancel._replace

    def fail_projection(*_args):
        raise OSError("Owned stop-state save failure")

    with monkeypatch.context() as patch:
        patch.setattr(review_cancel, "_replace", fail_projection)
        with pytest.raises(SkillError):
            request_cancellation(store, body, check_task=lambda: None)
    # The authoritative stop receipt survives the failed mutable projection.
    assert store.detail()["status"] == "stopping"
    assert not store.detail()["retry_available"]
    with pytest.raises(SkillError) as error:
        store.mark_running()
    assert error.value.code == "review_cancel_requested"
    assert review_cancel._replace is original
    repaired = request_cancellation(store, body, check_task=lambda: None)
    assert repaired["status"] == "stopping" and not repaired["termination_confirmed"]


def test_cancel_before_process_creation_never_launches_a_child(ctx, monkeypatch):
    job, _context, *_rest = ready_job(ctx)

    class BeforeLaunch(ProcessFixture):
        @contextmanager
        def execution(self, record, provider, ref):
            with super().execution(record, provider, ref) as spec:
                store = ReviewResultStore(ctx.workers, job["id"])
                request_cancellation(store, request_for(store.detail()), check_task=lambda: None)
                yield spec

    runtime = BeforeLaunch(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    monkeypatch.setattr(review_process.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("Cancelled before launch"))
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_cancelled", result
    assert ReviewResultStore(ctx.workers, job["id"]).detail()["status"] == "cancelled"


def test_revoked_content_does_not_hide_stop_control_or_require_content_access(ctx):
    store, job, context, selected, _candidate, _provider, _ref = prepared(ctx)
    store.mark_running()
    before = store.detail()
    state = create_initial_state("report-run", "DEMO", "Review original source")
    state.task_skill_context = context
    StateStore(ctx.workspace).write_project_current(state)
    ctx.service.availability.revoke(selected["skill_id"], selected["version_id"], actor="user", reason="test-stop",
                                     command_key="cancel-revoked", expected_revision=0)
    code, details = task_review_details(ctx.workers, context)
    assert code == 200 and not details["reviews"][0]["report_available"]
    assert details["reviews"][0]["cancel_available"] is True
    path = cancel_path(job).replace("review-worker", "report-run")
    code, result = handle_review_post(ctx.workspace, path, request_for(before))
    assert code == 202 and not result["termination_confirmed"], result
    # This is metadata-only cancellation; it cannot reopen revoked method bytes.
    with pytest.raises(SkillError):
        store.private_attempt()


def test_historical_and_foreign_tasks_cannot_request_stop(ctx):
    store, job, context, *_rest = prepared(ctx)
    state = create_initial_state("report-run", "DEMO", "Review original source")
    state.task_skill_context = context
    states = StateStore(ctx.workspace)
    states.write_project_current(state)
    path = cancel_path(job).replace("review-worker", "report-run")
    body = request_for(store.detail())
    code, foreign = handle_review_post(ctx.workspace, path.replace(job["id"], "job-skill-" + "0" * 64), body)
    assert code == 404, foreign
    states.archive_project_current("DEMO")
    states.write_project_current(create_initial_state("later", "DEMO", "Later task"))
    code, old = handle_review_post(ctx.workspace, path, body)
    assert code == 409 and old["error"] == "review_historical_readonly", old
    code, details = handle_review_get(ctx.workspace, path.rsplit("/cancel", 1)[0])
    assert code == 200 and not details["cancel_available"] and not details["retry_available"], details
    assert store.detail()["status"] == "prepared"


@pytest.mark.parametrize("cancel,wrong_prompt", [(False, False), (True, False), (False, True)])
def test_outer_envelope_cleanup_failure_never_confirms_cancellation_or_retry(ctx, monkeypatch, cancel, wrong_prompt):
    job, _context, *_rest = ready_job(ctx)

    class UnconfirmedEnvelope(ProcessFixture):
        @contextmanager
        def execution(self, record, provider, ref):
            try:
                with super().execution(record, provider, ref) as data:
                    if cancel:
                        store = ReviewResultStore(ctx.workers, job["id"])
                        request_cancellation(store, request_for(store.detail()), check_task=lambda: None)
                    yield (data[0], b"Changed prompt") if wrong_prompt else data
            finally:
                raise OSError("Owned outer envelope cleanup is not confirmed")

    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: UnconfirmedEnvelope(ctx.root))
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_termination_unknown", result
    store = ReviewResultStore(ctx.workers, job["id"])
    detail = store.detail()
    assert detail["status"] == "stopping" and not detail["retry_available"] and not detail["report_available"]
    if cancel:
        replay = request_cancellation(store, request_for(detail), check_task=lambda: None)
        assert replay["termination_confirmed"] is False and replay["status"] == "stopping"


def test_cancel_during_materialization_does_not_publish_a_report_or_launch(ctx, monkeypatch):
    job, _context, *_rest = ready_job(ctx)
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    original = ReviewResultStore.observe_delivery

    def stop_before_observation(store, *args, **kwargs):
        request_cancellation(store, request_for(store.detail()), check_task=lambda: None)
        return original(store, *args, **kwargs)

    monkeypatch.setattr(ReviewResultStore, "observe_delivery", stop_before_observation)
    monkeypatch.setattr(review_process.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Cancelled during preparation"))
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_cancelled", result
    assert ReviewResultStore(ctx.workers, job["id"]).detail()["status"] == "cancelled"


def test_current_task_recheck_failure_publishes_no_cancellation(ctx):
    store, *_rest = prepared(ctx)
    before = store.detail()

    def changed():
        raise SkillError("review_historical_readonly", "Task switched while request was admitted")

    with pytest.raises(SkillError):
        request_cancellation(store, request_for(before), check_task=changed)
    assert store.detail() == before and not store.detail()["cancel_requested"]
