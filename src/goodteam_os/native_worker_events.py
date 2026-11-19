"""Live observations and owned-process cancellation for native Codex Workers.

Events are observations attached to the existing Worker job, never dispatch or
acceptance authority. A saved PID is not sufficient permission to signal a process.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time


@dataclass
class _OwnedProcess:
    process: subprocess.Popen
    cancel: threading.Event = field(default_factory=threading.Event)
    settled: threading.Event = field(default_factory=threading.Event)
    stopped: bool = False


_processes: dict[str, _OwnedProcess] = {}
_process_lock = threading.Lock()


class NativeWorkerAborted(RuntimeError):
    def __init__(self, stdout: str, stderr: str):
        super().__init__("Native Worker was stopped by its owner.")
        self.stdout, self.stderr = stdout, stderr


def native_worker_capabilities(job_root: Path) -> dict:
    with _process_lock:
        owner = _processes.get(str(job_root.resolve()))
        active = owner is not None and not owner.settled.is_set()
    return {"event_stream": True, "abort": active, "steer": False, "follow_up": False,
            "process_owned": active}


def abort_native_worker(job_root: Path, *, wait_for_ack: bool = True) -> dict:
    """Stop only the live process registered by this host for this exact job."""
    with _process_lock:
        owner = _processes.get(str(job_root.resolve()))
        if owner is None or owner.settled.is_set():
            return {"accepted": False, "control_state": "not_running", "action": "abort"}
        owner.cancel.set()
    if wait_for_ack:
        owner.settled.wait(5)
    return {"accepted": True, "action": "abort",
            "control_state": "stopped" if owner.stopped else "requested"}


def _native_event(line: str, job: dict) -> dict | None:
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    kind = value.get("type")
    payload = {}
    if kind == "thread.started":
        payload = {"event": "session_started", "thread_id": str(value.get("thread_id", ""))[:256]}
    elif kind in {"turn.started", "turn.completed", "turn.failed", "error"}:
        payload = {"event": {"turn.started": "turn_started", "turn.completed": "turn_completed",
                             "turn.failed": "failure", "error": "failure"}[kind]}
        if kind in {"turn.failed", "error"}:
            error = value.get("error")
            payload["content"] = str(error.get("message", "") if isinstance(error, dict)
                                     else value.get("message", ""))[:8000]
    elif kind in {"item.started", "item.updated", "item.completed"}:
        item = value.get("item")
        if not isinstance(item, dict) or item.get("type") == "reasoning":
            return None
        payload = {"item_id": str(item.get("id", ""))[:256], "item_type": str(item.get("type", ""))[:64]}
        if item.get("type") == "agent_message":
            payload.update(event="worker_report", content=str(item.get("text", ""))[:8000])
        else:
            payload.update(event="tool_ended" if kind == "item.completed" else "tool_started",
                           status=str(item.get("status", "")))
    else:
        return None
    return {"schema_version": "native_worker_event.v1", "recorded_at": datetime.now(UTC).isoformat(),
            "job_id": job["id"], "native_type": kind,
            **{key: job[key] for key in ("run_id", "project_key", "project_task_id", "track_id", "execution_id")
               if job.get(key)}, **payload}


def read_native_worker_events(job_root: Path, *, after: int = 0, limit: int = 50, tail: bool = False) -> dict:
    """Read bounded complete records, either after a cursor or from the latest end."""
    if (type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200
            or type(tail) is not bool or tail and after):
        raise ValueError("Invalid native Worker event cursor or limit.")
    path = job_root / "native_events.jsonl"
    if path.is_symlink():
        raise ValueError("Native Worker events must stay in the original job.")
    if not path.is_file():
        return {"events": [], "cursor": 0, "capabilities": native_worker_capabilities(job_root)}
    events = []
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        start = max(0, size - 2_000_000) if tail else min(after, size)
        stream.seek(start)
        raw = stream.read(2_000_000)
        if tail and start:
            stream.seek(start - 1)
            if stream.read(1) != b"\n":
                # The bounded suffix may begin halfway through an event.
                boundary = raw.find(b"\n")
                start += boundary + 1 if boundary >= 0 else len(raw)
                raw = raw[boundary + 1:] if boundary >= 0 else b""
        cursor = start
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            cursor += len(line)
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and value.get("job_id") == job_root.name:
                events.append(value)
                if not tail and len(events) >= limit:
                    break
    return {"events": events[-limit:], "cursor": cursor,
            "snapshot_truncated": bool(start or len(events) > limit or cursor < size),
            "capabilities": native_worker_capabilities(job_root)}


def communicate_native_worker(process, prompt, *, timeout, job_root, job, redact):
    """Drain both pipes while publishing JSONL progress and respecting Stop."""
    from goodteam_os.skills.review_process import _terminate

    key = str(job_root.resolve())
    owner = _OwnedProcess(process)
    with _process_lock:
        if key in _processes:
            _terminate(process)
            raise RuntimeError("This native Worker already has a live owned process.")
        _processes[key] = owner
    deadline = time.monotonic() + timeout
    data = str(prompt or "").encode("utf-8")
    position = 0
    chunks = {"stdout": [], "stderr": []}
    pending = {"stdout": "", "stderr": ""}
    decoders = {name: codecs.getincrementaldecoder("utf-8")("replace") for name in chunks}
    try:
        with (job_root / "stdout.txt").open("w", encoding="utf-8") as out, \
                (job_root / "stderr.txt").open("w", encoding="utf-8") as err, \
                (job_root / "native_events.jsonl").open("a", encoding="utf-8") as events, \
                selectors.DefaultSelector() as selector:
            logs = {"stdout": out, "stderr": err}
            def receive(name, raw, *, final=False):
                text = decoders[name].decode(raw, final=final)
                chunks[name].append(text)
                pending[name] += text
                lines = pending[name].splitlines(keepends=True)
                pending[name] = ""
                if lines and not final and not lines[-1].endswith("\n"):
                    pending[name] = lines.pop()
                for line in lines:
                    safe = redact(line)
                    logs[name].write(safe)
                    logs[name].flush()
                    if name == "stdout" and (event := _native_event(safe, job)) is not None:
                        events.write(json.dumps(event, ensure_ascii=False) + "\n")
                        events.flush()

            for name, stream, mask in (("stdin", process.stdin, selectors.EVENT_WRITE),
                                       ("stdout", process.stdout, selectors.EVENT_READ),
                                       ("stderr", process.stderr, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, mask, name)
            while selector.get_map() or process.poll() is None:
                if owner.cancel.is_set():
                    _terminate(process)
                    owner.stopped = True
                    raise NativeWorkerAborted("".join(chunks["stdout"]), "".join(chunks["stderr"]))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout,
                        output="".join(chunks["stdout"]), stderr="".join(chunks["stderr"]))
                for selected, _ in selector.select(min(0.1, remaining)):
                    stream, name = selected.fileobj, selected.data
                    if name == "stdin":
                        try:
                            position += os.write(stream.fileno(), data[position:position + 8192])
                        except BrokenPipeError:
                            position = len(data)
                        if position == len(data):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        raw = os.read(stream.fileno(), 65536)
                        receive(name, raw, final=not raw)
                        if not raw:
                            selector.unregister(stream)
                            stream.close()
            return "".join(chunks["stdout"]), "".join(chunks["stderr"])
    except BaseException:
        # Parsing, persistence, cancellation and timeout failures must not orphan
        # the actual CLI/tools. The caller records the corresponding job outcome.
        _terminate(process)
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        with _process_lock:
            _processes.pop(key, None)
        owner.settled.set()
