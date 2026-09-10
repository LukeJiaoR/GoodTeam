from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from goodteam_os.graph import resolve_role_adapter
from goodteam_os.manager_execution import _configured_manager_model, _manager_settings, _model_name
from goodteam_os.projects import load_project_target


def runtime_snapshot(config):
    if config is None:
        return {"provider": "builtin", "model": "", "reasoning_depth": "default", "fingerprint": "builtin"}
    target = load_project_target(config)
    settings = _manager_settings(target)
    public = {
        "provider": resolve_role_adapter(target, "orchestrator"),
        "model": _model_name(_configured_manager_model(target)),
        "reasoning_depth": settings.get("reasoning_depth", "default"),
    }
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
    raw = str(error).lower()
    categories = (
        ("model_unavailable", ("model is not supported", "unsupported model", "model_not_found", "model not found", "does not exist")),
        ("authentication_failed", ("unauthorized", "authentication", "not logged in", "401", "login")),
        ("connection_failed", ("connection", "network", "dns")),
        ("timed_out", ("timeout", "timed out")),
        ("invalid_response", ("structured", "json", "readable result")),
    )
    code = next((name for name, terms in categories if any(term in raw for term in terms)), "execution_failed")
    supplied = getattr(error, "code", None)
    if supplied in {name for name, _terms in categories}:
        code = supplied
    return {"code": code, "message": "Task Assistant request failed. Check the runtime configuration and retry."}
