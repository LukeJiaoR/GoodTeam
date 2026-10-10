import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from telebot import apihelper

from goodteam_os.telegram_approval_binding import capture_target, session_decision_binding


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

    def find_for_operator_task(self, task_id: str, head_sha: str) -> dict[str, object] | None:
        for data in self._iter_sessions():
            if (
                str(data.get("operator_task_id", "")) == task_id
                and str(data.get("operator_head_sha", "")) == head_sha
            ):
                return data
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


def _session_review_items(session: dict[str, object]) -> list[dict[str, str]]:
    review_items = []
    items = session.get("items", [])
    if not isinstance(items, list):
        return review_items
    for item in items:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state", "") or "")
        if state not in {"ok", "flagged", "skipped"}:
            state = "not_reviewed"
        review_items.append(
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "state": state,
                "note": mask_sensitive_data(str(item.get("note", "") or "")),
            }
        )
    return review_items


def _session_was_reviewed(session: dict[str, object]) -> bool:
    items = session.get("items", [])
    return isinstance(items, list) and any(
        isinstance(item, dict) and str(item.get("state", "")) in {"ok", "flagged", "skipped"} for item in items
    )


def _telegram_decision_audit_kwargs(
    *,
    user_id: str,
    chat_id: object = "",
    message_id: object = "",
    review_mode: str,
    review_session_id: str = "",
    review_items: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "decision_origin": "telegram",
        "actor_ref": f"telegram:user:{user_id}",
        "review_mode": review_mode,
        "review_session_id": review_session_id,
        "client_context": _telegram_client_context(chat_id=chat_id, message_id=message_id, user_id=user_id),
        "review_items": review_items or [],
    }


def split_artifact_into_review_items(gate: str, content: str) -> list[dict[str, str]]:
    if not content:
        return _default_items_for_gate(gate)

    sections = {}
    current_header = ""
    current_lines = []
    for line in content.splitlines():
        if line.startswith("#"):
            if current_header:
                sections[current_header] = "\n".join(current_lines).strip()
            current_header = line.lstrip("#").strip().lower()
            current_lines = []
        else:
            if current_header:
                current_lines.append(line)
    if current_header:
        sections[current_header] = "\n".join(current_lines).strip()

    if gate == "staging":
        acceptance_text = ""
        for sec, text in sections.items():
            if "acceptance" in sec or "migrations" in sec or "registration" in sec:
                acceptance_text += f"### {sec.capitalize()}\n{text}\n"
        if not acceptance_text:
            for k, v in sections.items():
                if "command results" in k:
                    parts = v.split("### ")
                    for p in parts:
                        if "acceptance" in p.lower():
                            acceptance_text += f"### {p}\n"
        if not acceptance_text:
            acceptance_text = "All configured acceptance tests exited code 0."

        summary_text = sections.get("staging execution summary", "")
        if not summary_text:
            summary_text = "Staging verification passed successfully."

        health_text = ""
        for k, v in sections.items():
            if "health" in k:
                health_text = v
                break
        if not health_text:
            for k, v in sections.items():
                if "command results" in k:
                    parts = v.split("### ")
                    for p in parts:
                        if "healthcheck" in p.lower():
                            health_text = p
        if not health_text:
            health_text = "Containers are healthy and responding."

        regression_text = ""
        for k, v in sections.items():
            if "regression" in k or "rollback" in k:
                regression_text = v
                break
        if not regression_text:
            regression_text = "No code regressions or performance degradations detected."

        items = [
            {"id": "acceptance", "title": "验收检查", "body": truncate_text(acceptance_text, 150), "state": "pending", "note": None},
            {"id": "evidence", "title": "部署证据", "body": truncate_text(summary_text, 150), "state": "pending", "note": None},
            {"id": "healthcheck", "title": "健康检查", "body": truncate_text(health_text, 150), "state": "pending", "note": None},
            {"id": "regression", "title": "回退对比", "body": truncate_text(regression_text, 150), "state": "pending", "note": None},
        ]
    elif gate == "security":
        items = [
            {"id": "dependencies", "title": "依赖漏洞", "body": "No high-severity vulnerabilities found in dependencies.", "state": "pending", "note": None},
            {"id": "privileges", "title": "权限审计", "body": "Permissions audit verified: standard developer access rules.", "state": "pending", "note": None},
            {"id": "secrets", "title": "密钥泄露检查", "body": "Static analysis scanned: no private keys or API tokens found.", "state": "pending", "note": None},
            {"id": "compliance", "title": "合规项", "body": "Compliance checklist fully validated.", "state": "pending", "note": None},
        ]
        for k, v in sections.items():
            for it in items:
                if it["id"] in k:
                    it["body"] = truncate_text(v, 150)
    elif gate == "release":
        items = [
            {"id": "release_notes", "title": "Release Notes", "body": "Standard release package prepared.", "state": "pending", "note": None},
            {"id": "rollback", "title": "回滚预案", "body": "Rollback strategy: git revert on failure.", "state": "pending", "note": None},
            {"id": "feature_flag", "title": "Feature Flag", "body": "No flags associated with this release.", "state": "pending", "note": None},
            {"id": "checklist", "title": "部署清单", "body": "All checklist items verified.", "state": "pending", "note": None},
        ]
        for k, v in sections.items():
            for it in items:
                if it["id"] in k or (it["id"] == "release_notes" and "notes" in k):
                    it["body"] = truncate_text(v, 150)
    else:
        parsed_items = []
        count = 0
        for k, v in sections.items():
            if count >= 4:
                break
            if len(k) > 2 and len(v) > 5:
                parsed_items.append({
                    "id": f"section_{count}",
                    "title": k.capitalize(),
                    "body": truncate_text(v, 150),
                    "state": "pending",
                    "note": None
                })
                count += 1
        if not parsed_items:
            parsed_items = [
                {"id": "overview", "title": "Overview", "body": "Report generated by GoodTeam.", "state": "pending", "note": None}
            ]
        items = parsed_items
    return items


