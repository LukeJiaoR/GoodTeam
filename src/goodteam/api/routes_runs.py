"""Sub-agent runs and human-gated delivery routes (merge / PR / discard / checks)."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any

from goodteam.agents.worktree import branch_diff, cleanup_worktree, is_git_repo, merge_branch
from goodteam.api.http_helpers import HttpError, read_json, send_json
from goodteam.api.routes_projects import _require_project
from goodteam.integrations import github as gh_client


def _require_run(app: Any, run_id: str) -> dict[str, Any]:
    run = app.store.get_run(run_id)
    if not run:
        raise HttpError(404, "run_not_found", f"Run '{run_id}' not found.")
    return run


def _require_delivery(app: Any, delivery_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    delivery = app.store.get_delivery(delivery_id)
    if not delivery:
        raise HttpError(404, "delivery_not_found", f"Delivery '{delivery_id}' not found.")
    project = _require_project(app, delivery["project_id"])
    return delivery, project


def _enrich_delivery(app: Any, project: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
    run = app.store.get_run(delivery["run_id"]) if delivery.get("run_id") else None
    changed_files: list[str] = list(run["changed_files"]) if run else []
    diff_text = ""
    if is_git_repo(project["path"]) and delivery.get("branch_name") and delivery.get("base_ref"):
        diff = branch_diff(project["path"], delivery["base_ref"], delivery["branch_name"])
        if diff["changed_files"]:
            changed_files = diff["changed_files"]
        diff_text = diff["diff_text"]
    return {
        **delivery,
        "changed_files": changed_files,
        "diff_text": diff_text,
        "scope_ok": bool(run["scope_ok"]) if run is not None else True,
        "role_key": run["role_key"] if run is not None else "autopilot",
        "summary": run["summary"] if run is not None else (
            f"Iteration {delivery['iteration_id']}" if delivery.get("iteration_id") else ""
        ),
    }


def handle_list_runs(
    app: Any, handler: BaseHTTPRequestHandler, project_id: str, query: dict[str, list[str]]
) -> None:
    _require_project(app, project_id)
    raw_limit = (query.get("limit") or ["50"])[0]
    try:
        limit = max(1, min(int(raw_limit), 200))
    except ValueError:
        limit = 50
    send_json(handler, {"runs": app.store.list_runs(project_id, limit=limit)})


def handle_get_run(app: Any, handler: BaseHTTPRequestHandler, run_id: str) -> None:
    run = _require_run(app, run_id)
    project = _require_project(app, run["project_id"])
    events = app.store.list_run_events(run_id, limit=500)
    diff_text = ""
    if run.get("branch_name") and run.get("base_ref") and is_git_repo(project["path"]):
        diff_text = branch_diff(project["path"], run["base_ref"], run["branch_name"])["diff_text"]
    send_json(handler, {"run": run, "events": events, "diff_text": diff_text})


def handle_stop_run(app: Any, handler: BaseHTTPRequestHandler, run_id: str) -> None:
    _require_run(app, run_id)
    updated = app.runner.stop(run_id)
    send_json(handler, {"run": updated})


def handle_list_deliveries(app: Any, handler: BaseHTTPRequestHandler, project_id: str) -> None:
    project = _require_project(app, project_id)
    raw = app.store.list_deliveries(project_id)
    enriched = [_enrich_delivery(app, project, item) for item in raw]
    send_json(handler, {"deliveries": enriched})


def handle_merge_delivery(app: Any, handler: BaseHTTPRequestHandler, delivery_id: str) -> None:
    delivery, project = _require_delivery(app, delivery_id)
    if delivery["status"] != "pending":
        raise HttpError(400, "invalid_delivery_state", f"Delivery is already '{delivery['status']}'.")
    target_branch = delivery["base_ref"] or project["default_branch"]
    body = read_json(handler, required=False)
    message = str(body.get("message") or f"GoodTeam delivery {delivery['id']} ({delivery['branch_name']})")[:200]
    commit = merge_branch(project["path"], delivery["branch_name"], target_branch, message=message)

    run = app.store.get_run(delivery["run_id"]) if delivery.get("run_id") else None
    if run and run.get("worktree_path"):
        cleanup_worktree(
            project["path"],
            run["worktree_path"],
            branch_name=delivery["branch_name"] if delivery["branch_name"].startswith("goodteam/") else None,
            delete_branch=delivery["branch_name"].startswith("goodteam/"),
            state_dir=app.state_dir,
        )
    updated = app.store.update_delivery(delivery_id, status="merged", merge_commit=commit)
    assert updated is not None
    send_json(handler, {"delivery": _enrich_delivery(app, project, updated), "merge_commit": commit})


def handle_pr_delivery(app: Any, handler: BaseHTTPRequestHandler, delivery_id: str) -> None:
    delivery, project = _require_delivery(app, delivery_id)
    if delivery["status"] not in {"pending", "pr_open"}:
        raise HttpError(400, "invalid_delivery_state", f"Cannot open PR for '{delivery['status']}' delivery.")
    body = read_json(handler, required=False)
    run = app.store.get_run(delivery["run_id"]) if delivery.get("run_id") else None
    default_title = f"GoodTeam: {run['role_key']} ({delivery['branch_name']})" if run else f"GoodTeam: {delivery['branch_name']}"
    default_body = (run["summary"] if run and run.get("summary") else "Created by GoodTeam v2.")
    title = str(body.get("title") or default_title).strip()[:200]
    pr_body = str(body.get("body") or default_body).strip()[:8000]

    pr_info = gh_client.push_and_create_pr(
        project["path"],
        branch=delivery["branch_name"],
        base=delivery["base_ref"] or project["default_branch"],
        title=title,
        body=pr_body,
    )
    updated = app.store.update_delivery(delivery_id, status="pr_open", pr_url=pr_info.get("url", ""))
    assert updated is not None
    send_json(handler, {"delivery": _enrich_delivery(app, project, updated), "pr": pr_info})


def handle_discard_delivery(app: Any, handler: BaseHTTPRequestHandler, delivery_id: str) -> None:
    delivery, project = _require_delivery(app, delivery_id)
    if delivery["status"] in {"merged", "discarded"}:
        raise HttpError(400, "invalid_delivery_state", f"Delivery is already '{delivery['status']}'.")
    run = app.store.get_run(delivery["run_id"]) if delivery.get("run_id") else None
    wt_path = run.get("worktree_path") if run else None
    branch = delivery.get("branch_name") or ""
    cleanup_worktree(
        project["path"],
        wt_path,
        branch_name=branch if branch.startswith("goodteam/") else None,
        delete_branch=branch.startswith("goodteam/"),
        state_dir=app.state_dir,
    )
    updated = app.store.update_delivery(delivery_id, status="discarded")
    assert updated is not None
    send_json(handler, {"delivery": _enrich_delivery(app, project, updated)})


def handle_delivery_checks(app: Any, handler: BaseHTTPRequestHandler, delivery_id: str) -> None:
    delivery, project = _require_delivery(app, delivery_id)
    ref = delivery.get("pr_url") or delivery.get("branch_name") or ""
    if not ref:
        raise HttpError(400, "missing_pr_ref", "Delivery has no PR URL or branch name to check.")
    checks = gh_client.pr_checks(project["path"], ref)
    send_json(handler, {"delivery_id": delivery_id, **checks})
