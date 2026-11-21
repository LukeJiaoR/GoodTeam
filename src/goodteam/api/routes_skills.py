"""Skill library routes: list, read, GitHub/zip/local install, discover, and uninstall."""
from __future__ import annotations

import base64
import binascii
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from goodteam.api.http_helpers import HttpError, read_body_bytes, read_json, send_json
from goodteam.skills.installer import (
    MAX_ZIP_UNCOMPRESSED,
    discover_skills,
    ensure_builtins,
    install_github,
    install_local,
    install_zip,
    preview_github,
    preview_zip,
    read_skill_resource,
    uninstall_skill,
)


def _extract_zip_request(
    handler: BaseHTTPRequestHandler, query: dict[str, list[str]]
) -> tuple[bytes, str, list[str] | None]:
    content_type = (handler.headers.get("Content-Type") or "").lower()
    if "application/zip" in content_type or "application/octet-stream" in content_type:
        raw = read_body_bytes(handler, max_bytes=MAX_ZIP_UNCOMPRESSED + 1024)
        if not raw:
            raise HttpError(400, "empty_zip", "Uploaded zip body is empty.")
        filename = (query.get("filename") or ["upload.zip"])[0].strip() or "upload.zip"
        sel_raw = query.get("selected_paths")
        selected = [p for p in sel_raw if p] if sel_raw else None
        return raw, filename, selected

    body = read_json(handler, required=True)
    b64 = str(body.get("content_base64") or "").strip()
    if not b64:
        raise HttpError(400, "missing_zip", "content_base64 is required.")
    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HttpError(400, "invalid_base64", "Invalid base64 zip payload.") from exc
    filename = str(body.get("filename") or "upload.zip").strip()[:120] or "upload.zip"
    selected = body.get("selected_paths")
    if selected is not None and not isinstance(selected, list):
        raise HttpError(400, "invalid_selected_paths", "selected_paths must be a list of strings.")
    return raw, filename, ([str(p) for p in selected] if selected is not None else None)


def handle_list_skills(app: Any, handler: BaseHTTPRequestHandler) -> None:
    ensure_builtins(app.store)
    send_json(handler, {"skills": app.store.list_skills()})


def handle_skill_content(
    app: Any, handler: BaseHTTPRequestHandler, skill_id: str, query: dict[str, list[str]]
) -> None:
    ensure_builtins(app.store)
    rec = app.store.get_skill(skill_id)
    if not rec:
        raise HttpError(404, "skill_not_found", f"Skill '{skill_id}' not found.")
    rel_path = (query.get("path") or ["SKILL.md"])[0].strip() or "SKILL.md"
    content = read_skill_resource(rec, rel_path)
    root = Path(rec["path"]).resolve()
    files = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    )[:200] if root.is_dir() else []
    send_json(handler, {
        "id": rec["id"],
        "name": rec["name"],
        "description": rec["description"],
        "source_type": rec["source_type"],
        "source_ref": rec["source_ref"],
        "commit_sha": rec["commit_sha"],
        "path": rel_path,
        "content": content,
        "files": files,
    })


def handle_github_preview(_app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    url = str(body.get("url") or body.get("source") or body.get("repo") or "").strip()
    send_json(handler, preview_github(url))


def handle_github_install(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    url = str(body.get("url") or body.get("source") or body.get("repo") or "").strip()
    selected = body.get("selected_paths")
    if selected is not None and not isinstance(selected, list):
        raise HttpError(400, "invalid_selected_paths", "selected_paths must be a list of strings.")
    installed = install_github(
        app.store,
        url,
        selected_paths=[str(p) for p in selected] if selected is not None else None,
        state_dir=app.state_dir,
    )
    send_json(handler, {"skills": installed}, status=201)


def handle_zip_preview(_app: Any, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    raw, _, _ = _extract_zip_request(handler, query)
    send_json(handler, preview_zip(raw))


def handle_zip_install(app: Any, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    raw, filename, selected = _extract_zip_request(handler, query)
    installed = install_zip(
        app.store,
        raw,
        filename=filename,
        selected_paths=selected,
        state_dir=app.state_dir,
    )
    send_json(handler, {"skills": installed}, status=201)


def handle_local_install(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    folder = str(body.get("path") or "").strip()
    if not folder:
        raise HttpError(400, "missing_path", "Local skill directory path is required.")
    rec = install_local(app.store, folder, state_dir=app.state_dir)
    send_json(handler, {"skill": rec, "skills": [rec]}, status=201)


def handle_discover_skills(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=False)
    project_id = str(body.get("project_id") or "").strip()
    project_path: str | None = None
    if project_id:
        project = app.store.get_project(project_id)
        if not project:
            raise HttpError(404, "project_not_found", f"Project '{project_id}' not found.")
        project_path = project["path"]
    include_user = bool(body.get("include_user_dirs", False))
    found = discover_skills(app.store, project_path=project_path, include_user_dirs=include_user)
    send_json(handler, {"skills": found})


def handle_delete_skill(app: Any, handler: BaseHTTPRequestHandler, skill_id: str) -> None:
    deleted = uninstall_skill(app.store, skill_id, state_dir=app.state_dir)
    if not deleted:
        raise HttpError(404, "skill_not_found", f"Skill '{skill_id}' not found.")
    send_json(handler, {"deleted": True, "id": skill_id})
