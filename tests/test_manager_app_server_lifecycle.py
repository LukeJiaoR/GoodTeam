"""[INPUT]: 离线 JSONL 运行时、Manager 会话与角色记忆。
[OUTPUT]: 原生会话、并发控制、停止与记忆快照回归检查。
[POS]: 覆盖 Manager app-server 的请求生命周期。
[PROTOCOL]: Update this header on change, then check AGENTS.md

Concurrent native Manager lifecycle using an offline JSONL subprocess peer.
"""
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
        threads.setdefault(params["threadId"], [])
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
        elif text == "model-context-too-small":
            turns[turn]["error"] = {"codexErrorInfo": "contextWindowExceeded", "message": "fixture input"}
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


def test_chat_survives_blocked_skill_storage(runtime, monkeypatch):
    native, log = runtime
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(native.workspace / "operator-state"))

    result = native.run(logical_id="plain-chat", text="Discuss my project idea.", context="")

    assert result["text"] == "actual answer"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    thread = next(call["params"] for call in calls if call["method"] == "thread/start")
    assert {tool["name"] for tool in thread["dynamicTools"]} == {
        "goodteam_project", "goodteam_workers", "goodteam_result",
    }


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


def test_manager_memory_is_frozen_filtered_and_refreshed_after_correction(runtime):
    from goodteam_os.memory import RoleMemoryStore

    native, log = runtime
    memory = RoleMemoryStore(native.root)
    original = memory.append_memory_event("orchestrator", horizon="long-term",
        text="ORIGINAL_MEMORY: support offline operation.", source="fixture:requirement")
    memory.append_memory_event("orchestrator", horizon="short-term",
        text="PRIVATE_EXECUTION_AUDIT", source="execution:fixture:final_provenance")
    with pytest.raises(ManagerExecutionError):
        native.run(logical_id="memory", text="fail-once", context="original", attempt_id="first")
    captured = native.store.read()["manager_app_server"]["requests"]["memory"]["memory_pickup"]
    def calls():
        return [json.loads(line)["params"] for line in log.read_text().splitlines()
                if json.loads(line)["method"] == "turn/start"]
    first = calls()[0]["input"][0]["text"]
    assert "ORIGINAL_MEMORY" in first and "PRIVATE_EXECUTION_AUDIT" not in first
    memory.append_memory_event("orchestrator", horizon="long-term",
        text="CORRECTED_MEMORY: support offline operation.", source="fixture:correction",
        supersedes=memory._event_id(original))
    native.run(logical_id="memory", text="fail-once", context="replacement", attempt_id="second")
    retry = calls()[1]["input"][0]["text"]
    assert "ORIGINAL_MEMORY" not in retry and "CORRECTED_MEMORY" not in retry
    assert "original" in retry and "replacement" not in retry
    assert native.store.read()["manager_app_server"]["requests"]["memory"]["memory_pickup"] == captured
    native.run(logical_id="new-memory", text="finish-now", context="latest")
    latest = calls()[2]
    assert "CORRECTED_MEMORY" in latest["input"][0]["text"]
    assert "ORIGINAL_MEMORY" not in latest["input"][0]["text"]
    assert latest["threadId"] != calls()[0]["threadId"]


def test_slow_background_memory_pickup_does_not_block_user_message(runtime, monkeypatch):
    import goodteam_os.memory as memory_module

    native, _ = runtime
    entered, release = threading.Event(), threading.Event()
    real_store = memory_module.runtime_role_memory_store

    def slow_store(*args, **kwargs):
        store = real_store(*args, **kwargs)
        capture = store.build_pickup_packet
        def build(*args, **kwargs):
            if kwargs.get("query") == "hold-background":
                entered.set()
                assert release.wait(3)
            return capture(*args, **kwargs)
        store.build_pickup_packet = build
        return store

    monkeypatch.setattr(memory_module, "runtime_role_memory_store", slow_store)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        background = pool.submit(native.run, logical_id="slow-memory", text="hold-background", context="evidence",
                                 background=True, phase="project_running_observation")
        try:
            assert entered.wait(2)
            reply = pool.submit(native.run, logical_id="foreground-memory", text="finish-now", context="latest")
            assert reply.result(timeout=2)["text"] == "actual answer"
        finally:
            release.set()
        wait_running(native, "slow-memory")
        native.stop("slow-memory")
        with pytest.raises(ManagerExecutionError, match="stopped"):
            background.result(timeout=3)


def test_stop_during_memory_pickup_prevents_native_start(runtime, monkeypatch):
    import goodteam_os.memory as memory_module

    native, log = runtime
    entered, release = threading.Event(), threading.Event()
    real_store = memory_module.runtime_role_memory_store

    def slow_store(*args, **kwargs):
        store = real_store(*args, **kwargs)
        capture = store.build_pickup_packet
        def build(*args, **kwargs):
            entered.set()
            assert release.wait(3)
            return capture(*args, **kwargs)
        store.build_pickup_packet = build
        return store

    monkeypatch.setattr(memory_module, "runtime_role_memory_store", slow_store)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(native.run, logical_id="stop-before-start", text="finish-now", context="original")
        try:
            assert entered.wait(2)
            assert native.stop("stop-before-start")["status"] == "stopped"
        finally:
            release.set()
        with pytest.raises(ManagerExecutionError, match="stopped"):
            pending.result(timeout=3)
    assert not log.exists() or not any(json.loads(line)["method"] == "turn/start" for line in log.read_text().splitlines())


