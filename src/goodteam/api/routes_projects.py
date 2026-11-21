"""Project lifecycle, local filesystem browser, and CLI doctor routes."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from pathlib import Path
import shutil
from typing import Any

from goodteam.agents.roles import seed_project_roles
from goodteam.agents.worktree import cleanup_worktree, detect_default_branch, is_git_repo
from goodteam.api.http_helpers import HttpError, read_json, send_json
from goodteam.config import attachments_dir, doctor, runtime_skills_dir, worktrees_dir
from goodteam.skills.installer import ensure_builtins


def _require_project(app: Any, project_id: str) -> dict[str, Any]:
    project = app.store.get_project(project_id)
    if not project:
        raise HttpError(404, "project_not_found", f"Project '{project_id}' not found.")
    return project


def _enrich_project(app: Any, project: dict[str, Any]) -> dict[str, Any]:
    runs = app.store.list_runs(project["id"], limit=50)
    active_runs = sum(1 for r in runs if r["status"] in {"queued", "running"})
    deliveries = app.store.list_deliveries(project["id"])
    pending_deliveries = sum(1 for d in deliveries if d["status"] == "pending")
    return {
        **project,
        "active_runs": active_runs,
        "pending_deliveries": pending_deliveries,
        "is_git_repo": is_git_repo(project["path"]),
    }


def handle_doctor(app: Any, handler: BaseHTTPRequestHandler) -> None:
    send_json(handler, doctor(app.state_dir))


def handle_fs_list(_app: Any, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    raw_path = (query.get("path") or [""])[0].strip()
    target = Path(raw_path).expanduser().resolve() if raw_path else Path.home().resolve()
    if not target.exists():
        raise HttpError(404, "path_not_found", f"Directory does not exist: {target}")
    if not target.is_dir():
        raise HttpError(400, "not_a_directory", f"Path is not a directory: {target}")

    entries: list[dict[str, Any]] = []
    try:
        children = list(target.iterdir())
    except PermissionError as exc:
        raise HttpError(403, "permission_denied", f"Cannot read directory: {target}") from exc

    for child in children:
        if child.name == ".DS_Store":
            continue
        try:
            is_dir = child.is_dir()
            git_repo = bool(is_dir and (child / ".git").exists())
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": str(child),
            "is_dir": is_dir,
            "is_git_repo": git_repo,
        })
    entries.sort(key=lambda item: (0 if item["is_dir"] else 1, item["name"].lower()))
    parent = str(target.parent) if target.parent != target else None
    send_json(handler, {"path": str(target), "parent": parent, "entries": entries})


def handle_fs_mkdir(_app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    raw_parent = str(body.get("parent") or "").strip()
    name = str(body.get("name") or "").strip()
    if not raw_parent:
        raise HttpError(400, "invalid_parent", "Parent directory is required.")
    if not name or len(name) > 200 or "/" in name or "\\" in name or "\x00" in name or name in {".", ".."}:
        raise HttpError(400, "invalid_folder_name", "Folder name must be a single valid path segment.")

    parent_dir = Path(raw_parent).expanduser().resolve()
    if not parent_dir.is_dir():
        raise HttpError(404, "parent_not_found", f"Parent directory does not exist: {parent_dir}")
    target = (parent_dir / name).resolve()
    if target.parent != parent_dir:
        raise HttpError(400, "invalid_folder_name", "Folder path escapes parent directory.")
    if target.exists():
        raise HttpError(409, "already_exists", f"Folder already exists: {name}")
    target.mkdir(parents=False, exist_ok=False)
    send_json(handler, {"path": str(target), "name": name, "parent": str(parent_dir)}, status=201)


def handle_list_projects(app: Any, handler: BaseHTTPRequestHandler) -> None:
    projects = [_enrich_project(app, proj) for proj in app.store.list_projects()]
    send_json(handler, {"projects": projects})


def handle_create_project(app: Any, handler: BaseHTTPRequestHandler) -> None:
    body = read_json(handler, required=True)
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        raise HttpError(400, "missing_path", "Project path is required.")
    target = Path(raw_path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HttpError(400, "invalid_path", f"Directory does not exist: {target}")

    existing = app.store.get_project_by_path(str(target))
    if existing:
        raise HttpError(409, "project_exists", "This directory is already imported as a project.")

    name = str(body.get("name") or target.name).strip()[:120] or target.name
    git_ok = is_git_repo(target)
    default_branch = str(body.get("default_branch") or "").strip() or (
        detect_default_branch(target) if git_ok else "main"
    )
    project = app.store.create_project(name=name, path=str(target), default_branch=default_branch)
    ensure_builtins(app.store)
    seed_project_roles(app.store, project["id"])
    send_json(handler, {"project": _enrich_project(app, project)}, status=201)


def _is_inside(child: Path, root: Path) -> bool:
    try:
        resolved_child = child.resolve()
        resolved_root = root.resolve()
        return resolved_child != resolved_root and resolved_root in resolved_child.parents
    except OSError:
        return False


def handle_delete_project(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    """Remove project from GoodTeam DB and clean GoodTeam-managed worktrees ONLY.

    NEVER deletes or mutates the user's local project source directory.
    """
    project = _require_project(app, project_id)
    app.autopilot.stop(project_id)
    app.manager.close_session(project_id)

    wt_root = worktrees_dir(app.state_dir)
    runs = app.store.list_runs(project_id, limit=500)
    for run in runs:
        if run["status"] in {"queued", "running"}:
            app.runner.stop(run["id"])
        raw_wt = run.get("worktree_path") or ""
        safe_wt = Path(raw_wt) if raw_wt and _is_inside(Path(raw_wt), wt_root) else None
        branch = run.get("branch_name") or ""
        delete_branch = branch.startswith("goodteam/")
        if safe_wt is not None or delete_branch:
            cleanup_worktree(
                project["path"],
                safe_wt,
                branch_name=branch if delete_branch else None,
                delete_branch=delete_branch,
                state_dir=app.state_dir,
            )

    for deliv in app.store.list_deliveries(project_id):
        branch = deliv.get("branch_name") or ""
        if branch.startswith("goodteam/"):
            cleanup_worktree(
                project["path"],
                None,
                branch_name=branch,
                delete_branch=True,
                state_dir=app.state_dir,
            )

    # Clean GoodTeam-owned attachments and staged manager skills (never project['path'])
    att_root = attachments_dir(app.state_dir)
    proj_att = att_root / project_id
    if _is_inside(proj_att, att_root) and proj_att.exists():
        shutil.rmtree(proj_att, ignore_errors=True)

    rt_root = runtime_skills_dir(app.state_dir)
    mgr_skills = rt_root / f"manager-{project_id}"
    if _is_inside(mgr_skills, rt_root) and mgr_skills.exists():
        shutil.rmtree(mgr_skills, ignore_errors=True)

    app.store.delete_project(project_id)
    app.event_bus.close_project(project_id)
    send_json(handler, {"deleted": True, "id": project_id})
