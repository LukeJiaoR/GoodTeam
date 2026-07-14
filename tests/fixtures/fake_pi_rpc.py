#!/usr/bin/env python3
"""Deterministic Pi RPC double; never contacts a model provider."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


if "--version" in sys.argv:
    print("pi 0.99.0-fake")
    raise SystemExit(0)

args = sys.argv[1:]


def option(name: str, default: str = "") -> str:
    try:
        return args[args.index(name) + 1]
    except (ValueError, IndexError):
        return default


session_dir = Path(option("--session-dir", os.getenv("TMPDIR", "/tmp")))
session_dir.mkdir(parents=True, exist_ok=True)
session_id = os.getenv("FAKE_PI_SESSION_ID", "fake-session-1")
session_file = session_dir / f"{session_id}.jsonl"
session_file.touch()
session_name = option("--name", "fake")
provider = option("--provider", "fake-provider")
model = option("--model", "fake-model")
thinking = "medium"
streaming = False
aborted = False
entries: list[dict[str, object]] = []
write_lock = threading.Lock()


def emit(payload: dict[str, object]) -> None:
    with write_lock:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def response(request: dict[str, object], *, data: dict[str, object] | None = None, success: bool = True, error: str = "") -> None:
    payload: dict[str, object] = {
        "id": request.get("id"),
        "type": "response",
        "command": request.get("type"),
        "success": success,
    }
    if data is not None:
        payload["data"] = data
    if error:
        payload["error"] = error
    emit(payload)


def finish_prompt(message: str) -> None:
    global streaming
    delay = float(os.getenv("FAKE_PI_SETTLE_DELAY", "0.01"))
    emit({"type": "agent_start"})
    emit({"type": "turn_start"})
    emit({"type": "tool_execution_start", "toolCallId": "tool-1", "toolName": "read", "args": {"path": "README.md"}})
    time.sleep(delay)
    if os.getenv("FAKE_PI_CRASH") == "1":
        os._exit(17)
    emit({"type": "tool_execution_end", "toolCallId": "tool-1", "toolName": "read", "isError": False})
    user_id = f"entry-{len(entries) + 1}"
    assistant_id = f"entry-{len(entries) + 2}"
    entries.extend(
        [
            {"type": "message", "id": user_id, "parentId": entries[-1]["id"] if entries else None, "message": {"role": "user", "content": message}},
            {"type": "message", "id": assistant_id, "parentId": user_id, "message": {"role": "assistant", "content": [{"type": "text", "text": "Fake Pi completed.\n\n## Learned\n- Persistent sessions stay local."}]}},
        ]
    )
    session_file.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )
    emit({"type": "turn_end", "message": {"role": "assistant"}, "toolResults": []})
    emit({"type": "agent_end", "messages": [], "willRetry": False})
    if os.getenv("FAKE_PI_AGENT_ERROR") == "1":
        emit({"type": "extension_error", "error": "fake extension failed"})
    streaming = False
    if os.getenv("FAKE_PI_NO_SETTLED") != "1":
        emit({"type": "agent_settled"})


for raw in sys.stdin.buffer:
    if not raw.strip():
        continue
    request = json.loads(raw.decode("utf-8"))
    command = request.get("type")
    if os.getenv("FAKE_PI_MALFORMED") == "1":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        continue
    if os.getenv("FAKE_PI_UNKNOWN_ID") == "1":
        request["id"] = "unknown-id"
    if os.getenv("FAKE_PI_TIMEOUT_COMMAND") == command:
        continue
    if command == "get_state":
        response(
            request,
            data={
                "model": {"provider": provider, "id": model},
                "thinkingLevel": thinking,
                "isStreaming": streaming,
                "isCompacting": False,
                "sessionFile": str(session_file),
                "sessionId": session_id,
                "sessionName": session_name,
                "messageCount": len(entries),
                "pendingMessageCount": 0,
            },
        )
    elif command == "set_thinking_level":
        thinking = str(request.get("level") or thinking)
        response(request)
    elif command == "prompt":
        streaming = True
        threading.Thread(target=finish_prompt, args=(str(request.get("message") or ""),), daemon=True).start()
        if os.getenv("FAKE_PI_DROP_PROMPT_RESPONSE") != "1":
            response(request)
    elif command in {"steer", "follow_up"}:
        response(request)
    elif command == "abort":
        aborted = True
        streaming = False
        response(request)
        emit({"type": "agent_settled"})
    elif command == "get_session_stats":
        response(request, data={"sessionFile": str(session_file), "sessionId": session_id, "totalMessages": len(entries), "tokens": {"total": 42}, "cost": 0})
    elif command == "get_entries":
        since = request.get("since")
        if since:
            indexes = [index for index, entry in enumerate(entries) if entry.get("id") == since]
            if not indexes:
                response(request, success=False, error="cursor not found")
                continue
            selected = entries[indexes[0] + 1 :]
        else:
            selected = entries
        response(request, data={"entries": selected, "leafId": entries[-1]["id"] if entries else None})
    elif command == "get_last_assistant_text":
        text = None if os.getenv("FAKE_PI_NO_TEXT") == "1" else "Fake Pi completed.\n\n## Learned\n- Persistent sessions stay local."
        response(request, data={"text": text})
    elif command == "switch_session":
        candidate = Path(str(request.get("sessionPath") or ""))
        if not candidate.is_file():
            response(request, success=False, error="session missing")
        else:
            session_file = candidate
            session_id = candidate.stem
            entries.clear()
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
            response(request, data={"cancelled": False})
    else:
        response(request, success=False, error=f"unsupported command: {command}")
