"""Persistent worker-session contract and Pi RPC implementation."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any, Protocol

from goodteam_os.pi_rpc import PiRpcError, PiRpcExited, PiRpcProcess, PiRpcRejected, monotonic_deadline, redact_pi_text


PI_CAPABILITIES = (
    "persistent_session",
    "resume",
    "steer",
    "follow_up",
    "abort",
    "event_stream",
    "entry_replay",
)
MEANINGFUL_EVENTS = {
    "agent_start": "turn_started",
    "agent_end": "turn_ended",
    "agent_settled": "settled",
    "turn_start": "turn_started",
    "turn_end": "turn_ended",
    "tool_execution_start": "tool_started",
    "tool_execution_end": "tool_ended",
    "extension_error": "failure",
    "auto_retry_start": "retry_started",
    "auto_retry_end": "retry_ended",
    "compaction_start": "compaction_started",
    "compaction_end": "compaction_ended",
}


@dataclass(frozen=True)
class SessionRuntimeCapabilities:
    persistent_session: bool = True
    resume: bool = True
    steer: bool = True
    follow_up: bool = True
    abort: bool = True
    event_stream: bool = True
    entry_replay: bool = True


@dataclass(frozen=True)
class SessionRuntimeSpec:
    name: str
    aliases: tuple[str, ...]
    label: str
    runtime_kind: str
    capabilities: SessionRuntimeCapabilities


class SessionRuntime(Protocol):
    def start_or_resume(self) -> dict[str, Any]: ...
    def send_prompt(self, message: str) -> dict[str, Any]: ...
    def send_resume_prompt(self, message: str) -> dict[str, Any]: ...
    def steer(self, message: str) -> dict[str, Any]: ...
    def follow_up(self, message: str) -> dict[str, Any]: ...
    def abort(self) -> dict[str, Any]: ...
    def get_state(self) -> dict[str, Any]: ...
    def get_entries(self) -> dict[str, Any]: ...
    def get_last_assistant_text(self) -> str | None: ...
    def wait_until_settled(self, timeout: float, tick: Callable[[], None] | None = None) -> None: ...
    def close(self) -> None: ...


PI_RUNTIME_SPEC = SessionRuntimeSpec(
    name="pi",
    aliases=("pi", "pi-rpc"),
    label="Pi RPC",
    runtime_kind="persistent_session",
    capabilities=SessionRuntimeCapabilities(),
)
SESSION_BACKENDS = {alias: PI_RUNTIME_SPEC for alias in PI_RUNTIME_SPEC.aliases}


class PiSessionMissing(PiRpcError):
    failure_class = "pi_session_missing"


class PiSessionMismatch(PiRpcError):
    failure_class = "pi_session_mismatch"


class PiResumeFailed(PiRpcError):
    failure_class = "pi_resume_failed"


class PiAgentFailed(PiRpcError):
    failure_class = "pi_agent_failed"


@dataclass(frozen=True)
class PiRuntimeConfig:
    binary: str
    provider: str
    model: str
    thinking_level: str
    session_dir: Path
    request_timeout: float = 30.0


def pi_runtime_config(project_root: Path, overrides: Mapping[str, object] | None = None) -> PiRuntimeConfig:
    values = overrides or {}
    binary = str(values.get("pi_path") or os.getenv("GOODTEAM_PI_PATH") or "pi")
    provider = str(values.get("pi_provider") or os.getenv("GOODTEAM_PI_PROVIDER") or "").strip()
    model = str(values.get("pi_model") or os.getenv("GOODTEAM_PI_MODEL") or "").strip()
    if model.lower() == "auto":
        model = ""
    thinking = str(values.get("pi_thinking_level") or os.getenv("GOODTEAM_PI_THINKING_LEVEL") or "").strip()
    configured_dir = str(values.get("pi_session_dir") or os.getenv("GOODTEAM_PI_SESSION_DIR") or "").strip()
    session_dir = Path(configured_dir).expanduser() if configured_dir else project_root / ".goodteam" / "pi" / "sessions"
    request_timeout = float(values.get("pi_request_timeout") or os.getenv("GOODTEAM_PI_REQUEST_TIMEOUT") or 30.0)
    return PiRuntimeConfig(binary, provider, model, thinking, session_dir.resolve(), request_timeout)


def pi_backend_doctor(project_root: Path | None = None, overrides: Mapping[str, object] | None = None) -> dict[str, Any]:
    root = (project_root or Path.cwd()).resolve()
    config = pi_runtime_config(root, overrides)
    resolved = which(config.binary) or (str(Path(config.binary).resolve()) if Path(config.binary).is_file() else "")
    available = bool(resolved)
    version = ""
    if available:
        import subprocess

        try:
            completed = subprocess.run(
                [resolved, "--version"], capture_output=True, text=True, check=False, timeout=5
            )
            if completed.returncode == 0:
                version = redact_pi_text((completed.stdout or completed.stderr).strip().splitlines()[0], limit=160)
        except (OSError, IndexError, subprocess.TimeoutExpired):
            version = ""
    writable = _directory_writable(config.session_dir)
    ready = available and bool(version) and writable
    return {
        "mode": "pi",
        "kind": "session",
        "runtime_kind": PI_RUNTIME_SPEC.runtime_kind,
        "capabilities": list(PI_CAPABILITIES),
        "command": "pi",
        "command_path": resolved,
        "available": available,
        "version": version,
        "provider": config.provider,
        "model": config.model or "auto",
        "thinking_level": config.thinking_level or "default",
        "session_directory": str(config.session_dir),
        "session_directory_writable": writable,
        "ready": ready,
        "hint": (
            "Pi RPC is ready. Authentication remains owned by Pi."
            if ready
            else "Pi is not ready. Install with `npm install -g @earendil-works/pi-coding-agent` and ensure the local session directory is writable."
        ),
    }


def _directory_writable(path: Path) -> bool:
    if not path.exists():
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    if not path.is_dir():
        return False
    try:
        fd, probe = tempfile.mkstemp(prefix=".goodteam-write-", dir=path)
        os.close(fd)
        Path(probe).unlink()
        return True
    except OSError:
        return False


class PiRpcSessionStore:
    def __init__(self, job_root: Path, session_root: Path) -> None:
        self.job_root = job_root
        self.session_root = session_root.resolve()
        self.metadata_path = job_root / "pi_session.json"
        self.protocol_path = job_root / "pi_protocol.jsonl"
        self.events_path = job_root / "pi_events.jsonl"
        self.entries_path = job_root / "pi_entries.jsonl"
        self.controls_path = job_root / "pi_controls.jsonl"
        self._lock = threading.Lock()

    def read_metadata(self) -> dict[str, Any] | None:
        if not self.metadata_path.is_file():
            return None
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def write_metadata(self, payload: dict[str, Any]) -> None:
        payload = {**payload, "updated_at": _now()}
        _atomic_json(self.metadata_path, payload)

    def append_protocol(self, record: dict[str, Any]) -> None:
        self._append(self.protocol_path, {"recorded_at": _now(), **record})
        event_type = str(record.get("type", ""))
        normalized = MEANINGFUL_EVENTS.get(event_type)
        if normalized:
            self.append_event(normalized, {"pi_event": event_type})

    def append_event(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self._append(self.events_path, {"recorded_at": _now(), "event": event, **(payload or {})})

    def append_entries(self, entries: list[dict[str, Any]]) -> str:
        existing = self._entry_ids()
        last = ""
        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if not entry_id:
                continue
            last = entry_id
            if entry_id in existing:
                continue
            self._append(self.entries_path, entry)
            existing.add(entry_id)
        return last

    def validate_session_file(self, value: str) -> Path:
        if not value:
            raise ValueError("Pi session file is missing")
        path = Path(value).expanduser().resolve()
        if not path.is_relative_to(self.session_root):
            raise ValueError("Pi session file is outside the configured session root")
        if not path.is_file():
            raise FileNotFoundError(f"Pi session file does not exist: {path.name}")
        return path

    def _entry_ids(self) -> set[str]:
        if not self.entries_path.is_file():
            return set()
        ids: set[str] = set()
        for line in self.entries_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("id"):
                ids.add(str(entry["id"]))
        return ids

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()


class PiSessionRuntime:
    def __init__(
        self,
        *,
        project_root: Path,
        job_root: Path,
        job: dict[str, Any],
        runtime_cwd: Path,
        config: PiRuntimeConfig,
        stderr_path: Path,
    ) -> None:
        self.project_root = project_root.resolve()
        self.job_root = job_root
        self.job = job
        self.runtime_cwd = runtime_cwd.resolve()
        self.config = config
        self.store = PiRpcSessionStore(job_root, config.session_dir)
        self.stderr_path = stderr_path
        self.rpc: PiRpcProcess | None = None
        self.metadata: dict[str, Any] = {}
        self.aborted = False
        self.already_settled = False
        self.agent_error = ""

    def start_or_resume(self) -> dict[str, Any]:
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        policy = str(self.job.get("session_policy") or "new")
        existing = self.store.read_metadata()
        explicit_ref = str(self.job.get("worker_session_ref") or "").strip()
        resume = policy == "resume" or (existing is not None and bool(existing.get("initial_prompt_sent")))
        if policy == "resume" and not explicit_ref and existing is None:
            raise PiSessionMissing("Pi resume requested without a valid worker_session_ref")
        source = existing
        if explicit_ref:
            source_path = (self.project_root / ".goodteam" / "workers" / "jobs" / explicit_ref / "pi_session.json").resolve()
            jobs_root = (self.project_root / ".goodteam" / "workers" / "jobs").resolve()
            if not source_path.is_relative_to(jobs_root) or not source_path.is_file():
                raise PiSessionMissing("Pi session reference is missing or untrusted")
            source = json.loads(source_path.read_text(encoding="utf-8"))
            self._validate_lineage(source)
            resume = True
        command = [self.config.binary, "--mode", "rpc", "--name", f"goodteam-{self.job['id']}", "--session-dir", str(self.config.session_dir)]
        if self.config.provider:
            command.extend(["--provider", self.config.provider])
        if self.config.model:
            command.extend(["--model", self.config.model])
        self.rpc = PiRpcProcess(command, cwd=self.runtime_cwd, stderr_path=self.stderr_path, on_record=self._on_record)
        self.rpc.start()
        now = _now()
        self.metadata = {
            "schema_version": "pi_session.v1",
            "adapter": "pi",
            "job_id": self.job["id"],
            "execution_id": str(self.job.get("execution_id") or self.job.get("run_id") or ""),
            "project_key": str(self.job.get("project_key") or ""),
            "role": str(self.job.get("role") or ""),
            "runtime_cwd": str(self.runtime_cwd),
            "session_name": f"goodteam-{self.job['id']}",
            "session_file": "",
            "session_id": "",
            "provider": self.config.provider,
            "model": self.config.model or "auto",
            "thinking_level": self.config.thinking_level or "default",
            "last_entry_id": "",
            "leaf_id": "",
            "status": "starting",
            "started_at": now,
            "updated_at": now,
            "settled_at": "",
            "last_error": "",
            "initial_prompt_sent": False,
            "pid": self.rpc.pid,
        }
        if source:
            for key in ("last_entry_id", "leaf_id", "initial_prompt_sent"):
                self.metadata[key] = source.get(key, self.metadata[key])
        self.store.write_metadata(self.metadata)
        if resume:
            if not source:
                raise PiSessionMissing("Pi session metadata is missing")
            try:
                session_file = self.store.validate_session_file(str(source.get("session_file", "")))
            except (ValueError, FileNotFoundError) as exc:
                raise PiSessionMissing(str(exc)) from exc
            try:
                response = self.rpc.request({"type": "switch_session", "sessionPath": str(session_file)}, timeout=self.config.request_timeout)
            except PiRpcError as exc:
                raise PiResumeFailed(str(exc)) from exc
            if bool(response.get("data", {}).get("cancelled")):
                raise PiResumeFailed("Pi session resume was cancelled")
            self.store.append_event("session_resumed", {"source_job_id": str(source.get("job_id", ""))})
        if self.config.thinking_level:
            self.rpc.request(
                {"type": "set_thinking_level", "level": self.config.thinking_level}, timeout=self.config.request_timeout
            )
        state = self.get_state()
        self.already_settled = bool(
            resume
            and source
            and source.get("status") == "settled"
            and not state.get("isStreaming")
            and not int(state.get("pendingMessageCount") or 0)
        )
        if resume and source and source.get("initial_prompt_sent") and not state.get("isStreaming") and not self.already_settled:
            last_text = self.get_last_assistant_text()
            if last_text:
                self.already_settled = True
            else:
                raise PiResumeFailed(
                    "Pi session recovery is ambiguous; the initial prompt was not repeated and no settled assistant result exists"
                )
        self.metadata["status"] = "running"
        self.store.write_metadata(self.metadata)
        self.store.append_event("session_started", {"resumed": resume})
        return state

    def send_prompt(self, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("Pi prompt must not be empty")
        rpc = self._rpc()
        rpc.clear_settled()
        response = rpc.request({"type": "prompt", "message": message}, timeout=self.config.request_timeout)
        self.metadata["initial_prompt_sent"] = True
        self.store.write_metadata(self.metadata)
        self.store.append_event("prompt_accepted")
        return response

    def send_resume_prompt(self, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("Pi resume prompt must not be empty")
        rpc = self._rpc()
        rpc.clear_settled()
        self.already_settled = False
        response = rpc.request({"type": "prompt", "message": message}, timeout=self.config.request_timeout)
        self.store.append_event("resume_prompt_accepted")
        return response

    def steer(self, message: str) -> dict[str, Any]:
        return self._message_control("steer", message)

    def follow_up(self, message: str) -> dict[str, Any]:
        return self._message_control("follow_up", message)

    def abort(self) -> dict[str, Any]:
        if self.aborted:
            return {"type": "response", "command": "abort", "success": True, "idempotent": True}
        response = self._rpc().request({"type": "abort"}, timeout=min(self.config.request_timeout, 5.0))
        self.aborted = True
        self.metadata["status"] = "aborted"
        self.store.write_metadata(self.metadata)
        self.store.append_event("abort")
        return response

    def get_state(self) -> dict[str, Any]:
        response = self._rpc().request({"type": "get_state"}, timeout=self.config.request_timeout)
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        session_file = str(data.get("sessionFile") or "")
        if session_file:
            path = Path(session_file).expanduser().resolve()
            if not path.is_relative_to(self.config.session_dir):
                raise PiSessionMismatch("Pi returned a session file outside the configured session root")
        self.metadata.update(
            {
                "session_file": session_file,
                "session_id": str(data.get("sessionId") or ""),
                "provider": _model_value(data, "provider") or self.metadata.get("provider", ""),
                "model": _model_value(data, "id") or self.metadata.get("model", "auto"),
                "thinking_level": str(data.get("thinkingLevel") or self.metadata.get("thinking_level", "default")),
                "isStreaming": bool(data.get("isStreaming")),
                "isCompacting": bool(data.get("isCompacting")),
                "pendingMessageCount": int(data.get("pendingMessageCount") or 0),
            }
        )
        self.store.write_metadata(self.metadata)
        return data

    def get_entries(self) -> dict[str, Any]:
        cursor = str(self.metadata.get("last_entry_id") or "")
        command: dict[str, Any] = {"type": "get_entries"}
        if cursor:
            command["since"] = cursor
        try:
            response = self._rpc().request(command, timeout=self.config.request_timeout)
        except PiRpcRejected:
            if not cursor:
                raise
            self.store.append_event("entry_cursor_invalid", {"cursor": cursor})
            response = self._rpc().request({"type": "get_entries"}, timeout=self.config.request_timeout)
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        last_persisted = self.store.append_entries([entry for entry in entries if isinstance(entry, dict)])
        if last_persisted:
            self.metadata["last_entry_id"] = last_persisted
        self.metadata["leaf_id"] = str(data.get("leafId") or "")
        self.store.write_metadata(self.metadata)
        return data

    def get_last_assistant_text(self) -> str | None:
        response = self._rpc().request({"type": "get_last_assistant_text"}, timeout=self.config.request_timeout)
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        text = data.get("text")
        return text if isinstance(text, str) and text.strip() else None

    def get_session_stats(self) -> dict[str, Any]:
        response = self._rpc().request({"type": "get_session_stats"}, timeout=self.config.request_timeout)
        return response.get("data") if isinstance(response.get("data"), dict) else {}

    def wait_until_settled(self, timeout: float, tick: Callable[[], None] | None = None) -> None:
        if self.already_settled:
            self.metadata["status"] = "settled"
            self.metadata["settled_at"] = _now()
            self.store.write_metadata(self.metadata)
            return
        deadline = monotonic_deadline(timeout)
        while time.monotonic() < deadline:
            if tick is not None:
                tick()
            rpc = self._rpc()
            if rpc.wait_settled(min(0.2, max(0.01, deadline - time.monotonic()))):
                if self.agent_error and not self.aborted:
                    raise PiAgentFailed(self.agent_error)
                self.metadata["status"] = "aborted" if self.aborted else "settled"
                self.metadata["settled_at"] = _now()
                self.store.write_metadata(self.metadata)
                return
            if not rpc.alive:
                raise PiRpcExited("Pi RPC process exited before agent_settled")
        try:
            self.abort()
        except PiRpcError:
            pass
        raise TimeoutError(f"Pi agent did not settle within {timeout:g}s")

    def close(self) -> None:
        if self.rpc is not None:
            self.rpc.close(abort=self.metadata.get("status") == "running")

    def _message_control(self, command: str, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError(f"Pi {command} message must not be empty")
        if self.metadata.get("status") != "running":
            raise PiRpcError(f"Pi session is not active ({self.metadata.get('status', 'unknown')})")
        if command == "follow_up":
            self._rpc().clear_settled()
            self.already_settled = False
        response = self._rpc().request({"type": command, "message": message}, timeout=self.config.request_timeout)
        self.store.append_event(f"{command}_accepted")
        return response

    def _validate_lineage(self, source: Mapping[str, Any]) -> None:
        source_project = str(source.get("project_key") or "")
        project = str(self.job.get("project_key") or "")
        if source_project != project:
            raise PiSessionMismatch("Pi session reference belongs to a different project")
        source_execution = str(source.get("execution_id") or "")
        execution = str(self.job.get("execution_id") or self.job.get("run_id") or "")
        if source_execution != execution and str(source.get("job_id")) != str(self.job.get("id")):
            raise PiSessionMismatch("Pi session reference belongs to a different execution lineage")

    def _on_record(self, record: dict[str, Any]) -> None:
        self.store.append_protocol(record)
        if record.get("type") == "extension_error":
            self.agent_error = str(record.get("error") or record.get("message") or "Pi extension failed")[:500]
        update = record.get("assistantMessageEvent")
        if isinstance(update, dict) and update.get("type") == "error" and update.get("reason") == "error":
            self.agent_error = str(update.get("error") or "Pi agent failed")[:500]

    def _rpc(self) -> PiRpcProcess:
        if self.rpc is None:
            raise PiRpcError("Pi RPC process has not started")
        return self.rpc


def _model_value(data: Mapping[str, Any], key: str) -> str:
    model = data.get("model")
    return str(model.get(key) or "") if isinstance(model, dict) else ""


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def capabilities_dict() -> dict[str, bool]:
    return asdict(PI_RUNTIME_SPEC.capabilities)
