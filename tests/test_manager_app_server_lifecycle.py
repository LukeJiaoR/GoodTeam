"""Concurrent native Manager lifecycle using an offline JSONL subprocess peer."""
import concurrent.futures
import json
import os
import sys
import threading
import time

import pytest

from goodteam_os.codex_app_server import CodexAppServerRejected, CodexAppServerTimeout
from goodteam_os.manager_app_server import ManagerAppServer, close_runtimes, runtime_for, stop_background_work
from goodteam_os.manager_execution import ManagerExecutionError


SERVER = r'''
import json, sys
from pathlib import Path

log = Path(sys.argv[1])
threads, turns, counts = {}, {}, {}
def send(value):
    print(json.dumps(value), flush=True)
def finish(thread, turn, status="completed", text="actual answer"):
    item = {"id":"answer-"+turn,"type":"agentMessage","phase":"final_answer","text":text}
    turns[turn].update(status=status, items=[item])
    send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"item":item}})
    send({"method":"turn/completed","params":{"threadId":thread,"turn":turns[turn]}})

for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params", {})
    with log.open("a") as handle:
        handle.write(json.dumps(request)+"\n")
    if method == "initialized":
        continue
    result = {}
    if method == "thread/start":
        thread = "thread-"+str(len(threads)+1)
        threads[thread] = []
        result = {"thread":{"id":thread}}
    elif method == "thread/resume":
        result = {"thread":{"id":params["threadId"]}}
    elif method == "thread/read":
        result = {"thread":{"id":params["threadId"], "turns":[turns[t] for t in threads[params["threadId"]]]}}
    elif method == "turn/start":
        text = params["input"][0]["text"].split("\n")[0]
        if text == "reject-start":
            send({"id":request["id"],"error":{"code":-32600,"message":"invalid test input"}})
            continue
        thread, turn = params["threadId"], "turn-"+str(len(turns)+1)
        turns[turn] = {"id":turn,"status":"inProgress","items":[]}
        threads[thread].append(turn)
        send({"id":request["id"],"result":{"turn":turns[turn]}})
        send({"method":"turn/started","params":{"threadId":thread,"turn":turns[turn]}})
        counts[text] = counts.get(text,0)+1
        if text == "unauthorized":
            turns[turn]["error"] = {"message": "Unexpected status 401 Unauthorized", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
            finish(thread, turn, "failed", "")
        elif text == "fail-once" and counts[text] == 1:
            finish(thread, turn, "failed", "partial response")
        elif not text.startswith("hold"):
            finish(thread, turn)
        continue
    elif method == "turn/steer":
        turn, thread = params["expectedTurnId"], params["threadId"]
        send({"id":request["id"],"result":{"turnId":turn}})
        status = "failed" if params["input"][0]["text"].startswith("fail-steered") else "completed"
        finish(thread, turn, status=status, text="answer incorporates the new requirement")
        continue
    elif method == "turn/interrupt":
        send({"id":request["id"],"result":{}})
        finish(params["threadId"], params["turnId"], "interrupted", "stopped")
        continue
    elif method != "initialize":
        raise AssertionError(method)
    send({"id":request["id"],"result":result})
'''


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS", "3")
    root = tmp_path / "project"
    root.mkdir()
    config = tmp_path / "project.yaml"
    config.write_text(f"key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n")
    script, log = tmp_path / "native.py", tmp_path / "requests.jsonl"
    script.write_text(SERVER)
    monkeypatch.setattr(ManagerAppServer, "_runtime_config", lambda self: (
        [sys.executable, "-u", str(script), str(log)], dict(os.environ),
        {"model": "fixture", "reasoning_depth": "high", "additional_prompt": "", "provider_connection": None},
        "fixture-config"))
    close_runtimes()
    native = runtime_for(config, "DEMO")
    yield native, log
    close_runtimes()


