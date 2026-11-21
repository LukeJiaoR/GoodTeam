"""[INPUT]: Telegram 更新、项目会话和 Worker 控制。
[OUTPUT]: 接收项目 Issue、消息和已授权运行控制。
[POS]: 可选 Telegram 渠道。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from telebot import apihelper



TOKEN_ENV = "GOODTEAM_TG_BOT_TOKEN"
CHAT_ID_ENV = "GOODTEAM_TG_CHAT_ID"
ALLOWED_USERS_ENV = "GOODTEAM_TG_ALLOWED_USERS"
DASHBOARD_URL_ENV = "GOODTEAM_DASHBOARD_URL"
DEFAULT_DASHBOARD_URL = "http://localhost:8788/"


@dataclass(frozen=True)
class TelegramApproval:
    project_key: str
    run_id: str
    gate: str
    decision: str



class TelegramReviewSessionStore:
    def __init__(self, workspace: Path):
        self.directory = (workspace / "state" / "tg-reviews").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _file_path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def _read_session_file(self, file_path: Path, *, now: datetime | None = None) -> dict[str, object] | None:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            expires_at = data.get("expires_at", "")
            if expires_at and datetime.fromisoformat(str(expires_at)) < (now or datetime.now(UTC)):
                file_path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            return None

    def _iter_sessions(self, *, now: datetime | None = None):
        for file_path in sorted(self.directory.glob("*.json")):
            data = self._read_session_file(file_path, now=now)
            if data is not None:
                yield data

    def _is_live_card_session(self, data: dict[str, object]) -> bool:
        return not data.get("superseded") and data.get("decided", False) is False and int(data.get("message_id", 0)) > 0

    def get(self, session_id: str) -> dict[str, object] | None:
        file_path = self._file_path(session_id)
        if not file_path.is_file():
            return None
        return self._read_session_file(file_path)

    def save(self, session_id: str, data: dict[str, object]) -> None:
        file_path = self._file_path(session_id)
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def find_by_chat_and_status(self, chat_id: int, decided: bool = False) -> dict[str, object] | None:
        for data in self._iter_sessions():
            try:
                if int(data.get("chat_id", 0)) == chat_id and data.get("decided", False) == decided:
                    return data
            except Exception:
                continue
        return None

    def find_live_by_card_message(self, chat_id: int, message_id: int) -> dict[str, object] | None:
        for data in self._iter_sessions():
            try:
                if (
                    int(data.get("chat_id", 0)) == chat_id
                    and int(data.get("message_id", 0)) == message_id
                    and self._is_live_card_session(data)
                ):
                    return data
            except Exception:
                continue
        return None

    def find_live_by_note_reply_message(self, chat_id: int, message_id: int) -> dict[str, object] | None:
        if message_id <= 0:
            return None
        for data in self._iter_sessions():
            try:
                if (
                    int(data.get("chat_id", 0)) == chat_id
                    and int(data.get("awaiting_note_message_id", 0) or 0) == message_id
                    and data.get("awaiting_note_for") is not None
                    and self._is_live_card_session(data)
                ):
                    return data
            except Exception:
                continue
        return None

    def list_live_for_chat(self, chat_id: int) -> list[dict[str, object]]:
        sessions = []
        for data in self._iter_sessions():
            try:
                if int(data.get("chat_id", 0)) == chat_id and self._is_live_card_session(data):
                    sessions.append(data)
            except Exception:
                continue
        return sessions

    def find_live_for_gate(self, project_key: str, run_id: str, gate: str) -> dict[str, object] | None:
        now = datetime.now(UTC)
        for data in self._iter_sessions(now=now):
            try:
                if (
                    data.get("project_key") == project_key
                    and data.get("run_id") == run_id
                    and data.get("gate") == gate
                    and self._is_live_card_session(data)
                ):
                    return data
            except Exception:
                continue
        return None

def generate_session_id(store: TelegramReviewSessionStore) -> str:
    for _ in range(100):
        sid = uuid4().hex[:6]
        if store.get(sid) is None:
            return sid
    raise RuntimeError("Unable to generate unique session ID")


def _dashboard_url(project_key: str) -> str:
    base_url = os.getenv(DASHBOARD_URL_ENV, "").strip() or DEFAULT_DASHBOARD_URL
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("project", project_key))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), parts.fragment))


def _is_public_https_url(url: str) -> bool:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    return parts.scheme == "https" and hostname not in {"localhost", "127.0.0.1", "::1"}


def truncate_text(text: str, max_chars: int = 150) -> str:
    text = text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = " ".join(lines)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars-3] + "..."


def mask_sensitive_data(text: str) -> str:
    text = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    text = re.sub(r'[a-zA-Z0-9_\-]+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', 'staging-host', text)
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', 'staging-ip', text)
    text = re.sub(r'/srv/[a-zA-Z0-9_\-]+', 'deployment-dir', text)
    text = re.sub(r'/Users/luke/[a-zA-Z0-9_\-/]+', 'local-workspace-dir', text)
    return text


def _telegram_client_context(*, chat_id: object = "", message_id: object = "", user_id: object = "") -> dict[str, str]:
    return {
        "telegram_chat_id": str(chat_id) if chat_id else "",
        "telegram_message_id": str(message_id) if message_id else "",
        "telegram_user_id": str(user_id) if user_id else "",
    }


def telegram_config_from_env() -> dict[str, object]:
    token = os.getenv(TOKEN_ENV, "")
    chat_id = os.getenv(CHAT_ID_ENV, "")
    allowed_users = [
        item.strip()
        for item in os.getenv(ALLOWED_USERS_ENV, "").split(",")
        if item.strip()
    ]
    return {
        "enabled": bool(token and chat_id),
        "token_env": TOKEN_ENV,
        "chat_id": chat_id,
        "allowed_users": allowed_users,
    }


def _parse_telegram_command(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    head = parts[0].split("@", 1)[0].lower()
    if head == "/gt" and len(parts) >= 2:
        command = parts[1].split("@", 1)[0].lower()
    elif head.startswith("/"):
        command = head[1:]
    else:
        return ""
    return command if command in {
        "status",
        "workers",
        "evidence",
        "view",
        "bug",
        "ask",
        "tell",
        "steer",
        "followup",
        "stop",
        "task",
    } else ""


TG_COMMAND_CONTEXT_CHOOSER_REQUIRED = "__tg_command_context_chooser_required__"
TG_ROLE_INBOX_NO_CONTEXT = (
    "M1 is run-scoped: no live Telegram approval context is available. "
    "No message was recorded. Reply to an approval card or start a run before using /gt ask or /gt tell."
)
CHOOSER_COMMAND_CODES = {
    "ask": "K",
    "tell": "T",
}
CHOOSER_COMMANDS_BY_CODE = {value: key for key, value in CHOOSER_COMMAND_CODES.items()}


def _session_run_tail(session: dict[str, object]) -> str:
    run_id = str(session.get("run_id", "") or "")
    if not run_id:
        return "unknown"
    if len(run_id) <= 6:
        return run_id
    return f"…{run_id[-6:]}"


def _render_command_context_chooser_keyboard(
    *,
    command: str,
    sessions: list[dict[str, object]],
    intent_ids_by_session: dict[str, str],
) -> dict[str, object]:
    command_code = CHOOSER_COMMAND_CODES[command]
    rows = []
    for session in sessions:
        session_id = str(session["session_id"])
        label = " · ".join(
            [
                str(session.get("project_key", "") or "unknown"),
                str(session.get("gate", "") or "unknown").upper(),
                _session_run_tail(session),
            ]
        )
        rows.append(
            [
                {
                    "text": label,
                    "callback_data": f"v1:Q:{session_id}:{command_code}:{intent_ids_by_session[session_id]}",
                }
            ]
        )
    return {"inline_keyboard": rows}


def _attach_command_chooser_intent(
    *,
    store: TelegramReviewSessionStore,
    sessions: list[dict[str, object]],
    command: str,
    raw_text: str,
    chat_id: object,
    message_id: object = "",
) -> dict[str, str]:
    intent_ids_by_session = {}
    for session in sessions:
        session_id = str(session["session_id"])
        intent_id = uuid4().hex[:6]
        intent_ids_by_session[session_id] = intent_id
        intents = session.get("chooser_intents", {})
        if not isinstance(intents, dict):
            intents = {}
        intents = dict(list(intents.items())[-4:])
        intents[intent_id] = {
            "command": command,
            "raw_text": raw_text,
            "chat_id": str(chat_id),
            "message_id": str(message_id) if message_id else "",
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        session["chooser_intents"] = intents
        store.save(session_id, session)
    return intent_ids_by_session


def _send_command_context_chooser(
    *,
    token: str,
    chat_id: object,
    workspace: Path,
    command: str,
    raw_text: str,
    user_id: str,
    project_key: str,
    message_id: object = "",
) -> dict[str, object]:
    store = TelegramReviewSessionStore(workspace)
    live_sessions = store.list_live_for_chat(int(chat_id))
    if not live_sessions:
        _send_masked_telegram_message(token=token, chat_id=chat_id, text="No pending approval gate.")
        return {"status": "processed", "command": command}
    if len(live_sessions) == 1:
        return _process_telegram_command(
            command,
            token=token,
            chat_id=chat_id,
            workspace=workspace,
            project_key=project_key,
            raw_text=raw_text,
            user_id=user_id,
            context_session=live_sessions[0],
            command_message_id=message_id,
        )

    intent_ids_by_session = _attach_command_chooser_intent(
        store=store,
        sessions=live_sessions,
        command=command,
        raw_text=raw_text,
        chat_id=chat_id,
        message_id=message_id,
    )
    _telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": "Choose the approval context for this command:",
            "reply_markup": _render_command_context_chooser_keyboard(
                command=command,
                sessions=live_sessions,
                intent_ids_by_session=intent_ids_by_session,
            ),
        },
    )
    return {"status": "processed", "command": command}


def _resolve_tg_command_session(
    *,
    command: str,
    workspace: Path,
    chat_id: int,
    reply_to_message_id: int = 0,
    project_key: str = "",
) -> tuple[dict[str, object] | None, str]:
    if command not in {"view", "ask", "tell"}:
        return None, ""

    role_inbox_command = command in {"ask", "tell"}
    mutating = command in {"approve", "reject", "changes", "ask", "tell"}
    store = TelegramReviewSessionStore(workspace)
    if reply_to_message_id:
        session = store.find_live_by_card_message(chat_id, reply_to_message_id)
        if session is not None:
            return session, ""
        if mutating:
            return None, "That reply is not attached to an active approval card. Reply to an approval card and try again."
        return None, ""

    live_sessions = store.list_live_for_chat(chat_id)
    if len(live_sessions) == 1:
        return live_sessions[0], ""
    if len(live_sessions) > 1 and mutating:
        return None, TG_COMMAND_CONTEXT_CHOOSER_REQUIRED
    if role_inbox_command:
        return None, TG_ROLE_INBOX_NO_CONTEXT
    if mutating:
        return None, "No pending approval gate."
    return None, ""


def _resolve_tg_note_reply_session(
    *,
    store: TelegramReviewSessionStore,
    chat_id: int,
    reply_to_message_id: int,
) -> dict[str, object] | None:
    if reply_to_message_id <= 0:
        return None

    session = store.find_live_by_note_reply_message(chat_id, reply_to_message_id)
    if session is not None:
        return session

    session = store.find_live_by_card_message(chat_id, reply_to_message_id)
    if session is None or session.get("awaiting_note_for") is None:
        return None

    expected_card_id = int(session.get("awaiting_note_card_message_id", session.get("message_id", 0)) or 0)
    if expected_card_id != reply_to_message_id:
        return None
    return session


def _send_stale_note_reply_message(*, token: str, chat_id: object) -> dict[str, object]:
    if token:
        _telegram_request(
            token,
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": (
                    "That review note reply is no longer active (stale context). "
                    "Open the latest review card and request a note again."
                ),
            },
        )
    return {"status": "processed", "reason": "stale note reply context"}


def _send_masked_telegram_message(*, token: str, chat_id: object, text: str) -> None:
    _telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": mask_sensitive_data(text),
        },
    )


def _project_runtime_root(workspace: Path, project_key: str, state: object | None = None) -> Path | None:
    from goodteam_os.project_runtime import resolve_project_runtime_root
    from goodteam_os.projects import load_project_target
    from goodteam_os.state import normalize_project_key

    candidates: list[Path] = []
    source_ref = str(getattr(state, "source_ref", "") or "") if state is not None else ""
    if source_ref and source_ref != "manual":
        candidates.append(Path(source_ref))

    projects_dir = workspace / "projects"
    key = normalize_project_key(project_key)
    try:
        for config_path in sorted(projects_dir.glob("*.local.yaml")):
            target = load_project_target(config_path)
            if normalize_project_key(str(target.get("key", config_path.stem))) == key:
                candidates.append(config_path)
                break
    except Exception:
        pass
    candidates.append(projects_dir / f"{key.lower()}.local.yaml")

    for config_path in candidates:
        try:
            if not config_path.is_file():
                continue
            target = load_project_target(config_path)
            repos = target.get("repos", {})
            if isinstance(repos, dict) and repos:
                root = resolve_project_runtime_root({str(k): Path(str(v)) for k, v in repos.items()})
                if root is not None:
                    return root
            return config_path.parent
        except Exception:
            continue
    return None


def _read_command_state(workspace: Path, project_key: str):
    from goodteam_os.state import StateStore

    try:
        return StateStore(workspace).read_project_current(project_key) if project_key else StateStore(workspace).read_current()
    except Exception:
        return None


def _jobs_for_run(root: Path | None, run_id: str) -> list[dict[str, object]]:
    if root is None or not run_id:
        return []
    try:
        from goodteam_os.workers import WorkerJobStore

        return [job for job in WorkerJobStore(root).list_jobs() if str(job.get("run_id", "")) == run_id]
    except Exception:
        return []


def _elapsed_label(job: dict[str, object]) -> str:
    def parse_time(value: object) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    start = parse_time(job.get("created_at"))
    if start is None:
        return "unknown"
    end = datetime.now(UTC) if str(job.get("status", "")) == "running" else parse_time(job.get("updated_at")) or datetime.now(UTC)
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _worker_error_excerpt(job: dict[str, object]) -> str:
    for key in ("last_error", "error"):
        value = str(job.get(key, "") or "").strip()
        if value:
            return truncate_text(value, 120)
    stderr_path = str(job.get("stderr_path", "") or "").strip()
    if stderr_path:
        try:
            content = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
            if content.strip():
                return truncate_text(content, 120)
        except Exception:
            pass
    summary = str(job.get("summary", "") or "").strip()
    return truncate_text(summary, 120) if summary else ""


def _format_status_command(*, workspace: Path, project_key: str) -> str:
    state = _read_command_state(workspace, project_key)
    if state is None:
        base = f"GoodTeam status\nProject: {project_key or 'unknown'}\nNo active run."
        from goodteam_os.telegram_runtime_controls import TelegramRuntimeControlService

        return (
            f"{base}\n\n"
            f"{TelegramRuntimeControlService(workspace, project_key, redact=mask_sensitive_data).status_section()}"
        )
    from goodteam_os.web import current_approval_gate

    root = _project_runtime_root(workspace, project_key or str(getattr(state, "project_key", "")), state)
    jobs = _jobs_for_run(root, str(getattr(state, "run_id", "") or ""))
    active_workers = sum(1 for job in jobs if str(job.get("status", "")) == "running")
    blockers = getattr(state, "blockers", [])
    blocker_count = len(blockers) if isinstance(blockers, list) else 0
    gate = current_approval_gate(state) or "none"
    run_id = str(getattr(state, "run_id", "") or "")
    run_label = f"…{run_id[-6:]}" if run_id else "unknown"
    base = "\n".join(
        [
            "GoodTeam status",
            f"Project: {getattr(state, 'project_key', project_key or 'unknown')}",
            f"Run: {run_label}",
            f"Pending gate: {gate}",
            f"Active workers: {active_workers}",
            f"Blockers: {blocker_count}",
        ]
    )
    from goodteam_os.telegram_runtime_controls import TelegramRuntimeControlService

    pi_section = TelegramRuntimeControlService(workspace, project_key, redact=mask_sensitive_data).status_section()
    return f"{base}\n\n{pi_section}"


def _format_workers_command(*, workspace: Path, project_key: str) -> str:
    state = _read_command_state(workspace, project_key)
    if state is None:
        return "GoodTeam workers\nNo active run."
    root = _project_runtime_root(workspace, project_key or str(getattr(state, "project_key", "")), state)
    jobs = _jobs_for_run(root, str(getattr(state, "run_id", "") or ""))
    if not jobs:
        return "GoodTeam workers\nNo worker jobs."
    lines = ["GoodTeam workers"]
    for job in sorted(jobs, key=lambda item: str(item.get("updated_at", "")), reverse=True)[:5]:
        full_job_id = str(job.get("id", "") or job.get("job_id", "") or "unknown")
        job_id = f"…{full_job_id[-8:]}" if len(full_job_id) > 8 else full_job_id
        role = str(job.get("role", "") or "unknown")
        status = str(job.get("status", "") or "unknown")
        error = _worker_error_excerpt(job)
        line = f"- {job_id} | {role} | {status} | elapsed {_elapsed_label(job)}"
        if error:
            line += f" | last error: {error}"
        lines.append(line)
    return "\n".join(lines)


def _release_readiness_status(dev_loop: dict[str, object]) -> str:
    readiness = dev_loop.get("release_readiness", {})
    if not isinstance(readiness, dict):
        return "unknown"
    if "up_to_date" in readiness:
        return "ready" if readiness.get("up_to_date") else "not ready"
    if "available" in readiness:
        return "available" if readiness.get("available") else "unknown"
    return "unknown"


def _format_evidence_command(*, workspace: Path, project_key: str) -> str:
    state = _read_command_state(workspace, project_key)
    root = _project_runtime_root(workspace, project_key, state)
    try:
        from goodteam_os.web import build_approval_evidence_snapshot, build_dev_loop

        snapshot = build_approval_evidence_snapshot(state=state, workspace=workspace, project_key=project_key)
        dev_loop = build_dev_loop(root)
    except Exception:
        snapshot = {}
        dev_loop = {}
    return "\n".join(
        [
            "GoodTeam evidence",
            f"CI: {snapshot.get('ci_status', 'unknown')}",
            f"Smoke: {snapshot.get('smoke_status', 'unknown')}",
            f"QA: {snapshot.get('qa_status', 'unknown')}",
            f"Release readiness: {_release_readiness_status(dev_loop)}",
            f"Blockers: {snapshot.get('blocker_count', 0)}",
        ]
    )


def _extract_command_arg(raw_text: str, command: str) -> str:
    """Extract the argument (everything after the command word) from raw message text."""
    text = raw_text.strip()
    # Try "/gt <cmd> <arg>" form first
    for prefix in (f"/gt {command}", f"/{command}"):
        if text.lower().startswith(prefix):
            return text[len(prefix):].strip()
    return ""


def _canonical_role_keys() -> tuple[str, ...]:
    from goodteam_os.roles import list_role_definitions

    return tuple(str(role.get("key", "")).strip() for role in list_role_definitions() if role.get("key"))


def _role_command_usage(command: str) -> str:
    body_label = "question" if command == "ask" else "instruction"
    return f"Usage: /gt {command} <role> <{body_label}>"


def _parse_role_command_args(raw_text: str, command: str) -> tuple[str, str, str]:
    arg = _extract_command_arg(raw_text, command)
    if not arg:
        return "", "", _role_command_usage(command)

    parts = arg.split(maxsplit=1)
    role_key = parts[0].strip() if parts else ""
    body = parts[1].strip() if len(parts) > 1 else ""
    if not role_key or not body:
        return "", "", _role_command_usage(command)

    valid_roles = _canonical_role_keys()
    if role_key not in valid_roles:
        return "", "", f"Unknown role: {role_key}\nValid role keys: {', '.join(valid_roles)}"

    return role_key, mask_sensitive_data(body), ""


def _telegram_role_message_refs(
    *,
    chat_id: object,
    message_id: object = "",
    user_id: str = "",
    session: dict[str, object],
) -> list[str]:
    refs = []
    if chat_id and message_id:
        refs.append(f"telegram:chat:{chat_id}/message:{message_id}")
    if user_id:
        refs.append(f"telegram:user:{user_id}")

    session_id = str(session.get("session_id", "") or "")
    if session_id:
        refs.append(f"tg-session:{session_id}")

    gate = str(session.get("gate", "") or "").strip()
    if gate:
        refs.append(f"approval:{gate}")
        try:
            from goodteam_os.roles import approval_artifact_map

            artifact_key = approval_artifact_map().get(gate, gate)
        except Exception:
            artifact_key = gate
        if artifact_key:
            refs.append(f"artifact:{artifact_key}")

    return refs


def _record_tg_role_message(
    *,
    workspace: Path,
    session: dict[str, object],
    command: str,
    role_key: str,
    body: str,
    chat_id: object,
    message_id: object = "",
    user_id: str = "",
) -> dict[str, object]:
    from goodteam_os.messages import append_message

    project_key = str(session.get("project_key", "") or "").strip()
    run_id = str(session.get("run_id", "") or "").strip()
    if not project_key or not run_id:
        raise ValueError(TG_ROLE_INBOX_NO_CONTEXT)

    return append_message(
        workspace,
        project_key,
        run_id,
        from_role="human",
        to=[role_key],
        kind="ask" if command == "ask" else "propose",
        gate=str(session.get("gate", "") or "").strip(),
        body=body,
        refs=_telegram_role_message_refs(
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            session=session,
        ),
    )


def _process_telegram_command(
    command: str,
    *,
    token: str,
    chat_id: object,
    workspace: Path,
    project_key: str,
    raw_text: str = "",
    user_id: str = "",
    context_session: dict[str, object] | None = None,
    context_error_text: str = "",
    command_message_id: object = "",
) -> dict[str, object]:
    if context_session is not None:
        project_key = str(context_session.get("project_key", project_key) or project_key)

    if command == "task":
        issue_url = _extract_command_arg(raw_text, "task")
        if not issue_url:
            _send_masked_telegram_message(
                token=token,
                chat_id=chat_id,
                text="Usage: /gt task <github-issue-url>",
            )
            return {"status": "processed", "command": command, "accepted": False}
        try:
            from goodteam_os.operator_manager_intake import enqueue_github_issue

            item, created = enqueue_github_issue(
                workspace=workspace,
                issue_url=issue_url,
            )
        except (RuntimeError, ValueError) as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=f"❌ {exc}")
            return {"status": "processed", "command": command, "accepted": False}
        _send_masked_telegram_message(
            token=token,
            chat_id=chat_id,
            text=(
                f"Sent to Manager: {item['id']}\n"
                f"Project: {item['project_key']}\n"
                f"Issue: {item['source_url']}\n"
                f"Phase: {item['phase']}"
            ),
        )
        return {
            "status": "processed",
            "command": command,
            "accepted": True,
            "created": created,
            "task_id": item["id"],
        }

    if command in {"steer", "followup", "stop"}:
        argument = _extract_command_arg(raw_text, command)
        if command in {"steer", "followup"} and not argument:
            _send_masked_telegram_message(
                token=token,
                chat_id=chat_id,
                text=f"Usage: /gt {command} <message>",
            )
            return {"status": "processed", "command": command}
        from goodteam_os.telegram_runtime_controls import (
            RuntimeControlResolutionError,
            TelegramRuntimeControlService,
        )

        service = TelegramRuntimeControlService(workspace, project_key, redact=mask_sensitive_data)
        try:
            result, response = service.control(
                command,
                message=argument if command != "stop" else "",
                reason=argument if command == "stop" else "",
                chat_id=chat_id,
                message_id=command_message_id,
                user_id=user_id,
            )
        except (RuntimeControlResolutionError, ValueError, OSError) as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=f"❌ {exc}")
            return {"status": "processed", "command": command, "accepted": False}
        _send_masked_telegram_message(token=token, chat_id=chat_id, text=response)
        return {
            "status": "processed",
            "command": command,
            "accepted": bool(result.get("accepted")),
            "control_id": str(result.get("control_id") or ""),
            "control_state": str(result.get("control_state") or ""),
        }

    if command in {"ask", "tell"}:
        role_key, body, parse_error = _parse_role_command_args(raw_text, command)
        if parse_error:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=parse_error)
            return {"status": "processed", "command": command}

        if context_error_text == TG_COMMAND_CONTEXT_CHOOSER_REQUIRED:
            return _send_command_context_chooser(
                token=token,
                chat_id=chat_id,
                workspace=workspace,
                command=command,
                raw_text=raw_text,
                user_id=user_id,
                project_key=project_key,
                message_id=command_message_id,
            )

        if context_error_text:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=context_error_text)
            return {"status": "processed", "command": command}

        if context_session is None:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=TG_ROLE_INBOX_NO_CONTEXT)
            return {"status": "processed", "command": command}

        try:
            message = _record_tg_role_message(
                workspace=workspace,
                session=context_session,
                command=command,
                role_key=role_key,
                body=body,
                chat_id=chat_id,
                message_id=command_message_id,
                user_id=user_id,
            )
        except ValueError as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=str(exc))
            return {"status": "processed", "command": command}

        gate = str(message.get("gate", "") or "none").upper()
        project_label = str(context_session.get("project_key", project_key) or project_key or "unknown")
        _send_masked_telegram_message(
            token=token,
            chat_id=chat_id,
            text="\n".join(
                [
                    f"Recorded for {role_key}.",
                    f"Project: {project_label}",
                    f"Gate: {gate}",
                    "No worker was started.",
                ]
            ),
        )
        return {"status": "processed", "command": command}

    if command == "view":
        from goodteam_os.web import current_approval_gate
        from goodteam_os.roles import approval_artifact_map

        state = _read_command_state(workspace, project_key)
        gate_arg = _extract_command_arg(raw_text, "view")
        session_gate = str(context_session.get("gate", "") or "") if context_session else ""

        if session_gate:
            gate = session_gate
        elif gate_arg:
            # Verify it's a real key in state.approvals
            approvals = getattr(state, "approvals", {}) if state is not None else {}
            if gate_arg not in approvals:
                _send_masked_telegram_message(
                    token=token, chat_id=chat_id,
                    text=f"No artifact found for gate {gate_arg}"
                )
                return {"status": "processed", "command": command}
            gate = gate_arg
        else:
            gate = current_approval_gate(state) if state is not None else ""
            if not gate:
                _send_masked_telegram_message(
                    token=token, chat_id=chat_id,
                    text="No pending approval gate."
                )
                return {"status": "processed", "command": command}

        artifact_key = approval_artifact_map().get(gate, gate)
        artifact_path = str(getattr(state, "artifacts", {}).get(artifact_key, "") if state is not None else "")

        if not artifact_path:
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text=f"No artifact found for gate {gate}"
            )
            return {"status": "processed", "command": command}

        path = Path(artifact_path)
        if not path.is_absolute():
            path = (workspace / path).resolve()
        if not path.is_file():
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text=f"No artifact found for gate {gate}"
            )
            return {"status": "processed", "command": command}

        content = mask_sensitive_data(path.read_text(encoding="utf-8"))
        chunk_size = 3800
        if len(content) <= chunk_size:
            _telegram_request(
                token,
                "sendMessage",
                {"chat_id": str(chat_id), "text": f"📄 {gate}:\n\n{content}"},
            )
        else:
            chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
            n = len(chunks)
            for i, chunk in enumerate(chunks, 1):
                _telegram_request(
                    token,
                    "sendMessage",
                    {"chat_id": str(chat_id), "text": f"📄 {gate} (part {i}/{n}):\n\n{chunk}"},
                )
        return {"status": "processed", "command": command}

    if command == "bug":
        arg = _extract_command_arg(raw_text, "bug")

        severity = "p2"
        match = re.match(r"^\[(p[123])\]\s*", arg, flags=re.IGNORECASE)
        if match:
            severity = match.group(1).lower()
            arg = arg[match.end():].strip()

        description = arg.strip()
        if not description:
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text="Usage: /gt bug [p1|p2|p3] <description>",
            )
            return {"status": "processed", "command": "bug"}

        from goodteam_os.web import report_bug

        try:
            result = report_bug(
                workspace=workspace,
                project_key=project_key,
                description=description,
                severity=severity,
            )
        except ValueError as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=str(exc))
            return {"status": "processed", "command": "bug"}

        confirm = (
            "🐛 Bug 已提交为 Planner 任务\n"
            f"严重度: {severity}\n"
            f"任务: {result['task_id']}\n"
            f"批次: {result['run_id']} · {result['status']}\n"
            "在项目任务页查看规划结果，后续修复沿任务队列推进；此提交不是已完成修复，也没有 FIX_PLAN 审批门。"
        )
        _send_masked_telegram_message(token=token, chat_id=chat_id, text=confirm)
        return {"status": "processed", "command": "bug"}

    text = {
        "status": _format_status_command,
        "workers": _format_workers_command,
        "evidence": _format_evidence_command,
    }[command](workspace=workspace, project_key=project_key)
    _send_masked_telegram_message(token=token, chat_id=chat_id, text=text)
    return {"status": "processed", "command": command}


def poll_telegram_approvals(
    *,
    workspace: Path,
    project_key: str = "",
    once: bool = False,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, object]:
    token = os.getenv(TOKEN_ENV, "")
    chat_id = os.getenv(CHAT_ID_ENV, "")
    if not token or not chat_id:
        raise ValueError("Telegram approval channel is disabled. Set GOODTEAM_TG_BOT_TOKEN and GOODTEAM_TG_CHAT_ID.")
    offset = 0
    processed = 0
    seen_callbacks: set[str] = set()
    allowed_users = [str(item) for item in telegram_config_from_env()["allowed_users"]]
    while True:
        if heartbeat is not None:
            heartbeat()
        payload = _telegram_request(token, "getUpdates", {"timeout": 20, "offset": offset})
        for update in payload.get("result", []):
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            result = process_telegram_update(
                update,
                workspace=workspace,
                allowed_users=allowed_users,
                processed_callbacks=seen_callbacks,
                project_key=project_key,
            )
            if result["status"] not in {"processed", "duplicate"}:
                continue
            if result["status"] == "processed":
                processed += 1
        if heartbeat is not None:
            heartbeat()
        if once:
            return {"processed": processed, "offset": offset}


def _send_chooser_stale_message(*, token: str, chat_id: object) -> dict[str, object]:
    _send_masked_telegram_message(
        token=token,
        chat_id=chat_id,
        text="That approval context is no longer active. Send the command again to choose a live approval.",
    )
    return {"status": "ignored", "reason": "session no longer active"}


def _process_command_context_chooser_callback(
    *,
    token: str,
    callback_id: str,
    processed_callbacks: set[str],
    workspace: Path,
    session_id: str,
    command_code: str,
    intent_id: str,
    chat_id: int,
    message_id: int,
    user_id: str,
    project_key: str,
) -> dict[str, object]:
    store = TelegramReviewSessionStore(workspace)
    session = store.get(session_id)
    if (
        session is None
        or session.get("decided", False)
        or session.get("superseded", False)
        or int(session.get("message_id", 0) or 0) <= 0
        or int(session.get("chat_id", 0) or 0) != chat_id
    ):
        return _send_chooser_stale_message(token=token, chat_id=chat_id)

    command = CHOOSER_COMMANDS_BY_CODE.get(command_code)
    if command is None:
        return {"status": "ignored", "reason": "invalid chooser command"}

    intents = session.get("chooser_intents", {})
    intent = intents.get(intent_id) if isinstance(intents, dict) else None
    if not isinstance(intent, dict) or str(intent.get("chat_id", "")) != str(chat_id):
        return _send_chooser_stale_message(token=token, chat_id=chat_id)
    if str(intent.get("session_id", "")) != str(session_id):
        return _send_chooser_stale_message(token=token, chat_id=chat_id)

    raw_text = str(intent.get("raw_text", "") or f"/gt {command}")
    command_message_id = str(intent.get("message_id", "") or "")
    if command != str(intent.get("command", "") or command):
        return {"status": "ignored", "reason": "chooser command mismatch"}

    result = _process_telegram_command(
        command,
        token=token,
        chat_id=chat_id,
        workspace=workspace,
        project_key=project_key,
        raw_text=raw_text,
        user_id=user_id,
        context_session=session,
        command_message_id=command_message_id or message_id,
    )
    if result.get("status") == "processed" and result.get("accepted") is not False:
        # Consume only after the bound decision succeeds. Do not overwrite a
        # newer decided session saved by the command handler.
        saved = store.get(session_id) or session
        remaining_intents = dict(saved.get("chooser_intents", {}))
        remaining_intents.pop(intent_id, None)
        saved["chooser_intents"] = remaining_intents
        store.save(session_id, saved)
        processed_callbacks.add(callback_id)
        return {"status": "processed", "callback_id": callback_id}
    return result


def process_telegram_update(
    update: dict[str, object],
    *,
    workspace: Path,
    allowed_users: list[str],
    processed_callbacks: set[str],
    project_key: str = "",
) -> dict[str, object]:
    token = os.getenv(TOKEN_ENV, "")

    # 1. Handle normal messages (ForceReply replies)
    message = dict(update.get("message", {}) or {})
    if message:
        msg_text = str(message.get("text", "") or "")
        command = _parse_telegram_command(msg_text)
        if command:
            user = dict(message.get("from", {}) or {})
            user_id = str(user.get("id", ""))
            if allowed_users and user_id not in allowed_users:
                return {"status": "ignored", "reason": "unauthorized user"}
            if command in {"steer", "followup", "stop", "task"} and user_id not in allowed_users:
                return {"status": "ignored", "reason": "unauthorized user"}
            chat_id = int(message.get("chat", {}).get("id", 0))
            reply_to_message_id = int(dict(message.get("reply_to_message", {}) or {}).get("message_id", 0) or 0)
            context_session, context_error_text = _resolve_tg_command_session(
                command=command,
                workspace=workspace,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                project_key=project_key,
            )
            return _process_telegram_command(
                command,
                token=token,
                chat_id=chat_id,
                workspace=workspace,
                project_key=project_key,
                raw_text=msg_text,
                user_id=user_id,
                context_session=context_session,
                context_error_text=context_error_text,
                command_message_id=message.get("message_id", ""),
            )

        return {"status": "ignored", "reason": "not a reply message"}

    # 2. Handle callback queries
    callback = dict(update.get("callback_query", {}) or {})
    if not callback:
        return {"status": "ignored", "reason": "not callback"}

    callback_id = str(callback.get("id", ""))
    if callback_id in processed_callbacks:
        return {"status": "duplicate", "callback_id": callback_id}

    user = dict(callback.get("from", {}) or {})
    user_id = str(user.get("id", ""))
    if allowed_users and user_id not in allowed_users:
        return {"status": "ignored", "reason": "unauthorized user"}

    callback_data = str(callback.get("data", ""))

    if callback_data.startswith("v1:"):
        parts = callback_data.split(":")
        if len(parts) < 3:
            return {"status": "ignored", "reason": "invalid callback format"}
        action = parts[1]
        session_id = parts[2]
        arg = parts[3] if len(parts) > 3 else ""

        chat_id = int(callback.get("message", {}).get("chat", {}).get("id", 0))
        message_id = int(callback.get("message", {}).get("message_id", 0))

        if action == "Q":
            intent_id = parts[4] if len(parts) > 4 else ""
            return _process_command_context_chooser_callback(
                token=token,
                callback_id=callback_id,
                processed_callbacks=processed_callbacks,
                workspace=workspace,
                session_id=session_id,
                command_code=arg,
                intent_id=intent_id,
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                project_key=project_key,
            )

    return {"status": "ignored", "reason": "unknown callback"}

DISABLE_NETWORK_ENV = "GOODTEAM_DISABLE_TG_NETWORK"



def _telegram_request(token: str, method: str, params: dict[str, object]) -> dict[str, object]:
    if os.getenv(DISABLE_NETWORK_ENV) == "1":
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 1}}
        if method == "editMessageText":
            return {"ok": True, "result": {"message_id": int(params.get("message_id", 1) or 1)}}
        if method == "deleteMessage":
            return {"ok": True, "result": True}
        if method == "answerCallbackQuery":
            return {"ok": True, "result": True}
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": {}}

    try:
        if method == "sendMessage":
            result = apihelper.send_message(
                token,
                chat_id=params["chat_id"],
                text=params["text"],
                parse_mode=params.get("parse_mode"),
                reply_markup=_telegram_reply_markup(params.get("reply_markup")),
            )
        elif method == "editMessageText":
            result = apihelper.edit_message_text(
                token,
                text=params["text"],
                chat_id=params.get("chat_id"),
                message_id=params.get("message_id"),
                parse_mode=params.get("parse_mode"),
                reply_markup=_telegram_reply_markup(params.get("reply_markup")),
            )
        elif method == "deleteMessage":
            result = apihelper.delete_message(
                token,
                chat_id=params["chat_id"],
                message_id=params["message_id"],
            )
        elif method == "answerCallbackQuery":
            result = apihelper.answer_callback_query(
                token,
                callback_query_id=params["callback_query_id"],
                text=params.get("text"),
            )
        elif method == "getUpdates":
            result = apihelper.get_updates(
                token,
                offset=params.get("offset"),
                timeout=params.get("timeout"),
            )
        else:
            raise ValueError(f"Unsupported Telegram Bot API method: {method}")
    except Exception as exc:
        return {"ok": False, "description": str(exc)}
    return {"ok": True, "result": result}


def _telegram_reply_markup(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _safe_part(value: str) -> str:
    if ":" in value or not value:
        raise ValueError("Telegram callback values cannot be empty or contain ':'.")
    return value