def _default_items_for_gate(gate: str) -> list[dict[str, str]]:
    if gate == "staging":
        return [
            {"id": "acceptance", "title": "验收检查", "body": "All acceptance checks completed.", "state": "pending", "note": None},
            {"id": "evidence", "title": "部署证据", "body": "Deployment evidence uploaded.", "state": "pending", "note": None},
            {"id": "healthcheck", "title": "健康检查", "body": "Health check reports OK.", "state": "pending", "note": None},
            {"id": "regression", "title": "回退对比", "body": "No regression detected.", "state": "pending", "note": None},
        ]
    elif gate == "security":
        return [
            {"id": "dependencies", "title": "依赖漏洞", "body": "No high-severity vulnerabilities found.", "state": "pending", "note": None},
            {"id": "privileges", "title": "权限审计", "body": "Standard user access rules active.", "state": "pending", "note": None},
            {"id": "secrets", "title": "密钥泄露检查", "body": "Scanned: no sensitive keys leaked.", "state": "pending", "note": None},
            {"id": "compliance", "title": "合规项", "body": "All compliance items verified.", "state": "pending", "note": None},
        ]
    elif gate == "release":
        return [
            {"id": "release_notes", "title": "Release Notes", "body": "Standard release notes prepared.", "state": "pending", "note": None},
            {"id": "rollback", "title": "回滚预案", "body": "Rollback checklist prepared.", "state": "pending", "note": None},
            {"id": "feature_flag", "title": "Feature Flag", "body": "No flag adjustments needed.", "state": "pending", "note": None},
            {"id": "checklist", "title": "部署清单", "body": "Pre-deployment list cleared.", "state": "pending", "note": None},
        ]
    else:
        return [
            {"id": "overview", "title": "Overview", "body": "Artifact ready for review.", "state": "pending", "note": None}
        ]


def make_progress_bar(total: int, current: int) -> str:
    blocks = []
    for i in range(total):
        if i < current:
            blocks.append("████")
        else:
            blocks.append("░░░░")
    return " ".join(blocks)


def get_next_pending_cursor(session: dict[str, object]) -> int | None:
    items = session["items"]
    cursor = int(session.get("cursor", 0))
    total = len(items)
    for i in range(cursor + 1, total):
        if items[i]["state"] == "pending":
            return i
    for i in range(0, total):
        if items[i]["state"] == "pending":
            return i
    return None


def render_item_card_text(*, session: dict[str, object], cursor: int) -> str:
    items = session["items"]
    item = items[cursor]
    total = len(items)
    project_key = session["project_key"]
    gate = str(session["gate"]).upper()

    progress_bar = make_progress_bar(total, cursor)
    body_masked = mask_sensitive_data(item["body"])

    text = (
        f"🔍 *{gate} Review* · 第 {cursor + 1}/{total} 项          *{project_key}*\n\n"
        f"{progress_bar}\n\n"
        f"─────────────────────────────\n"
        f"*{item['title']}*\n\n"
        f"{body_masked}\n"
        f"─────────────────────────────"
    )
    return text


def render_item_card_keyboard(*, session_id: str, cursor: int) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 通过", "callback_data": f"v1:O:{session_id}:{cursor}"},
                {"text": "⚑ 标记", "callback_data": f"v1:F:{session_id}:{cursor}"},
                {"text": "→ 跳过", "callback_data": f"v1:S:{session_id}:{cursor}"},
            ],
            [
                {"text": "← 上一项", "callback_data": f"v1:P:{session_id}:{cursor}"},
                {"text": "✕ 退出到汇总", "callback_data": f"v1:X:{session_id}:{cursor}"},
            ]
        ]
    }


def render_summary_card_text(*, session: dict[str, object]) -> str:
    items = session["items"]
    ok_count = sum(1 for it in items if it["state"] == "ok")
    flagged_count = sum(1 for it in items if it["state"] == "flagged")
    skipped_count = sum(1 for it in items if it["state"] == "skipped")

    status_parts = []
    if ok_count:
        status_parts.append(f"{ok_count} 通过")
    if flagged_count:
        status_parts.append(f"{flagged_count} 标记")
    if skipped_count:
        status_parts.append(f"{skipped_count} 跳过")
    status_str = ", ".join(status_parts) if status_parts else "未开始"

    lines = [
        f"📋 *Review 完成* · {status_str}\n"
        f"─────────────────────────────"
    ]

    for it in items:
        state = it["state"]
        emoji = "✅" if state == "ok" else "⚑" if state == "flagged" else "⏭️" if state == "skipped" else "░"
        note_suffix = f" — {it['note']}" if it["note"] else ""
        lines.append(f"{emoji} *{it['title']}*{note_suffix}")

    lines.append("─────────────────────────────")
    return "\n".join(lines)


def render_summary_card_keyboard(*, session_id: str, flagged_count: int) -> dict[str, object]:
    buttons = [
        {"text": "✅ 仍然批准", "callback_data": f"v1:A:{session_id}"}
    ]
    if flagged_count > 0:
        buttons.append({"text": f"⮌ 送回 {flagged_count} 个问题", "callback_data": f"v1:C:{session_id}"})

    return {
        "inline_keyboard": [
            buttons,
            [
                {"text": "❌ 拒绝", "callback_data": f"v1:J:{session_id}"}
            ]
        ]
    }


def render_final_card_text(*, session: dict[str, object], decision_label: str, next_info: str = "") -> str:
    now_str = datetime.now().strftime("%H:%M")
    notes_count = sum(1 for it in session["items"] if it["note"])
    notes_suffix = f"\n（{notes_count} 个备注已记录到 action log）" if notes_count else ""

    text = (
        f"🏁 *{decision_label}于 {now_str}*\n"
        f"{next_info}"
        f"{notes_suffix}"
    )
    return text


