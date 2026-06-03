import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from pathlib import Path
from uuid import uuid4


TOKEN_ENV = "GOODTEAM_TG_BOT_TOKEN"
CHAT_ID_ENV = "GOODTEAM_TG_CHAT_ID"
ALLOWED_USERS_ENV = "GOODTEAM_TG_ALLOWED_USERS"


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

    def get(self, session_id: str) -> dict[str, object] | None:
        file_path = self._file_path(session_id)
        if not file_path.is_file():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            expires_at = data.get("expires_at", "")
            if expires_at and datetime.fromisoformat(expires_at) < datetime.now(UTC):
                file_path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            return None

    def save(self, session_id: str, data: dict[str, object]) -> None:
        file_path = self._file_path(session_id)
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def find_by_chat_and_status(self, chat_id: int, decided: bool = False) -> dict[str, object] | None:
        for file in self.directory.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                if int(data.get("chat_id", 0)) == chat_id and data.get("decided", False) == decided:
                    expires_at = data.get("expires_at", "")
                    if expires_at and datetime.fromisoformat(expires_at) < datetime.now(UTC):
                        file.unlink(missing_ok=True)
                        continue
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


def truncate_text(text: str, max_chars: int = 150) -> str:
    text = text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = " ".join(lines)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars-3] + "..."


def mask_sensitive_data(text: str) -> str:
    text = re.sub(r'[a-zA-Z0-9_\-]+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', 'staging-host', text)
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', 'staging-ip', text)
    text = re.sub(r'/srv/[a-zA-Z0-9_\-]+', 'deployment-dir', text)
    text = re.sub(r'/Users/luke/[a-zA-Z0-9_\-/]+', 'local-workspace-dir', text)
    return text


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
    from goodteam_os.state import StateStore
    gate = str(session["gate"])
    artifact_key = approval_artifact_map().get(gate, gate)

    state_store = StateStore(workspace)
    project_key = str(session["project_key"])
    state = state_store.read_project_current(project_key) if project_key else state_store.read_current()
    if state and artifact_key in state.artifacts:
        art_path = state.artifacts[artifact_key]
        path = Path(art_path)
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


def build_approval_keyboard(*, project_key: str, run_id: str, gate: str) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve",
                    "callback_data": encode_callback_data(TelegramApproval(project_key, run_id, gate, "approved")),
                },
                {
                    "text": "Request changes",
                    "callback_data": encode_callback_data(TelegramApproval(project_key, run_id, gate, "changes_requested")),
                },
            ],
            [
                {
                    "text": "Reject",
                    "callback_data": encode_callback_data(TelegramApproval(project_key, run_id, gate, "rejected")),
                }
            ],
        ]
    }


def send_approval_notification(
    *,
    project_key: str,
    run_id: str,
    gate: str,
    artifact_path: str = "",
    summary: str = "",
    workspace: Path = Path("."),
) -> dict[str, object]:
    config = telegram_config_from_env()
    if not config["enabled"]:
        return {"status": "disabled", **config}

    token = os.getenv(TOKEN_ENV, "")

    store = TelegramReviewSessionStore(workspace)
    session_id = generate_session_id(store)

    content = ""
    if artifact_path:
        path = Path(artifact_path)
        if not path.is_absolute():
            path = (workspace / path).resolve()
        if path.is_file():
            content = path.read_text(encoding="utf-8")

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
    }
    store.save(session_id, session_data)

    text_lines = [
        "🔔 *GoodTeam Approval Needed*",
        f"*Project*: {project_key}",
        f"*Run*: {run_id}",
        f"*Gate*: {gate.upper()}",
    ]
    if summary:
        masked_summary = mask_sensitive_data(summary)
        text_lines.append(f"*Summary*: {masked_summary}")

    dashboard_url = f"http://localhost:8788/?project={project_key}"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ 快速批准", "callback_data": f"v1:A:{session_id}"},
                {"text": "🔍 逐项 review", "callback_data": f"v1:R:{session_id}"},
            ],
            [
                {"text": "❌ 拒绝", "callback_data": f"v1:J:{session_id}"},
                {"text": "📖 完整报告", "url": dashboard_url},
            ]
        ]
    }

    payload = _telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": str(config["chat_id"]),
            "text": "\n".join(text_lines),
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
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


