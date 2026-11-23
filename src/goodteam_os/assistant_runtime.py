"""[INPUT]: 项目配置、角色配置与任务会话状态。
[OUTPUT]: Manager 运行时快照、验证状态和公开失败原因。
[POS]: 将内部运行时状态转换为任务助手可展示的数据。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from goodteam_os.manager_execution import _configured_manager_model, _manager_settings, _model_name
from goodteam_os.projects import load_project_target
from goodteam_os.worker_routing import resolve_role_adapter


def runtime_snapshot(config):
    if config is None:
        return {"provider": "builtin", "model": "", "reasoning_depth": "default", "fingerprint": "builtin"}
    target = load_project_target(config)
    settings = _manager_settings(target)
    public = {
        "provider": (resolve_role_adapter(target, "orchestrator")
                     if resolve_role_adapter(target, "orchestrator") in {"builtin", "fake", "noop", "human"} else "codex"),
        "model": _model_name(_configured_manager_model(target)),
        "reasoning_depth": settings.get("reasoning_depth", "default"),
    }
    if public["provider"] == "codex":
        public["provider_authority"] = "native_managed"
        public["transport"] = "app-server"
    encoded = json.dumps({**public, "settings": settings}, sort_keys=True).encode()
    return {**public, "fingerprint": hashlib.sha256(encoded).hexdigest()}


def runtime_view(config, session):
    current = runtime_snapshot(config)
    checked = session.get("runtime_verification") or {}
    status = checked.get("status", "unverified") if checked.get("fingerprint") == current["fingerprint"] else "unverified"
    return {**current, "status": status, "checked_at": checked.get("checked_at") if status != "unverified" else None,
            "error": checked.get("error") if status in {"failed", "interrupted"} else None}


def record_runtime(store, snapshot, status):
    def mutate(session):
        session["runtime_verification"] = {
            **snapshot, "status": status, "checked_at": datetime.now(UTC).isoformat(),
        }
        return session
    store._mutate(mutate)


def safe_failure(error):
    # Only return fixed diagnostics; provider logs may contain credentials or prompts.
    if getattr(error, "code", None) == "manager_resource_busy":
        return {"code": "manager_resource_busy", "message": "The Manager is processing another request. Retry this saved message when it is available."}
    raw = str(error).lower()
    categories = (
        ("context_window_exceeded", ("contextwindowexceeded", "context_window_exceeded",
                                     "context_length_exceeded", "maximum context length",
                                     "exceeds the context window")),
        ("model_unavailable", ("model is not supported", "unsupported model", "model_not_found", "model not found", "does not exist")),
        ("authentication_failed", ("unauthorized", "authentication", "not logged in", "401", "login")),
        ("connection_failed", ("connection", "network", "dns")),
        ("timed_out", ("timeout", "timed out")),
        ("invalid_response", ("structured", "json", "readable result")),
    )
    code = next((name for name, terms in categories if any(term in raw for term in terms)), "execution_failed")
    supplied = getattr(error, "code", None)
    if supplied in {"manager_stopped", "manager_interrupted", "manager_runtime_changed",
                    "operation_conflict"}:
        return {"code": supplied, "message": {
            "manager_stopped": "Manager work was stopped.",
            "manager_interrupted": "The original Manager turn was interrupted. Its native execution and tool receipts are retained.",
            "manager_runtime_changed": "Finish or stop the current Manager turn before changing its runtime.",
            "operation_conflict": "This request identity belongs to different original input.",
        }[supplied]}
    if supplied in {name for name, _terms in categories}:
        code = supplied
    if code == "context_window_exceeded":
        return {"code": code, "message": (
            "The current model cannot fit this session's context. The session is retained. "
            "Choose a model with a larger context window before retrying.")}
    return {"code": code, "message": "Task Assistant request failed. Check the runtime configuration and retry."}