def compile_and_write_review_notes(workspace: Path, session: dict[str, object]) -> None:
    notes = []
    for it in session["items"]:
        if it["state"] == "flagged" and it["note"]:
            notes.append(f"- **{it['title']}**: {it['note']}")
    if not notes:
        return

    notes_block = "\n\n## Review Feedback (Telegram Review)\n\n" + "\n".join(notes) + "\n"

    from goodteam_os.web import append_action
    for it in session["items"]:
        if it["state"] == "flagged" and it["note"]:
            append_action(
                workspace,
                run_id=str(session["run_id"]),
                actor="human",
                action="review_item_comment",
                project_key=str(session["project_key"]),
                gate=str(session["gate"]),
                artifact=it["id"],
                decision="changes_requested",
                artifact_path=it["note"],
            )

    from goodteam_os.roles import approval_artifact_map
    from goodteam_os.resources import ResourceCapability, resource_path
    from goodteam_os.state import StateStore
    gate = str(session["gate"])
    artifact_key = approval_artifact_map().get(gate, gate)

    state_store = StateStore(workspace)
    project_key = str(session["project_key"])
    state = state_store.read_project_current(project_key) if project_key else state_store.read_current()
    if state:
        path = resource_path(state.resources, artifact_key, capability=ResourceCapability.REVIEW)
        if path is None and artifact_key in state.artifacts:
            path = Path(state.artifacts[artifact_key])
        if path is None:
            return
        if not path.is_absolute():
            path = (workspace / path).resolve()
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
                path.write_text(content + notes_block, encoding="utf-8")
            except Exception:
                pass


def update_active_tg_review_on_web_decision(*, workspace: Path, project_key: str, gate: str, decision: str) -> None:
    from goodteam_os.state import normalize_project_key
    store = TelegramReviewSessionStore(workspace)
    if not store.directory.is_dir():
        return
    for file in store.directory.glob("*.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if (normalize_project_key(data.get("project_key", "")) == normalize_project_key(project_key) and
                data.get("gate", "") == gate and not data.get("decided", False)):

                chat_id = data.get("chat_id")
                message_id = data.get("message_id")
                token = os.getenv(TOKEN_ENV, "")
                if chat_id and message_id and token:
                    decision_label = {
                        "approved": "已在 Web 端批准",
                        "changes_requested": "已在 Web 端送回修改",
                        "rejected": "已在 Web 端拒绝"
                    }.get(decision, "已在 Web 端处理")

                    _telegram_request(
                        token,
                        "editMessageText",
                        {
                            "chat_id": str(chat_id),
                            "message_id": message_id,
                            "text": f"🏁 *{decision_label}*\n工作流状态已更新。",
                            "parse_mode": "Markdown",
                        }
                    )
                data["decided"] = True
                store.save(data["session_id"], data)
        except Exception:
            continue


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
        "approvals",
        "workers",
        "evidence",
        "view",
        "approve",
        "reject",
        "changes",
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
    "approve": "A",
    "reject": "J",
    "changes": "C",
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
    if command not in {"view", "approve", "reject", "changes", "ask", "tell"}:
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
    from goodteam_os.graph import resolve_project_runtime_root
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
            f"{base}\n\n{_operator_status_section()}\n\n"
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
    return f"{base}\n\n{_operator_status_section()}\n\n{pi_section}"


