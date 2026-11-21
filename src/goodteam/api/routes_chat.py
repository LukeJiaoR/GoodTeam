"""Manager Chat, SSE streaming, Attachments, Autopilot, Tasks, and GitHub Issues routes."""
from __future__ import annotations

import base64
import binascii
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import queue
import re
from typing import Any

from goodteam.api.events import CLOSE_EVENT, EventBus
from goodteam.api.http_helpers import HttpError, read_json, send_json
from goodteam.api.routes_projects import _require_project
from goodteam.config import attachments_dir
from goodteam.integrations import github as gh_client
from goodteam.schema import new_id


MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
VALID_TASK_STATUSES = {"todo", "in_progress", "review", "done", "blocked"}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(raw: str) -> str:
    leaf = Path((raw or "").replace("\\", "/")).name
    cleaned = _SAFE_FILENAME_RE.sub("_", leaf).strip("._")
    return cleaned[:100] or "attachment.bin"


def handle_sse(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    handler.close_connection = True
    sub_queue = app.event_bus.subscribe(project_id)
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        handler.wfile.write(b": connected\n\n")
        handler.wfile.flush()

        while True:
            try:
                event_type, payload = sub_queue.get(timeout=app.sse_keepalive_seconds)
            except queue.Empty:
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
                continue
            if event_type == CLOSE_EVENT:
                break
            handler.wfile.write(EventBus.format_sse(event_type, payload))
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        app.event_bus.unsubscribe(project_id, sub_queue)


def handle_list_messages(
    app: Any, handler: BaseHTTPRequestHandler, project_id: str, query: dict[str, list[str]]
) -> None:
    _require_project(app, project_id)
    raw_limit = (query.get("limit") or ["200"])[0]
    try:
        limit = max(1, min(int(raw_limit), 500))
    except ValueError:
        limit = 200
    send_json(handler, {"messages": app.store.list_messages(project_id, limit=limit)})


def handle_post_message(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    body = read_json(handler, required=True)
    content = str(body.get("content") if body.get("content") is not None else (body.get("text") or "")).strip()
    attachments = body.get("attachments") or []
    if not isinstance(attachments, list):
        raise HttpError(400, "invalid_attachments", "attachments must be a list.")
    if not content and not attachments:
        raise HttpError(400, "empty_message", "Message content or attachment is required.")
    result = app.manager.session(project_id).send(
        content,
        attachments=attachments,
        sync=bool(body.get("sync")),
    )
    send_json(handler, result, status=200 if result.get("steered") else 201)


def handle_stop_chat(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    stopped = app.manager.session(project_id).stop()
    send_json(handler, {"stopped": stopped})


def handle_upload_attachment(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    body = read_json(handler, required=True)
    raw_name = str(body.get("filename") or body.get("name") or "attachment.bin")
    b64 = str(body.get("content_base64") or "").strip()
    mime_type = str(body.get("mime_type") or "application/octet-stream").strip()[:120]
    if not b64:
        raise HttpError(400, "missing_content", "content_base64 is required.")
    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw_bytes = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HttpError(400, "invalid_base64", "Invalid base64 attachment payload.") from exc
    if len(raw_bytes) > MAX_ATTACHMENT_BYTES:
        raise HttpError(413, "attachment_too_large", "Attachment exceeds 15MB limit.")

    safe_name = _safe_filename(raw_name)
    att_id = new_id("att")
    base_dir = (attachments_dir(app.state_dir) / project_id).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest = (base_dir / f"{att_id}-{safe_name}").resolve()
    try:
        dest.relative_to(base_dir)
    except ValueError:
        raise HttpError(400, "invalid_filename", "Attachment path escapes storage directory.") from None
    dest.write_bytes(raw_bytes)
    send_json(
        handler,
        {
            "id": att_id,
            "name": safe_name,
            "filename": safe_name,
            "path": str(dest),
            "mime_type": mime_type,
            "size": len(raw_bytes),
        },
        status=201,
    )


def handle_get_autopilot(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    send_json(handler, app.autopilot.status(project_id))


def handle_start_autopilot(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    body = read_json(handler, required=True)
    objective = str(body.get("objective") or "").strip()
    if not objective:
        raise HttpError(400, "missing_objective", "Autopilot objective is required.")
    max_rounds = int(body.get("max_rounds") or 5)
    state = app.autopilot.start(project_id, objective, max_rounds=max_rounds)
    send_json(handler, state, status=201)


def handle_stop_autopilot(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    send_json(handler, app.autopilot.stop(project_id))


def handle_list_tasks(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    send_json(handler, {"tasks": app.store.list_tasks(project_id)})


def handle_create_task(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    _require_project(app, project_id)
    body = read_json(handler, required=True)
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        raise HttpError(400, "missing_title", "Task title is required.")
    status = str(body.get("status") or "todo").strip()
    if status not in VALID_TASK_STATUSES:
        raise HttpError(400, "invalid_status", f"Status must be one of {sorted(VALID_TASK_STATUSES)}.")
    task = app.store.create_task(
        project_id=project_id,
        title=title,
        description=str(body.get("description") or "")[:8000],
        status=status,
        assigned_role=str(body.get("assigned_role") or "").strip()[:80],
        priority=int(body.get("priority") or 0),
        source_ref=str(body.get("source_ref") or "").strip()[:200],
    )
    app.event_bus.publish(project_id, "task_updated", task)
    send_json(handler, {"task": task}, status=201)


def handle_update_task(app: Any, handler: BaseHTTPRequestHandler, project_id: str, task_id: str) -> None:
    _require_project(app, project_id)
    existing = app.store.get_task(task_id)
    if not existing or existing["project_id"] != project_id:
        raise HttpError(404, "task_not_found", f"Task '{task_id}' not found.")
    body = read_json(handler, required=True)
    fields: dict[str, Any] = {}
    if "title" in body:
        title = str(body["title"] or "").strip()[:200]
        if not title:
            raise HttpError(400, "invalid_title", "Task title cannot be empty.")
        fields["title"] = title
    if "description" in body:
        fields["description"] = str(body["description"] or "")[:8000]
    if "status" in body:
        status = str(body["status"] or "").strip()
        if status not in VALID_TASK_STATUSES:
            raise HttpError(400, "invalid_status", f"Status must be one of {sorted(VALID_TASK_STATUSES)}.")
        fields["status"] = status
    if "assigned_role" in body:
        fields["assigned_role"] = str(body["assigned_role"] or "").strip()[:80]
    if "priority" in body:
        fields["priority"] = int(body["priority"] or 0)
    updated = app.store.update_task(task_id, **fields)
    if updated:
        app.event_bus.publish(project_id, "task_updated", updated)
    send_json(handler, {"task": updated})


def handle_list_github_issues(
    app: Any, handler: BaseHTTPRequestHandler, project_id: str, query: dict[str, list[str]]
) -> None:
    project = _require_project(app, project_id)
    state = (query.get("state") or ["open"])[0]
    raw_limit = (query.get("limit") or ["30"])[0]
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise HttpError(400, "invalid_limit", "limit must be an integer.") from exc
    issues = gh_client.list_issues(project["path"], state=state, limit=limit)
    send_json(handler, {"issues": issues})


def handle_import_github_issue(
    app: Any, handler: BaseHTTPRequestHandler, project_id: str, number_str: str
) -> None:
    project = _require_project(app, project_id)
    try:
        number = int(number_str)
    except ValueError as exc:
        raise HttpError(400, "invalid_issue_number", "Issue number must be an integer.") from exc
    task = gh_client.import_issue_to_task(app.store, project_id, project["path"], number)
    app.event_bus.publish(project_id, "task_updated", task)
    send_json(handler, {"task": task}, status=201)
