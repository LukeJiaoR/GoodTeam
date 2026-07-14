from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from goodteam_os.graph import resolve_project_runtime_root
from goodteam_os.projects import load_project_target
from goodteam_os.session_runtimes import SESSION_BACKENDS
from goodteam_os.state import StateStore, normalize_project_key
from goodteam_os.workers import WorkerJobStore


@dataclass(frozen=True)
class ActivePiSession:
    project_key: str
    root: Path
    job: dict[str, object]
    status: dict[str, object]


class RuntimeControlResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _tail(value: object, length: int) -> str:
    text = str(value or "")
    return text if len(text) <= length else f"…{text[-length:]}"


class TelegramRuntimeControlService:
    """Resolve and control a project-bound Pi session without Telegram SDK types."""

    def __init__(
        self,
        workspace: Path,
        project_key: str,
        *,
        redact: Callable[[str], str] | None = None,
    ):
        self.workspace = workspace
        self.project_key = normalize_project_key(project_key) if project_key.strip() else ""
        self.redact = redact or (lambda value: value)

    def _configured_project_root(self) -> tuple[str, Path]:
        if not self.project_key:
            raise RuntimeControlResolutionError(
                "unknown_project",
                "No project is configured for this Telegram poller. Start it with an explicit project key.",
            )
        for config_path in sorted((self.workspace / "projects").glob("*.local.yaml")):
            try:
                target = load_project_target(config_path)
            except Exception:
                continue
            key = normalize_project_key(str(target.get("key", config_path.stem)))
            if key != self.project_key:
                continue
            repos = target.get("repos", {})
            if not isinstance(repos, dict) or not repos:
                raise RuntimeControlResolutionError(
                    "runtime_root_unavailable",
                    f"Project runtime root unavailable for {self.project_key}.",
                )
            root = resolve_project_runtime_root({str(name): Path(str(path)) for name, path in repos.items()})
            if root is None:
                raise RuntimeControlResolutionError(
                    "runtime_root_unavailable",
                    f"Project runtime root unavailable for {self.project_key}.",
                )
            return key, root
        raise RuntimeControlResolutionError("unknown_project", f"Unknown configured project: {self.project_key}.")

    def _session_candidates(self) -> list[ActivePiSession]:
        key, root, run_id = self._current_project_context()
        if not run_id:
            return []
        store = WorkerJobStore(root)
        sessions: list[ActivePiSession] = []
        for job in store.list_jobs():
            if str(job.get("run_id") or "") != run_id:
                continue
            job_project = str(job.get("project_key") or key)
            if normalize_project_key(job_project) != key:
                continue
            if str(job.get("adapter") or "") not in SESSION_BACKENDS:
                continue
            job_id = str(job.get("id") or "")
            try:
                status = store.session_status(job_id)
            except (OSError, ValueError):
                continue
            sessions.append(ActivePiSession(key, root, job, status))
        return sessions

    def _current_project_context(self) -> tuple[str, Path, str]:
        key, root = self._configured_project_root()
        state = StateStore(self.workspace).read_project_current(key)
        if state is None:
            return key, root, ""
        return key, root, str(state.run_id or "")

    def active_sessions(self) -> list[ActivePiSession]:
        return [
            session
            for session in self._session_candidates()
            if str(session.status.get("session_status") or "") == "running"
            and session.status.get("process_alive")
            and str(session.status.get("pi_session_id") or "")
        ]

    def resolve_one(self) -> ActivePiSession:
        candidates = self._session_candidates()
        sessions = [
            session
            for session in candidates
            if str(session.status.get("session_status") or "") == "running"
            and session.status.get("process_alive")
            and str(session.status.get("pi_session_id") or "")
        ]
        if not sessions:
            if len(candidates) == 1:
                status = str(candidates[0].status.get("session_status") or "unknown")
                if status == "aborted":
                    raise RuntimeControlResolutionError("session_aborted", "Pi session is aborted.")
                if status in {"settled", "completed"}:
                    raise RuntimeControlResolutionError("session_settled", "Pi session is already settled.")
                if status == "running":
                    raise RuntimeControlResolutionError(
                        "process_unhealthy",
                        "Pi session process is not alive or its heartbeat is stale.",
                    )
            raise RuntimeControlResolutionError("no_active_session", "No active Pi session.")
        if len(sessions) > 1:
            lines = ["Multiple active Pi sessions found. Choose one:"]
            for session in sessions:
                lines.append(
                    "- "
                    + " · ".join(
                        [
                            session.project_key,
                            str(session.job.get("role") or "unknown"),
                            _tail(session.job.get("id"), 8),
                        ]
                    )
                )
            raise RuntimeControlResolutionError("multiple_active_sessions", "\n".join(lines))
        return sessions[0]

    def status_section(self) -> str:
        try:
            session = self.resolve_one()
        except RuntimeControlResolutionError as exc:
            if exc.code == "no_active_session":
                return "Pi session\nNo active Pi session"
            return f"Pi session\n{exc}"
        status = session.status
        provider = str(status.get("provider") or "unknown")
        model = str(status.get("model") or "auto")
        return "\n".join(
            [
                "Pi session",
                f"Job: {_tail(status.get('job_id'), 8)}",
                f"Session: {_tail(status.get('pi_session_id'), 4)}",
                f"Status: {status.get('session_status', 'unknown')}",
                f"Streaming: {'yes' if status.get('isStreaming') else 'no'}",
                f"Compacting: {'yes' if status.get('isCompacting') else 'no'}",
                f"Pending messages: {status.get('pendingMessageCount', 0)}",
                f"Provider/model: {provider} / {model}",
                "Heartbeat: healthy",
                "Process: alive",
            ]
        )

    @staticmethod
    def stable_control_id(*, chat_id: object, message_id: object, user_id: str, command: str) -> str:
        identity = f"{chat_id}\0{message_id}\0{user_id}\0{command.strip().lower()}"
        return f"control-tg-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"

    def control(
        self,
        command: str,
        *,
        message: str = "",
        reason: str = "",
        chat_id: object,
        message_id: object,
        user_id: str,
    ) -> tuple[dict[str, object], str]:
        action = {"steer": "steer", "followup": "follow_up", "stop": "abort"}.get(command)
        if action is None:
            raise ValueError(f"Unsupported Telegram runtime control: {command}")
        control_id = self.stable_control_id(
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            command=command,
        )
        key, root, run_id = self._current_project_context()
        store = WorkerJobStore(root)
        existing = store.find_session_control(control_id, run_id=run_id, project_key=key) if run_id else None
        if existing is not None:
            job_id, request = existing
            if str(request.get("action") or "") != action:
                raise ValueError("Telegram control identity is already bound to another action")
            result = store.session_control(
                job_id,
                action,
                message=self.redact(message),
                reason=self.redact(reason),
                control_id=control_id,
            )
            return result, self.format_control_result(command, key, result)

        session = self.resolve_one()
        store = WorkerJobStore(session.root)
        result = store.session_control(
            str(session.job["id"]),
            action,
            message=self.redact(message),
            reason=self.redact(reason),
            actor=f"telegram:user:{user_id}",
            source="telegram",
            control_id=control_id,
            audit_context={
                "telegram_chat_id": str(chat_id),
                "telegram_message_id": str(message_id),
                "telegram_user_id": user_id,
                "command": command,
                "project_key": session.project_key,
            },
        )
        return result, self.format_control_result(command, session.project_key, result)

    def format_control_result(self, command: str, project_key: str, result: dict[str, object]) -> str:
        state = str(result.get("control_state") or "queued")
        label = {"steer": "Steering", "followup": "Follow-up", "stop": "Stop"}[command]
        if state == "rpc_accepted" and result.get("accepted"):
            lines = [
                f"✅ {label} accepted",
                f"Project: {project_key}",
                f"Job: {_tail(result.get('job_id'), 8)}",
                f"Session: {_tail(result.get('pi_session_id'), 4)}",
                f"Control: {_tail(result.get('control_id'), 4)}",
                "State: rpc_accepted",
            ]
            if command == "stop":
                lines.append("Current Pi task stopped. GoodTeam service remains running.")
            return "\n".join(lines)
        if state == "failed":
            reason = self.redact(str(result.get("error") or "Pi rejected the control command"))
            return f"❌ {label} failed\nReason: {reason}"
        return f"⏳ {label} queued but not yet acknowledged\nState: {state}"