def test_duplicate_request_captures_memory_once(runtime, monkeypatch):
    import goodteam_os.memory as memory_module

    native, log = runtime
    entered, release = threading.Event(), threading.Event()
    real_store, captures = memory_module.runtime_role_memory_store, []

    def slow_store(*args, **kwargs):
        store = real_store(*args, **kwargs)
        capture = store.build_pickup_packet
        def build(*args, **kwargs):
            captures.append(1)
            entered.set()
            assert release.wait(3)
            return capture(*args, **kwargs)
        store.build_pickup_packet = build
        return store

    monkeypatch.setattr(memory_module, "runtime_role_memory_store", slow_store)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(native.run, logical_id="duplicate", text="hold-original", context="original")
        try:
            assert entered.wait(2)
            second = pool.submit(native.run, logical_id="duplicate", text="hold-original", context="original")
        finally:
            release.set()
        record = wait_running(native, "duplicate")
        native.client.steer_turn(record["thread_id"], record["turn_id"], [{"type": "text", "text": "finish fixture"}])
        assert first.result(timeout=3)["text"] == second.result(timeout=3)["text"]
    assert len(captures) == 1
    assert sum(json.loads(line)["method"] == "turn/start" for line in log.read_text().splitlines()) == 1


def test_discovery_uses_only_its_frozen_valid_memory(runtime):
    from goodteam_os.memory import RoleMemoryStore

    native, log = runtime
    store = RoleMemoryStore(native.root)
    original = store.append_memory_event("orchestrator", horizon="long-term",
        text="OLD_DISCOVERY_FACT", source="fixture:original")
    pickup = store.build_pickup_packet("orchestrator", query="OLD_DISCOVERY_FACT")
    context = json.dumps({"schema_version": "project_outcome_discovery_packet.v1", "memory_pickup": pickup})
    store.append_memory_event("orchestrator", horizon="long-term", text="NEW_DISCOVERY_FACT",
        source="fixture:correction", supersedes=store._event_id(original))
    native.run(logical_id="discovery", text="finish-now", context=context,
               phase="project_outcome_discovery", background=True)
    call = next(json.loads(line) for line in log.read_text().splitlines() if json.loads(line)["method"] == "turn/start")
    submitted = call["params"]["input"][0]["text"]
    assert "OLD_DISCOVERY_FACT" not in submitted and "NEW_DISCOVERY_FACT" not in submitted
    assert native.store.read()["manager_app_server"]["requests"]["discovery"]["context"] == context


def test_correction_does_not_steer_a_thread_with_superseded_memory(runtime):
    from goodteam_os.memory import RoleMemoryStore

    native, log = runtime
    store = RoleMemoryStore(native.root)
    original = store.append_memory_event("orchestrator", horizon="long-term",
        text="OLD_CONVERSATION_FACT", source="fixture:original")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(native.run, logical_id="in-flight", text="hold-original", context="original")
        record = wait_running(native, "in-flight")
        store.append_memory_event("orchestrator", horizon="long-term", text="NEW_CONVERSATION_FACT",
            source="fixture:correction", supersedes=store._event_id(original))
        second = pool.submit(native.run, logical_id="after-correction", text="finish-now", context="latest")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            request = native.store.read()["manager_app_server"]["requests"].get("after-correction")
            if request and request.get("memory_pickup"):
                break
            time.sleep(0.01)
        assert request and not second.done()
        native.client.steer_turn(record["thread_id"], record["turn_id"], [{"type": "text", "text": "finish fixture"}])
        first.result(timeout=3)
        second.result(timeout=3)
    calls = [json.loads(line)["params"] for line in log.read_text().splitlines() if json.loads(line)["method"] == "turn/start"]
    assert calls[1]["threadId"] != calls[0]["threadId"]
    assert "NEW_CONVERSATION_FACT" in calls[1]["input"][0]["text"]
    assert "OLD_CONVERSATION_FACT" not in calls[1]["input"][0]["text"]


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



def test_background_model_context_error_keeps_native_thread(runtime, monkeypatch):
    native, log = runtime
    runtime_config = native._runtime_config
    def changed_runtime():
        command, env, public, fingerprint = runtime_config()
        return command, env, {**public, "model": "smaller"}, fingerprint + "-smaller"

    # Use the same logical request and original input on the explicit retry.
    request = dict(logical_id="background-context", text="model-context-too-small",
                   context="fixture context", background=True)
    errors = []
    for attempt in ("first", "second"):
        with pytest.raises(ManagerExecutionError) as error:
            native.run(**request, attempt_id=attempt)
        errors.append(error.value)
        monkeypatch.setattr(native, "_runtime_config", changed_runtime)
        # Make native readback available on reconnect in the subprocess fixture.
        if attempt == "first":
            monkeypatch.setattr(native, "reconcile", lambda _: {"native_status": "failed"})
    assert [error.code for error in errors] == ["context_window_exceeded"] * 2
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    initial = next(record for record in requests if record["method"] == "turn/start")
    resumed = next(record for record in requests if record["method"] == "thread/resume")
    assert resumed["params"]["model"] == "smaller"
    assert resumed["params"]["threadId"] == initial["params"]["threadId"]
    assert all(record["method"] != "turn/interrupt" for record in requests)