def wait_running(runtime, logical_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = runtime.store.read().get("manager_app_server", {}).get("requests", {}).get(logical_id) or {}
        if record.get("status") == "running" and record.get("turn_id"):
            return record
        time.sleep(0.01)
    pytest.fail("Native turn never became running")


def test_background_analysis_does_not_block_chat_steer_or_scoped_stop(runtime):
    native, log = runtime
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        background = pool.submit(native.run, logical_id="analysis", text="hold-background", context="snapshot",
                                 background=True, phase="project_running_observation")
        background_record = wait_running(native, "analysis")
        first = pool.submit(native.run, logical_id="z-original", text="hold-chat", context="initial")
        first_record = wait_running(native, "z-original")
        second = pool.submit(native.run, logical_id="a-followup", text="change requirement", context="latest")
        response = second.result(timeout=3)
        assert response["text"] == "answer incorporates the new requirement"
        assert response["manager_execution"]["logical_id"] == "a-followup" and response["reply_suppressed"] is False
        assert first.result(timeout=3)["reply_suppressed"] is True
        assert not background.done()
        assert response["manager_execution"]["thread_id"] == first_record["thread_id"] != background_record["thread_id"]
        # Simulate lost completion receipts. Sorted JSON puts the latest input
        # first lexically; native recovery must still publish only that input.
        for identifier in ("z-original", "a-followup"):
            native._record(identifier, status="interrupted", result=None)
        assert native.reconcile("z-original")["result"]["reply_suppressed"] is True
        recovered = native.store.read()["manager_app_server"]["requests"]["a-followup"]
        assert recovered["result"]["reply_suppressed"] is False
        assert recovered["result"]["manager_execution"]["logical_id"] == "a-followup"
        continued = pool.submit(native.run, logical_id="user-3", text="hold-next-chat", context="updated")
        wait_running(native, "user-3")
        stops = stop_background_work(native.workspace, native.key)
        assert len(stops) == 1 and stops[0]["status"] == "stopped"
        with pytest.raises(ManagerExecutionError, match="stopped"):
            background.result(timeout=3)
        assert not continued.done()
        assert native.stop("user-3")["native_state"] == "interrupted"
        with pytest.raises(ManagerExecutionError, match="stopped"):
            continued.result(timeout=3)
    assert any(json.loads(line)["method"] == "thread/resume" for line in log.read_text().splitlines())


def test_failed_start_and_turn_timeout_release_owner_and_interrupt_native_work(runtime, monkeypatch):
    native, log = runtime
    with pytest.raises(CodexAppServerRejected):
        native.run(logical_id="rejected", text="reject-start", context="snapshot")
    assert not native.active and not native.by_request and native.client.alive
    monkeypatch.setenv("GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS", "0.05")
    with pytest.raises(CodexAppServerTimeout):
        native.run(logical_id="timed-out", text="hold-forever", context="snapshot")
    assert not native.active and not native.by_request and native.client.alive
    methods = [json.loads(line)["method"] for line in log.read_text().splitlines()]
    assert "turn/interrupt" in methods
    assert native.store.read()["manager_app_server"]["requests"]["timed-out"]["status"] == "interrupted"
    assert native.run(logical_id="fresh", text="finish-now", context="snapshot")["text"] == "actual answer"


def test_explicit_retry_requires_native_terminal_receipt_and_keeps_original_input(runtime):
    native, log = runtime
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(native.run, logical_id="owner", text="hold-original", context="owner evidence", attempt_id="owner-1")
        wait_running(native, "owner")
        steered = pool.submit(native.run, logical_id="retry", text="fail-steered", context="original evidence", attempt_id="attempt-1")
        for future in (owner, steered):
            with pytest.raises(ManagerExecutionError):
                future.result(timeout=3)
    records = native.store.read()["manager_app_server"]["requests"]
    assert records["retry"]["runtime"] == records["owner"]["runtime"]
    before = log.read_bytes()
    with pytest.raises(ManagerExecutionError, match="not replayed"):
        native.run(logical_id="retry", text="fail-steered", context="replacement evidence", attempt_id="attempt-1")
    assert sum(json.loads(line)["method"] == "turn/start" for line in log.read_text().splitlines()) == 1
    result = native.run(logical_id="retry", text="fail-steered", context="replacement evidence", attempt_id="attempt-2")
    assert result["text"] == "actual answer" and log.read_bytes() != before
    turns = [json.loads(line)["params"] for line in log.read_text().splitlines() if json.loads(line)["method"] == "turn/start"]
    assert len(turns) == 2 and "original evidence" in turns[1]["input"][0]["text"]
    assert "replacement evidence" not in turns[1]["input"][0]["text"]
    record = native.store.read()["manager_app_server"]["requests"]["retry"]
    assert record["attempt_id"] == "attempt-2" and record["attempt_history"][0]["attempt_id"] == "attempt-1"


def test_native_recovery_cannot_revive_a_concurrently_stopped_request(runtime, monkeypatch):
    native, _ = runtime
    native.run(logical_id="recover", text="finish-now", context="original")
    native._record("recover", status="interrupted")
    captured, release = threading.Event(), threading.Event()
    original = native.client.read_thread
    def read(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.set()
        assert release.wait(2)
        return result
    monkeypatch.setattr(native.client, "read_thread", read)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        recovering = pool.submit(native.reconcile, "recover")
        assert captured.wait(2)
        native.stop("recover")
        release.set()
        assert recovering.result(timeout=3)["status"] == "stopped"
    assert native.store.read()["manager_app_server"]["requests"]["recover"]["status"] == "stopped"



def test_provider_failure_keeps_authentication_category_and_diagnostics(runtime):
    from goodteam_os.assistant_runtime import safe_failure

    native, _ = runtime
    with pytest.raises(ManagerExecutionError) as failure:
        native.run(logical_id="authentication", text="unauthorized", context="snapshot")
    assert safe_failure(failure.value)["code"] == "authentication_failed"
    assert not native.active and not native.by_request
    assert native.client.stderr_path == native.store.path.with_name("manager-app-server.stderr.log")
