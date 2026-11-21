"""Provider configuration, token usage, project memory, and Manager settings routes."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any

from goodteam.agents.roles import VALID_EFFORTS, normalize_skill_bindings
from goodteam.api.http_helpers import HttpError, read_json, send_json
from goodteam.api.routes_projects import _require_project
from goodteam.integrations import memory as mem_store
from goodteam.manager.tools import get_manager_settings
from goodteam.providers import ProviderError, fetch_models, normalize_provider_input, test_connection


def _resolve_provider_connection(app: Any, body: dict[str, Any]) -> dict[str, Any]:
    pid = str(body.get("provider_id") or "").strip()
    if pid and not body.get("base_url"):
        found = app.provider_store.get(pid)
        if not found:
            raise ProviderError("provider_not_found", "Provider not found.", 404)
        return found
    edit_id = str(body.get("id") or pid or "").strip()
    prev = app.provider_store.get(edit_id) if edit_id else None
    payload = dict(body)
    if not payload.get("name"):
        payload["name"] = (prev or {}).get("name") or "Preview"
    return normalize_provider_input(payload, prev)


def handle_list_providers(app: Any, handler: BaseHTTPRequestHandler) -> None:
    send_json(handler, app.provider_store.view())


def handle_save_provider(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    record = app.provider_store.save(body)
    send_json(handler, {"provider": record, **app.provider_store.view()}, status=201)


def handle_delete_provider(app: Any, handler: BaseHTTPRequestHandler, provider_id: str) -> None:
    deleted = app.provider_store.delete(provider_id)
    if not deleted:
        raise HttpError(404, "provider_not_found", f"Provider '{provider_id}' not found.")
    send_json(handler, {"deleted": True, "id": provider_id, **app.provider_store.view()})


def handle_activate_provider(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    agent = str(body.get("agent") or "").strip().lower()
    raw_pid = body.get("provider_id")
    pid = str(raw_pid).strip() if raw_pid and str(raw_pid).strip() != "native" else None
    send_json(handler, app.provider_store.activate(agent, pid))


def handle_provider_models(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    conn = _resolve_provider_connection(app, body)
    send_json(handler, fetch_models(conn))


def handle_provider_test(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    conn = _resolve_provider_connection(app, body)
    send_json(handler, test_connection(conn))


def handle_usage(app: Any, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    project_id = (query.get("project_id") or [""])[0].strip() or None
    send_json(handler, {"usage": app.store.usage_summary(project_id)})


def handle_get_memory(
    app: Any, handler: BaseHTTPRequestHandler, project_id: str, query: dict[str, list[str]]
) -> None:
    project = _require_project(app, project_id)
    files = mem_store.list_files(project["path"])
    search_q = (query.get("query") or [""])[0].strip()
    filename = (query.get("filename") or [""])[0].strip()
    if search_q:
        hits = mem_store.search(project["path"], search_q)
        send_json(handler, {"query": search_q, "hits": hits, "files": files})
        return
    if filename:
        content = mem_store.read_file(project["path"], filename)
        send_json(handler, {"filename": filename, "content": content, "files": files})
        return
    send_json(handler, {"files": files})


def handle_put_memory(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    project = _require_project(app, project_id)
    body = read_json(handler, required=True)
    filename = str(body.get("filename") or "").strip()
    content = str(body.get("content") or "")
    mode = str(body.get("mode") or "replace").strip()
    result = mem_store.write_file(project["path"], filename, content, mode=mode)
    files = mem_store.list_files(project["path"])
    send_json(handler, {**result, "files": files})


def handle_get_manager_settings(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    project = _require_project(app, project_id)
    settings = get_manager_settings(project)
    send_json(handler, {"settings": settings})


def handle_put_manager_settings(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    project = _require_project(app, project_id)
    body = read_json(handler, required=True)
    current = get_manager_settings(project)

    effort = str(body.get("reasoning_effort", current["reasoning_effort"]) or "high").strip().lower()
    if effort not in VALID_EFFORTS:
        raise HttpError(400, "invalid_effort", f"reasoning_effort must be one of {list(VALID_EFFORTS)}.")

    provider_id = body.get("provider_id", current["provider_id"])
    if provider_id in ("", "default"):
        provider_id = None
    elif provider_id is not None and provider_id != "native":
        found = app.provider_store.get(str(provider_id))
        if not found or found["agent"] != "codex":
            raise HttpError(400, "invalid_provider", "Manager provider must be a configured Codex provider or 'native'.")
        provider_id = found["id"]

    bindings = normalize_skill_bindings(
        body.get("skill_bindings", current["skill_bindings"])
    )
    updated_manager = {
        "model": str(body.get("model", current["model"]) or "").strip()[:200],
        "reasoning_effort": effort,
        "additional_prompt": str(body.get("additional_prompt", current["additional_prompt"]) or "")[:8000],
        "provider_id": provider_id,
        "skill_bindings": bindings,
    }
    proj_settings = dict(project.get("settings") or {})
    proj_settings["manager"] = updated_manager
    updated_proj = app.store.update_project(project_id, settings=proj_settings)
    assert updated_proj is not None
    send_json(handler, {"settings": get_manager_settings(updated_proj)})