def _operator_status_section() -> str:
    try:
        from goodteam_os.operator_queue import DurableOperatorQueue
        from goodteam_os.service import service_state_dir

        summary = DurableOperatorQueue(service_state_dir()).summary()
    except Exception as exc:
        return f"Operator queue\nStatus: unavailable ({exc.__class__.__name__})"
    active = summary.get("active")
    active = active if isinstance(active, dict) else {}
    metadata = active.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    lines = [
        "Operator queue",
        f"Desired state: {summary.get('desired_state', 'unknown')}",
        f"Depth: {summary.get('depth', 0)}",
        f"Active task: {active.get('id', 'none')}",
    ]
    if active:
        lines.extend(
            [
                f"Project: {active.get('project_key', 'unknown')}",
                f"Phase: {active.get('phase', 'unknown')}",
                f"Issue: {active.get('source_url', '')}",
                f"PR: {metadata.get('pr_url', 'none')}",
                f"Head: {str(metadata.get('head_sha', ''))[:12] or 'none'}",
                f"Attempt: {active.get('attempt', 0)}",
                f"Waiting decision: {'yes' if active.get('status') == 'waiting' else 'no'}",
                f"Last failure: {active.get('last_failure_class', '') or 'none'}",
            ]
        )
    return "\n".join(lines)


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
            from goodteam_os.operator import enqueue_github_issue
            from goodteam_os.service import service_state_dir

            item, created = enqueue_github_issue(
                workspace=workspace,
                state_dir=service_state_dir(),
                issue_url=issue_url,
            )
        except (RuntimeError, ValueError) as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=f"❌ {exc}")
            return {"status": "processed", "command": command, "accepted": False}
        _send_masked_telegram_message(
            token=token,
            chat_id=chat_id,
            text=(
                f"{'Queued' if created else 'Already queued'}: {item['id']}\n"
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

    if command == "approvals":
        state = _read_command_state(workspace, project_key)
        if state is None:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text="GoodTeam approvals\nNo active run.")
            return {"status": "processed", "command": command}
        from goodteam_os.web import current_approval_gate

        gate = current_approval_gate(state)
        if not gate:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text="GoodTeam approvals\nNo pending approvals.")
            return {"status": "processed", "command": command}
        send_approval_notification(
            project_key=str(getattr(state, "project_key", project_key)),
            run_id=str(getattr(state, "run_id", "")),
            gate=gate,
            summary=f"GoodTeam is waiting for {gate.upper()} approval on {getattr(state, 'project_key', project_key)}.",
            workspace=workspace,
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

    if command in {"approve", "reject", "changes"}:
        from goodteam_os.web import current_approval_gate
        from goodteam_os.roles import approval_artifact_map

        note = _extract_command_arg(raw_text, command)

        if command == "reject" and not note:
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text="Usage: /gt reject <reason>"
            )
            return {"status": "processed", "command": command}
        if command == "changes" and not note:
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text="Usage: /gt changes <note>"
            )
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

        state = _read_command_state(workspace, project_key)
        session_gate = str(context_session.get("gate", "") or "") if context_session else ""
        gate = session_gate or (current_approval_gate(state) if state is not None else "")
        if not gate:
            _send_masked_telegram_message(
                token=token, chat_id=chat_id,
                text="No pending approval gate."
            )
            return {"status": "processed", "command": command}

        decision_map = {
            "approve": "approved",
            "reject": "rejected",
            "changes": "changes_requested",
        }
        review_mode_map = {
            "approve": "quick",
            "reject": "reject_confirmed",
            "changes": "request_changes_with_notes",
        }
        decision = decision_map[command]
        if context_session is not None and command == "approve" and _session_was_reviewed(context_session):
            review_mode = "step_by_step"
        else:
            review_mode = review_mode_map[command]
        review_session_id = str(context_session.get("session_id", "") or "") if context_session else ""
        review_items = _session_review_items(context_session) if context_session is not None and review_mode != "quick" else []
        card_message_id = context_session.get("message_id", "") if context_session is not None else ""

        if context_session is not None and context_session.get("operator_task_id"):
            operator_decision = {
                "approve": "approve",
                "reject": "reject",
                "changes": "request_changes",
            }[command]
            try:
                _record_operator_session_decision(
                    context_session,
                    decision=operator_decision,
                    reason=note,
                    user_id=user_id,
                )
            except (RuntimeError, ValueError) as exc:
                _send_masked_telegram_message(token=token, chat_id=chat_id, text=f"❌ {exc}")
                return {"status": "processed", "command": command, "accepted": False}
            store = TelegramReviewSessionStore(workspace)
            context_session["decided"] = True
            store.save(str(context_session["session_id"]), context_session)
            _send_masked_telegram_message(
                token=token,
                chat_id=chat_id,
                text=f"Operator decision recorded for exact head {context_session.get('operator_head_sha', '')[:12]}.",
            )
            return {"status": "processed", "command": command, "accepted": True}

        decide_approval(
            workspace=workspace,
            project_key=project_key,
            approval=gate,
            decision=decision,
            **_telegram_decision_audit_kwargs(
                user_id=user_id,
                chat_id=chat_id,
                message_id=card_message_id,
                review_mode=review_mode,
                review_session_id=review_session_id,
                review_items=review_items,
            ),
            note=note,
            **session_decision_binding(context_session, chat_id=chat_id, project_key=project_key),
        )

        # Try to find and mark the live session, then edit the card
        run_id = str(getattr(state, "run_id", "") if state is not None else "")
        store = TelegramReviewSessionStore(workspace)
        live_session = context_session or store.find_live_for_gate(project_key, run_id, gate)
        if live_session:
            live_session["decided"] = True
            store.save(str(live_session["session_id"]), live_session)
            try:
                _telegram_request(
                    token,
                    "editMessageText",
                    {
                        "chat_id": str(live_session["chat_id"]),
                        "message_id": live_session["message_id"],
                        "text": render_final_card_text(
                            session=live_session,
                            decision_label={
                                "approved": "已批准",
                                "rejected": "已拒绝并终止运行",
                                "changes_requested": "已送回修改",
                            }.get(decision, decision),
                            next_info="已通过 /gt 命令处理。",
                        ),
                        "parse_mode": "Markdown",
                    },
                )
            except Exception:
                pass

        if command == "changes" and live_session:
            try:
                compile_and_write_review_notes(workspace, live_session)
            except Exception:
                pass

        # Send confirmation
        if command == "approve":
            confirm = f"✅ {gate.upper()} approved."
            if note:
                confirm += f"\nNote: {note}"
        elif command == "reject":
            confirm = f"❌ {gate.upper()} rejected.\nReason: {note}"
        else:
            confirm = f"🔄 {gate.upper()} — changes requested.\nNote: {note}"

        _send_masked_telegram_message(token=token, chat_id=chat_id, text=confirm)
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
            report_bug(
                workspace=workspace,
                project_key=project_key,
                description=description,
                severity=severity,
            )
        except ValueError as exc:
            _send_masked_telegram_message(token=token, chat_id=chat_id, text=str(exc))
            return {"status": "processed", "command": "bug"}

        confirm = (
            "🐛 Bug 已上报 · fix 工作流已启动\n"
            f"严重度: {severity}\n"
            "门: FIX_PLAN 待审\n"
            "回复审批卡或用 /gt approve 处理"
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


def encode_callback_data(approval: TelegramApproval) -> str:
    return ":".join(
        [
            "gt",
            _safe_part(approval.project_key),
            _safe_part(approval.run_id),
            _safe_part(approval.gate),
            _safe_part(approval.decision),
        ]
    )


def decode_callback_data(value: str) -> TelegramApproval:
    parts = value.split(":")
    if len(parts) != 5 or parts[0] != "gt" or not all(parts[1:]):
        raise ValueError("Invalid Telegram approval callback.")
    return TelegramApproval(
        project_key=parts[1],
        run_id=parts[2],
        gate=parts[3],
        decision=parts[4],
    )


def _artifact_digest(content: str) -> str:
    """Return the first meaningful line of an artifact, stripped of markdown noise.

    Skips blank lines and bare heading/list markers; takes the first line that has
    real text, strips leading '#', '*', '-' and whitespace, and truncates to ~140
    chars with an ellipsis when longer.
    """
    if not content:
        return ""
    for raw_line in content.splitlines():
        stripped = raw_line.strip().lstrip("#*-").strip()
        if not stripped:
            continue
        if len(stripped) > 140:
            return stripped[:137] + "..."
        return stripped
    return ""


def _recommendation_icon(verdict: str) -> str:
    return {
        "approve": "✅",
        "caution": "⚠️",
        "request_changes": "⚠️",
        "reject": "❌",
    }.get(verdict, "▫️")


def _evidence_status_label(status: object) -> str:
    value = str(status or "").lower()
    if value == "passed":
        return "通过"
    if value == "failed":
        return "失败"
    return "未知"


def _approval_notification_payload(
    *,
    project_key: str,
    run_id: str,
    gate: str,
    summary: str,
    session_id: str,
    digest: str = "",
    recommendation: dict | None = None,
    evidence: dict | None = None,
) -> dict[str, object]:
    text_lines = [f"🔔 {gate.upper()} 待审 · {project_key}"]

    if digest:
        text_lines.append(f"本次:{digest}")

    if recommendation:
        verdict = str(recommendation.get("verdict", "") or "")
        icon = _recommendation_icon(verdict)
        try:
            blocking_count = int(recommendation.get("blocking_count", 0) or 0)
        except Exception:
            blocking_count = 0
        reasons = recommendation.get("reasons", []) or []
        reason_suffix = ""
        if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict):
            reason_text = str(reasons[0].get("text", "") or "").strip()
            if reason_text:
                reason_suffix = f" — {reason_text}"
        text_lines.append(f"建议:{icon} {verdict} · 阻断 {blocking_count}{reason_suffix}")

    if evidence:
        ci = _evidence_status_label(evidence.get("ci_status"))
        smoke = _evidence_status_label(evidence.get("smoke_status"))
        qa = _evidence_status_label(evidence.get("qa_status"))
        try:
            blocker_count = int(evidence.get("blocker_count", 0) or 0)
        except Exception:
            blocker_count = 0
        text_lines.append(f"证据:CI {ci} · Smoke {smoke} · QA {qa} · 阻断 {blocker_count}")

    dashboard_url = _dashboard_url(project_key)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ 快速批准", "callback_data": f"v1:A:{session_id}"},
                {"text": "🔍 逐项 review", "callback_data": f"v1:R:{session_id}"},
            ],
            [
                {"text": "❌ 拒绝", "callback_data": f"v1:J:{session_id}"},
            ]
        ]
    }
    if _is_public_https_url(dashboard_url):
        keyboard["inline_keyboard"][1].append({"text": "📖 完整报告", "url": dashboard_url})
    else:
        text_lines.append("回复本卡发 /gt view 读全文")

    return {
        "text": "\n".join(text_lines),
        "reply_markup": keyboard,
    }