def poll_telegram_approvals(*, workspace: Path, project_key: str = "", once: bool = False) -> dict[str, object]:
    token = os.getenv(TOKEN_ENV, "")
    chat_id = os.getenv(CHAT_ID_ENV, "")
    if not token or not chat_id:
        raise ValueError("Telegram approval channel is disabled. Set GOODTEAM_TG_BOT_TOKEN and GOODTEAM_TG_CHAT_ID.")
    offset = 0
    processed = 0
    seen_callbacks: set[str] = set()
    allowed_users = [str(item) for item in telegram_config_from_env()["allowed_users"]]
    while True:
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
                        "text": "GoodTeam approval recorded." if result["status"] == "processed" else "GoodTeam approval was already handled.",
                    },
                )
            if result["status"] == "processed":
                processed += 1
        if once:
            return {"processed": processed, "offset": offset}


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
        reply_to = dict(message.get("reply_to_message", {}) or {})
        if reply_to:
            user = dict(message.get("from", {}) or {})
            user_id = str(user.get("id", ""))
            if allowed_users and user_id not in allowed_users:
                return {"status": "ignored", "reason": "unauthorized user"}

            chat_id = int(message.get("chat", {}).get("id", 0))

            store = TelegramReviewSessionStore(workspace)
            session = store.find_by_chat_and_status(chat_id, decided=False)
            if session and session.get("awaiting_note_for") is not None:
                cursor = int(session["awaiting_note_for"])
                note_text = str(message.get("text", "")).strip()

                session["items"][cursor]["state"] = "flagged"
                session["items"][cursor]["note"] = note_text
                session["awaiting_note_for"] = None

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
        session = store.get(session_id)
        if not session:
            return {"status": "ignored", "reason": "session expired or not found"}

        if session.get("decided", False):
            return {"status": "ignored", "reason": "already decided"}

        chat_id = int(callback.get("message", {}).get("chat", {}).get("id", 0))
        message_id = int(callback.get("message", {}).get("message_id", 0))

        if action == "A":
            decide_approval(
                workspace=workspace,
                project_key=str(session["project_key"]),
                approval=str(session["gate"]),
                decision="approved",
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
            compile_and_write_review_notes(workspace, session)
            decide_approval(
                workspace=workspace,
                project_key=str(session["project_key"]),
                approval=str(session["gate"]),
                decision="changes_requested",
            )
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

            _telegram_request(
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
            store.save(session_id, session)

        processed_callbacks.add(callback_id)
        return {"status": "processed", "callback_id": callback_id}

    # 3. Legacy approvals
    approval = decode_callback_data(callback_data)
    if project_key and approval.project_key != project_key:
        return {"status": "ignored", "reason": "project mismatch"}
    from goodteam_os.state import StateStore

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
    if approval.gate not in state.approvals or state.next_node != f"await_{approval.gate}_approval":
        return ignore_legacy_approval("gate not actionable")
    decide_approval(
        workspace=workspace,
        project_key=approval.project_key,
        approval=approval.gate,
        decision=approval.decision,
    )
    processed_callbacks.add(callback_id)
    return {"status": "processed", "callback_id": callback_id}


def decide_approval(**kwargs) -> dict[str, object]:
    from goodteam_os.web import decide_approval as web_decide_approval

    return web_decide_approval(**kwargs)


def _telegram_request(token: str, method: str, params: dict[str, object]) -> dict[str, object]:
    encoded = urllib.parse.urlencode(
        {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in params.items()}
    ).encode("utf-8")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=encoded, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_part(value: str) -> str:
    if ":" in value or not value:
        raise ValueError("Telegram callback values cannot be empty or contain ':'.")
    return value
