"""Protocol subprocess evidence; these tests do not invoke a model provider."""

import concurrent.futures
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from goodteam_os.codex_app_server import (
    CodexAppServer,
    CodexAppServerExited,
    CodexAppServerRejected,
    CodexAppServerTimeout,
)


SERVER = r'''
import json, os, sys

def send(record):
    print(json.dumps(record), flush=True)

def finish(turn, status="completed", text="done"):
    send({"method":"item/completed", "params":{"threadId":"native", "turnId":turn,
        "item":{"id":"message-" + turn,"type":"agentMessage","text":text}}})
    send({"method":"turn/completed", "params":{"threadId":"native",
        "turn":{"id":turn,"status":status,"items":[]}}})

initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    params = msg.get("params", {})
    if method == "initialize":
        assert msg["params"]["capabilities"]["experimentalApi"]
        assert "GOODTEAM_DATABASE_URL" not in os.environ
        send({"id":msg["id"],"result":{"userAgent":"test"}})
        continue
    if method == "initialized":
        initialized = True
        print("API_KEY=do-not-record-this", file=sys.stderr, flush=True)
        continue
    assert initialized
    if msg.get("id") == "server-tool":
        assert msg["result"]["success"]
        assert msg["result"]["contentItems"][0]["type"] == "inputText"
        finish("work", text="queued task-42")
        continue
    if method in {"thread/start", "thread/resume", "thread/read"}:
        send({"id":msg["id"],"result":{"thread":{"id":"native","status":{"type":"idle"}}}})
    elif method == "turn/start":
        turn = params["input"][0]["text"]
        send({"method":"turn/started","params":{"threadId":"native","turn":{"id":turn}}})
        if turn == "fast":
            finish(turn)
        send({"id":msg["id"],"result":{"turn":{"id":turn,"status":"inProgress"}}})
        if turn == "work":
            send({"method":"item/agentMessage/delta","params":{"threadId":"native","turnId":turn,"delta":"checking"}})
            send({"id":"server-tool","method":"item/tool/call","params":{
                "threadId":"native","turnId":turn,"callId":"call-1","tool":"goodteam_delegate",
                "arguments":{"task_id":"task-42"}}})
    elif method == "turn/steer":
        assert params["expectedTurnId"] == "work"
        send({"id":msg["id"],"result":{"turnId":"work"}})
    elif method == "turn/interrupt":
        send({"id":msg["id"],"result":{}})
        finish(params["turnId"], "interrupted")
    elif method == "stall":
        pass
    elif method == "crash":
        sys.exit(1)
    else:
        send({"id":msg["id"],"error":{"code":-32601,"message":"unknown method"}})
'''


def make_client(tmp_path: Path, **kwargs) -> CodexAppServer:
    script = tmp_path / "app_server.py"
    script.write_text(SERVER)
    return CodexAppServer([sys.executable, "-u", str(script)], cwd=tmp_path, **kwargs)


def test_bidirectional_tools_keep_steering_live_and_deliver_final_events(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_DATABASE_URL", "private-control-plane")
    tool_started = threading.Event()
    release_tool = threading.Event()
    events = []
    completed = []

    def on_event(record):
        events.append(record)
        if record["method"] == "turn/completed":
            # Completion waiting includes delivery, even if the event arrives
            # before the turn/start response or the callback is temporarily busy.
            time.sleep(0.02)
            completed.append(record["params"]["turn"]["id"])

    def on_request(method, params):
        assert method == "item/tool/call"
        assert params["arguments"] == {"task_id": "task-42"}
        tool_started.set()
        assert release_tool.wait(2)
        assert client.read_thread("native")["thread"]["id"] == "native"
        return {"success": True, "contentItems": [{"type": "inputText", "text": "queued task-42"}]}

    client = make_client(tmp_path, on_event=on_event, on_request=on_request)
    try:
        client.start(timeout=2)
        thread = client.start_thread(dynamicTools=[{
            "type": "function", "name": "goodteam_delegate", "description": "Delegate an authorized task",
            "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
        }])["thread"]["id"]
        turn = client.start_turn(thread, [{"type": "text", "text": "work"}])["turn"]["id"]
        assert tool_started.wait(2)
        assert client.steer_turn(thread, turn, [{"type": "text", "text": "Change the requirement"}]) == {"turnId": turn}
        release_tool.set()
        assert client.wait_turn(thread, turn, timeout=2)["status"] == "completed"
        assert completed == ["work"]
        assert any(event["params"].get("item", {}).get("text") == "queued task-42" for event in events)
        client.start_turn(thread, [{"type": "text", "text": "fast"}])
        assert client.wait_turn(thread, "fast", timeout=2)["status"] == "completed"
        assert completed[-1] == "fast"
        client.start_turn(thread, [{"type": "text", "text": "hold"}])
        client.interrupt_turn(thread, "hold")
        assert client.wait_turn(thread, "hold", timeout=2)["status"] == "interrupted"
    finally:
        release_tool.set()
        client.close()
    assert not client.alive


def test_timeout_disconnect_and_restart_preserve_explicit_request_outcomes(tmp_path):
    client = make_client(tmp_path)
    try:
        client.start(timeout=2)
        with pytest.raises(CodexAppServerRejected, match="unknown method"):
            client.request("unsupported", timeout=1)
        with pytest.raises(CodexAppServerTimeout):
            client.request("stall", timeout=0.05)
        # A timed-out acknowledgement does not manufacture another request.
        assert client.read_thread("native")["thread"]["id"] == "native"
        with concurrent.futures.ThreadPoolExecutor() as pool:
            waiter = pool.submit(client.wait_turn, "native", "pending", 2)
            with pytest.raises(CodexAppServerExited):
                client.request("crash", timeout=1)
            with pytest.raises(CodexAppServerExited):
                waiter.result(timeout=2)
        old_pid = client.pid
        client.close()
        client.start(timeout=2)
        assert client.pid != old_pid
        assert client.resume_thread("native")["thread"]["id"] == "native"
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(client.request, "stall", timeout=2)
            client.close()
            with pytest.raises(CodexAppServerExited):
                pending.result(timeout=2)
    finally:
        client.close()


def test_stderr_diagnostics_redact_provider_credentials(tmp_path):
    log = tmp_path / "stderr.log"
    client = make_client(tmp_path, stderr_path=log, env=dict(os.environ))
    try:
        client.start(timeout=2)
        client.read_thread("native")
        deadline = time.monotonic() + 2
        while (not log.exists() or not log.stat().st_size) and time.monotonic() < deadline:
            time.sleep(0.01)
        text = log.read_text()
        assert "do-not-record-this" not in text
        assert "<redacted>" in text
    finally:
        client.close()