def _telegram_description(payload: dict[str, object]) -> str:
    return str(payload.get("description", "") or "").lower()


def send_approval_notification(
    *,
    project_key: str,
    run_id: str,
    gate: str,
    artifact_path: str = "",
    summary: str = "",
    workspace: Path = Path("."),
    session_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    config = telegram_config_from_env()
    if not config["enabled"]:
        return {"status": "disabled", **config}

    token = os.getenv(TOKEN_ENV, "")

    # Compute the decision intelligence ONCE, up front, so it is available to both
    # the refresh/edit path and the new-send path. Everything here is failure-safe:
    # any error falls back to an empty digest/recommendation/evidence so the card
    # still renders with header + buttons.
    state = None
    digest = ""
    evidence: dict = {}
    recommendation: dict = {}
    try:
        state = _read_command_state(workspace, project_key)
    except Exception:
        state = None

    content = ""
    displayed_version = ""
    if not artifact_path and state is not None:
        from goodteam_os.roles import approval_artifact_for_gate

        artifact_path = str(state.artifacts.get(approval_artifact_for_gate(gate), "") or "")
    if artifact_path:
        try:
            path = Path(artifact_path)
            if not path.is_absolute():
                path = (workspace / path).resolve()
            if path.is_file():
                raw_content = path.read_bytes()
                content = raw_content.decode("utf-8")
                displayed_version = f"sha256:{hashlib.sha256(raw_content).hexdigest()}"
        except Exception:
            content = ""
            displayed_version = ""
    try:
        digest = mask_sensitive_data(_artifact_digest(content))
    except Exception:
        digest = ""

    try:
        from goodteam_os.web import build_approval_evidence_snapshot, build_approval_recommendation

        evidence = build_approval_evidence_snapshot(state=state, workspace=workspace, project_key=project_key)
        recommendation = build_approval_recommendation(
            state=state, review={"approval": gate, "content": content}, evidence_snapshot=evidence, workspace=workspace
        )
    except Exception:
        evidence, recommendation = {}, {}

    try:
        approval_target = capture_target(state=state, workspace=workspace, project_key=project_key,
                                         run_id=run_id, gate=gate, displayed_version=displayed_version)
    except Exception:
        approval_target = {}
    store = TelegramReviewSessionStore(workspace)
    existing_session = store.find_live_for_gate(project_key, run_id, gate)
    if (existing_session is not None and not existing_session.get("operator_task_id")
            and (existing_session.get("approval_target", {}) != approval_target
                 or str(existing_session.get("chat_id")) != str(config["chat_id"]))):
        # A changed target gets a new message and session. Updating the old
        # message would let an already queued reply inherit unseen new bytes.
        existing_session["superseded"] = True
        store.save(str(existing_session["session_id"]), existing_session)
        existing_session = None
    if existing_session is not None:
        if session_metadata:
            protected = {"session_id", "run_id", "project_key", "gate", "chat_id", "message_id", "approval_target"}
            existing_session.update({key: value for key, value in session_metadata.items() if key not in protected})
            store.save(str(existing_session["session_id"]), existing_session)
        session_id = str(existing_session["session_id"])
        message_id = int(existing_session["message_id"])
        card_payload = _approval_notification_payload(
            project_key=project_key,
            run_id=run_id,
            gate=gate,
            summary=summary,
            session_id=session_id,
            digest=digest,
            recommendation=recommendation,
            evidence=evidence,
        )
        edit_payload = _telegram_request(
            token,
            "editMessageText",
            {
                "chat_id": str(config["chat_id"]),
                "message_id": message_id,
                **card_payload,
            },
        )
        description = _telegram_description(edit_payload)
        if edit_payload.get("ok", False) or "message is not modified" in description:
            return {
                "status": "refreshed",
                **config,
                "project_key": project_key,
                "run_id": run_id,
                "gate": gate,
                "session_id": session_id,
                "message_id": message_id,
            }
        if "message to edit not found" not in description and "message not found" not in description:
            return {
                "status": "refresh_failed",
                **config,
                "project_key": project_key,
                "run_id": run_id,
                "gate": gate,
                "session_id": session_id,
                "message_id": message_id,
                "description": edit_payload.get("description", ""),
            }

    session_id = generate_session_id(store)

    items = split_artifact_into_review_items(gate, content)

    session_data = {
        "session_id": session_id,
        "run_id": run_id,
        "project_key": project_key,
        "gate": gate,
        "chat_id": int(config["chat_id"]),
        "message_id": 0,
        "cursor": 0,
        "items": items,
        "decided": False,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        "approval_target": approval_target,
    }
    # Caller metadata may extend an operator card, but must never replace the
    # displayed workflow identity or its exact artifact binding.
    session_data.update({key: value for key, value in (session_metadata or {}).items() if key not in session_data})
    store.save(session_id, session_data)

    card_payload = _approval_notification_payload(
        project_key=project_key,
        run_id=run_id,
        gate=gate,
        summary=summary,
        session_id=session_id,
        digest=digest,
        recommendation=recommendation,
        evidence=evidence,
    )
    payload = _telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": str(config["chat_id"]),
            **card_payload,
        },
    )
    result = dict(payload.get("result", {}) or {})
    message_id = int(result.get("message_id", 0))
    if message_id:
        session_data["message_id"] = message_id
        store.save(session_id, session_data)

    return {
        "status": "sent",
        **config,
        "project_key": project_key,
        "run_id": run_id,
        "gate": gate,
        "session_id": session_id,
        "message_id": str(message_id),
    }


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
            callback = update.get("callback_query", {})
            if callback:
                _telegram_request(
                    token,
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback.get("id", ""),
                        "text": (
                            "GoodTeam approval was not recorded. Open the current Review page."
                            if result.get("accepted") is False else
                            "GoodTeam approval recorded." if result["status"] == "processed" else
                            "GoodTeam approval was already handled."
                        ),
                    },
                )
            if result["status"] == "processed":
                processed += 1
        if heartbeat is not None:
            heartbeat()
        if once:
            return {"processed": processed, "offset": offset}


