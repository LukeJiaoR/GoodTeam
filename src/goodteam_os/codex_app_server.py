"""[INPUT]: Codex app-server 子进程、事件回调与工具回调。
[OUTPUT]: 原生线程、执行轮次和 JSONL 连接状态。
[POS]: Manager 原生会话的双向传输层，不拥有业务状态。
[PROTOCOL]: Update this header on change, then check AGENTS.md

Bidirectional stdio client for Codex app-server's JSONL protocol.

Only transport and native thread/turn identities live here. GoodTeam's task
services own tool authorization, dispatch, and recovery. A lost acknowledgement
is never retried automatically: a turn or a tool may already have taken effect.
Protocol: https://learn.chatgpt.com/docs/app-server
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import re
import select
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4


class CodexAppServerError(RuntimeError):
    failure_class = "codex_app_server_error"


class CodexAppServerExited(CodexAppServerError):
    failure_class = "codex_app_server_exited"


class CodexAppServerTimeout(CodexAppServerError):
    failure_class = "codex_app_server_timeout"


class CodexAppServerRejected(CodexAppServerError):
    def __init__(self, method: str, error: dict[str, Any]):
        self.method = method
        self.error = error
        super().__init__(f"Codex app-server {method}: {error.get('message', 'request rejected')}")


@dataclass
class _Connection:
    process: subprocess.Popen
    condition: threading.Condition = field(default_factory=threading.Condition)
    write_lock: threading.Lock = field(default_factory=threading.Lock)
    pending: dict[str, dict] = field(default_factory=dict)
    expired: set[str] = field(default_factory=set)
    turns: OrderedDict = field(default_factory=OrderedDict)
    events: queue.Queue = field(default_factory=queue.Queue)
    threads: list[threading.Thread] = field(default_factory=list)
    closed: threading.Event = field(default_factory=threading.Event)
    error: CodexAppServerError | None = None
    event_error: CodexAppServerError | None = None
    initialized: bool = False


class CodexAppServer:
    """One local app-server process, safe for concurrent request callers.

    ``on_event(record)`` runs in order on a dedicated event thread. Server
    requests call ``on_request(method, params)`` off the stdout reader so a tool
    can issue a nested request and Stop/steer can proceed while a tool runs.
    Callbacks must perform bounded operations; they do not own this transport.
    ``wait_turn`` returns only after the completion event callback has run.
    """

    def __init__(
        self,
        command: Sequence[str] = ("codex", "app-server"),
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        stderr_path: Path | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_request: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        from goodteam_os.database import provider_environment

        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = provider_environment(env)
        self.stderr_path = Path(stderr_path) if stderr_path else None
        self.on_event = on_event
        self.on_request = on_request
        self._connection: _Connection | None = None
        self._lifecycle_lock = threading.RLock()
        self._secrets = tuple(
            value for key, value in self.env.items()
            if len(value) >= 8 and re.search(r"(?i)(token|secret|password|api.?key)", key)
        )

    @property
    def pid(self) -> int | None:
        conn = self._connection
        return conn.process.pid if conn else None

    @property
    def alive(self) -> bool:
        conn = self._connection
        return bool(conn and not conn.closed.is_set() and conn.process.poll() is None and conn.error is None)

    def start(self, *, timeout: float = 30.0) -> None:
        with self._lifecycle_lock:
            if self.alive and self._connection.initialized:
                return
            if self._connection is not None:
                self.close()
            try:
                process = subprocess.Popen(
                    self.command, cwd=self.cwd, env=self.env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE if self.stderr_path else subprocess.DEVNULL,
                    bufsize=0,
                )
            except OSError as exc:
                raise CodexAppServerError(f"Cannot start Codex app-server: {self._redact(str(exc))}") from exc
            conn = self._connection = _Connection(process)
            for target, name in ((self._read_stdout, "reader"), (self._dispatch_events, "events")):
                thread = threading.Thread(target=target, args=(conn,), name=f"goodteam-codex-{name}", daemon=True)
                conn.threads.append(thread)
                thread.start()
            if self.stderr_path:
                thread = threading.Thread(target=self._read_stderr, args=(conn,), name="goodteam-codex-stderr", daemon=True)
                conn.threads.append(thread)
                thread.start()
            try:
                self.request("initialize", {
                    "clientInfo": {"name": "goodteam_manager", "title": "GoodTeam Manager", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                }, timeout=timeout)
                self._write(conn, {"method": "initialized", "params": {}}, timeout=timeout)
                conn.initialized = True
            except BaseException:
                self.close()
                raise

    def request(self, method: str, params: dict | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        conn = self._connection
        if conn is None or conn.closed.is_set():
            raise CodexAppServerExited("Codex app-server is not running")
        request_id = f"gt-{uuid4()}"
        holder: dict[str, Any] = {}
        deadline = time.monotonic() + timeout
        with conn.condition:
            if conn.error:
                raise conn.error
            conn.pending[request_id] = holder
        try:
            self._write(conn, {"id": request_id, "method": method, "params": params or {}}, timeout=timeout)
            with conn.condition:
                while not holder and conn.error is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # Late responses belong to this timed-out request, not
                        # a future retry. The caller must reconcile its effect.
                        conn.expired.add(request_id)
                        raise CodexAppServerTimeout(f"Codex app-server {method} acknowledgement timed out")
                    conn.condition.wait(remaining)
                if not holder:
                    raise conn.error or CodexAppServerExited("Codex app-server closed")
                if "error" in holder:
                    error = dict(holder["error"])
                    error["message"] = self._redact(str(error.get("message", "request rejected")))
                    raise CodexAppServerRejected(method, error)
                result = holder.get("result")
                if not isinstance(result, dict):
                    raise CodexAppServerError(f"Codex app-server {method} returned a non-object result")
                return result
        finally:
            with conn.condition:
                conn.pending.pop(request_id, None)

    def start_thread(self, **params) -> dict:
        return self.request("thread/start", params)

    def resume_thread(self, thread_id: str, **params) -> dict:
        return self.request("thread/resume", {**params, "threadId": thread_id})

    def read_thread(self, thread_id: str, **params) -> dict:
        return self.request("thread/read", {**params, "threadId": thread_id})

    def start_turn(self, thread_id: str, input: list[dict], **params) -> dict:
        return self.request("turn/start", {**params, "threadId": thread_id, "input": input})

    def steer_turn(self, thread_id: str, turn_id: str, input: list[dict]) -> dict:
        return self.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id, "input": input})

    def interrupt_turn(self, thread_id: str, turn_id: str) -> dict:
        return self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def wait_turn(self, thread_id: str, turn_id: str, timeout: float | None = None) -> dict:
        conn = self._connection
        if conn is None:
            raise CodexAppServerExited("Codex app-server is not running")
        deadline = None if timeout is None else time.monotonic() + timeout
        key = (thread_id, turn_id)
        with conn.condition:
            while key not in conn.turns:
                if conn.event_error:
                    raise conn.event_error
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise CodexAppServerTimeout(f"Codex app-server turn {turn_id} did not finish")
                conn.condition.wait(remaining)
            return conn.turns[key]

    def close(self, *, grace: float = 1.0) -> None:
        with self._lifecycle_lock:
            conn = self._connection
            if conn is None or conn.closed.is_set():
                return
            conn.closed.set()
            error = CodexAppServerExited("Codex app-server connection closed")
            with conn.condition:
                conn.error = conn.error or error
                conn.event_error = conn.event_error or error
                conn.condition.notify_all()
            conn.events.put(error)
            process = conn.process
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace)
            for thread in conn.threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=grace)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def _write(self, conn: _Connection, record: dict, *, timeout: float = 30.0) -> None:
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        deadline = time.monotonic() + timeout
        offset = 0
        if not conn.write_lock.acquire(timeout=max(timeout, 0)):
            raise CodexAppServerTimeout("Codex app-server input is busy")
        try:
            if conn.error:
                raise conn.error
            if conn.closed.is_set() or conn.process.poll() is not None:
                raise CodexAppServerExited("Codex app-server exited before input was written")
            fd = conn.process.stdin.fileno()
            os.set_blocking(fd, False)
            while offset < len(encoded):
                if conn.closed.is_set():
                    raise CodexAppServerExited("Codex app-server closed during input write")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexAppServerTimeout("Codex app-server input write timed out")
                if not select.select([], [fd], [], min(remaining, 0.1))[1]:
                    continue
                try:
                    written = os.write(fd, encoded[offset:])
                    if written <= 0:
                        raise CodexAppServerExited("Codex app-server input made no progress")
                    offset += written
                except BlockingIOError:
                    continue
        except CodexAppServerError as exc:
            if offset:
                # A partial JSON line cannot share a pipe with the next request.
                # Terminate this connection and preserve its unknown outcome.
                with conn.condition:
                    conn.error = conn.error or exc
                    conn.condition.notify_all()
                if conn.process.poll() is None:
                    conn.process.terminate()
            raise
        except (OSError, ValueError) as exc:
            raise CodexAppServerExited("Codex app-server input pipe closed") from exc
        finally:
            conn.write_lock.release()

    def _read_stdout(self, conn: _Connection) -> None:
        failure: CodexAppServerError = CodexAppServerExited("Codex app-server stdout closed")
        try:
            while not conn.closed.is_set():
                line = conn.process.stdout.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                if not line.endswith(b"\n") or len(line) > 16 * 1024 * 1024:
                    raise CodexAppServerError("Codex app-server returned an oversized or incomplete JSONL record")
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise CodexAppServerError("Codex app-server returned malformed JSONL") from exc
                if not isinstance(record, dict):
                    raise CodexAppServerError("Codex app-server returned a non-object JSONL record")
                if "method" in record:
                    if "id" in record:
                        threading.Thread(target=self._serve_request, args=(conn, record), daemon=True,
                                         name="goodteam-codex-tool").start()
                    else:
                        conn.events.put(record)
                elif "id" in record:
                    with conn.condition:
                        holder = conn.pending.get(record["id"])
                        if holder is not None:
                            holder.update(record)
                            conn.condition.notify_all()
                        elif record["id"] in conn.expired:
                            conn.expired.discard(record["id"])
                        else:
                            raise CodexAppServerError("Codex app-server returned an unknown response id")
                else:
                    raise CodexAppServerError("Codex app-server returned an invalid JSON-RPC record")
        except (OSError, ValueError, CodexAppServerError) as exc:
            failure = exc if isinstance(exc, CodexAppServerError) else CodexAppServerExited("Codex app-server output pipe closed")
            if conn.process.poll() is None:
                conn.process.terminate()
        finally:
            with conn.condition:
                conn.error = conn.error or failure
                conn.condition.notify_all()
            # Process already queued events before delivering the disconnect to
            # waiters. Fast final events must survive an immediate process exit.
            conn.events.put(failure)

    def _dispatch_events(self, conn: _Connection) -> None:
        while True:
            record = conn.events.get()
            if isinstance(record, CodexAppServerError):
                with conn.condition:
                    conn.event_error = conn.event_error or record
                    conn.condition.notify_all()
                return
            try:
                if self.on_event is not None:
                    self.on_event(record)
            except Exception as exc:
                error = CodexAppServerError(f"Codex event handler failed: {self._redact(str(exc))}")
                with conn.condition:
                    conn.error = conn.event_error = error
                    conn.condition.notify_all()
                if conn.process.poll() is None:
                    conn.process.terminate()
                return
            if record.get("method") == "turn/completed":
                params = record.get("params") or {}
                turn = params.get("turn") or {}
                key = (params.get("threadId"), turn.get("id"))
                with conn.condition:
                    conn.turns[key] = turn
                    # Keep completion receipts long enough for late callers,
                    # without growing with an indefinitely reused conversation.
                    while len(conn.turns) > 256:
                        conn.turns.popitem(last=False)
                    conn.condition.notify_all()

    def _serve_request(self, conn: _Connection, record: dict) -> None:
        if conn.closed.is_set():
            return
        try:
            if self.on_request is None:
                response = {"id": record["id"], "error": {"code": -32601, "message": "Client request is not supported"}}
            else:
                result = self.on_request(record["method"], record.get("params") or {})
                response = {"id": record["id"], "result": result}
        except Exception as exc:
            response = {"id": record["id"], "error": {"code": -32603, "message": self._redact(str(exc))}}
        try:
            self._write(conn, response)
        except CodexAppServerError:
            # The connection may have been stopped during a tool. Never send
            # that old result into a replacement connection.
            pass

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "<redacted>")
        value = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1<redacted>", value)
        value = re.sub(r"(?i)(api[_-]?key|access[_-]?token|authorization|cookie|password)(\s*[:=]\s*)([^\s,;]+)",
                       r"\1\2<redacted>", value)
        value = re.sub(r"\b(?:sk|gho|ghp|xox[baprs])-[A-Za-z0-9_-]{12,}\b", "<redacted-token>", value)
        return value[:16384]

    def _read_stderr(self, conn: _Connection) -> None:
        try:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            with self.stderr_path.open("a", encoding="utf-8") as handle:
                remaining = 131072
                while not conn.closed.is_set():
                    line = conn.process.stderr.readline(65536)
                    if not line:
                        return
                    if len(line) == 65536 and not line.endswith(b"\n"):
                        # Do not log split credentials across chunk boundaries.
                        while line and not line.endswith(b"\n"):
                            line = conn.process.stderr.readline(65536)
                        line = b"[oversized stderr line omitted]\n"
                    if remaining > 0:
                        safe = self._redact(line.decode("utf-8", errors="replace"))[:remaining]
                        handle.write(safe)
                        handle.flush()
                        remaining -= len(safe)
        except (OSError, ValueError):
            # Diagnostics never block protocol progress or leak through stderr.
            return
