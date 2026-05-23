import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from goodteam_os.web import decide_approval


TOKEN_ENV = "GOODTEAM_TG_BOT_TOKEN"
CHAT_ID_ENV = "GOODTEAM_TG_CHAT_ID"
ALLOWED_USERS_ENV = "GOODTEAM_TG_ALLOWED_USERS"


@dataclass(frozen=True)
class TelegramApproval:
    project_key: str
    run_id: str
    gate: str
    decision: str


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
    approval = decode_callback_data(str(callback.get("data", "")))
    if project_key and approval.project_key != project_key:
        return {"status": "ignored", "reason": "project mismatch"}
    decide_approval(
        workspace=workspace,
        project_key=approval.project_key,
        approval=approval.gate,
        decision=approval.decision,
    )
    processed_callbacks.add(callback_id)
    return {"status": "processed", "callback_id": callback_id}


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