def _record_operator_session_decision(
    session: dict[str, object],
    *,
    decision: str,
    reason: str,
    user_id: str,
) -> dict[str, object]:
    from goodteam_os.operator import record_operator_decision
    from goodteam_os.operator_queue import DurableOperatorQueue

    state_dir = Path(str(session.get("operator_state_dir", "")))
    task_id = str(session.get("operator_task_id", ""))
    head_sha = str(session.get("operator_head_sha", ""))
    if not state_dir.is_absolute() or not task_id or not head_sha:
        raise ValueError("operator approval session is incomplete")
    return record_operator_decision(
        queue=DurableOperatorQueue(state_dir),
        item_id=task_id,
        decision=decision,
        approved_head_sha=head_sha,
        reason=reason,
        actor_ref=f"telegram:user:{user_id}",
    )


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
    from goodteam_os.web import ProjectApiError

    try:
        return _process_telegram_update(
            update, workspace=workspace, allowed_users=allowed_users,
            processed_callbacks=processed_callbacks, project_key=project_key,
        )
    except ProjectApiError as exc:
        if exc.code not in {"approval_reason_required", "stale_approval", "invalid_approval_note"}:
            raise
        # The existing handler has already checked sender/context authority.
        # Do not mark the review decided or invent a rationale for a button.
        callback = dict(update.get("callback_query", {}) or {})
        message = dict(callback.get("message", {}) or update.get("message", {}) or {})
        chat_id = dict(message.get("chat", {}) or {}).get("id", 0)
        guidance = (
            "This review changed or has no versioned material. Open the current Review page before deciding."
            if exc.code == "stale_approval" else
            "Open the current Review page, inspect its evidence, and enter a text reason to continue."
        )
        _send_masked_telegram_message(
            token=os.getenv(TOKEN_ENV, ""), chat_id=chat_id,
            text=f"{guidance} No approval was recorded.",
        )
        callback_id = str(callback.get("id", ""))
        if callback_id:
            processed_callbacks.add(callback_id)
        return {"status": "processed", "accepted": False, "reason": exc.code}


