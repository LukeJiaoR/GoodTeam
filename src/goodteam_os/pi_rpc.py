"""Strict JSONL subprocess client for Pi RPC mode."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4


class PiRpcError(RuntimeError):
    failure_class = "pi_protocol_error"


class PiRpcStartError(PiRpcError):
    failure_class = "pi_process_start_failed"


class PiRpcTimeout(PiRpcError):
    failure_class = "pi_request_timeout"


class PiRpcExited(PiRpcError):
    failure_class = "pi_process_exited"


class PiRpcRejected(PiRpcError):
    def __init__(self, command: str, response: dict[str, Any]):
        message = str(response.get("error") or response.get("message") or "request rejected")
        super().__init__(f"Pi RPC {command} failed: {message}")
        self.command = command
        self.response = response


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|authorization|cookie|password)(\s*[:=]\s*)([^\s,;]+)"
)


def redact_pi_text(value: str, *, limit: int = 16_384) -> str:
    """Mask common credential shapes and bound diagnostic evidence."""
    masked = _SECRET_RE.sub(r"\1\2<redacted>", value)
    masked = re.sub(r"\b(?:sk|gho|ghp|xox[baprs])-[A-Za-z0-9_-]{12,}\b", "<redacted-token>", masked)
    return masked[:limit]


class PiRpcProcess:
    """Thread-safe Pi RPC client with one LF-only stdout reader."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        stderr_path: Path,
        on_record: Callable[[dict[str, Any]], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.stderr_path = stderr_path
        self.on_record = on_record
        self.env = env
        self.process: subprocess.Popen[bytes] | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._closed = threading.Event()
        self._settled = threading.Event()
        self._protocol_error: PiRpcError | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.process is not None:
            return
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self.stderr_path.write_text("", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
            )
        except OSError as exc:
            raise PiRpcStartError(f"Unable to start Pi RPC: {exc}") from exc
        self._reader = threading.Thread(target=self._read_stdout, name="goodteam-pi-rpc", daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="goodteam-pi-stderr", daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def request(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        if not self.alive:
            raise PiRpcExited("Pi RPC process is not running")
        request_id = str(command.get("id") or f"gt-{uuid4()}")
        payload = {**command, "id": request_id}
        event = threading.Event()
        response_holder: dict[str, Any] = {}
        with self._pending_lock:
            self._pending[request_id] = (event, response_holder)
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            with self._write_lock:
                process = self.process
                if process is None or process.stdin is None or process.poll() is not None:
                    raise PiRpcExited("Pi RPC process exited before request write")
                process.stdin.write(encoded)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise PiRpcExited(f"Pi RPC request write failed: {exc}") from exc
        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise PiRpcTimeout(f"Pi RPC {payload.get('type', 'request')} timed out after {timeout:g}s")
        if self._protocol_error is not None:
            raise self._protocol_error
        response = response_holder.get("response")
        if not isinstance(response, dict):
            raise PiRpcExited("Pi RPC exited without a response")
        if response.get("success") is not True:
            raise PiRpcRejected(str(payload.get("type", "request")), response)
        return response

    def wait_settled(self, timeout: float) -> bool:
        return self._settled.wait(timeout)

    def clear_settled(self) -> None:
        self._settled.clear()

    def close(self, *, abort: bool = False, grace: float = 2.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        process = self.process
        if process is None:
            return
        if abort and process.poll() is None:
            try:
                self.request({"type": "abort"}, timeout=min(grace, 1.0))
            except PiRpcError:
                pass
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace)
        for thread in (self._reader, self._stderr_reader):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=grace)
        self._wake_pending(PiRpcExited("Pi RPC process closed"))

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        buffer = b""
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    self._handle_line(raw[:-1] if raw.endswith(b"\r") else raw)
            if buffer:
                self._handle_line(buffer[:-1] if buffer.endswith(b"\r") else buffer)
        except OSError as exc:
            if not self._closed.is_set():
                self._set_protocol_error(PiRpcError(f"Pi RPC stdout read failed: {exc}"))
        finally:
            if not self._closed.is_set() and self._protocol_error is None:
                code = process.poll()
                self._wake_pending(PiRpcExited(f"Pi RPC process exited (code {code})"))

    def _handle_line(self, raw: bytes) -> None:
        if not raw.strip():
            return
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            sample = redact_pi_text(raw.decode("utf-8", errors="replace"), limit=512)
            self._set_protocol_error(PiRpcError(f"Malformed Pi RPC JSON: {sample!r}: {exc}"))
            return
        if not isinstance(record, dict):
            self._set_protocol_error(PiRpcError("Pi RPC record must be a JSON object"))
            return
        if self.on_record is not None:
            self.on_record(record)
        if record.get("type") == "response":
            request_id = record.get("id")
            if not isinstance(request_id, str):
                self._set_protocol_error(PiRpcError("Pi RPC response omitted request id"))
                return
            with self._pending_lock:
                pending = self._pending.pop(request_id, None)
            if pending is None:
                self._set_protocol_error(PiRpcError(f"Unknown Pi RPC response id: {request_id}"))
                return
            event, holder = pending
            holder["response"] = record
            event.set()
        elif record.get("type") == "agent_settled":
            self._settled.set()

    def _set_protocol_error(self, error: PiRpcError) -> None:
        if self._protocol_error is None:
            self._protocol_error = error
        self._wake_pending(error)

    def _wake_pending(self, error: PiRpcError) -> None:
        if self._protocol_error is None:
            self._protocol_error = error
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for event, _holder in pending:
            event.set()

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        with self.stderr_path.open("a", encoding="utf-8") as handle:
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                handle.write(redact_pi_text(chunk.decode("utf-8", errors="replace")))
                handle.flush()


def monotonic_deadline(seconds: float) -> float:
    return time.monotonic() + seconds
