"""Agent Role CRUD and default reset routes."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any

from goodteam.agents.roles import save_role, seed_project_roles
from goodteam.api.http_helpers import HttpError, read_json, send_json
from goodteam.api.routes_projects import _require_project
from goodteam.skills.installer import ensure_builtins


def handle_list_roles(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    ensure_builtins(app.store)
    roles = seed_project_roles(app.store, project_id)
    send_json(handler, {"roles": roles})


def handle_create_role(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    body = read_json(handler, required=True)
    key = str(body.get("key") or "").strip().lower()
    if key and app.store.get_role(project_id, key) is not None:
        raise HttpError(409, "role_exists", f"Role '{key}' already exists in this project.")
    role = save_role(app.store, project_id, body)
    send_json(handler, {"role": role}, status=201)


def handle_update_role(app: Any, handler: BaseHTTPRequestHandler, project_id: str, role_key: str) -> None:
    _require_project(app, project_id)
    if app.store.get_role(project_id, role_key) is None:
        raise HttpError(404, "role_not_found", f"Role '{role_key}' not found.")
    body = read_json(handler, required=True)
    role = save_role(app.store, project_id, body, key_override=role_key)
    send_json(handler, {"role": role})


def handle_delete_role(app: Any, handler: BaseHTTPRequestHandler, project_id: str, role_key: str) -> None:
    _require_project(app, project_id)
    deleted = app.store.delete_role(project_id, role_key)
    if not deleted:
        raise HttpError(404, "role_not_found", f"Role '{role_key}' not found.")
    send_json(handler, {"deleted": True, "key": role_key})


def handle_reset_roles(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    ensure_builtins(app.store)
    roles = seed_project_roles(app.store, project_id, force=True)
    send_json(handler, {"roles": roles})
