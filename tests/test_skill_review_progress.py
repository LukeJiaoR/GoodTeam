"""Owned model-free children preserve bounded observations, never partial reports."""
from copy import deepcopy
import json
import os
import sys
import time

import pytest

from goodteam_os import service
from goodteam_os.skills import review_process
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_process import ReviewProcess, run_review_process
from goodteam_os.skills.review_progress import ProcessProgress
from goodteam_os.skills.review_results import ReviewResultStore
from test_skill_review_results import prepared
from test_task_skill_entrypoints import ctx as ctx

THREAD = "01234567-89ab-cdef-0123-456789abcdef"
SENSITIVE = "synthetic-secret-must-not-be-persisted"


def observations():
    values = []
    progress = ProcessProgress(900, lambda value: values.append(deepcopy(value)))
    progress.emit(force=True)
    return progress, values


def test_progress_filters_native_content_and_never_regresses_delivery_phase():
    progress, values = observations()
    events = [
        {"type": "thread.started", "thread_id": THREAD, "secret": SENSITIVE},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"text": SENSITIVE}},
        {"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 9, "secret": SENSITIVE}},
    ]
    wire = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
    progress.output(wire[:13])
    progress.output(wire[13:])
    progress.emit("input_delivered", force=True)  # CLI output may race with closing stdin.
    progress.output(("401 Unauthorized " + SENSITIVE).encode(), stderr=True)
    progress.emit(force=True)
    assert values[-1]["phase"] == "native_turn_completed"
    assert values[-1]["thread_id"] == THREAD
    assert values[-1]["usage"] == {"input_tokens": 40, "output_tokens": 9}
    assert values[-1]["diagnostic_classes"] == ["authentication"]
    assert values[-1]["termination_confirmed"] is False
    assert SENSITIVE not in json.dumps(values)


def owned_waiting_child(tmp_path, monkeypatch):
    ready = tmp_path / "fixture-ready"
    event = json.dumps({"type": "thread.started", "thread_id": THREAD}) + '\n{"type":"turn.started"}\n'
    body = ("import os,sys,time\nfrom pathlib import Path\n"
            "Path('fixture-ready').write_text('ready')\n"
            "sys.stdin.buffer.read()\n"
            "sys.stdout.write(" + repr(event) + ");sys.stdout.flush()\n"
            "sys.stderr.write(" + repr("error sending request " + SENSITIVE) + ");sys.stderr.flush()\n"
            "time.sleep(30)\n")
    popen, children = review_process.subprocess.Popen, []

    def start(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        expires = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < expires:
            time.sleep(0.005)
        if not ready.exists():
            review_process._terminate(child)
            pytest.fail("The isolated observation fixture did not become ready")
        return child

    monkeypatch.setattr(review_process.subprocess, "Popen", start)
    return ReviewProcess((sys.executable, "-I", "-c", body), tmp_path, {}, 0.4), children


def test_timeout_keeps_native_phase_diagnostic_class_and_confirmed_local_stop(tmp_path, monkeypatch):
    spec, children = owned_waiting_child(tmp_path, monkeypatch)
    values = []
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b"owned fixture input", check_current=lambda: None,
                           on_progress=lambda value: values.append(deepcopy(value)))
    assert error.value.code == "review_timeout"
    assert values[-1]["phase"] == "native_turn_started"
    assert values[-1]["thread_id"] == THREAD
    assert values[-1]["usage"] is None
    assert values[-1]["diagnostic_classes"] == ["network"]
    assert values[-1]["termination_confirmed"] is True
    assert values[-1]["error_code"] == "review_timeout"
    assert values[-1]["stdout_bytes"] > 0 and values[-1]["stderr_bytes"] > 0
    assert SENSITIVE not in json.dumps(values)
    assert len(children) == 1 and children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def test_observation_failure_still_reaps_the_owned_child(tmp_path, monkeypatch):
    spec, children = owned_waiting_child(tmp_path, monkeypatch)

    def broken_store(value):
        if value["phase"] == "native_turn_started":
            raise SkillError("review_progress_invalid", "Owned fixture storage failure")

    with pytest.raises(SkillError) as error:
        run_review_process(spec, b"owned fixture input", check_current=lambda: None, on_progress=broken_store)
    assert error.value.code == "review_progress_invalid"
    assert len(children) == 1 and children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def test_uncertain_termination_cannot_be_masked_by_a_progress_write_failure(tmp_path, monkeypatch):
    spec, children = owned_waiting_child(tmp_path, monkeypatch)
    terminate = review_process._terminate

    def unavailable_confirmation(child):
        terminate(child)  # Test owns cleanup; product receives no confirmation.
        raise SkillError("review_termination_unknown", "Owned fixture probe failure")

    def broken_store(value):
        if value["error_code"] == "review_termination_unknown":
            raise SkillError("review_progress_invalid", "Owned fixture storage failure")

    monkeypatch.setattr(review_process, "_terminate", unavailable_confirmation)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b"owned fixture input", check_current=lambda: None, on_progress=broken_store)
    assert error.value.code == "review_termination_unknown"
    assert children[0].returncode is not None


@pytest.mark.parametrize("deadline,ceiling", [(121, 120), (3601, 3600), (float("inf"), 3600), (900, 0)])
def test_unbounded_or_unselected_long_transport_is_rejected_before_spawn(tmp_path, monkeypatch, deadline, ceiling):
    monkeypatch.setattr(review_process.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Invalid budget must not launch"))
    spec = ReviewProcess((sys.executable, "-I", "-c", "pass"), tmp_path, {}, deadline,
                         deadline_ceiling_seconds=ceiling)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b"owned fixture input", check_current=lambda: None)
    assert error.value.code == "review_process_invalid"


def test_provisional_progress_survives_failed_attempt_without_report_authority(ctx):
    results, job, *_ = prepared(ctx)
    results.mark_running()
    progress, values = observations()
    progress.output((json.dumps({"type": "thread.started", "thread_id": THREAD}) + "\n").encode())
    progress.value.update(error_code="review_timeout", termination_confirmed=True, returncode=-15)
    progress.emit(force=True)
    results.observe_progress(values[-1])
    results.fail("review_timeout")
    restored = ReviewResultStore(ctx.workers, job["id"]).detail(include_report=True)
    assert restored["process_progress"]["thread_id"] == THREAD
    assert restored["process_progress"]["termination_confirmed"] is True
    assert restored["stages"]["content_delivered"] == "unobserved"
    assert not restored["report_available"] and "report" not in restored
    with pytest.raises(SkillError) as error:
        results.observe_progress(values[-1])
    assert error.value.code == "review_progress_stopped"


def test_foreign_progress_binding_is_rejected_on_report_read(ctx):
    results, job, *_ = prepared(ctx)
    results.mark_running()
    _progress, values = observations()
    results.observe_progress(values[-1])
    path = service.service_state_dir() / "skills" / "review-results" / job["id"] / "progress.json"
    receipt = json.loads(path.read_text())
    receipt["binding_digest"] = "sha256:" + "f" * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(SkillError) as error:
        results.detail()
    assert error.value.code == "review_result_corrupt"