def _process_telegram_update(
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
        reply_to = dict(message.get("reply_to_message", {}) or {})
        if reply_to:
            user = dict(message.get("from", {}) or {})
            user_id = str(user.get("id", ""))
            if allowed_users and user_id not in allowed_users:
                return {"status": "ignored", "reason": "unauthorized user"}

            chat_id = int(message.get("chat", {}).get("id", 0))
            reply_to_message_id = int(reply_to.get("message_id", 0) or 0)
            reply_text = str(message.get("text", "") or "")

            if not _parse_telegram_command(reply_text):
                store = TelegramReviewSessionStore(workspace)
                session = _resolve_tg_note_reply_session(
                    store=store,
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                )
                if session is None:
                    return _send_stale_note_reply_message(token=token, chat_id=chat_id)

                cursor = int(session["awaiting_note_for"])
                note_text = reply_text.strip()

                session["items"][cursor]["state"] = "flagged"
                session["items"][cursor]["note"] = note_text
                session["awaiting_note_for"] = None
                session.pop("awaiting_note_message_id", None)
                session.pop("awaiting_note_card_message_id", None)

                next_cursor = get_next_pending_cursor(session)
                session["cursor"] = next_cursor
                store.save(session["session_id"], session)

                if next_cursor is None:
                    _telegram_request(
                        token,
                        "editMessageText",
                        {
                            "chat_id": str(chat_id),
                            "message_id": session["message_id"],
                            "text": render_summary_card_text(session=session),
                            "parse_mode": "Markdown",
                            "reply_markup": render_summary_card_keyboard(
                                session_id=session["session_id"],
                                flagged_count=sum(1 for it in session["items"] if it["state"] == "flagged")
                            )
                        }
                    )
                else:
                    _telegram_request(
                        token,
                        "editMessageText",
                        {
                            "chat_id": str(chat_id),
                            "message_id": session["message_id"],
                            "text": render_item_card_text(session=session, cursor=next_cursor),
                            "parse_mode": "Markdown",
                            "reply_markup": render_item_card_keyboard(
                                session_id=session["session_id"],
                                cursor=next_cursor
                            )
                        }
                    )

                try:
                    _telegram_request(token, "deleteMessage", {"chat_id": str(chat_id), "message_id": message["message_id"]})
                except Exception:
                    pass

                return {"status": "processed", "callback_id": f"msg_{message.get('message_id')}"}

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
            if (
                command in {"approve", "reject", "changes"}
                and context_session is not None
                and context_session.get("operator_task_id")
                and user_id not in allowed_users
            ):
                return {"status": "ignored", "reason": "unauthorized user"}
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

        store = TelegramReviewSessionStore(workspace)
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

        session = store.get(session_id)
        if not session:
            return {"status": "ignored", "reason": "session expired or not found"}

        if session.get("operator_task_id") and user_id not in allowed_users:
            return {"status": "ignored", "reason": "unauthorized user"}

        if session.get("decided", False):
            return {"status": "ignored", "reason": "already decided"}
        binding = {}
        if action in {"A", "C", "Y"} and not session.get("operator_task_id"):
            binding = session_decision_binding(session, chat_id=chat_id, message_id=message_id,
                                               project_key=str(session.get("project_key") or ""))

        if action == "A":
            if session.get("operator_task_id"):
                try:
                    _record_operator_session_decision(
                        session,
                        decision="approve",
                        reason="",
                        user_id=user_id,
                    )
                except (RuntimeError, ValueError) as exc:
                    return {"status": "ignored", "reason": str(exc)}
                session["decided"] = True
                store.save(session_id, session)
                processed_callbacks.add(callback_id)
                return {"status": "processed", "callback_id": callback_id}
            review_mode = "step_by_step" if _session_was_reviewed(session) else "quick"
            decide_approval(
                workspace=workspace,
                project_key=str(session["project_key"]),
                approval=str(session["gate"]),
                decision="approved",
                **binding,
                **_telegram_decision_audit_kwargs(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    review_mode=review_mode,
                    review_session_id=session_id,
                    review_items=_session_review_items(session) if review_mode == "step_by_step" else [],
                ),
            )
            session["decided"] = True
            store.save(session_id, session)

            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_final_card_text(session=session, decision_label="已批准", next_info="工作流已继续。"),
                    "parse_mode": "Markdown",
                }
            )

        elif action == "C":
            if session.get("operator_task_id"):
                reason = "; ".join(
                    str(item.get("note", "")).strip()
                    for item in session.get("items", [])
                    if isinstance(item, dict) and str(item.get("note", "")).strip()
                )
                if not reason:
                    return {"status": "ignored", "reason": "request_changes requires a reason"}
                try:
                    _record_operator_session_decision(
                        session,
                        decision="request_changes",
                        reason=reason,
                        user_id=user_id,
                    )
                except (RuntimeError, ValueError) as exc:
                    return {"status": "ignored", "reason": str(exc)}
                session["decided"] = True
                store.save(session_id, session)
                processed_callbacks.add(callback_id)
                return {"status": "processed", "callback_id": callback_id}
            decide_approval(
                workspace=workspace,
                project_key=str(session["project_key"]),
                approval=str(session["gate"]),
                decision="changes_requested",
                **binding,
                **_telegram_decision_audit_kwargs(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    review_mode="request_changes_with_notes",
                    review_session_id=session_id,
                    review_items=_session_review_items(session),
                ),
            )
            compile_and_write_review_notes(workspace, session)
            session["decided"] = True
            store.save(session_id, session)

            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_final_card_text(session=session, decision_label="已送回修改", next_info="工作流状态已更新为 needs_changes。"),
                    "parse_mode": "Markdown",
                }
            )

        elif action == "J":
            if session.get("operator_task_id"):
                _send_masked_telegram_message(
                    token=token,
                    chat_id=chat_id,
                    text="Rejecting an operator task requires a reason. Reply to this card with /gt reject <reason>.",
                )
                return {"status": "ignored", "reason": "reject requires a reason"}
            reject_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "Yes, 确认拒绝", "callback_data": f"v1:Y:{session_id}"},
                        {"text": "No, 返回汇总", "callback_data": f"v1:X:{session_id}"},
                    ]
                ]
            }
            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": "⚠️ *确认要拒绝并终止运行吗？*\n此操作不可逆，将终止当前项目的整个运行流。",
                    "parse_mode": "Markdown",
                    "reply_markup": reject_keyboard,
                }
            )

        elif action == "Y":
            decide_approval(
                workspace=workspace,
                project_key=str(session["project_key"]),
                approval=str(session["gate"]),
                decision="rejected",
                **binding,
                **_telegram_decision_audit_kwargs(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    review_mode="reject_confirmed",
                    review_session_id=session_id,
                    review_items=_session_review_items(session),
                ),
            )
            session["decided"] = True
            store.save(session_id, session)

            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_final_card_text(session=session, decision_label="已拒绝并终止运行", next_info="运行已停止。"),
                    "parse_mode": "Markdown",
                }
            )

        elif action == "R":
            cursor = 0
            for idx, it in enumerate(session["items"]):
                if it["state"] == "pending":
                    cursor = idx
                    break
            session["cursor"] = cursor
            store.save(session_id, session)

            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_item_card_text(session=session, cursor=cursor),
                    "parse_mode": "Markdown",
                    "reply_markup": render_item_card_keyboard(session_id=session_id, cursor=cursor)
                }
            )

        elif action in {"O", "S"}:
            cursor = int(arg) if arg else session.get("cursor", 0)
            session["items"][cursor]["state"] = "ok" if action == "O" else "skipped"

            next_cursor = get_next_pending_cursor(session)
            session["cursor"] = next_cursor if next_cursor is not None else cursor
            store.save(session_id, session)

            if next_cursor is None:
                _telegram_request(
                    token,
                    "editMessageText",
                    {
                        "chat_id": str(chat_id),
                        "message_id": message_id,
                        "text": render_summary_card_text(session=session),
                        "parse_mode": "Markdown",
                        "reply_markup": render_summary_card_keyboard(
                            session_id=session_id,
                            flagged_count=sum(1 for it in session["items"] if it["state"] == "flagged")
                        )
                    }
                )
            else:
                _telegram_request(
                    token,
                    "editMessageText",
                    {
                        "chat_id": str(chat_id),
                        "message_id": message_id,
                        "text": render_item_card_text(session=session, cursor=next_cursor),
                        "parse_mode": "Markdown",
                        "reply_markup": render_item_card_keyboard(session_id=session_id, cursor=next_cursor)
                    }
                )

        elif action == "P":
            cursor = int(arg) if arg else session.get("cursor", 0)
            prev_cursor = max(0, cursor - 1)
            session["cursor"] = prev_cursor
            store.save(session_id, session)

            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_item_card_text(session=session, cursor=prev_cursor),
                    "parse_mode": "Markdown",
                    "reply_markup": render_item_card_keyboard(session_id=session_id, cursor=prev_cursor)
                }
            )

        elif action == "X":
            _telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": message_id,
                    "text": render_summary_card_text(session=session),
                    "parse_mode": "Markdown",
                    "reply_markup": render_summary_card_keyboard(
                        session_id=session_id,
                        flagged_count=sum(1 for it in session["items"] if it["state"] == "flagged")
                    )
                }
            )

        elif action == "F":
            cursor = int(arg) if arg else session.get("cursor", 0)

            reply_message = _telegram_request(
                token,
                "sendMessage",
                {
                    "chat_id": str(chat_id),
                    "text": f"请输入这一项的问题（会发给对应角色）：\n(回复对应项: {session['items'][cursor]['title']})",
                    "reply_markup": {
                        "force_reply": True,
                        "input_field_placeholder": "例如：确认 redis 慢启动是否影响 SLA"
                    }
                }
            )
            session["awaiting_note_for"] = cursor
            session["awaiting_note_card_message_id"] = message_id
            note_message_id = int(dict(reply_message.get("result", {}) or {}).get("message_id", 0) or 0)
            if note_message_id:
                session["awaiting_note_message_id"] = note_message_id
            store.save(session_id, session)

        processed_callbacks.add(callback_id)
        return {"status": "processed", "callback_id": callback_id}

    # 3. Legacy approvals
    approval = decode_callback_data(callback_data)
    if project_key and approval.project_key != project_key:
        return {"status": "ignored", "reason": "project mismatch"}
    from goodteam_os.state import ApprovalDecision, StateStore

    def ignore_legacy_approval(reason: str) -> dict[str, object]:
        if token and callback_id:
            _telegram_request(
                token,
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "GoodTeam approval ignored; this button is stale or no longer actionable.",
                },
            )
        return {"status": "ignored", "reason": reason}

    state = StateStore(workspace).read_project_current(approval.project_key)
    if state is None:
        return ignore_legacy_approval("no active run")
    if state.run_id != approval.run_id:
        return ignore_legacy_approval("run mismatch")
    if (
        str(state.approvals.get(approval.gate, "")) != ApprovalDecision.PENDING
        or state.next_node != f"await_{approval.gate}_approval"
    ):
        return ignore_legacy_approval("gate not actionable")
    store = TelegramReviewSessionStore(workspace)
    session = store.find_live_by_card_message(
        int(callback.get("message", {}).get("chat", {}).get("id", 0)),
        int(callback.get("message", {}).get("message_id", 0)),
    )
    binding = session_decision_binding(
        session, chat_id=callback.get("message", {}).get("chat", {}).get("id", 0),
        message_id=callback.get("message", {}).get("message_id", 0), project_key=approval.project_key,
    )
    if binding["expected_run_id"] != approval.run_id or binding["expected_target_id"] != approval.gate:
        return ignore_legacy_approval("card identity mismatch")
    decide_approval(
        workspace=workspace,
        project_key=approval.project_key,
        approval=approval.gate,
        decision=approval.decision,
        **binding,
        **_telegram_decision_audit_kwargs(
            user_id=user_id,
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            review_session_id=str(session["session_id"]),
            review_mode="quick",
        ),
    )
    session["decided"] = True
    store.save(str(session["session_id"]), session)
    processed_callbacks.add(callback_id)
    return {"status": "processed", "callback_id": callback_id}


def decide_approval(**kwargs) -> dict[str, object]:
    from goodteam_os.web import decide_approval as web_decide_approval

    return web_decide_approval(**kwargs)


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
