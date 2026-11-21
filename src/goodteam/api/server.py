"""GoodTeam v2 application wiring and ThreadingHTTPServer router."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from goodteam.agents.runner import SubagentRunner
from goodteam.api import (
    routes_chat,
    routes_projects,
    routes_runs,
    routes_settings,
    routes_skills,
    routes_team,
)
from goodteam.api.events import EventBus
from goodteam.api.http_helpers import (
    HttpError,
    decode_segment,
    map_exception,
    send_error_json,
    serve_static,
)
from goodteam.manager.autopilot import AutopilotController
from goodteam.manager.session import ManagerHub, MockTurnFn
from goodteam.providers import ProviderStore
from goodteam.skills.installer import ensure_builtins
from goodteam.store import Store


def default_frontend_dist() -> Path | None:
    env_dist = os.environ.get("GOODTEAM_FRONTEND_DIST", "").strip()
    if env_dist:
        p = Path(env_dist).expanduser().resolve()
        return p if p.is_dir() else None
    repo_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return repo_dist if repo_dist.is_dir() else None


class GoodTeamApp:
    def __init__(
        self,
        *,
        state_dir: str | Path | None = None,
        frontend_dist: str | Path | None = None,
        mock_turn_fn: MockTurnFn | None = None,
        sse_keepalive_seconds: float = 15.0,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve() if state_dir is not None else None
        self.frontend_dist = (
            Path(frontend_dist).expanduser().resolve() if frontend_dist is not None else default_frontend_dist()
        )
        self.sse_keepalive_seconds = max(0.2, float(sse_keepalive_seconds))
        self.store = Store(state_dir=self.state_dir)
        self.provider_store = ProviderStore(state_dir=self.state_dir)
        self.event_bus = EventBus()
        self.runner = SubagentRunner(
            self.store,
            self.provider_store,
            state_dir=self.state_dir,
            event_bus=self.event_bus,
            on_complete=self._on_run_complete,
        )
        self.manager = ManagerHub(
            self.store,
            self.provider_store,
            self.runner,
            state_dir=self.state_dir,
            event_bus=self.event_bus,
            mock_turn_fn=mock_turn_fn,
        )
        self.autopilot = AutopilotController(
            self.store, self.runner, self.manager, event_bus=self.event_bus
        )
        ensure_builtins(self.store)

    def _on_run_complete(self, run: dict[str, Any]) -> None:
        self.manager.on_subagent_finished(run)

    def close(self) -> None:
        for proj in self.store.list_projects():
            self.autopilot.stop(proj["id"])
            self.autopilot.wait(proj["id"], timeout=3.0)
        self.event_bus.close_all()
        self.manager.close()
        self.runner.shutdown()
        self.store.close()


_PROJECT_SIMPLE = {
    ("GET", ("events",)): routes_chat.handle_sse,
    ("POST", ("chat", "stop")): routes_chat.handle_stop_chat,
    ("POST", ("attachments",)): routes_chat.handle_upload_attachment,
    ("GET", ("autopilot",)): routes_chat.handle_get_autopilot,
    ("POST", ("autopilot", "start")): routes_chat.handle_start_autopilot,
    ("POST", ("autopilot", "stop")): routes_chat.handle_stop_autopilot,
    ("GET", ("tasks",)): routes_chat.handle_list_tasks,
    ("POST", ("tasks",)): routes_chat.handle_create_task,
    ("GET", ("deliveries",)): routes_runs.handle_list_deliveries,
    ("GET", ("roles",)): routes_team.handle_list_roles,
    ("POST", ("roles",)): routes_team.handle_create_role,
    ("POST", ("roles", "reset")): routes_team.handle_reset_roles,
    ("PUT", ("memory",)): routes_settings.handle_put_memory,
    ("GET", ("manager-settings",)): routes_settings.handle_get_manager_settings,
    ("PUT", ("manager-settings",)): routes_settings.handle_put_manager_settings,
    ("POST", ("messages",)): routes_chat.handle_post_message,
}

_PROJECT_QUERY = {
    ("GET", ("messages",)): routes_chat.handle_list_messages,
    ("GET", ("github", "issues")): routes_chat.handle_list_github_issues,
    ("GET", ("runs",)): routes_runs.handle_list_runs,
    ("GET", ("memory",)): routes_settings.handle_get_memory,
}


def _dispatch_project(
    app: GoodTeamApp, h: BaseHTTPRequestHandler, method: str, pid: str, rest: list[str], q: dict[str, list[str]]
) -> bool:
    if not rest:
        if method == "DELETE":
            routes_projects.handle_delete_project(app, h, pid)
            return True
        return False
    key = (method, tuple(rest))
    if key in _PROJECT_SIMPLE:
        _PROJECT_SIMPLE[key](app, h, pid)
        return True
    if key in _PROJECT_QUERY:
        _PROJECT_QUERY[key](app, h, pid, q)
        return True
    if len(rest) == 2 and rest[0] == "tasks" and method == "PATCH":
        routes_chat.handle_update_task(app, h, pid, rest[1])
        return True
    if len(rest) == 4 and rest[0] == "github" and rest[1] == "issues" and rest[3] == "import" and method == "POST":
        routes_chat.handle_import_github_issue(app, h, pid, rest[2])
        return True
    if len(rest) == 2 and rest[0] == "roles":
        if method == "PATCH":
            routes_team.handle_update_role(app, h, pid, rest[1])
            return True
        if method == "DELETE":
            routes_team.handle_delete_role(app, h, pid, rest[1])
            return True
    return False


_STATIC_API = {
    ("GET", ("doctor",)): routes_projects.handle_doctor,
    ("POST", ("fs", "mkdir")): routes_projects.handle_fs_mkdir,
    ("GET", ("projects",)): routes_projects.handle_list_projects,
    ("POST", ("projects",)): routes_projects.handle_create_project,
    ("GET", ("skills",)): routes_skills.handle_list_skills,
    ("POST", ("skills", "github", "preview")): routes_skills.handle_github_preview,
    ("POST", ("skills", "github", "install")): routes_skills.handle_github_install,
    ("POST", ("skills", "local", "install")): routes_skills.handle_local_install,
    ("POST", ("skills", "discover")): routes_skills.handle_discover_skills,
    ("GET", ("providers",)): routes_settings.handle_list_providers,
    ("POST", ("providers",)): routes_settings.handle_save_provider,
    ("POST", ("providers", "activate")): routes_settings.handle_activate_provider,
    ("POST", ("providers", "models")): routes_settings.handle_provider_models,
    ("POST", ("providers", "test")): routes_settings.handle_provider_test,
}

_STATIC_API_QUERY = {
    ("GET", ("fs", "list")): routes_projects.handle_fs_list,
    ("POST", ("skills", "zip", "preview")): routes_skills.handle_zip_preview,
    ("POST", ("skills", "zip", "install")): routes_skills.handle_zip_install,
    ("GET", ("usage",)): routes_settings.handle_usage,
}

_DELIVERY_ACTIONS = {
    ("POST", "merge"): routes_runs.handle_merge_delivery,
    ("POST", "pr"): routes_runs.handle_pr_delivery,
    ("POST", "discard"): routes_runs.handle_discard_delivery,
    ("GET", "checks"): routes_runs.handle_delivery_checks,
}


def _dispatch_api(
    app: GoodTeamApp, h: BaseHTTPRequestHandler, method: str, raw_path: str, q: dict[str, list[str]]
) -> bool:
    raw_segments = [s for s in raw_path.strip("/").split("/") if s]
    if not raw_segments or raw_segments[0] != "api":
        return False
    segs = [decode_segment(s) for s in raw_segments[1:]]
    if not segs:
        return False

    key = (method, tuple(segs))
    if key in _STATIC_API:
        _STATIC_API[key](app, h)
        return True
    if key in _STATIC_API_QUERY:
        _STATIC_API_QUERY[key](app, h, q)
        return True

    head = segs[0]
    if head == "projects" and len(segs) >= 2:
        return _dispatch_project(app, h, method, segs[1], segs[2:], q)
    if head == "runs":
        if len(segs) == 2 and method == "GET":
            routes_runs.handle_get_run(app, h, segs[1])
            return True
        if len(segs) == 3 and segs[2] == "stop" and method == "POST":
            routes_runs.handle_stop_run(app, h, segs[1])
            return True
    if head == "deliveries" and len(segs) == 3 and (method, segs[2]) in _DELIVERY_ACTIONS:
        _DELIVERY_ACTIONS[(method, segs[2])](app, h, segs[1])
        return True
    if head == "skills":
        if len(segs) == 3 and segs[2] == "content" and method == "GET":
            routes_skills.handle_skill_content(app, h, segs[1], q)
            return True
        if len(segs) == 2 and method == "DELETE":
            routes_skills.handle_delete_skill(app, h, segs[1])
            return True
    if head == "providers" and len(segs) == 2 and method == "DELETE":
        routes_settings.handle_delete_provider(app, h, segs[1])
        return True
    return False


class GoodTeamHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: GoodTeamApp) -> None:
        self.app = app
        super().__init__(server_address, _GoodTeamHandler)


class _GoodTeamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _handle(self, method: str) -> None:
        app: GoodTeamApp = self.server.app  # type: ignore[attr-defined]
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=False)
        try:
            if parsed.path.startswith("/api/"):
                if not _dispatch_api(app, self, method, parsed.path, query):
                    raise HttpError(404, "not_found", f"No API route for {method} {parsed.path}")
                return
            if method == "GET":
                dist = app.frontend_dist or default_frontend_dist()
                if serve_static(self, dist, parsed.path):
                    return
                raise HttpError(
                    404,
                    "frontend_not_built",
                    "Frontend assets not found. Run 'cd frontend && npm run build' first.",
                )
            raise HttpError(405, "method_not_allowed", f"Method {method} is not allowed on {parsed.path}")
        except Exception as exc:
            status, code, message = map_exception(exc)
            send_error_json(self, status, code, message)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8788,
    *,
    app: GoodTeamApp | None = None,
    state_dir: str | Path | None = None,
    frontend_dist: str | Path | None = None,
    mock_turn_fn: MockTurnFn | None = None,
    sse_keepalive_seconds: float = 15.0,
) -> tuple[GoodTeamHTTPServer, GoodTeamApp]:
    resolved_app = app or GoodTeamApp(
        state_dir=state_dir,
        frontend_dist=frontend_dist,
        mock_turn_fn=mock_turn_fn,
        sse_keepalive_seconds=sse_keepalive_seconds,
    )
    server = GoodTeamHTTPServer((host, port), resolved_app)
    return server, resolved_app
