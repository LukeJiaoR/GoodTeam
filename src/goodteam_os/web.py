"""[INPUT]: GoodTeam 请求、工作区与项目服务。
[OUTPUT]: Web 服务、项目任务与交付 API、历史执行和操作记录查询。
[POS]: 校验 HTTP 请求并路由至工作区和项目服务。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from datetime import datetime, UTC
from html import escape
from goodteam_os.http_transport import (
    BoundedHTTPRequestHandler as BaseHTTPRequestHandler,
    BoundedThreadingHTTPServer as ThreadingHTTPServer,
    RequestBodyError,
    read_request_body,
)
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, unquote_to_bytes, urlparse
from urllib.request import urlopen
from uuid import uuid4

from goodteam_os import context as project_context
from goodteam_os.database import provider_environment
from goodteam_os.worker_capabilities import REASONING_DEPTHS
from goodteam_os.execution import ExecutionAPI
from goodteam_os.frontend_assets import FrontendAssetsNotFound, resolve_frontend_dist
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.assistant_drafts import handle_draft_action
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_control_plane import (
    TeamControlPlaneError,
    TeamControlPlaneStore,
    execute_team_commands,
)
from goodteam_os.models import list_model_profiles
from goodteam_os.native_picker import choose_native_path
from goodteam_os.projects import DELIVERY_MODES, check_project_target, load_project_target, update_worker_backend_config
from goodteam_os.project_runtime import resolve_project_runtime_root
from goodteam_os.project_import import (
    ProjectImportError,
    confirm_project_mode,
    import_project,
    read_bound_project_readiness,
)
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.project_readiness import IGNORED_NAMES, ProjectReadinessStore, build_capabilities  # noqa: F401
from goodteam_os.project_readiness_executor import (
    ProjectReadinessExecutor,
    ReadinessExecutionError,
)
from goodteam_os.repository_access import AnchoredRepository, canonical_relative_path
from goodteam_os.repository_snapshot import RepositorySnapshotError, inspect_repository_git
from goodteam_os._trusted_system_executable import (
    TrustedExecutableError,
    open_verified_executable,
    resolve_trusted_system_git,
)
from goodteam_os.service import service_state_dir
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.staging import (
    deployment_backend_config,
    delivery_contract_report,
)
from goodteam_os.project_runtime import normalize_project_key
from goodteam_os.tasks import (
    LocalTaskStore,
)
from goodteam_os.team_workspace import build_team_workspace
from goodteam_os.assistant_workspace import build_project_assistant
from goodteam_os.assistant_queue import QUEUE_ACTIONS, handle_queue_action
from goodteam_os.workers import BUILTIN_WORKER_MODES, CLI_BACKENDS, SESSION_BACKENDS, WorkerJobStore, discover_installed_agents
from goodteam_os.cross_project_memory import retrieve_report as retrieve_cross_project_memory

if TYPE_CHECKING:
    from goodteam_os.skills.codex_runtime import CodexReviewAdmission


PRD_REVIEW_ROLES = [
    "Product / Requirement",
    "Architect",
    "Review / QA",
]

_PREFERRED_WORKER_BACKEND_ORDER = ("codex", "claude", "codebuddy", "gemini", "agy", "opencode", "pi", "fake", "noop", "human")
WORKER_BACKEND_OPTIONS = [
    mode
    for mode in _PREFERRED_WORKER_BACKEND_ORDER
    if mode in {*BUILTIN_WORKER_MODES, *CLI_BACKENDS, *SESSION_BACKENDS}
]
WORKER_BACKEND_ACCEPTED_OPTIONS = sorted({*WORKER_BACKEND_OPTIONS, *CLI_BACKENDS, *SESSION_BACKENDS, "manual"})
WORKER_ROLES = [
    "orchestrator",
    "research",
    "product_requirement",
    "architect",
    "review",
    "planner",
    "ui",
    "implementation",
    "frontend",
    "backend",
    "qa",
    "code_review",
    "release",
    "retro_memory",
]
WORKER_REASONING_DEPTHS = REASONING_DEPTHS
WORKER_ADDITIONAL_PROMPT_MAX_CHARS = 4000
WORKER_DESIRED_INSTANCES_MAX = 8
PRE_REVIEW_REPORT_DIR = Path("/tmp")
DEV_LOOP_SUBPROCESS_TIMEOUT_SECONDS = 10
FS_LIST_MAX_ENTRIES = 500
ARTIFACT_PREVIEW_MAX_CHARS = 8000
FS_LIST_IGNORED_NAMES = {".git", ".github", ".venv", "__pycache__", "node_modules", ".next", "dist", "build"}

IMPORT_ERROR_STATUS = {
    "project_root_not_absolute": 400,
    "project_root_not_found": 404,
    "project_root_not_directory": 400,
    "project_root_not_readable": 403,
    "project_workspace_not_isolated": 400,
    "project_readiness_mismatch": 409,
}
IMPORT_ERROR_RECOVERY = {
    "project_root_not_absolute": ["choose_another_directory"],
    "project_root_not_found": ["choose_another_directory"],
    "project_root_not_directory": ["choose_another_directory"],
    "project_root_not_readable": ["fix_permissions", "choose_another_directory"],
    "project_workspace_not_isolated": ["choose_another_directory"],
    "project_readiness_mismatch": ["reload_project", "return_to_projects"],
}

READINESS_ERROR_STATUS = {
    "project_mode_unconfirmed": 409,
    "project_root_identity_missing": 409,
    "project_root_changed": 409,
    "project_entry_changed": 409,
    "readiness_artifact_mismatch": 409,
    "readiness_artifact_not_found": 404,
    "readiness_idempotency_conflict": 409,
    "readiness_input_conflict": 409,
    "readiness_input_invalid": 400,
    "readiness_input_not_expected": 409,
    "readiness_input_not_supported": 400,
    "readiness_not_ready": 409,
    "readiness_not_started": 409,
    "readiness_retry_required": 409,
    "readiness_step_binding_mismatch": 409,
    "readiness_step_not_found": 404,
    "readiness_step_not_retryable": 409,
}


class ProjectApiError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        *,
        details: dict[str, object] | None = None,
        recovery: list[str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        self.recovery = recovery or []


class ProjectConfigNotFoundError(ValueError):
    """No local project config exists; distinct from a broken configured project."""


def _run_readonly_command(command: list[str], workspace: Path, warnings: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=workspace,
            env=provider_environment(),
            capture_output=True,
            text=True,
            timeout=DEV_LOOP_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        warnings.append(f"{command[0]} not available")
    except subprocess.TimeoutExpired:
        warnings.append(f"{command[0]} timed out")
    except OSError as exc:
        warnings.append(f"{command[0]} failed: {exc.__class__.__name__}")
    return None


def _parse_git_worktrees(output: str) -> list[dict[str, str]]:
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            if current.get("path"):
                worktrees.append(
                    {
                        "path": current.get("path", ""),
                        "branch": current.get("branch", ""),
                        "head": current.get("head", ""),
                    }
                )
            current = {}
            continue
        key, _, value = stripped.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value[:7]
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
    if current.get("path"):
        worktrees.append(
            {
                "path": current.get("path", ""),
                "branch": current.get("branch", ""),
                "head": current.get("head", ""),
            }
        )
    return worktrees


def _check_status_value(check: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = str(check.get(key, "")).lower()
        if value:
            return value
    return ""


def _map_ci_rollup(status_check_rollup: object) -> str:
    if not isinstance(status_check_rollup, list) or not status_check_rollup:
        return "none"

    has_pending = False
    for raw_check in status_check_rollup:
        if not isinstance(raw_check, dict):
            has_pending = True
            continue
        conclusion = _check_status_value(raw_check, "conclusion")
        state = _check_status_value(raw_check, "state", "status")
        if conclusion in {"failure", "startup_failure", "cancelled", "timed_out", "action_required"}:
            return "fail"
        if state in {"failure", "failed", "error", "cancelled", "timed_out", "action_required"}:
            return "fail"
        if conclusion in {"", "neutral", "skipped"} and state not in {"success", "completed"}:
            has_pending = True
        elif state in {"", "queued", "pending", "in_progress", "waiting", "requested", "expected"}:
            has_pending = True

    return "pending" if has_pending else "pass"


def _request_origin_is_same_origin(*, origin: str, host: str) -> bool:
    if not origin:
        return True
    if not host:
        return False
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    request_host = host.split("@")[-1].lower()
    origin_host = parsed.netloc.lower()
    if origin_host == request_host:
        return True
    local_hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    origin_name = (parsed.hostname or "").lower()
    request_name = request_host.rsplit(":", 1)[0]
    return origin_name in local_hosts and request_name in local_hosts


def _request_host_is_loopback(host: str) -> bool:
    if not host:
        return False
    try:
        parsed = urlparse(f"//{host}")
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _protected_execution_origin_is_trusted(*, origin: str, request_host: str,
                                         server_host: str, server_port: int, fetch_site: str = "") -> bool:
    """The HTTP dashboard's exact origin, anchored to its configured listener.

    Missing Origin is retained for explicit non-browser JSON clients; it is not
    authenticated human approval. A browser cross-site marker is never trusted.
    """
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False
    try:
        target = urlparse("//" + request_host)
        hostname = (target.hostname or "").lower()
        configured = server_host.strip("[]").lower()
        local_names = {"localhost", "127.0.0.1", "::1"}
        allowed = local_names if configured in local_names or configured in {"0.0.0.0", "::"} else {configured}
        if (hostname not in allowed or (target.port or 80) != server_port
                or target.username is not None or target.password is not None
                or target.path or target.query or target.fragment):
            return False
        if not origin:
            return True
        source = urlparse(origin)
        return (source.scheme == "http" and source.hostname == hostname
                and (source.port or 80) == (target.port or 80)
                and source.username is None and source.password is None
                and not source.path and not source.query and not source.fragment)
    except ValueError:
        return False


def _native_picker_request_targets_local_server(*, request_host: str, port: int) -> bool:
    if not request_host:
        return False
    try:
        parsed = urlparse(f"//{request_host}")
        request_port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname == "localhost":
        is_loopback = True
    else:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    return is_loopback and request_port == port


def _native_picker_origin_is_trusted(*, origin: str, request_host: str, port: int) -> bool:
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
        origin_port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    origin_hostname = (parsed.hostname or "").lower()
    try:
        origin_is_loopback = origin_hostname == "localhost" or ipaddress.ip_address(origin_hostname).is_loopback
    except ValueError:
        origin_is_loopback = False
    if not origin_is_loopback:
        return False

    # The production console must use the exact server authority. Vite's fixed
    # development port is the sole cross-port exception because it proxies /api.
    if parsed.netloc.lower() == request_host.lower():
        return True
    return origin_port == 8790 and port != 8790


def _parse_gh_pull_requests(output: str, warnings: list[str]) -> list[dict[str, object]]:
    try:
        raw_pull_requests = json.loads(output)
    except json.JSONDecodeError:
        warnings.append("gh pr list returned invalid json")
        return []
    if not isinstance(raw_pull_requests, list):
        warnings.append("gh pr list returned unexpected json")
        return []

    pull_requests: list[dict[str, object]] = []
    for item in raw_pull_requests:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number", 0))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        pull_requests.append(
            {
                "number": number,
                "title": str(item.get("title", "")),
                "head_branch": str(item.get("headRefName", "")),
                "ci": _map_ci_rollup(item.get("statusCheckRollup")),
                "url": str(item.get("url", "")),
            }
        )
    return pull_requests


def _server_action_audit_dir(root: Path) -> Path:
    return root / ".goodteam" / "server-actions"


SERVER_ACTION_AUDIT_FIELDS = (
    "id",
    "timestamp",
    "started_at",
    "finished_at",
    "actor",
    "origin",
    "action",
    "returncode",
    "timed_out",
    "argv",
    "typed_confirmation",
    "audit_path",
    "stdout_tail",
    "stderr_tail",
    "rollback_hint",
)


def _server_action_audit_limit(limit: object) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 25
    return min(max(parsed, 1), 100)


def _sanitize_server_action_audit_record(record: dict[str, object]) -> dict[str, object]:
    return {field: record[field] for field in SERVER_ACTION_AUDIT_FIELDS if field in record}


def build_server_action_audit_log(root: Path | None, *, limit: int = 25) -> dict[str, object]:
    warnings: list[str] = []
    if root is None:
        return {
            "available": False,
            "audit_dir": "",
            "records": [],
            "warnings": ["no project selected"],
            "generated_at": datetime.now(UTC).isoformat(),
        }

    audit_dir = _server_action_audit_dir(root.resolve())
    records: list[dict[str, object]] = []
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*.json")):
            try:
                raw_record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append(f"ignored malformed audit record: {path.name}")
                continue
            if not isinstance(raw_record, dict):
                warnings.append(f"ignored malformed audit record: {path.name}")
                continue
            records.append(_sanitize_server_action_audit_record(raw_record))

    records.sort(key=lambda record: str(record.get("timestamp", "")), reverse=True)
    return {
        "available": True,
        "audit_dir": str(audit_dir),
        "records": records[: _server_action_audit_limit(limit)],
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _release_dir_for_root(root: Path) -> Path:
    configured = os.environ.get("GOODTEAM_RELEASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return root / "../goodteam-release"


def _readonly_head(path: Path, *, cwd: Path, warnings: list[str], warning_label: str) -> str:
    result = _run_readonly_command(["git", "-C", str(path), "rev-parse", "HEAD"], cwd, warnings)
    if result is None:
        return ""
    if result.returncode != 0:
        warnings.append(f"{warning_label} HEAD unavailable")
        return ""
    return result.stdout.strip()


def build_release_readiness(root: Path, warnings: list[str]) -> dict[str, object]:
    if not (root / "scripts" / "graduate-release.sh").is_file() or not (root / "scripts" / "release-up.sh").is_file():
        return {
            "available": False,
            "reason": "release scripts unavailable",
        }
    release_dir = _release_dir_for_root(root)
    dev_head = _readonly_head(root, cwd=root, warnings=warnings, warning_label="dev")
    release_exists = release_dir.is_dir()
    release_head = ""
    if release_exists:
        release_head = _readonly_head(release_dir, cwd=root, warnings=warnings, warning_label="release")

    ref = dev_head or "HEAD"
    command_prefix = f"GOODTEAM_RELEASE_DIR={release_dir} "
    return {
        "available": True,
        "release_dir": str(release_dir),
        "release_exists": release_exists,
        "dev_head": dev_head,
        "release_head": release_head,
        "up_to_date": bool(dev_head and release_head and dev_head == release_head),
        "release_up_command": f"{command_prefix}scripts/release-up.sh --ref {ref}",
        "graduate_command": f"{command_prefix}scripts/graduate-release.sh --ref {ref}",
    }


def build_dev_loop(root: Path | None) -> dict[str, object]:
    warnings: list[str] = []
    worktrees: list[dict[str, str]] = []
    pull_requests: list[dict[str, object]] = []

    if root is None:
        warnings.append("no project selected")
        return {
            "worktrees": worktrees,
            "pull_requests": pull_requests,
            "pre_reviews": [],
            "is_git_repo": False,
            "warnings": warnings,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    root = root.resolve()
    git_root_result = _run_readonly_command(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        root,
        warnings,
    )
    git_root = ""
    if git_root_result is not None and git_root_result.returncode == 0:
        git_root = git_root_result.stdout.strip()
    try:
        valid_git_root = bool(git_root) and Path(git_root).resolve().is_relative_to(root)
    except OSError:
        valid_git_root = False
    if not valid_git_root:
        warnings.append("project repo has no git history yet")
        return {
            "worktrees": worktrees,
            "pull_requests": pull_requests,
            "pre_reviews": [],
            "is_git_repo": False,
            "warnings": warnings,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    git_result = _run_readonly_command(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        root,
        warnings,
    )
    if git_result is not None:
        if git_result.returncode == 0:
            worktrees = _parse_git_worktrees(git_result.stdout)
        else:
            warnings.append("git worktree list failed")
    gh_result = _run_readonly_command(
        ["gh", "pr", "list", "--json", "number,title,headRefName,url,statusCheckRollup"],
        root,
        warnings,
    )
    if gh_result is not None:
        if gh_result.returncode == 0:
            pull_requests = _parse_gh_pull_requests(gh_result.stdout, warnings)
        else:
            warnings.append("gh pr list failed")

    pre_reviews = []
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        report_path = PRE_REVIEW_REPORT_DIR / f"pre-review-{number}-report.md"
        present = report_path.is_file()
        pre_reviews.append(
            {
                "pr": number,
                "present": present,
                "report_path": str(report_path),
            }
        )
    return {
        "worktrees": worktrees,
        "pull_requests": pull_requests,
        "pre_reviews": pre_reviews,
        "is_git_repo": True,
        "release_readiness": build_release_readiness(root, warnings),
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_team_view_payload(workspace: Path, project_key: str) -> dict[str, object]:
    """Current Team truth for the Team surface: capability view plus ledger metadata."""
    key = normalize_project_key(project_key)
    config_path = _project_config_for_key(workspace, key)
    from goodteam_os.team_control_plane import build_team_capability_view, ensure_team_state

    state = ensure_team_state(workspace, key, config_path)
    if state is None:
        raise ProjectApiError(
            "team_state_unavailable",
            "Team control-plane state is unavailable for this project.",
            404,
            recovery=["reload_project"],
        )
    store = TeamControlPlaneStore(workspace, key)
    state = store.reconcile_pending_audit() or state
    return {
        "team": build_team_capability_view(state),
        "events": store.read_events(limit=200),
    }


def build_team_events_payload(workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    _project_config_for_key(workspace, key)
    store = TeamControlPlaneStore(workspace, key)
    store.reconcile_pending_audit()
    events = store.read_events(limit=500)
    return {
        "schema_version": "team_decision_ledger.v1",
        "project_key": key,
        "event_count": len(events),
        "events": events,
    }


_project_config_paths_in_work_state: ContextVar[dict[tuple[Path, str], Path] | None] = ContextVar(
    "project_config_paths_in_work_state", default=None,
)


def _goal_page_limit(query, *, default=1):
    raw = query.get("goal_limit", [str(default)])[0]
    if not isinstance(raw, str) or len(raw) > 2 or not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 50:
        raise ProjectApiError("invalid_goal_page", "Goal page limit must be between 1 and 50.", 400)
    return int(raw)


def build_goal_history(workspace: Path, project_key: str, *, limit: int = 20, before: str = ""):
    from goodteam_os.goal_continuation import goal_views
    from goodteam_os.goals import GoalError, GoalStore

    key = normalize_project_key(project_key)
    _project_config_for_key(workspace, key)
    from goodteam_os.goals import goal_store_exists

    if not goal_store_exists(workspace):
        if before:
            raise ProjectApiError("invalid_goal_page", "Unknown Goal history cursor.", 400)
        return {"goals": [], "next_before": "", "project_key": key}
    try:
        page = GoalStore(workspace, key).page_goals(limit=limit, before=before)
    except GoalError as exc:
        raise ProjectApiError("invalid_goal_page", str(exc), 400) from exc
    return {**page, "goals": goal_views(workspace, page["goals"]), "project_key": key}


def build_project_work_state(workspace: Path, project_key: str, *, goal_limit: int | None = None) -> dict[str, object]:
    # Reuse only path resolution within this projection. Config contents stay
    # live, and subsequent requests/mutations perform their own lookup.
    token = _project_config_paths_in_work_state.set({})
    try:
        return _build_project_work_state(workspace, project_key, goal_limit=goal_limit)
    finally:
        _project_config_paths_in_work_state.reset(token)


def _build_project_work_state(workspace: Path, project_key: str, *, goal_limit: int | None = None) -> dict[str, object]:
    project_config = _project_config_for_key(workspace, project_key)
    start_context = build_start_console_context(workspace, project_config=project_config)
    project_root = resolve_project_runtime_root(dict(load_project_target(project_config).get("repos", {})))
    task_assistant_session = (
        TaskAssistantService(workspace, project_config=project_config).session(project_key, project_root, **({"goal_limit": goal_limit} if goal_limit is not None else {}))
        if project_root is not None
        else None
    )
    from goodteam_os.project_iteration import project_iteration_backlog, latest_iteration_choice
    iteration_backlog = project_iteration_backlog(project_root, project_key)
    worker_job_records = WorkerJobStore(project_root).list_jobs() if project_root is not None else []
    worker_jobs = _serialize_worker_job_items(worker_job_records)
    team_payload = None
    try:
        team_payload = build_team_view_payload(workspace, project_key)["team"]
    except (ProjectApiError, ValueError):
        team_payload = None
    team_workspace = build_team_workspace(
        task_assistant_session, project_root=project_root, project_key=project_key, worker_jobs=worker_job_records,
    )
    return {
        "type": "project",
        "start_context": start_context,
        "team": team_payload,
        "task_assistant_session": task_assistant_session,
        "iteration_backlog": iteration_backlog,
        "iteration_choice": latest_iteration_choice(task_assistant_session, iteration_backlog),
        "worker_jobs": worker_jobs,
        "team_workspace": team_workspace,
        "project_assistant": build_project_assistant(
            task_assistant_session, team_workspace, project_key=project_key,
            jobs=worker_job_records,
        ),
    }


def maybe_send_favicon_response(handler: object, path: str) -> bool:
    if path != "/favicon.ico":
        return False
    handler.send_response(204)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def _allowed_cors_origin(origin: str, *, host: str, port: int) -> str:
    if not origin:
        return ""
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return ""
    hostname = parsed.hostname or ""
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    if host not in {"0.0.0.0", "::"}:
        allowed_hosts.add(host)
    if hostname not in allowed_hosts:
        return ""
    expected_port = port if parsed.scheme == "http" else 443
    origin_port = parsed.port if parsed.port is not None else (80 if parsed.scheme == "http" else 443)
    if origin_port != expected_port:
        return ""
    return origin


def _send_cors_headers(handler: object, *, host: str, port: int) -> None:
    origin = _allowed_cors_origin(str(handler.headers.get("Origin", "")), host=host, port=port)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")


def discover_project_directories(base_dir: Path | None = None) -> list[dict[str, str]]:
    base_dir = Path.home() if base_dir is None else base_dir
    if not base_dir.is_dir():
        return []
    ignored_names = {".git", ".github", ".venv", "__pycache__", "node_modules", ".data", ".next", "dist", "build"}
    directories = []
    candidates = [base_dir]
    for _ in range(2):
        next_candidates = []
        for parent in candidates:
            for path in sorted(parent.iterdir(), key=lambda item: item.name.lower()):
                if path.name.startswith(".") or path.name in ignored_names or not path.is_dir():
                    continue
                directories.append({"name": path.name, "path": str(path)})
                next_candidates.append(path)
        candidates = next_candidates
    unique = []
    seen = set()
    for item in directories:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        unique.append(item)
    return unique


class FsListBadRequestError(ValueError):
    pass


class FsListNotFoundError(ValueError):
    pass


class FsListPermissionError(ValueError):
    pass


class FsMkdirConflictError(ValueError):
    pass


def list_directory_for_picker(path_value: str | None) -> dict[str, object]:
    raw_path = "" if path_value is None else path_value.strip()
    if not raw_path:
        requested = Path.home()
    else:
        requested = Path(raw_path)
        if not requested.is_absolute():
            raise FsListBadRequestError("path must be absolute.")

    try:
        resolved = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FsListNotFoundError("path not found.") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FsListBadRequestError("path is invalid.") from exc

    if not resolved.is_dir():
        raise FsListNotFoundError("path is not a directory.")

    parent = None if resolved.parent == resolved else str(resolved.parent)
    entries: list[dict[str, object]] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda item: item.name.lower())
    except PermissionError as exc:
        raise FsListPermissionError("path is not readable.") from exc
    except OSError as exc:
        raise FsListBadRequestError("path cannot be read.") from exc

    for child in children:
        if len(entries) >= FS_LIST_MAX_ENTRIES:
            break
        if child.name in FS_LIST_IGNORED_NAMES:
            continue
        try:
            is_dir = child.is_dir()
            child_path = child.resolve(strict=True)
            is_git_repo = is_dir and (child / ".git").exists()
        except (OSError, RuntimeError, ValueError):
            continue
        entries.append(
            {"name": child.name, "path": str(child_path), "is_dir": is_dir, "is_git_repo": is_git_repo}
        )

    return {"path": str(resolved), "parent": parent, "entries": entries}


def validate_single_path_segment(name_value: object) -> str:
    name = "" if name_value is None else str(name_value)
    if not name or name in {".", ".."} or "\x00" in name:
        raise FsListBadRequestError("name must be a non-empty single path segment.")
    if "/" in name or "\\" in name or ".." in name or set(name) == {"."}:
        raise FsListBadRequestError("name must be a non-empty single path segment.")
    return name


def create_subdirectory(parent_value: str | Path | None, name_value: object) -> dict[str, object]:
    raw_parent = "" if parent_value is None else str(parent_value).strip()
    if not raw_parent:
        raise FsListBadRequestError("parent is required.")
    parent = Path(raw_parent).expanduser()
    if not parent.is_absolute():
        raise FsListBadRequestError("parent must be absolute.")
    name = validate_single_path_segment(name_value)
    try:
        resolved_parent = parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FsListNotFoundError("parent not found.") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FsListBadRequestError("parent is invalid.") from exc
    if not resolved_parent.is_dir():
        raise FsListNotFoundError("parent is not a directory.")
    target = resolved_parent / name
    if target.exists():
        raise FsMkdirConflictError("path already exists.")
    try:
        target.mkdir()
        resolved_target = target.resolve(strict=True)
    except FileExistsError as exc:
        raise FsMkdirConflictError("path already exists.") from exc
    except OSError as exc:
        raise FsListBadRequestError("directory cannot be created.") from exc
    return {"path": str(resolved_target)}


def _project_directory_base(repos: dict[str, object], fallback: Path) -> Path:
    repo_paths = [Path(str(path)).expanduser() for path in repos.values() if str(path).strip()]
    existing_repo_paths = [path.resolve() for path in repo_paths if path.is_dir()]
    if not existing_repo_paths:
        return fallback

    common_path = Path(os.path.commonpath([str(path) for path in existing_repo_paths]))
    return common_path if common_path.is_dir() else existing_repo_paths[0].parent


def _slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "project"


def _validate_directory_path(label: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute.")
    if not path.is_dir():
        raise ValueError(f"{label} path does not exist or is not a directory.")
    return path


def _single_line_config_value(label: str, value: str, *, default: str = "") -> str:
    text = value.strip() or default
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be a single line.")
    return text


def _config_scalar(label: str, value: str, *, default: str = "") -> str:
    text = _single_line_config_value(label, value, default=default)
    if not text:
        return ""
    if any(char in text for char in [":", "#", '"', "'"]) or text != text.strip():
        return json.dumps(text)
    return text


def _config_mapping_key(label: str, value: str) -> str:
    key = _single_line_config_value(label, value)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"{label} may only contain letters, numbers, underscores, and hyphens.")
    return key


GIT_INIT_SUBPROCESS_TIMEOUT_SECONDS = 30


def _request_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _run_git_project_command(
    command: list[str],
    root_path: Path,
    warnings: list[str],
    *,
    env: dict[str, str] | None = None,
    expected_identity: dict[str, int] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    executable_fd = None
    try:
        if command[0] == "git":
            executable, _, identity = resolve_trusted_system_git(root_path)
            executable_fd = open_verified_executable(executable, identity)
            command = [executable, "-c", "core.fsmonitor=false", *command[1:]]
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env["GIT_TERMINAL_PROMPT"] = "0"
        with AnchoredRepository(root_path, expected_identity=expected_identity) as repository:
            result = subprocess.run(
                command,
                cwd=root_path,
                env=provider_environment(env),
                capture_output=True,
                text=True,
                timeout=GIT_INIT_SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
            repository.verify_root()
            return result
    except FileNotFoundError:
        warnings.append(f"{command[0]} not available")
    except subprocess.TimeoutExpired:
        warnings.append(f"{command[0]} timed out")
    except OSError as exc:
        warnings.append(f"{command[0]} failed: {exc.__class__.__name__}")
    except TrustedExecutableError:
        warnings.append("Trusted Git executable is unavailable or changed; repair Git installation and retry.")
    finally:
        if executable_fd is not None:
            os.close(executable_fd)
    return None


def _project_git_baseline(root_path: Path) -> dict[str, object]:
    try:
        state = inspect_repository_git(root_path)
    except (RepositorySnapshotError, OSError) as exc:
        return {
            "state": "invalid",
            "head_sha": None,
            "baseline_required": True,
            "error": f"Git baseline inspection failed: {exc} Repair the repository root or Git metadata and retry.",
        }
    state["baseline_required"] = state["state"] != "valid_head"
    if state["baseline_required"]:
        state["error"] = (
            "Git baseline required. Select the exact initial_files for /git-init, "
            "or create the initial commit manually, then retry."
        )
    return state


def _initial_git_files(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError("initial_files must be a non-empty list of relative file paths.")
    paths = [canonical_relative_path(item) for item in value]
    if any(path.split("/")[0] in {".git", ".goodteam"} for path in paths):
        raise ValueError("initial_files cannot include Git metadata or GoodTeam state.")
    return list(dict.fromkeys(paths))


def _git_configured_identity(root_path: Path, warnings: list[str], expected_identity: dict[str, int]) -> bool:
    name = _run_git_project_command(["git", "-C", str(root_path), "config", "--get", "user.name"], root_path, warnings, expected_identity=expected_identity)
    email = _run_git_project_command(["git", "-C", str(root_path), "config", "--get", "user.email"], root_path, warnings, expected_identity=expected_identity)
    return bool(
        name is not None
        and name.returncode == 0
        and name.stdout.strip()
        and email is not None
        and email.returncode == 0
        and email.stdout.strip()
    )


def _initialize_project_git(
    *,
    root_path: Path,
    project_name: str,
    project_key: str,
    init_git: bool,
    create_remote: bool,
    remote_private: bool,
    initial_files: list[str] | None = None,
    scaffold_new_project: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {"initialized": False, "remote_created": False, "warnings": []}
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    selected_files = _initial_git_files(initial_files)
    baseline = _project_git_baseline(root_path)
    existing_git = baseline["state"] != "non_git"
    if baseline["state"] == "invalid":
        return {**result, **baseline, "warnings": [baseline["error"]]}
    root_identity = baseline["root_identity"]

    def run(command: list[str], *, env: dict[str, str] | None = None):
        return _run_git_project_command(command, root_path, warnings, env=env, expected_identity=root_identity)

    if init_git and baseline["state"] != "valid_head" and (selected_files or scaffold_new_project):
        if selected_files is not None:
            # Only regular files below the pinned root are accepted; directory
            # pathspecs would silently include files the caller did not select.
            with AnchoredRepository(root_path, expected_identity=root_identity) as repository:
                for path in selected_files:
                    repository.read_bytes(path, max_bytes=0)
        readme = root_path / "README.md"
        gitignore = root_path / ".gitignore"
        if scaffold_new_project:
            selected_files = []
            for path, content in (
                (readme, f"# {project_name}\n"),
                (gitignore, ".DS_Store\n.env\n.venv/\n__pycache__/\n.goodteam/\n"),
            ):
                try:
                    with path.open("x", encoding="utf-8") as handle:
                        handle.write(content)
                    selected_files.append(path.name)
                except OSError as exc:
                    warnings.append(f"git scaffold file {path.name} skipped: {exc.__class__.__name__}")

        init = None
        if baseline["state"] == "non_git":
            init = run(["git", "-C", str(root_path), "init", "-b", "main"])
        if baseline["state"] == "non_git" and (init is None or init.returncode != 0):
            stderr = "" if init is None else init.stderr.strip()
            warnings.append(f"git init failed{f': {stderr}' if stderr else ''}")
        elif selected_files:
            add = run(["git", "--literal-pathspecs", "-C", str(root_path), "add", "--", *selected_files])
            if add is None or add.returncode != 0:
                stderr = "" if add is None else add.stderr.strip()
                warnings.append(f"git add failed{f': {stderr}' if stderr else ''}")
            else:
                commit_command = ["git", "-C", str(root_path), "--literal-pathspecs", "commit", "--only", "-m", "Initial commit (GoodTeam scaffold)", "--", *selected_files]
                if not _git_configured_identity(root_path, warnings, root_identity):
                    commit_command[3:3] = ["-c", "user.name=GoodTeam", "-c", "user.email=noreply@goodteam.local"]
                commit = run(commit_command)
                if commit is None or commit.returncode != 0:
                    stderr = "" if commit is None else commit.stderr.strip()
                    warnings.append(f"git commit failed{f': {stderr}' if stderr else ''}")
                else:
                    result["initialized"] = _project_git_baseline(root_path)["state"] == "valid_head"

    baseline = _project_git_baseline(root_path)
    result.update(baseline)
    if baseline.get("error"):
        warnings.append(baseline["error"])

    if create_remote:
        if baseline["baseline_required"]:
            warnings.append("create-remote skipped: establish a valid Git baseline first")
        elif existing_git:
            warnings.append("create-remote skipped: project root already contains .git")
        else:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                warnings.append("create-remote requested but no GITHUB_TOKEN/GH_TOKEN set")
            else:
                visibility = "--private" if remote_private else "--public"
                gh = run(
                    [
                        "gh",
                        "repo",
                        "create",
                        project_key.lower(),
                        visibility,
                        "--source",
                        str(root_path),
                        "--remote",
                        "origin",
                        "--push",
                    ],
                    env={**os.environ, "GH_TOKEN": token},
                )
                if gh is not None and gh.returncode == 0:
                    result["remote_created"] = True
                else:
                    stderr = "" if gh is None else gh.stderr.strip()
                    warnings.append(f"gh repo create failed{f': {stderr}' if stderr else ''}")
    return result


def initialize_project_git_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    project_key = str(request.get("project_key", "") or "").strip()
    if not project_key:
        raise ValueError("project_key is required.")
    project_config = _project_config_for_key(workspace, project_key)
    target = load_project_target(project_config)
    root_path = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root_path is None:
        raise ValueError(f"No runtime root found for project: {normalize_project_key(project_key)}")
    scaffold = request.get("scaffold_new_project") is True
    if scaffold:
        readiness = read_bound_project_readiness(workspace, normalize_project_key(project_key), root_path)
        if readiness.get("project_mode") != "new_project" or any(
            entry.name not in {".git", ".goodteam", *IGNORED_NAMES} for entry in root_path.iterdir()
        ):
            raise ValueError("Scaffolding requires an empty imported new project. Select existing files explicitly instead.")
    project_name = str(target.get("name", "") or target.get("key", "") or project_key)
    return _initialize_project_git(
        root_path=root_path,
        project_name=project_name,
        project_key=project_key,
        init_git=True,
        create_remote=_request_bool(request.get("create_remote"), False),
        remote_private=_request_bool(request.get("remote_private"), True),
        initial_files=_initial_git_files(request.get("initial_files")),
        scaffold_new_project=scaffold,
    )


def create_local_project_config(
    *,
    workspace: Path,
    project_name: str,
    project_key: str,
    project_root: str = "",
    docs_path: str = "",
    backend_path: str = "",
    frontend_path: str = "",
    task_sync: list[str] | None = None,
    plane_api_host_url: str = "",
    plane_workspace_slug: str = "",
    plane_project_id: str = "",
    plane_api_key_env: str = "",
    plane_mcp_server: str = "",
    worker_backend_mode: str = "codex",
    workers: dict[str, str] | None = None,
    model_profile: str = "balanced",
    delivery_mode: str = "init_project",
    delivery_standards: str = "",
    role_model_overrides: dict[str, str] | None = None,
    worker_type_settings: dict[str, dict[str, object]] | None = None,
    deployment_backend_mode: str = "",
    staging_ssh_target: str = "",
    staging_remote_dir: str = "",
    staging_git_ref: str = "",
    staging_public_url: str = "",
    staging_deploy_command: str = "",
    staging_healthcheck_command: str = "",
    staging_smoke_command: str = "",
    delivery_contract_required: bool = False,
    delivery_contract_minimum_evidence: str = "infrastructure",
    delivery_contract_fatal_patterns: str = "",
    delivery_contract_acceptance: str = "",
    init_git: bool = True,
    create_remote: bool = False,
    remote_private: bool = True,
    initial_files: list[str] | None = None,
) -> dict[str, object]:
    name = _single_line_config_value("Project name", project_name)
    key = _single_line_config_value("Project key", project_key).upper()
    initial_files = _initial_git_files(initial_files)
    if not name:
        raise ValueError("Project name is required.")
    if not key:
        raise ValueError("Project key is required.")

    repos = []
    if project_root.strip():
        root_path = _validate_directory_path("Project root", project_root)
        scaffold_new_project = not any(root_path.iterdir())
        repos.append(("root", root_path))
        try:
            if not (root_path / "docs").exists():
                create_subdirectory(root_path, "docs")
        except FsMkdirConflictError:
            pass
        except Exception as exc:
            import warnings

            warnings.warn(f"project docs directory creation skipped for {root_path}: {exc}", RuntimeWarning, stacklevel=2)
    else:
        if docs_path.strip():
            repos.append(("docs", _validate_directory_path("Docs", docs_path)))
        if backend_path.strip():
            repos.append(("backend", _validate_directory_path("Backend", backend_path)))
        if frontend_path.strip():
            repos.append(("frontend", _validate_directory_path("Frontend", frontend_path)))
    if not repos:
        raise ValueError("Select at least one project directory.")

    git_result: dict[str, object] = {"initialized": False, "remote_created": False, "warnings": []}
    if project_root.strip():
        git_result = _initialize_project_git(
            root_path=root_path,
            project_name=name,
            project_key=key,
            init_git=init_git,
            create_remote=create_remote,
            remote_private=remote_private,
            initial_files=initial_files,
            scaffold_new_project=scaffold_new_project,
        )

    path = workspace / "projects" / f"{_slug(key)}.local.yaml"
    profile = _single_line_config_value("Model profile", model_profile, default="balanced")
    mode = normalize_delivery_mode(delivery_mode)
    overrides = {
        _config_mapping_key("Role model override role", str(role)): _single_line_config_value(
            "Role model override model", str(model)
        )
        for role, model in (role_model_overrides or {}).items()
        if str(role).strip() and str(model).strip()
    }
    lines = [
        f"name: {_config_scalar('Project name', name)}",
        f"key: {_config_scalar('Project key', key)}",
        f"model_profile: {_config_scalar('Model profile', profile)}",
        f"delivery_mode: {mode}",
    ]
    custom_standards = [line.strip() for line in delivery_standards.splitlines() if line.strip()]
    if mode == "custom" and custom_standards:
        lines.append("delivery_standards:")
        lines.extend(
            f"  standard_{index:04d}: {_config_scalar('Delivery standard', standard)}"
            for index, standard in enumerate(custom_standards, start=1)
        )
    if overrides:
        lines.append("role_model_overrides:")
        lines.extend(f"  {role}: {_config_scalar('Role model override model', model)}" for role, model in sorted(overrides.items()))
    lines.extend([
        "repos:",
    ])
    lines.extend(f"  {repo}: {_config_scalar('Repo path', str(repo_path))}" for repo, repo_path in repos)
    sync_values = [_single_line_config_value("Task sync value", str(value)) for value in (task_sync or []) if value]
    lines.extend(["task_backend:", "  mode: local", "  sync:"])
    if sync_values:
        lines[-1] = "  sync: " + ",".join(_config_scalar("Task sync value", value) for value in sync_values)
    else:
        lines[-1] = "  sync: "
    plane_config = {
        "api_host_url": _single_line_config_value("Plane API host URL", plane_api_host_url),
        "workspace_slug": _single_line_config_value("Plane workspace slug", plane_workspace_slug),
        "project_id": _single_line_config_value("Plane project ID", plane_project_id),
        "api_key_env": _single_line_config_value("Plane API key env", plane_api_key_env, default="PLANE_API_KEY"),
        "mcp_server": _single_line_config_value("Plane MCP server", plane_mcp_server, default="plane"),
    }
    if "plane" in sync_values:
        lines.append("  plane:")
        lines.extend(f"    {key}: {_config_scalar('Plane config value', value)}" for key, value in plane_config.items() if value)
    worker_mode = _single_line_config_value("Worker backend mode", worker_backend_mode, default="codex")
    worker_overrides = {
        _config_mapping_key("Worker role", str(role)): _single_line_config_value("Worker backend", str(backend))
        for role, backend in (workers or {}).items()
        if str(role).strip() and str(backend).strip()
    }
    normalized_type_settings = normalize_worker_type_settings(worker_type_settings or {})
    _validate_worker_backend_config(
        mode=worker_mode,
        workers=worker_overrides,
        role_models=overrides,
        worker_type_settings=normalized_type_settings,
    )
    lines.extend(["worker_backend:", f"  mode: {_config_scalar('Worker backend mode', worker_mode)}"])
    if worker_overrides:
        lines.append("workers:")
        lines.extend(
            f"  {role}: {_config_scalar('Worker backend', backend)}"
            for role, backend in _ordered_worker_overrides(worker_overrides).items()
        )
    if normalized_type_settings:
        lines.append("worker_type_settings:")
        for role, settings in _ordered_worker_type_settings(normalized_type_settings).items():
            lines.append(f"  {role}:")
            lines.append(f"    reasoning_depth: {_config_scalar('Worker reasoning depth', str(settings['reasoning_depth']))}")
            if settings["additional_prompt"]:
                lines.append(f"    additional_prompt: {json.dumps(str(settings['additional_prompt']), ensure_ascii=False)}")
            lines.append(f"    desired_instances: {int(settings['desired_instances'])}")
    deployment_backend = {
        "mode": _single_line_config_value("Deployment backend mode", deployment_backend_mode),
        "staging_ssh_target": _single_line_config_value("Staging SSH target", staging_ssh_target),
        "staging_remote_dir": _single_line_config_value("Staging remote dir", staging_remote_dir),
        "staging_git_ref": _single_line_config_value("Staging git ref", staging_git_ref),
        "staging_public_url": _single_line_config_value("Staging public URL", staging_public_url),
        "staging_deploy_command": _single_line_config_value("Staging deploy command", staging_deploy_command),
        "staging_healthcheck_command": _single_line_config_value(
            "Staging healthcheck command", staging_healthcheck_command
        ),
        "staging_smoke_command": _single_line_config_value("Staging smoke command", staging_smoke_command),
    }
    if deployment_backend["mode"]:
        lines.extend(["deployment_backend:", f"  mode: {_config_scalar('Deployment backend mode', deployment_backend['mode'])}"])
        for config_key in [
            "staging_ssh_target",
            "staging_remote_dir",
            "staging_git_ref",
            "staging_public_url",
            "staging_deploy_command",
            "staging_healthcheck_command",
            "staging_smoke_command",
        ]:
            value = deployment_backend[config_key]
            if value:
                lines.append(f"  {config_key}: {_config_scalar(config_key, value)}")
    delivery_contract_lines = _delivery_contract_lines(
        required=delivery_contract_required,
        minimum_evidence=delivery_contract_minimum_evidence,
        fatal_patterns=delivery_contract_fatal_patterns,
        acceptance=delivery_contract_acceptance,
    )
    if delivery_contract_lines:
        lines.extend(delivery_contract_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    task_backend: dict[str, object] = {"mode": "local", "sync": sync_values}
    if "plane" in sync_values:
        task_backend["plane"] = plane_config
    return {
        "project_config": str(path),
        "project_key": key,
        "project_name": name,
        "repos": {repo: str(repo_path) for repo, repo_path in repos},
        "task_backend": task_backend,
        "worker_backend": {"mode": worker_mode},
        "deployment_backend": {"mode": deployment_backend["mode"], **({key: value for key, value in deployment_backend.items() if key != "mode" and value})},
        "delivery_contract": delivery_contract_report(load_project_target(path)),
        "model_profile": profile,
        "delivery_mode": mode,
        "delivery_standards": custom_standards if mode == "custom" else [],
        "role_model_overrides": overrides,
        "worker_type_settings": normalized_type_settings,
        "git": git_result,
    }


def _delivery_contract_lines(
    *,
    required: bool,
    minimum_evidence: str,
    fatal_patterns: str,
    acceptance: str,
) -> list[str]:
    pattern_values = [
        _single_line_config_value("Delivery fatal pattern", line)
        for line in fatal_patterns.splitlines()
        if line.strip()
    ]
    checks = []
    for raw_line in acceptance.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            continue
        check_id, check_type, command = parts
        if check_type not in {"ssh_command", "local_command"}:
            check_type = "ssh_command"
        checks.append(
            (
                _single_line_config_value("Delivery acceptance check ID", check_id),
                _single_line_config_value("Delivery acceptance check type", check_type),
                _single_line_config_value("Delivery acceptance command", command),
            )
        )
    if not required and not pattern_values and not checks:
        return []
    evidence = _single_line_config_value(
        "Delivery contract minimum evidence",
        minimum_evidence,
        default="functional" if required else "infrastructure",
    )
    lines = [
        "delivery_contract:",
        f"  required: {'true' if required else 'false'}",
        f"  minimum_evidence: {evidence}",
    ]
    if pattern_values:
        lines.append("  fatal_output_patterns:")
        for index, pattern in enumerate(pattern_values, start=1):
            lines.append(f"    pattern_{index}: {_config_scalar('Delivery fatal pattern', pattern)}")
    if checks:
        lines.append("  staging_acceptance:")
        for index, (check_id, check_type, command) in enumerate(checks, start=1):
            lines.extend(
                [
                    f"    check_{index}:",
                    f"      id: {_config_scalar('Delivery acceptance check ID', check_id)}",
                    f"      type: {_config_scalar('Delivery acceptance check type', check_type)}",
                    f"      command: {_config_scalar('Delivery acceptance command', command)}",
                ]
            )
    return lines


def resolve_runtime_model_config(model_config: Path) -> Path:
    """Resolve the default model catalog independently of the server cwd."""
    candidate = model_config.expanduser()
    if candidate.is_file():
        return candidate.resolve()
    if str(model_config) not in {"models.yaml", "./models.yaml"}:
        return candidate
    configured = os.getenv("GOODTEAM_MODEL_CONFIG", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path.resolve()
    repository_default = Path(__file__).resolve().parents[2] / "models.yaml"
    return repository_default.resolve() if repository_default.is_file() else candidate


def start_planning_batch(
    *, workspace: Path, idea: str, project_key: str, model_config: Path,
    model_profile: str, delivery_mode: str = "", delivery_standards: list[str] | str = "",
    source_type: str = "new_project", project_config: Path | None = None,
    role_model_overrides: list[str] | None = None, load_profile: str = "greenfield",
    run_id: str = "",
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("A project request is required.")
    key = normalize_project_key(project_key)
    request_id = str(run_id).strip() or str(uuid4())
    config = Path(project_config).expanduser() if project_config is not None else _project_config_for_key(workspace, key)
    target = load_project_target(config)
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root is None:
        raise ValueError("Select this project's actual repository before starting its task.")
    values = delivery_standards.splitlines() if isinstance(delivery_standards, str) else delivery_standards
    criteria = [str(value).strip() for value in values if str(value).strip()]
    metadata = {"source_type": source_type, "delivery_mode": delivery_mode or target.get("delivery_mode", ""),
                "load_profile": load_profile, "requested_write_domains": []}
    runtime = {"model_config": str(resolve_runtime_model_config(model_config)),
               "model_profile": model_profile, "role_model_overrides": role_model_overrides or []}
    from goodteam_os.assistant_queue import queue_project_request
    from goodteam_os.project_iteration_execution import run_queued_iteration_batch

    proposal = queue_project_request(workspace=workspace, project_key=key, root=root,
        request_id=request_id, text=idea.strip(), acceptance_criteria=criteria,
        runtime_request=runtime, metadata=metadata)
    result = run_queued_iteration_batch(workspace=workspace, project_key=key, root=root,
        project_config=config, proposal_ids=[proposal["id"]], expected_revisions={proposal["id"]: proposal["revision"]},
        confirmed=True, background=True)
    return {**result, "project_key": key, "request_id": request_id,
            "task_id": proposal["project_task_id"], "phase": "planning", "dry_run": False}


def report_bug(
    *,
    workspace: Path,
    project_key: str,
    description: str,
    title: str = "",
    severity: str = "p2",
    run_id: str = "",
) -> dict[str, object]:
    """Submit defect context to the same ordinary planning task entry."""
    title = (title or "").strip()
    description = (description or "").strip()
    severity = (severity or "p2").strip() or "p2"
    if not description and not title:
        raise ValueError("Bug description is required.")

    # Resolve the project's config so the fix run inherits repo/model context.
    # Config-light projects may have none — pass through.
    project_config: Path | None
    try:
        project_config = _project_config_for_key(workspace, project_key)
    except ValueError:
        project_config = None

    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    model_profile = str(target.get("model_profile", "") or "") or "balanced"

    heading = f"[BUG][{severity}] {title}".strip() if title else f"[BUG][{severity}]"
    idea = f"{heading}\n\n{description}".strip()

    result = start_planning_batch(
        workspace=workspace,
        idea=idea,
        project_key=project_key,
        model_config=Path("models.yaml"),
        model_profile=model_profile,
        source_type="new_project",
        project_config=project_config,
        load_profile="fix",
        run_id=run_id,
    )
    result["severity"] = severity
    result["title"] = title
    return result


def delete_project(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    config_path = _project_config_for_key(workspace, key)
    projects_dir = (workspace / "projects").resolve()
    resolved_config = config_path.resolve()
    if projects_dir not in resolved_config.parents or not resolved_config.name.endswith(".local.yaml"):
        raise ValueError("Project deletion is limited to local project configs under workspace/projects.")

    state_projects_dir = (workspace / "state" / "projects").resolve()
    state_dir = (state_projects_dir / key).resolve()
    if state_projects_dir not in state_dir.parents:
        raise ValueError("Project deletion is limited to project-scoped GoodTeam state.")

    resolved_config.unlink()
    if state_dir.exists():
        shutil.rmtree(state_dir)

    return {
        "deleted": True,
        "project_key": key,
        "project_config": str(config_path),
        "state_dir": str(state_dir),
    }


def open_path_in_file_manager(*, workspace: Path, relative_path: str) -> dict[str, object]:
    target_path = (workspace / relative_path).resolve()
    resolved_workspace = workspace.resolve()
    if not target_path.is_relative_to(resolved_workspace):
        raise ValueError("Access denied: Path is outside workspace.")

    if not target_path.exists():
        if target_path.parent.exists():
            target_path = target_path.parent
        else:
            target_path = workspace.resolve()

    import sys
    import subprocess
    path_str = str(target_path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False)
        elif sys.platform == "win32":
            os.startfile(path_str)
        else:
            subprocess.run(["xdg-open", path_str], check=False)
        return {"opened": True, "path": path_str}
    except Exception as exc:
        return {"opened": False, "error": str(exc)}



def build_start_console_context(
    workspace: Path,
    model_config: Path = Path("models.yaml"),
    project_config: Path | None = None,
    project_base: Path | None = None,
) -> dict[str, object]:
    project_base = Path.home() if project_base is None else project_base
    resolved_model_config = resolve_runtime_model_config(model_config)
    profiles = list_model_profiles(resolved_model_config) if resolved_model_config.is_file() else ["balanced"]
    project_report: dict[str, object] = {}
    if project_config is not None and project_config.is_file():
        project_report = check_project_target(load_project_target(project_config))
    project = dict(project_report.get("project", {}))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    deployment_backend = deployment_backend_config(project_config) if project_config is not None and project_config.is_file() else {"mode": ""}
    delivery_contract = delivery_contract_report(target) if target else delivery_contract_report({})
    directory_base = _project_directory_base(dict(target.get("repos", {})), project_base)
    return {
        "workspace": str(workspace),
        "model_config": str(model_config),
        "project_config": str(project_config or ""),
        "project_report": project_report,
        "profiles": profiles,
        "project_key": str(project.get("key", "GTO") or "GTO"),
        "project_name": str(project.get("name", "GoodTeam") or "GoodTeam"),
        "model_profile": str(target.get("model_profile", "balanced") or "balanced"),
        "delivery_mode": normalize_delivery_mode(str(target.get("delivery_mode", "init_project") or "init_project")),
        "role_model_overrides": dict(target.get("role_model_overrides", {})) if isinstance(target.get("role_model_overrides", {}), dict) else {},
        "worker_type_settings": normalize_worker_type_settings(target.get("worker_type_settings", {}), strict=False),
        "task_backend": normalize_task_backend(target.get("task_backend", {"mode": "local", "sync": []})),
        "worker_backend": normalize_worker_backend(target.get("worker_backend", {"mode": "codex"})),
        "worker_backend_options": WORKER_BACKEND_OPTIONS,
        "worker_roles": WORKER_ROLES,
        "deployment_backend": deployment_backend,
        "delivery_contract": delivery_contract,
        "project_base": str(directory_base),
        "candidate_dirs": discover_project_directories(directory_base),
    }


def _workspace_product_settings_path(workspace: Path) -> Path:
    return workspace / ".goodteam" / "product-settings.json"


def _workspace_model_profiles(model_config: Path = Path("models.yaml")) -> list[str]:
    model_config = resolve_runtime_model_config(model_config)
    if not model_config.is_file():
        return ["balanced"]
    profiles = list_model_profiles(model_config)
    return profiles or ["balanced"]


def read_workspace_product_settings(workspace: Path) -> dict[str, str]:
    defaults = {
        "worker_backend": "codex",
        "model_profile": "balanced",
    }
    path = _workspace_product_settings_path(workspace)
    if not path.is_file():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectApiError(
            "workspace_settings_invalid",
            "GoodTeam could not read the workspace product settings.",
            500,
            recovery=["repair_workspace_settings", "retry_request"],
        ) from exc
    if not isinstance(payload, dict):
        raise ProjectApiError(
            "workspace_settings_invalid",
            "Workspace product settings must be a JSON object.",
            500,
            recovery=["repair_workspace_settings", "retry_request"],
        )
    return {
        "worker_backend": str(payload.get("worker_backend", defaults["worker_backend"])),
        "model_profile": str(payload.get("model_profile", defaults["model_profile"])),
    }


def build_workspace_product_settings(workspace: Path) -> dict[str, object]:
    defaults = read_workspace_product_settings(workspace)
    overview = build_project_overview_items(workspace)
    return {
        "defaults": defaults,
        "options": {
            "worker_backends": WORKER_BACKEND_OPTIONS,
            "model_profiles": _workspace_model_profiles(),
        },
        "diagnostics": {
            "workspace": str(workspace.expanduser().resolve()),
            "settings_path": str(_workspace_product_settings_path(workspace)),
            "project_count": len(overview),
        },
    }


def save_workspace_product_settings(
    workspace: Path,
    request: dict[str, object],
) -> dict[str, object]:
    worker_backend = str(request.get("worker_backend", "")).strip()
    model_profile = str(request.get("model_profile", "")).strip()
    if worker_backend not in WORKER_BACKEND_OPTIONS:
        raise ProjectApiError(
            "invalid_worker_backend",
            "Choose a configured GoodTeam worker backend.",
            400,
            details={"worker_backend": worker_backend},
            recovery=["choose_worker_backend"],
        )
    model_profiles = _workspace_model_profiles()
    if model_profile not in model_profiles:
        raise ProjectApiError(
            "invalid_model_profile",
            "Choose an available GoodTeam model profile.",
            400,
            details={"model_profile": model_profile},
            recovery=["choose_model_profile"],
        )
    path = _workspace_product_settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "worker_backend": worker_backend,
                    "model_profile": model_profile,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return build_workspace_product_settings(workspace)


def discover_project_configs(workspace: Path) -> list[Path]:
    paths: list[Path] = []
    for project_dir in _project_config_dirs(workspace):
        if project_dir.is_dir():
            paths.extend(project_dir.glob("*.local.yaml"))
    try:
        paths.extend(ProjectRegistry().list_registered_configs())
    except Exception:
        pass
    unique = sorted({path.resolve(): path.resolve() for path in paths if path.is_file()}.values(), key=lambda item: item.name.lower())
    return unique


def _project_config_dirs(workspace: Path) -> list[Path]:
    candidates = [
        workspace / "projects",
        workspace.parent / "projects",
    ]
    goodteam_home = os.getenv("GOODTEAM_HOME")
    if goodteam_home:
        candidates.append(Path(goodteam_home).expanduser() / "projects")
    else:
        candidates.append(Path.home() / ".goodteam" / "projects")
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def build_project_overview_items(workspace: Path) -> list[dict[str, str]]:
    items = []
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        project_key = normalize_project_key(str(target.get("key", config_path.stem)))
        items.append({"project_key": project_key, "project_config": str(config_path)})
    return items


def _project_config_for_key(workspace: Path, project_key: str) -> Path:
    key = normalize_project_key(project_key)
    paths = _project_config_paths_in_work_state.get()
    cache_key = (workspace, key)
    if paths is not None and cache_key in paths:
        return paths[cache_key]
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        if normalize_project_key(str(target.get("key", config_path.stem))) == key:
            if paths is not None:
                paths[cache_key] = config_path
            return config_path
    fallback = workspace / "projects" / f"{key.lower()}.local.yaml"
    if fallback.is_file():
        if paths is not None:
            paths[cache_key] = fallback
        return fallback
    raise ProjectConfigNotFoundError(f"No local project config found for project: {key}")


def _project_target_and_root(
    workspace: Path,
    project_key: str,
) -> tuple[str, dict[str, object], Path]:
    key = normalize_project_key(project_key)
    try:
        config = _project_config_for_key(workspace, key)
    except ValueError as exc:
        raise ProjectApiError(
            "project_not_found",
            str(exc),
            404,
            details={"project_key": key},
            recovery=["return_to_projects"],
        ) from exc
    target = load_project_target(config)
    loaded_key = normalize_project_key(str(target.get("key", key)))
    if loaded_key != key:
        raise ProjectApiError(
            "project_config_changed",
            "The project configuration changed while it was being read.",
            409,
            details={"project_key": key},
            recovery=["reload_project", "return_to_projects"],
        )
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root is None:
        raise ProjectApiError(
            "project_root_unavailable",
            f"Project root is unavailable: {key}",
            409,
            details={"project_key": key},
            recovery=["edit_project_settings", "return_to_projects"],
        )
    return key, target, root


def build_project_home(
    workspace: Path,
    project_key: str,
    *,
    readiness_override: dict[str, object] | None = None,
) -> dict[str, object]:
    key, target, root = _project_target_and_root(workspace, project_key)
    readiness = readiness_override
    if readiness is None:
        try:
            readiness = ProjectReadinessExecutor(workspace).status(key, root)
        except ValueError as exc:
            if not str(exc).startswith("No readiness found for project:"):
                raise
            raise ProjectApiError(
                "project_readiness_missing",
                f"Project readiness is missing: {key}",
                409,
                details={"project_key": key},
                recovery=["return_to_projects", "reload_project"],
            ) from exc
    worker_preflight = check_project_target(target).get("worker_backend_preflight", {})
    worker_preflight = dict(worker_preflight) if isinstance(worker_preflight, Mapping) else {}
    pending = next(
        (
            step
            for step in readiness["steps"]
            if isinstance(step, dict) and step.get("status") != "complete"
        ),
        None,
    )
    readiness_inputs = readiness.get("inputs", {})
    readiness_inputs = dict(readiness_inputs) if isinstance(readiness_inputs, Mapping) else {}
    command_confirmation = readiness_inputs.get("command_verification", {})
    command_confirmation = dict(command_confirmation) if isinstance(command_confirmation, Mapping) else {}
    command_values = command_confirmation.get("values", {})
    command_values = dict(command_values) if isinstance(command_values, Mapping) else {}
    confirmed_command_ids = {
        str(value)
        for value in command_values.get("confirmed_command_ids", [])
        if str(value)
    }
    constraints_confirmation = readiness_inputs.get("constraints_confirmation", {})
    constraints_confirmation = dict(constraints_confirmation) if isinstance(constraints_confirmation, Mapping) else {}
    constraint_values = constraints_confirmation.get("values", {})
    constraint_values = dict(constraint_values) if isinstance(constraint_values, Mapping) else {}
    confirmed_commands: list[str] = []
    constraints: list[str] = []
    artifacts = readiness.get("artifacts", {})
    for artifact in artifacts.values() if isinstance(artifacts, Mapping) else []:
        if not isinstance(artifact, Mapping):
            continue
        content = artifact.get("content", {})
        if not isinstance(content, Mapping):
            continue
        commands = content.get("commands", [])
        for command in commands if isinstance(commands, list) else []:
            if not isinstance(command, Mapping) or str(command.get("id", "")) not in confirmed_command_ids:
                continue
            value = str(command.get("command", "") or command.get("value", "")).strip()
            if value and value not in confirmed_commands:
                confirmed_commands.append(value)
        raw_constraints = content.get("constraints", [])
        values = raw_constraints if isinstance(raw_constraints, list) else [raw_constraints]
        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in constraints:
                constraints.append(normalized)
    for value in (readiness_inputs.get("constraints", ""), constraint_values.get("note", "")):
        for line in str(value).splitlines():
            normalized = line.strip()
            if normalized and normalized not in constraints:
                constraints.append(normalized)
    return {
        "project": {
            "project_key": key,
            "display_name": str(target.get("name", key)),
            "root": str(root),
        },
        "facts": {
            "root": str(root),
            "detected_mode": readiness["project_mode"],
            "project_goal": str(readiness.get("inputs", {}).get("goal", "")),
            "constraints": constraints,
            "constraints_confirmed": constraint_values.get("confirmed") is True,
            "confirmed_commands": confirmed_commands,
            "prohibited_operations": str(readiness_inputs.get("prohibited_operations", "")),
            "worker_status": "ready" if worker_preflight.get("ready") is True else "unavailable",
            "worker_backend": str(worker_preflight.get("mode", "")),
            "worker_hint": str(worker_preflight.get("hint", "")),
        },
        "readiness": readiness,
        "git_baseline": _project_git_baseline(root),
        "capabilities": {
            **build_capabilities(readiness),
        },
        "next_action": pending
        or {"id": "shared_readiness_complete", "status": "complete"},
    }


def _project_attention_snapshot(workspace, key, readiness):
    from goodteam_os.project_attention import build_project_attention

    # Do not call build_project_work_state or TaskAssistantService.session here:
    # those routes may initialize/migrate Team/session state. A cross-project
    # scan must never dispatch, reconcile or write another project's records.
    session = TaskAssistantSessionStore(workspace, key).read()
    if session.get("project_key") != key:
        raise ValueError("Attention source belongs to another project")
    active = any(batch.get("status") in {"claimed", "running"} and batch.get("confirmed") is True
                 and batch.get("project_root") == readiness["canonical_root"]
                 for batch in session.get("project_task_batches", {}).values())
    return build_project_attention(project_key=key, readiness=readiness, session=session), active


def build_project_list(workspace: Path) -> dict[str, object]:
    # Reuse project binding lookups across the overview's approval projections.
    # Only paths are cached, and only for this read, including its error paths.
    token = _project_config_paths_in_work_state.set({})
    try:
        return _build_project_list(workspace)
    finally:
        _project_config_paths_in_work_state.reset(token)


def _build_project_list(workspace: Path) -> dict[str, object]:
    projects: list[dict[str, object]] = []
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        key = normalize_project_key(str(target.get("key", config_path.stem)))
        root = resolve_project_runtime_root(dict(target.get("repos", {})))
        readiness = None
        if root is not None:
            try:
                readiness = read_bound_project_readiness(workspace, key, root)
            except ValueError as exc:
                if not str(exc).startswith("No readiness found for project:"):
                    raise
        if readiness is None:
            readiness = {
                "schema_version": 1,
                "project_key": key,
                "canonical_root": str(root or ""),
                "project_mode": "legacy",
                "lifecycle": "legacy",
                "steps": [],
                "updated_at": None,
            }
        steps = readiness.get("steps", [])
        attention_count = sum(
            1
            for step in steps
            if isinstance(step, dict) and step.get("status") in {"needs_input", "failed"}
        )
        try:
            attention, has_active_batch = _project_attention_snapshot(workspace, key, readiness)
        except (ValueError, OSError, TypeError, KeyError):
            # Unknown is not an empty queue. Keep ordinary project navigation
            # available if an optional attention source cannot be read.
            attention, has_active_batch = None, False
        projects.append(
            {
                "attention": attention,
                "project": {
                    "project_key": key,
                    "display_name": str(target.get("name", key)),
                    "root": str(root or ""),
                },
                "readiness": readiness,
                "capabilities": build_capabilities(readiness),
                "attention_count": attention_count,
                "has_active_batch": has_active_batch,
                }
        )
    return {"projects": projects}


def handle_project_api_get(
    workspace: Path,
    path: str,
    *, codex_review_admission: "CodexReviewAdmission | None" = None,
) -> tuple[int, dict[str, object]] | None:

    if path == "/api/providers":
        from goodteam_os.providers import ProviderStore
        return 200, ProviderStore().view()
    if path == "/api/providers/usage":
        from goodteam_os.provider_usage import usage_summary
        return 200, usage_summary()

    if path == "/api/skills":
        return 200, SkillLibraryService(service_state_dir(), worker_roots=(workspace,)).list_library()
    skill_proposal_match = re.fullmatch(r"/api/skills/proposals/(proposal-[0-9a-f]{32})", path)
    if skill_proposal_match:
        return 200, SkillLibraryService(service_state_dir(), worker_roots=(workspace,)).get_proposal(skill_proposal_match.group(1))
    if path == "/api/projects":
        return 200, build_project_list(workspace)
    if path == "/api/settings":
        return 200, build_workspace_product_settings(workspace)
    home_match = re.fullmatch(r"/api/projects/([^/]+)/home", path)
    if home_match:
        encoded_key = home_match.group(1)
        return 200, build_project_home(workspace, _decode_project_api_key(encoded_key))
    batch_match = re.fullmatch(r"/api/projects/([^/]+)/iteration-batches/(task-batch-[0-9a-f]{64})", path)
    if batch_match:
        from goodteam_os.project_iteration_execution import iteration_batch_report
        from goodteam_os.project_iteration_quality import project_batch_quality_evidence
        from goodteam_os.project_iteration_recovery import _batch, effective_dispatch_id, project_batch_recovery_evidence
        from goodteam_os.dispatch_workspace import dispatch_artifact_root

        key, _target, root = _project_target_and_root(workspace, _decode_project_api_key(batch_match.group(1)))
        try:
            run_id = batch_match.group(2)
            _store, batch = _batch(workspace, key, root, run_id)
            recovery = project_batch_recovery_evidence(workspace=workspace, project_key=key, root=root, run_id=run_id)
            report = {"run_id": effective_dispatch_id(batch), "batch_run_id": run_id,
                      "project_root": str(Path(root).resolve()), "status": batch["status"]}
            if (dispatch_artifact_root(root, effective_dispatch_id(batch)) / "report.json").exists():
                report = iteration_batch_report(workspace=workspace, project_key=key, root=root, run_id=run_id)
            quality = project_batch_quality_evidence(workspace=workspace, project_key=key, root=root, run_id=run_id)
            return 200, {**report, "recovery": recovery,
                         **({"quality": quality} if quality["required"] or batch.get("manager_acceptance") else {})}
        except (OSError, ValueError) as exc:
            raise ProjectApiError("iteration_batch_report_unavailable", str(exc), 409) from exc
    team_events_match = re.fullmatch(r"/api/projects/([^/]+)/team/events", path)
    if team_events_match:
        key = _decode_project_api_key(team_events_match.group(1))
        _project_config_for_key(workspace, key)
        return 200, build_team_events_payload(workspace, key)
    team_match = re.fullmatch(r"/api/projects/([^/]+)/team", path)
    if team_match:
        key = _decode_project_api_key(team_match.group(1))
        _project_config_for_key(workspace, key)
        return 200, build_team_view_payload(workspace, key)
    if path.startswith("/api/projects/") and path.endswith("/home"):
        encoded_key = path.removeprefix("/api/projects/").removesuffix("/home")
        _decode_project_api_key(encoded_key)
    artifact_match = re.fullmatch(
        r"/api/projects/([^/]+)/readiness/steps/([^/]+)/artifact",
        path,
    )
    if artifact_match:
        key = _decode_project_api_key(artifact_match.group(1))
        step_id = _decode_readiness_step_id(artifact_match.group(2))
        key, _target, root = _project_target_and_root(workspace, key)
        readiness = ProjectReadinessExecutor(workspace).status(key, root)
        step = next(
            (
                value
                for value in readiness.get("steps", [])
                if isinstance(value, dict) and value.get("id") == step_id
            ),
            None,
        )
        if step is None:
            raise ReadinessExecutionError(
                "readiness_step_not_found",
                f"Unknown readiness step: {step_id}",
                details={"step_id": step_id},
                recovery=["reload_project"],
            )
        refs = list(step.get("artifact_refs", []) or [])
        if not refs:
            raise ReadinessExecutionError(
                "readiness_artifact_not_found",
                "This readiness step has no artifact.",
                details={"step_id": step_id},
                recovery=["reload_project", "retry_readiness"],
            )
        return 200, ProjectReadinessExecutor(workspace).artifact(key, root, str(refs[-1]))
    return None


def handle_skill_api_post(
    workspace: Path,
    path: str,
    request: dict[str, object],
) -> tuple[int, dict[str, object]] | None:
    service = SkillLibraryService(service_state_dir(), worker_roots=(workspace,))
    try:
        if path == "/api/skills/local/preview":
            source_path = str(request.get("path", "") or "")
            command_key = str(request.get("command_key", "") or "")
            if not source_path.strip() or not command_key.strip():
                raise ProjectApiError(
                    "invalid_request",
                    "Local Skill preview requires path and command_key.",
                    400,
                    recovery=["retry_request"],
                )
            return 200, service.preview_local(source_path, command_key=command_key)
        if path == "/api/skills/local/install":
            proposal_id = str(request.get("proposal_id", "") or "")
            if not proposal_id.strip():
                raise ProjectApiError(
                    "invalid_request",
                    "Local Skill install requires proposal_id.",
                    400,
                    recovery=["retry_request"],
                )
            return 200, service.install(proposal_id)
    except SkillConflict as exc:
        recovery = ["repreview_skill"] if exc.code == "proposal_expired" else ["reload_skill_library", "retry_request"]
        raise ProjectApiError(exc.code, str(exc), 409, recovery=recovery) from exc
    except SkillError as exc:
        status = 404 if exc.code == "proposal_missing" else 409 if exc.code in {"proposal_changed"} else 400
        recovery = ["repreview_skill"] if exc.code == "proposal_changed" else ["retry_request"]
        raise ProjectApiError(exc.code, str(exc), status, recovery=recovery) from exc
    return None


def handle_project_api_post(
    workspace: Path,
    path: str,
    request: dict[str, object],
    *,
    codex_review_admission: "CodexReviewAdmission | None" = None,
    codex_correction_admission=None,
) -> tuple[int, dict[str, object]] | None:

    if path in {"/api/providers", "/api/providers/models"}:
        from goodteam_os.providers import ProviderStore, ProviderError
        try:
            store = ProviderStore()
            return 200, store.fetch_models(request) if path.endswith("/models") else store.update(request)
        except ProviderError as exc:
            raise ProjectApiError(exc.code, str(exc), exc.status) from None

    if path == "/api/settings":
        return 200, save_workspace_product_settings(workspace, request)
    if path == "/api/projects/import":
        root = str(request.get("root", "")).strip()
        if not root:
            raise ProjectApiError(
                "invalid_project_root",
                "Project root is required.",
                400,
                recovery=["choose_another_directory"],
            )
        defaults = read_workspace_product_settings(workspace)
        result = import_project(
            workspace,
            root,
            display_name=str(request.get("display_name", "")),
            requested_key=str(request.get("project_key", "")),
            default_model_profile=defaults["model_profile"],
            default_worker_mode=defaults["worker_backend"],
        )
        imported_project = dict(result.get("project", {}) or {})
        imported_key = normalize_project_key(str(imported_project.get("project_key", "")))
        imported_root = Path(str(imported_project.get("root", "")))
        readiness = ProjectReadinessExecutor(workspace).status(imported_key, imported_root)
        result["readiness"] = readiness
        result["capabilities"] = build_capabilities(readiness)
        return (201 if result["created"] else 200), result
    attachment_upload_match = re.fullmatch(r"/api/projects/([^/]+)/task-assistant/attachments", path)
    if attachment_upload_match:
        from goodteam_os.conversation_attachments import upload_attachment

        key = _decode_project_api_key(attachment_upload_match.group(1))
        key, _target, root = _project_target_and_root(workspace, key)
        return 201, {"attachment": upload_attachment(workspace, key, root, request)}
    task_assistant_message_match = re.fullmatch(
        r"/api/projects/([^/]+)/task-assistant/messages",
        path,
    )
    if task_assistant_message_match:
        key = _decode_project_api_key(task_assistant_message_match.group(1))
        key, _target, root = _project_target_and_root(workspace, key)
        project_config = _project_config_for_key(workspace, key)
        try:
            service = TaskAssistantService(workspace, project_config=project_config)
            inputs = dict(project_key=key, root=root,
                          message_id=str(request.get("message_id", "")), text=str(request.get("text", "")),
                          context_run_id=request.get("context_run_id", ""), attachments=request.get("attachments", []))
            if request.get("background") is True:
                from goodteam_os.assistant_requests import execute_message

                return 202, execute_message(service, **inputs, background=True)
            return 200, service.send_message(**inputs)
        except ManagerExecutionError as exc:
            if exc.code == "manager_resource_busy":
                raise ProjectApiError("manager_resource_busy", str(exc), 409, recovery=["retry_request"]) from exc
            raise ProjectApiError("manager_execution_failed", str(exc), 502) from exc
        except ValueError as exc:
            raise ProjectApiError("invalid_request", str(exc), 409) from exc
    task_assistant_action_match = re.fullmatch(
        r"/api/projects/([^/]+)/task-assistant/actions",
        path,
    )
    if task_assistant_action_match:
        key = _decode_project_api_key(task_assistant_action_match.group(1))
        action = str(request.get("action", ""))
        if action == "stop_project_continuation":
            from goodteam_os.project_iteration_continuation import stop_project_continuation

            try:
                value = stop_project_continuation(workspace=workspace, project_key=key,
                                                  continuation_id=request.get("continuation_id"))
                return 200, {"project_continuation": value,
                             "task_assistant_session": TaskAssistantSessionStore(workspace, key).read()}
            except ValueError as exc:
                raise ProjectApiError("project_continuation_unavailable", str(exc), 409) from exc
        if action == "stop_goal":
            from goodteam_os.goal_continuation import stop_goal_action

            try:
                result = stop_goal_action(workspace, key, request)
            except ValueError as exc:
                raise ProjectApiError("goal_action_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
            # Stop is already durable. Projection failure cannot undo it or
            # falsely report that revocation failed. Outbox retains card sync.
            try:
                key, target, root = _project_target_and_root(workspace, key)
                from goodteam_os.goals import GoalStore

                GoalStore(workspace, key).sync_tasks(result["goal"]["id"], LocalTaskStore(root, key))
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key),
                ).session(key, root)
            except Exception:
                result["projection_unavailable"] = True
            return 200, result
        key, target, root = _project_target_and_root(workspace, key)
        if action in {"retry_project_batch", "abandon_project_batch"}:
            from goodteam_os.project_iteration_recovery import recover_project_batch

            allowed = {"action", "run_id", "expected_recovery_version", "idempotency_key", "confirmed"}
            if action == "retry_project_batch":
                allowed |= {"billing_acknowledged", "retry_task_ids", "resume_continuation", "continuation_id"}
            if set(request) - allowed:
                raise ProjectApiError("batch_recovery_input_invalid", "Recovery accepts only the displayed original batch and task scope.", 409)
            try:
                result = recover_project_batch(workspace=workspace, project_key=key, root=root, request=request)
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key)).session(key, root)
                return 202 if action == "retry_project_batch" else 200, result
            except (ValueError, OSError) as exc:
                raise ProjectApiError("batch_recovery_unavailable", str(exc), 409,
                                      recovery=["inspect_project_batch_evidence"]) from exc
        if action == "advance_project_batch_quality":
            from goodteam_os.project_iteration_quality import advance_project_batch_quality

            allowed = {"action", "run_id", "expected_input_digest", "confirmed", "billing_acknowledged"}
            if set(request) - allowed:
                raise ProjectApiError("batch_quality_input_invalid",
                                      "This action accepts only the existing batch and its frozen input identity.", 409)
            try:
                return 200, advance_project_batch_quality(
                    workspace=workspace, project_key=key, root=root, run_id=request.get("run_id"),
                    expected_input_digest=request.get("expected_input_digest"),
                    confirmed=request.get("confirmed") is True,
                    billing_acknowledged=request.get("billing_acknowledged") is True,
                    codex_review_admission=codex_review_admission,
                    codex_correction_admission=codex_correction_admission,
                )
            except (ValueError, OSError) as exc:
                raise ProjectApiError("batch_quality_unavailable", str(exc), 409,
                                      recovery=["inspect_project_batch_evidence"]) from exc
        if action in {"start_project_continuation", "accept_project_batch_delivery"}:
            from goodteam_os.project_iteration_continuation import (
                accept_project_batch_delivery, configure_project_continuation,
            )

            try:
                if action == "start_project_continuation":
                    result = {"project_continuation": configure_project_continuation(
                        workspace=workspace, project_key=key, root=root,
                        project_config=_project_config_for_key(workspace, key), request=request,
                    )}
                else:
                    result = accept_project_batch_delivery(workspace=workspace, project_key=key,
                                                           root=root, request=request)
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key),
                ).session(key, root)
                return 200, result
            except (ValueError, OSError) as exc:
                raise ProjectApiError("project_continuation_unavailable", str(exc), 409,
                                      recovery=["reload_project"]) from exc
        if action == "plan_next_iteration":
            from goodteam_os.project_iteration_planner import plan_next_iteration

            try:
                result = plan_next_iteration(
                    workspace=workspace, project_key=key, root=root,
                    project_config=_project_config_for_key(workspace, key),
                    expected_backlog_version=request.get("expected_backlog_version"),
                    confirmed=request.get("confirmed") is True,
                )
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key),
                ).session(key, root)
                return 200, result
            except ManagerExecutionError as exc:
                raise ProjectApiError("manager_execution_failed", str(exc), 502) from exc
            except ValueError as exc:
                raise ProjectApiError("iteration_plan_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        if action == "queue_iteration_choice":
            from goodteam_os.assistant_queue import queue_manager_iteration_choice

            if request.get("confirmed") is not True:
                raise ProjectApiError("iteration_queue_unavailable",
                                      "Confirm handing the Manager selection to the ordinary task queue.", 409)
            try:
                result = queue_manager_iteration_choice(
                    workspace=workspace, project_key=key, root=root,
                    expected_backlog_version=request.get("expected_backlog_version"),
                )
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key),
                ).session(key, root)
                return 200, result
            except ValueError as exc:
                raise ProjectApiError("iteration_queue_unavailable", str(exc), 409,
                                      recovery=["reload_project"]) from exc
        if action in {"start_iteration_choice", "start_queued_task_batch"}:
            from goodteam_os.project_iteration_execution import run_queued_iteration_batch, start_recommended_iteration

            try:
                inputs = dict(workspace=workspace, project_key=key, root=root,
                    project_config=_project_config_for_key(workspace, key), confirmed=request.get("confirmed") is True)
                if action == "start_iteration_choice":
                    result = start_recommended_iteration(**inputs,
                        expected_choice_event_id=request.get("expected_choice_event_id"))
                else:
                    result = run_queued_iteration_batch(**inputs, proposal_ids=request.get("proposal_ids"),
                        expected_revisions=request.get("expected_revisions"), background=True)
                return 202, result
            except ValueError as exc:
                raise ProjectApiError("task_batch_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        if action in {"create_goal", "decide_goal_gate", "retry_goal_assessment"}:
            from goodteam_os.goal_continuation import handle_goal_action

            try:
                result = handle_goal_action(workspace, key, root, request)
                result["task_assistant_session"] = TaskAssistantService(
                    workspace, project_config=_project_config_for_key(workspace, key),
                ).session(key, root)
                return 200, result
            except ValueError as exc:
                raise ProjectApiError("goal_action_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        if action in QUEUE_ACTIONS:
            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                return 200, handle_queue_action(service, project_key=key, root=root, request=request)
            except ValueError as exc:
                raise ProjectApiError("task_draft_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        if action in {"edit_task", "resolve_draft", "confirm_project_mode"}:
            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                return 200, handle_draft_action(service, project_key=key, root=root, request=request)
            except ValueError as exc:
                raise ProjectApiError("task_draft_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        if action == "stop_manager_request":
            from goodteam_os.assistant_requests import stop_message

            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                return 200, stop_message(service, project_key=key, root=root,
                    message_id=str(request.get("message_id", "")), expected_attempt=request.get("expected_attempt"))
            except ManagerExecutionError as exc:
                raise ProjectApiError("manager_execution_failed", str(exc), 502) from exc
            except ValueError as exc:
                raise ProjectApiError("invalid_request", str(exc), 409) from exc
        if action in {"retry_message", "check_runtime"}:
            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                if action == "check_runtime":
                    if request.get("confirmed") is not True:
                        raise ValueError("Confirm the potentially billable runtime check first.")
                    return 200, service.check_runtime(project_key=key, root=root, message_id=str(request.get("message_id", "")))
                if request.get("background") is True:
                    from goodteam_os.assistant_requests import retry_message

                    return 202, retry_message(service, project_key=key, root=root,
                        message_id=str(request.get("message_id", "")), expected_attempt=request.get("expected_attempt"),
                        background=True)
                return 200, service.retry_message(
                    project_key=key, root=root, message_id=str(request.get("message_id", "")),
                    expected_attempt=request.get("expected_attempt"),
                )
            except ManagerExecutionError as exc:
                if exc.code == "manager_resource_busy":
                    raise ProjectApiError("manager_resource_busy", str(exc), 409, recovery=["retry_request"]) from exc
                raise ProjectApiError("manager_execution_failed", str(exc), 502) from exc
            except ValueError as exc:
                raise ProjectApiError("invalid_request", str(exc), 409) from exc
        if action in {"confirm_team_decision", "dismiss_team_decision"}:
            proposal_id = str(request.get("proposal_id", ""))
            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                if action == "confirm_team_decision":
                    session = service.confirm_pending_team_decision(project_key=key, proposal_id=proposal_id)
                else:
                    session = service.dismiss_pending_team_decision(project_key=key, proposal_id=proposal_id)
            except ValueError as exc:
                raise ProjectApiError(
                    "team_decision_unavailable",
                    str(exc),
                    409,
                    recovery=["reload_project", "retry_request"],
                ) from exc
            return 200, {
                "task_assistant_session": session,
                "applied": action == "confirm_team_decision",
                "team": build_team_view_payload(workspace, key)["team"],
            }
        if action not in {"confirm_task", "continue_preparation"}:
            raise ProjectApiError(
                "invalid_request",
                "Unknown Task Assistant action.",
                400,
                recovery=["retry_request"],
            )
        executor = ProjectReadinessExecutor(workspace)
        from goodteam_os.assistant_drafts import start_selected_project_task

        service = TaskAssistantService(workspace, readiness_executor=executor,
                                       project_config=_project_config_for_key(workspace, key))
        try:
            selected_result = start_selected_project_task(service, project_key=key, root=root, request=request)
        except ValueError as exc:
            raise ProjectApiError("task_draft_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        return 202, selected_result
    team_commands_match = re.fullmatch(r"/api/projects/([^/]+)/team/commands", path)
    if team_commands_match:
        key = _decode_project_api_key(team_commands_match.group(1))
        config_path = _project_config_for_key(workspace, key)
        raw_commands = request.get("commands", [])
        if not isinstance(raw_commands, list) or not raw_commands:
            raise ProjectApiError(
                "invalid_request",
                "Team commands payload must include a non-empty commands list.",
                400,
                recovery=["retry_request"],
            )
        commands = [dict(item) for item in raw_commands if isinstance(item, dict)]
        if len(commands) != len(raw_commands):
            raise ProjectApiError(
                "invalid_request",
                "Each team command must be a JSON object.",
                400,
                recovery=["retry_request"],
            )
        expected_team_revision = request.get("expected_team_revision")
        if expected_team_revision is not None and type(expected_team_revision) is not int:
            raise ProjectApiError(
                "invalid_request",
                "expected_team_revision must be an integer.",
                400,
                recovery=["reload_team", "retry_request"],
            )
        try:
            outcome = execute_team_commands(
                workspace,
                key,
                commands,
                actor="user",
                surface="team_ui",
                reason=str(request.get("reason", "") or "")[:300],
                source_ref={"client_context": dict(request.get("client_context") or {})} if request.get("client_context") else None,
                command_key=str(request.get("command_key", "") or "")[:200],
                config_path=config_path,
                expected_revision=expected_team_revision,
            )
        except TeamControlPlaneError as exc:
            raise ProjectApiError(
                "team_command_rejected",
                str(exc),
                409,
                recovery=["reload_team", "retry_request"],
            ) from exc
        return 200, {
            "ok": True,
            "decision_id": outcome["decision_id"],
            "changed": outcome["changed"],
            "replayed": outcome["replayed"],
            "team_revision": outcome["team_revision"],
            "events": outcome["events"],
            "team": outcome["team"],
        }
    mode_match = re.fullmatch(r"/api/projects/([^/]+)/mode", path)
    if mode_match:
        key = _decode_project_api_key(mode_match.group(1))
        mode = str(request.get("mode", ""))
        if mode not in {"new_project", "existing_project"}:
            raise ProjectApiError(
                "invalid_project_mode",
                "Project mode must be new_project or existing_project.",
                400,
                details={"mode": mode},
                recovery=["choose_project_mode"],
            )
        key, _target, root = _project_target_and_root(workspace, key)
        try:
            readiness = confirm_project_mode(workspace, key, root, mode)
        except ValueError as exc:
            if not str(exc).startswith("No readiness found for project:"):
                raise
            raise ProjectApiError(
                "project_readiness_missing",
                f"Project readiness is missing: {key}",
                409,
                details={"project_key": key},
                recovery=["return_to_projects", "reload_project"],
            ) from exc
        return 200, build_project_home(
            workspace,
            key,
            readiness_override=readiness,
        )

    readiness_match = re.fullmatch(
        r"/api/projects/([^/]+)/readiness/(start|input|retry|cancel)",
        path,
    )
    if readiness_match:
        key = _decode_project_api_key(readiness_match.group(1))
        action = readiness_match.group(2)
        key, _target, root = _project_target_and_root(workspace, key)
        executor = ProjectReadinessExecutor(workspace)
        if action == "start":
            raw_inputs = request.get("inputs", {})
            if not isinstance(raw_inputs, dict):
                raise ProjectApiError(
                    "invalid_request",
                    "Readiness inputs must be a JSON object.",
                    400,
                    recovery=["retry_request"],
                )
            executor.start(
                key,
                root,
                raw_inputs,
                idempotency_key=str(request.get("idempotency_key", "")),
            )
            return 202, build_project_home(workspace, key)
        if action == "input":
            raw_values = request.get("values", {})
            if not isinstance(raw_values, dict):
                raise ProjectApiError(
                    "invalid_request",
                    "Readiness input values must be a JSON object.",
                    400,
                    recovery=["retry_request"],
                )
            executor.submit_input(
                key,
                root,
                str(request.get("step_id", "")),
                raw_values,
            )
            return 202, build_project_home(workspace, key)
        if action == "retry":
            executor.retry(key, root, str(request.get("step_id", "")))
            return 202, build_project_home(workspace, key)
        return 200, build_project_home(
            workspace,
            key,
            readiness_override=executor.cancel(key, root),
        )
    return None


def _decode_project_api_key(encoded_key: str) -> str:
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", encoded_key):
            raise ValueError("malformed percent escape")
        decoded = unquote_to_bytes(encoded_key).decode("utf-8", errors="strict")
        if "/" in decoded or "\x00" in decoded:
            raise ValueError("project key must be one URL segment")
        return normalize_project_key(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectApiError(
            "invalid_project_key",
            "Project key must be one valid UTF-8 URL segment.",
            400,
            details={"project_key": encoded_key},
            recovery=["return_to_projects", "choose_project"],
        ) from exc


def _decode_readiness_step_id(encoded_step_id: str) -> str:
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", encoded_step_id):
            raise ValueError("malformed percent escape")
        decoded = unquote_to_bytes(encoded_step_id).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectApiError(
            "invalid_readiness_step_id",
            "Readiness step ID must be one valid URL segment.",
            400,
            recovery=["reload_project"],
        ) from exc
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", decoded):
        raise ProjectApiError(
            "invalid_readiness_step_id",
            "Readiness step ID must be one valid URL segment.",
            400,
            recovery=["reload_project"],
        )
    return decoded


def _is_skill_api_post_path(path: str) -> bool:
    return path in {"/api/skills/local/preview", "/api/skills/local/install"}


def _check_provider_http_request(handler, path):
    if path not in {"/api/providers", "/api/providers/models"}:
        return
    client = ipaddress.ip_address(str(handler.client_address[0]))
    if not client.is_loopback or not _protected_execution_origin_is_trusted(
        origin=str(handler.headers.get("Origin", "")), request_host=str(handler.headers.get("Host", "")),
        server_host=str(handler.server.server_address[0]), server_port=handler.server.server_port,
        fetch_site=str(handler.headers.get("Sec-Fetch-Site", "")),
    ):
        raise ProjectApiError("same_origin_required", "Configure providers from the local GoodTeam origin.", 403)
    if str(handler.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower() != "application/json":
        raise ProjectApiError("json_content_type_required", "Provider requests require application/json.", 415)


def _is_project_api_post_path(path: str) -> bool:
    return bool(
        path in {"/api/projects/import", "/api/settings", "/api/providers", "/api/providers/models"}
        or re.fullmatch(
            r"/api/projects/[^/]+/(mode|readiness/(start|input|retry|cancel)|task-assistant/(messages|actions|attachments)|team/commands)",
            path,
        )
    )


def _parse_project_api_request(raw_body: bytes) -> dict[str, object]:
    try:
        request = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectApiError(
            "invalid_request",
            "Request body must be a JSON object.",
            400,
            recovery=["retry_request"],
        ) from exc
    if not isinstance(request, dict):
        raise ProjectApiError(
            "invalid_request",
            "Request body must be a JSON object.",
            400,
            recovery=["retry_request"],
        )
    return request


def _project_api_error_response(
    error: Exception,
) -> tuple[int, dict[str, object]]:
    request_id = uuid4().hex
    if isinstance(error, (ProjectApiError, RequestBodyError)):
        status = error.status
        code = error.code
        message = error.message
        details = error.details
        recovery = error.recovery
    elif isinstance(error, ProjectImportError):
        status = IMPORT_ERROR_STATUS.get(error.code, 400)
        code = error.code
        message = str(error)
        details = error.details
        recovery = IMPORT_ERROR_RECOVERY.get(error.code, [])
    elif isinstance(error, ReadinessExecutionError):
        status = READINESS_ERROR_STATUS.get(error.code, 400)
        code = error.code
        message = str(error)
        details = error.details
        recovery = error.recovery
    elif isinstance(error, (PermissionError, FsListPermissionError)):
        status = 403
        code = "permission_denied"
        message = str(error) or "Permission denied."
        details = {}
        recovery = ["fix_permissions", "retry_request"]
    elif isinstance(error, FsListNotFoundError):
        status = 404
        code = "path_not_found"
        message = str(error)
        details = {}
        recovery = ["choose_another_directory"]
    elif isinstance(error, FsMkdirConflictError):
        status = 409
        code = "path_conflict"
        message = str(error)
        details = {}
        recovery = ["choose_another_directory"]
    elif isinstance(error, (ValueError, FsListBadRequestError)):
        status = 400
        code = "invalid_request"
        message = str(error)
        details = {}
        recovery = ["retry_request"]
    else:
        logging.getLogger(__name__).exception(
            "Unexpected project API error (request_id=%s)",
            request_id,
        )
        status = 500
        code = "internal_error"
        message = "GoodTeam could not complete this request."
        details = {}
        recovery = ["retry", "report_request_id"]
    return status, {
        "code": code,
        "message": message,
        "details": details,
        "recovery": recovery,
        "request_id": request_id,
    }


def is_product_spa_path(path: str) -> bool:
    return bool(
        path in {"/", "/projects", "/settings"}
        or re.fullmatch(
            r"/projects/[^/]+/(?:work|knowledge|team|settings)",
            path,
        )
    )


def resolve_project_root_for_request(workspace: Path, request: dict[str, object]) -> Path:
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        return workspace
    config_path = _project_config_for_key(workspace, project_key)
    target = load_project_target(config_path)
    repos = target.get("repos", {})
    if isinstance(repos, dict) and repos:
        return resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()})
    return config_path.parent


CONTEXT_URL_PER_SOURCE_BYTES = 1_048_576
CONTEXT_URL_TOTAL_BYTES = 5_242_880
CONTEXT_URL_TIMEOUT_SECONDS = 5


def context_estimate_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project_key is required")
    project_config, project_root = _context_project_paths(workspace, project_key)
    declared_sources = _normalize_context_sources(request.get("sources", []))
    if declared_sources:
        _persist_context_sources(project_config, declared_sources)
        sources = _context_source_packets(project_root, declared_sources)
        estimate = project_context.estimate_context_tokens(sources)
        needs_generation = project_context.read_context_entry(project_root) is None
    else:
        result = project_context.load_or_generate_context(
            project_config=project_config,
            root=project_root,
            generate=False,
        )
        estimate = result.get("estimate") or {"bytes": 0, "est_tokens": 0, "source_count": 0}
        needs_generation = bool(result.get("needs_generation", result.get("entry") is None))
    return {
        "estimate": estimate,
        "needs_generation": needs_generation,
        "recommended_model": os.getenv("GOODTEAM_CONTEXT_MODEL", "gpt-5.4-mini"),
    }


def context_generate_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    if request.get("confirm") is not True:
        raise ValueError("confirmation required")
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project_key is required")
    project_config, project_root = _context_project_paths(workspace, project_key)
    declared_sources = _normalize_context_sources(request.get("sources", []))
    if declared_sources:
        _persist_context_sources(project_config, declared_sources)
        sources = _context_source_packets(project_root, declared_sources)
        estimate = project_context.estimate_context_tokens(sources)
        generated = project_context.generate_context_entry(
            sources=sources,
            root=project_root,
        )
        return {
            "generated": True,
            "understanding_path": generated["understanding_path"],
            "index_path": generated["index_path"],
            "estimate": estimate,
        }
    result = project_context.load_or_generate_context(
        project_config=project_config,
        root=project_root,
        generate=True,
    )
    entry = result.get("entry") or {}
    return {
        "generated": True,
        "understanding_path": str(project_root / project_context.CONTEXT_DIR_NAME / project_context.UNDERSTANDING_FILE),
        "index_path": str(project_root / project_context.CONTEXT_DIR_NAME / project_context.INDEX_FILE),
        "estimate": result.get("estimate") or {"bytes": 0, "est_tokens": 0, "source_count": 0},
        "source": entry.get("source", "generated") if isinstance(entry, dict) else "generated",
    }


def _context_project_paths(workspace: Path, project_key: str) -> tuple[Path, Path]:
    project_config = _project_config_for_key(workspace, project_key)
    target = load_project_target(project_config)
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root is None:
        raise ValueError(f"No project root found for project: {project_key}")
    return project_config, root


def _normalize_context_sources(raw_sources: object) -> list[dict[str, str]]:
    if not isinstance(raw_sources, list):
        return []
    sources: list[dict[str, str]] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        value = str(raw.get("value", "")).strip()
        if kind not in {"file", "dir", "url"} or not value:
            continue
        sources.append({"kind": kind, "value": value})
    return sources


def _context_source_packets(root: Path, declared_sources: list[dict[str, str]]) -> list[dict[str, object]]:
    packets: list[dict[str, object]] = []
    total_url_bytes = 0
    for source in declared_sources:
        kind = source["kind"]
        value = source["value"]
        if kind == "url":
            packet = _fetch_context_url_source(
                value,
                per_source_cap=CONTEXT_URL_PER_SOURCE_BYTES,
                timeout=CONTEXT_URL_TIMEOUT_SECONDS,
            )
            content = str(packet.get("content", ""))
            url_bytes = len(content.encode("utf-8"))
            if url_bytes > CONTEXT_URL_PER_SOURCE_BYTES:
                raise ValueError(f"URL source exceeds {CONTEXT_URL_PER_SOURCE_BYTES} byte cap: {value}")
            total_url_bytes += url_bytes
            if total_url_bytes > CONTEXT_URL_TOTAL_BYTES:
                raise ValueError(f"URL sources exceed {CONTEXT_URL_TOTAL_BYTES} byte total cap")
            packets.append(packet)
            continue
        path = _resolve_context_local_path(root, value)
        if kind == "file":
            if not path.is_file():
                raise ValueError(f"Context file not found: {value}")
            packets.append(
                {
                    "repo": "declared_context",
                    "path": str(path),
                    "purpose": "Declared project context file",
                    "kind": kind,
                    "value": value,
                }
            )
            continue
        if not path.is_dir():
            raise ValueError(f"Context directory not found: {value}")
        packets.append(
            {
                "repo": "declared_context",
                "path": str(path),
                "purpose": "Project context directory",
                "summary": project_context.build_code_skeleton(path),
                "kind": kind,
                "value": value,
            }
        )
    return packets


def _resolve_context_local_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _fetch_context_url_source(url: str, *, per_source_cap: int, timeout: float) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported context URL scheme: {url}")
    with urlopen(url, timeout=timeout) as response:
        body = response.read(per_source_cap + 1)
    content = body.decode("utf-8", errors="replace")
    return {
        "repo": "declared_context",
        "url": url,
        "content": content,
        "purpose": "Untrusted URL context source",
        "kind": "url",
        "value": url,
    }


def _persist_context_sources(project_config: Path, sources: list[dict[str, str]]) -> None:
    existing = project_config.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    skipping = False
    for line in existing:
        if line.startswith("context_sources:"):
            skipping = True
            continue
        if skipping:
            if line and not line.startswith(" "):
                skipping = False
            else:
                continue
        if not skipping:
            kept.append(line)
    kept.append("context_sources:")
    for index, source in enumerate(sources, start=1):
        kept.append(f"  source_{index:04d}:")
        kept.append(f"    kind: {_config_scalar('Context source kind', source['kind'])}")
        kept.append(f"    value: {_config_scalar('Context source value', source['value'])}")
    project_config.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def _send_chat_attachment(handler, workspace, path):
    match = re.fullmatch(r"/api/projects/([^/]+)/task-assistant/attachments/([0-9a-f]{64})", path)
    if not match:
        return False
    from goodteam_os.conversation_attachments import read_attachment

    try:
        key, _target, root = _project_target_and_root(workspace, _decode_project_api_key(match.group(1)))
        metadata, original, _text = read_attachment(workspace, key, root, match.group(2))
        body = original.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", metadata["media_type"])
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "private, no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        disposition = "inline" if metadata["media_type"].startswith("image/") else "attachment"
        handler.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(metadata['name'], safe='')}")
        handler.end_headers()
        handler.wfile.write(body)
    except Exception as exc:
        handler._send_api_error(exc)
    return True


def handle_execution_get(path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, object]] | None:
    match = re.fullmatch(r"/api/executions/([^/]+)(?:/(ledger|review-workbench))?", path)
    if match is None:
        return None
    execution_id = match.group(1)
    surface = match.group(2) or ""
    try:
        api = ExecutionAPI(_execution_project_root_from_query(query))
        if surface == "ledger":
            return 200, api.ledger(execution_id)
        if surface == "review-workbench":
            return 200, api.get_review_workbench(execution_id)
        return 200, api.replay(execution_id)
    except ValueError as exc:
        return _execution_error_response(exc)


def handle_execution_post(path: str, request: dict[str, object]) -> tuple[int, dict[str, object]] | None:
    try:
        correction_match = re.fullmatch(r"/executions/([^/]+)/correction/(start|stop|prepare-worker)", path)
        if correction_match is not None:
            api = ExecutionAPI(_execution_project_root_from_body(request))
            execution_id, action = correction_match.groups()
            if action == "stop":
                return 200, api.stop_correction_loop(execution_id, loop_id=_required_str(request, "loop_id"))
            if action == "prepare-worker":
                return 200, api.prepare_correction_worker(execution_id, loop_id=_required_str(request, "loop_id"),
                    receipt_id=_required_str(request, "validation_receipt"), head_sha=_required_str(request, "head"),
                    operation_id=_required_str(request, "operation_id"))
            return 200, api.start_correction_loop(execution_id,
                receipt_id=_required_str(request, "validation_receipt"), head_sha=_required_str(request, "head"))

        decision_match = re.fullmatch(r"/executions/([^/]+)/review-workbench/decision", path)
        if decision_match is not None:
            return _record_review_workbench_decision(decision_match.group(1), request)

        match = re.fullmatch(r"/executions/([^/]+)/(pr|ci)", path)
        if match is None:
            return None
        execution_id = match.group(1)
        api = ExecutionAPI(_execution_project_root_from_body(request))
        command = match.group(2)
        if command == "pr":
            return 200, api.create_or_reuse_pr(execution_id)
        return 200, api.observe_ci(execution_id)
    except ValueError as exc:
        return _execution_error_response(exc)


def _record_review_workbench_decision(
    execution_id: str,
    request: dict[str, object],
) -> tuple[int, dict[str, object]]:
    api = ExecutionAPI(_execution_project_root_from_body(request))
    decision = _required_str(request, "decision").lower()
    reviewer = _required_str(request, "reviewer")
    reason = str(request.get("reason", "")).strip()
    api.replay(execution_id)
    if request.get("validation_receipt") or request.get("head"):
        # The same-origin form records an explicit human attestation only. It
        # cannot execute commands, accept delivery or authenticate independence.
        result = api.attest_candidate_review(execution_id,
            receipt_id=_required_str(request, "validation_receipt"), head_sha=_required_str(request, "head"),
            reviewer=reviewer, decision=decision, reason=reason, origin="explicit_operator_workbench")
        return 200, {"status": "recorded", "execution_id": execution_id, "human_record": result,
                     "acceptance_status": "unknown"}
    if decision == "approve":
        review = api.get_review_workbench(execution_id)
        workbench_status = str(review.get("status", "")).strip()
        if workbench_status != "completed":
            raise ValueError("approve decision requires completed execution")
    result = api.record_review_decision(execution_id, decision, reviewer, reason)
    review = api.get_review_workbench(execution_id)
    return 200, {
        **result,
        "execution_id": execution_id,
        "reviewer_decision": dict(review.get("reviewer_decision", {})),
        "workbench_status": str(review.get("status", "")),
    }


def _execution_project_root_from_query(query: dict[str, list[str]]) -> Path:
    values = query.get("project_root", [])
    value = values[0] if values else ""
    if not value.strip():
        raise ValueError("project_root is required")
    return Path(value)


def _execution_project_root_from_body(request: dict[str, object]) -> Path:
    value = str(request.get("project_root", "")).strip()
    if not value:
        raise ValueError("project_root is required")
    return Path(value)


def _required_str(request: dict[str, object], field_name: str) -> str:
    value = str(request.get(field_name, "")).strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def _execution_error_response(exc: ValueError) -> tuple[int, dict[str, object]]:
    message = str(exc)
    status = 404 if message.startswith("Execution run not found: ") else 400
    return status, {"status": "error", "error": message}


def normalize_task_backend(value: object) -> dict[str, object]:
    backend = dict(value) if isinstance(value, dict) else {}
    mode = str(backend.get("mode", "local") or "local")
    sync = backend.get("sync", [])
    if isinstance(sync, list):
        sync_values = [str(item) for item in sync if item]
    elif isinstance(sync, dict):
        sync_values = []
    else:
        sync_values = [part.strip() for part in str(sync).split(",") if part.strip()] if sync else []
    return {"mode": mode, "sync": sync_values}


def normalize_worker_backend(value: object) -> dict[str, object]:
    backend = dict(value) if isinstance(value, dict) else {}
    return {"mode": str(backend.get("mode", "codex") or "codex")}


def update_project_worker_backend_settings(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    """Deprecated role-keyed settings bridge, adapted onto the control plane.

    The canonical mutation path is `POST /api/projects/<key>/team/commands`.
    This adapter only translates the legacy role-keyed payload into the same
    domain commands; it never writes worker config directly.
    """
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project is required")
    mode = str(request.get("worker_backend_mode", "") or "").strip()
    if not mode:
        raise ValueError("worker_backend_mode is required")
    workers_value = request.get("workers", {})
    if not isinstance(workers_value, dict):
        raise ValueError("workers must be an object")
    workers = {str(role).strip(): str(backend).strip() for role, backend in workers_value.items()}
    role_models_present = "role_models" in request or "role_model_overrides" in request
    role_models_value = request.get("role_models", request.get("role_model_overrides", {}))
    if not isinstance(role_models_value, dict):
        raise ValueError("role_models must be an object")
    role_models = {str(role).strip(): str(model).strip() for role, model in role_models_value.items()}
    worker_type_settings_present = "worker_type_settings" in request
    worker_type_settings = normalize_worker_type_settings(request.get("worker_type_settings", {}))
    _validate_worker_backend_config(
        mode=mode,
        workers=workers,
        role_models=role_models,
        worker_type_settings=worker_type_settings,
    )

    config_path = _project_config_for_key(workspace, project_key)
    from goodteam_os.team_control_plane import MANAGER_ROLE, attached_team_state, ensure_team_state

    # A settings write is a team touchpoint: migrate once before translating.
    ensure_team_state(workspace, normalize_project_key(project_key), config_path)
    target = load_project_target(config_path)
    state = attached_team_state(target)
    if state is None:
        # Unmigrated/synthetic target: preserve the legacy direct write path.
        existing_role_models = target.get("role_model_overrides", {})
        existing_worker_type_settings = normalize_worker_type_settings(target.get("worker_type_settings", {}), strict=False)
        role_model_overrides = (
            {str(role): str(model) for role, model in existing_role_models.items()}
            if not role_models_present and isinstance(existing_role_models, dict)
            else {}
        )
        for role, model in role_models.items():
            if not model or model.lower() == "default":
                role_model_overrides.pop(role, None)
            else:
                role_model_overrides[role] = model
        update_worker_backend_config(
            config_path,
            mode=mode,
            workers=_ordered_worker_overrides(workers),
            role_model_overrides=_ordered_role_model_overrides(role_model_overrides),
            worker_type_settings=_ordered_worker_type_settings(
                worker_type_settings if worker_type_settings_present else existing_worker_type_settings
            ),
        )
        report = check_project_target(load_project_target(config_path))
        return {
            "ok": True,
            "role_worker_backends": report["role_worker_backends"],
            "worker_backends_preflight": report["worker_backends_preflight"],
            "role_model_overrides": _ordered_role_model_overrides(role_model_overrides),
            "worker_type_settings": _ordered_worker_type_settings(
                worker_type_settings if worker_type_settings_present else existing_worker_type_settings
            ),
        }

    # Migrated project: translate the role-keyed payload into control-plane
    # commands under one decision.
    from goodteam_os.team_control_plane import build_team_capability_view

    commands: list[dict[str, object]] = []
    view = build_team_capability_view(state)
    for entry in view["roles"]:
        role = str(entry["role"])
        roster = [dict(worker) for worker in entry["workers"]]
        active = [worker for worker in roster if worker["lifecycle"] == "active"]
        target_provider = workers.get(role) or mode
        target_model = ""
        if role in role_models:
            raw_model = role_models[role]
            # The legacy contract treats "default" (and blank) as clearing the
            # per-role model override.
            if raw_model.strip() and raw_model.strip().lower() != "default":
                target_model = raw_model.split(":", 1)[1].strip() if ":" in raw_model else raw_model.strip()
        role_settings = dict(worker_type_settings.get(role, {}) or {})
        for worker in active:
            command: dict[str, object] = {
                "kind": "change_worker_runtime",
                "worker_id": worker["id"],
                "provider": target_provider,
            }
            if role_models_present:
                command["model"] = target_model
            if worker_type_settings_present:
                command["reasoning_depth"] = str(role_settings.get("reasoning_depth", "default") or "default")
                command["additional_prompt"] = str(role_settings.get("additional_prompt", "") or "")
            commands.append(command)
        if worker_type_settings_present:
            desired = int(role_settings.get("desired_instances", 1) or 1)
            inactive_pool = [worker for worker in roster if worker["lifecycle"] == "inactive"]
            while len(active) < desired:
                if inactive_pool:
                    worker = inactive_pool.pop(0)
                    commands.append({"kind": "activate_worker", "worker_id": worker["id"]})
                    active.append(worker)
                else:
                    commands.append(
                        {
                            "kind": "initialize_worker",
                            "role": role,
                            "ensure_role_enabled": True,
                            "runtime": {
                                "provider": target_provider,
                                "model": target_model,
                                "reasoning_depth": str(role_settings.get("reasoning_depth", "default") or "default"),
                                "additional_prompt": str(role_settings.get("additional_prompt", "") or ""),
                            },
                        }
                    )
                    active.append({"id": ""})
            while len(active) > desired:
                worker = active.pop()
                if worker.get("id"):
                    commands.append({"kind": "inactivate_worker", "worker_id": worker["id"]})
    if MANAGER_ROLE in workers or MANAGER_ROLE in role_models or (worker_type_settings_present and MANAGER_ROLE in worker_type_settings):
        manager_command: dict[str, object] = {"kind": "change_manager_runtime"}
        if MANAGER_ROLE in workers:
            manager_command["provider"] = workers[MANAGER_ROLE]
        if role_models_present and MANAGER_ROLE in role_models:
            raw_model = role_models[MANAGER_ROLE]
            if raw_model.strip() and raw_model.strip().lower() != "default":
                manager_command["model"] = raw_model.split(":", 1)[1].strip() if ":" in raw_model else raw_model.strip()
        if worker_type_settings_present:
            manager_settings = dict(worker_type_settings.get(MANAGER_ROLE, {}) or {})
            manager_command["reasoning_depth"] = str(manager_settings.get("reasoning_depth", "default") or "default")
            manager_command["additional_prompt"] = str(manager_settings.get("additional_prompt", "") or "")
        commands.append(manager_command)
    if commands:
        execute_team_commands(
            workspace,
            normalize_project_key(project_key),
            commands,
            actor="user",
            surface="api",
            reason="Legacy role-keyed settings bridge.",
            config_path=config_path,
        )
    report = check_project_target(load_project_target(config_path))
    return {
        "ok": True,
        "role_worker_backends": report["role_worker_backends"],
        "worker_backends_preflight": report["worker_backends_preflight"],
        "role_model_overrides": _ordered_role_model_overrides(
            dict(load_project_target(config_path).get("role_model_overrides", {}) or {})
        ),
        "worker_type_settings": normalize_worker_type_settings(
            load_project_target(config_path).get("worker_type_settings", {}), strict=False
        ),
    }


def _validate_worker_backend_config(
    *,
    mode: str,
    workers: dict[str, str],
    role_models: dict[str, str] | None = None,
    worker_type_settings: dict[str, dict[str, object]] | None = None,
) -> None:
    valid_backends = set(WORKER_BACKEND_ACCEPTED_OPTIONS)
    if mode not in valid_backends:
        raise ValueError(f"Unknown worker backend: {mode}. Use one of: {', '.join(WORKER_BACKEND_ACCEPTED_OPTIONS)}")
    valid_roles = set(WORKER_ROLES)
    for role, backend in workers.items():
        if not role:
            raise ValueError("worker role cannot be empty")
        if role not in valid_roles:
            raise ValueError(f"Unknown worker role: {role}. Use one of: {', '.join(WORKER_ROLES)}")
        if backend not in valid_backends:
            raise ValueError(f"Unknown worker backend for {role}: {backend}. Use one of: {', '.join(WORKER_BACKEND_ACCEPTED_OPTIONS)}")
    for role, model in (role_models or {}).items():
        if not role:
            raise ValueError("worker role cannot be empty")
        if role not in valid_roles:
            raise ValueError(f"Unknown worker role: {role}. Use one of: {', '.join(WORKER_ROLES)}")
    for role in (worker_type_settings or {}):
        if role not in valid_roles:
            raise ValueError(f"Unknown worker role: {role}. Use one of: {', '.join(WORKER_ROLES)}")


def normalize_worker_type_settings(value: object, *, strict: bool = True) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        if strict:
            raise ValueError("worker_type_settings must be an object")
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for raw_role, raw_settings in value.items():
        role = str(raw_role).strip()
        if not isinstance(raw_settings, dict):
            if strict:
                raise ValueError(f"worker_type_settings.{role} must be an object")
            continue
        reasoning_depth = str(raw_settings.get("reasoning_depth", "default") or "default").strip().lower()
        additional_prompt = str(raw_settings.get("additional_prompt", "") or "").strip()
        desired_raw = raw_settings.get("desired_instances", 1) if role != "orchestrator" else 1
        try:
            if isinstance(desired_raw, bool):
                raise ValueError
            desired_instances = int(desired_raw)
        except (TypeError, ValueError):
            if strict:
                raise ValueError(f"worker_type_settings.{role}.desired_instances must be an integer") from None
            desired_instances = 1
        if reasoning_depth not in WORKER_REASONING_DEPTHS:
            if strict:
                raise ValueError(
                    f"worker_type_settings.{role}.reasoning_depth must be one of: {', '.join(WORKER_REASONING_DEPTHS)}"
                )
            reasoning_depth = "default"
        if len(additional_prompt) > WORKER_ADDITIONAL_PROMPT_MAX_CHARS:
            if strict:
                raise ValueError(
                    f"worker_type_settings.{role}.additional_prompt must be at most {WORKER_ADDITIONAL_PROMPT_MAX_CHARS} characters"
                )
            additional_prompt = additional_prompt[:WORKER_ADDITIONAL_PROMPT_MAX_CHARS].rstrip()
        if not 1 <= desired_instances <= WORKER_DESIRED_INSTANCES_MAX:
            if strict:
                raise ValueError(
                    f"worker_type_settings.{role}.desired_instances must be between 1 and {WORKER_DESIRED_INSTANCES_MAX}"
                )
            desired_instances = 1
        if reasoning_depth != "default" or additional_prompt or desired_instances != 1:
            normalized[role] = {
                "reasoning_depth": reasoning_depth,
                "additional_prompt": additional_prompt,
                **({"desired_instances": desired_instances} if role != "orchestrator" else {}),
            }
    return normalized


def _ordered_worker_overrides(workers: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    for role in WORKER_ROLES:
        if workers.get(role):
            ordered[role] = workers[role]
    for role in sorted(set(workers) - set(WORKER_ROLES)):
        if workers.get(role):
            ordered[role] = workers[role]
    return ordered


def _ordered_role_model_overrides(role_models: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    for role in WORKER_ROLES:
        model = role_models.get(role, "").strip()
        if model:
            ordered[role] = model
    for role in sorted(set(role_models) - set(WORKER_ROLES)):
        model = role_models.get(role, "").strip()
        if model:
            ordered[role] = model
    return ordered


def _ordered_worker_type_settings(
    settings: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {role: settings[role] for role in WORKER_ROLES if role in settings}


def normalize_delivery_mode(value: str) -> str:
    mode = (value or "init_project").strip()
    if mode not in DELIVERY_MODES:
        raise ValueError(f"Unknown delivery mode: {mode}")
    return mode


def _serialize_worker_job_items(worker_jobs: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    from goodteam_os.skills.worker_requests import public_skill_job_fields

    items = []
    for job in worker_jobs:
        items.append(
            {
                "id": str(job.get("id", "")),
                "role": str(job.get("role", "")),
                "adapter": str(job.get("adapter", "")),
                "status": str(job.get("status", "")),
                "updated_at": str(job.get("updated_at", "")),
                "heartbeat_at": str(job.get("heartbeat_at", "")),
                "pid": str(job.get("pid", "")),
                "summary": str(job.get("summary", "")),
                **public_skill_job_fields(job),
            }
        )
    return items


def build_memory_event_citations(project_root: Path, event_id: str) -> list[dict[str, str]]:
    job_root = WorkerJobStore(project_root).root
    if not job_root.is_dir():
        return []
    citations: list[dict[str, str]] = []
    for path in sorted(job_root.iterdir(), key=lambda item: item.name):
        job = _read_json_file(path / "job.json")
        status = _read_json_file(path / "status.json")
        if job is None or status is None:
            continue
        memory_citations = status.get("memory_citations", [])
        if not isinstance(memory_citations, list) or event_id not in {str(item) for item in memory_citations}:
            continue
        merged = {**job, **status}
        citations.append(
            {
                "id": str(merged.get("id", path.name)),
                "role": str(merged.get("role", "")),
                "adapter": str(merged.get("adapter", "")),
                "status": str(merged.get("status", "")),
                "updated_at": str(merged.get("updated_at", "")),
                "summary": str(merged.get("summary", "")),
            }
        )
    return citations


def _memory_job_sort_key(job: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(job.get("updated_at", "")),
        str(job.get("created_at", "")),
        str(job.get("id", "")),
    )


def _read_json_file(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_relevant_event(event: dict[str, object]) -> dict[str, object]:
    score = event.get("score", 0.0)
    try:
        normalized_score = float(score)
    except (TypeError, ValueError):
        normalized_score = 0.0
    return {
        "id": str(event.get("id", "")),
        "score": normalized_score,
        "horizon": str(event.get("horizon", "")),
        "text": str(event.get("text", "")),
        "source": str(event.get("source", "")),
        "timestamp": str(event.get("timestamp", "")),
    }


def build_memory_overview(project_root: Path | None, project_key: str) -> dict[str, object]:
    if project_root is None:
        return {"project_key": project_key, "roles": []}

    memory_root = project_root / ".goodteam" / "memory" / "roles"
    if not memory_root.is_dir():
        return {"project_key": project_key, "roles": []}

    role_store = RoleMemoryStore(project_root)
    job_store = WorkerJobStore(project_root)
    latest_jobs: dict[str, dict[str, object]] = {}
    for job in job_store.list_jobs():
        role_key = str(job.get("role", "")).strip()
        if not role_key:
            continue
        current = latest_jobs.get(role_key)
        if current is None or _memory_job_sort_key(job) > _memory_job_sort_key(current):
            latest_jobs[role_key] = job

    roles: list[dict[str, object]] = []
    for role_dir in sorted(path for path in memory_root.iterdir() if path.is_dir()):
        role_key = role_dir.name
        packet = role_store.build_pickup_packet(role_key)
        role_payload: dict[str, object] = {
            "role": role_key,
            "summaries": {
                "long-term": str(dict(packet.get("summaries", {})).get("long-term", "")),
                "mid-term": str(dict(packet.get("summaries", {})).get("mid-term", "")),
            },
            "short_term": str(packet.get("short_term", "")),
            "event_count": int(packet.get("event_count", 0)),
            "context_status": packet.get("context_status", "unavailable"),
            "context_reasons": packet.get("context_reasons", []),
        }
        latest_job = latest_jobs.get(role_key)
        if latest_job is not None:
            job_id = str(latest_job.get("id", ""))
            pickup_packet = _read_json_file(job_store.root / job_id / "role_pickup_packet.json")
            if pickup_packet is not None:
                role_payload["latest_retrieval"] = {
                    "job_id": job_id,
                    "relevant_events": [
                        _normalize_relevant_event(event)
                        for event in pickup_packet.get("relevant_events", [])
                        if isinstance(event, dict)
                    ],
                }
        roles.append(role_payload)

    return {"project_key": project_key, "roles": roles}


def build_memory_events(
    project_root: Path | None,
    project_key: str,
    role: str,
    *,
    query: str = "",
    limit: int = 50,
    include_execution: bool = False,
) -> dict[str, object]:
    if project_root is None:
        return {"project_key": project_key, "role": role, "events": []}

    role_store = RoleMemoryStore(project_root)
    if not role_store.role_root(role).is_dir():
        return {"project_key": project_key, "role": role, "events": []}
    return {
        "project_key": project_key,
        "role": role,
        "events": role_store.list_events(role, query=query, limit=limit, include_execution=include_execution),
    }


def build_memory_event_detail(project_root: Path | None, event_id: str) -> dict[str, object] | None:
    if project_root is None:
        return None

    role_store = RoleMemoryStore(project_root)
    event = role_store.find_event(event_id)
    if event is None:
        return None
    supersede_info = role_store.event_supersede_info(event_id) or {
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
    }
    return {
        "id": event_id,
        "role": str(event.get("role_key", "")),
        "horizon": str(event.get("horizon", "")),
        "timestamp": str(event.get("timestamp", "")),
        "source": str(event.get("source", "")),
        "text": str(event.get("text", "")),
        "action": str(event.get("action", "")),
        "supersedes": str(supersede_info.get("supersedes", "")),
        "superseded_by": list(supersede_info.get("superseded_by", [])),
        "is_superseded": bool(supersede_info.get("is_superseded", False)),
        "cited_by_jobs": build_memory_event_citations(project_root, event_id),
    }


def render_frontend_assets_diagnostic(workspace: Path, error: FrontendAssetsNotFound) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GoodTeam React frontend unavailable</title>
  <style>
    body {{ margin: 0; padding: 32px; font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #f7f7f7; color: #17211d; }}
    main {{ max-width: 920px; margin: 0 auto; background: white; border: 2px solid #e5e5e5; border-radius: 16px; padding: 24px; }}
    h1 {{ font: 800 24px/1.2 system-ui, sans-serif; }}
    code, pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    pre {{ background: #f1f1f1; border-radius: 10px; padding: 16px; }}
  </style>
</head>
<body>
  <main>
    <h1>GoodTeam React frontend is unavailable</h1>
    <p>The server is using this data workspace:</p>
    <pre>{escape(str(workspace.expanduser().resolve()))}</pre>
    <p>Application assets are resolved independently from the data workspace. The legacy dashboard was not loaded.</p>
    <pre>{escape(str(error))}</pre>
  </main>
</body>
</html>"""


def serve(
    workspace: Path,
    host: str = "127.0.0.1",
    port: int = 8788,
    frontend_dist: Path | None = None,
    ready_file: Path | None = None,
    ready_token: str = "",
    heartbeat_callback: Callable[[], None] | None = None,
    *,
    codex_review_admission: "CodexReviewAdmission | None" = None,
    codex_correction_admission=None,
) -> None:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs
    from goodteam_os.execution.correction_native import correction_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)
    correction_kwargs = correction_worker_kwargs(codex_correction_admission)
    if frontend_dist is not None:
        dist_dir = resolve_frontend_dist(environ={"GOODTEAM_FRONTEND_DIST": str(frontend_dist)})
        frontend_error: FrontendAssetsNotFound | None = None
    else:
        try:
            dist_dir = resolve_frontend_dist()
            frontend_error = None
        except FrontendAssetsNotFound as exc:
            dist_dir = None
            frontend_error = exc

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if _send_chat_attachment(self, workspace, parsed.path):
                return
            query = parse_qs(parsed.query)
            project_key = query.get("project", [""])[0]
            if maybe_send_favicon_response(self, parsed.path):
                return
            if parsed.path in {"/", "/index.html"} and frontend_error is not None:
                self._send_html(503, render_frontend_assets_diagnostic(workspace, frontend_error))
                return
            execution_response = handle_execution_get(parsed.path, query)
            if execution_response is not None:
                status, payload = execution_response
                self._send_json(status, payload)
                return
            try:
                project_response = handle_project_api_get(workspace, parsed.path, **runtime_kwargs)
            except Exception as exc:
                self._send_api_error(exc)
                return
            if project_response is not None:
                status, payload = project_response
                self._send_json(status, payload)
                return
            if parsed.path == "/api/goal-history":
                try:
                    self._send_json(200, build_goal_history(
                        workspace, project_key, limit=_goal_page_limit(query, default=20),
                        before=query.get("before", [""])[0],
                    ))
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/state":
                try:
                    res_data = build_project_work_state(
                        workspace, project_key,
                        **({"goal_limit": _goal_page_limit(query)} if "goal_limit" in query else {}),
                    )
                    self._send_json(200, res_data)
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/devloop":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(200, build_dev_loop(project_root))
                return
            if parsed.path == "/api/server-actions/audit":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(
                    200,
                    build_server_action_audit_log(
                        project_root,
                        limit=_server_action_audit_limit(query.get("limit", ["25"])[0]),
                    ),
                )
                return
            if parsed.path == "/api/fs/list":
                try:
                    self._send_json(200, list_directory_for_picker(query.get("path", [""])[0]))
                except FsListBadRequestError as exc:
                    self._send_api_error(exc)
                except FsListNotFoundError as exc:
                    self._send_api_error(exc)
                except FsListPermissionError as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/memory":
                try:
                    if not project_key:
                        self._send_json(200, {"project_key": "", "roles": []})
                        return
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    self._send_json(200, build_memory_overview(project_root, project_key))
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/memory/events":
                try:
                    role = query.get("role", [""])[0].strip()
                    memory_query = query.get("q", [""])[0].strip()
                    if not project_key or not role:
                        raise ValueError("project and role are required.")
                    try:
                        limit = int(query.get("limit", ["50"])[0])
                    except ValueError:
                        limit = 50
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    self._send_json(
                        200,
                        build_memory_events(
                            project_root,
                            project_key,
                            role,
                            query=memory_query,
                            limit=limit,
                            include_execution=query.get("include_execution", ["false"])[0] == "true",
                        ),
                    )
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/memory/event":
                try:
                    event_id = query.get("id", [""])[0].strip()
                    if not project_key or not event_id:
                        raise ValueError("project and id are required.")
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    event_detail = build_memory_event_detail(project_root, event_id)
                    if event_detail is None:
                        raise ProjectApiError(
                            "memory_event_not_found",
                            f"Memory event not found: {event_id}",
                            404,
                            details={"event_id": event_id},
                            recovery=["return_to_project_knowledge"],
                        )
                    else:
                        self._send_json(200, event_detail)
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/memory/cross":
                try:
                    role = query.get("role", [""])[0].strip()
                    memory_query = query.get("q", [""])[0].strip()
                    scope = [
                        key.strip()
                        for key in query.get("scope", [""])[0].split(",")
                        if key.strip()
                    ]
                    if not role or not memory_query or not scope:
                        raise ValueError("role, q, and non-empty scope are required.")
                    try:
                        limit = int(query.get("k", ["5"])[0])
                    except ValueError:
                        limit = 5
                    report = retrieve_cross_project_memory(
                        workspace,
                        role,
                        memory_query,
                        scope=scope,
                        k=limit,
                    )
                    self._send_json(200, report)
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/agents":
                try:
                    provider = query.get("provider", [""])[0].strip()
                    agents = discover_installed_agents(provider) if provider else discover_installed_agents()
                    self._send_json(200, {"agents": agents})
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if not parsed.path.startswith("/api/") and dist_dir is not None:
                rel_path = parsed.path.lstrip("/")
                if not rel_path or rel_path == "index.html":
                    filepath = dist_dir / "index.html"
                else:
                    filepath = (dist_dir / rel_path).resolve()
                if not filepath.is_file() and is_product_spa_path(parsed.path):
                    filepath = dist_dir / "index.html"
                
                if filepath.is_file() and filepath.is_relative_to(dist_dir):
                    try:
                        import mimetypes
                        content_type, _ = mimetypes.guess_type(str(filepath))
                        if not content_type:
                            if filepath.suffix == ".css":
                                content_type = "text/css"
                            elif filepath.suffix == ".js":
                                content_type = "application/javascript"
                            else:
                                content_type = "application/octet-stream"
                        body = filepath.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        _send_cors_headers(self, host=host, port=port)
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    except Exception:
                        pass

            if parsed.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            if frontend_error is not None:
                self._send_html(503, render_frontend_assets_diagnostic(workspace, frontend_error))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            project_path = urlparse(self.path).path
            try:
                _check_provider_http_request(self, project_path)
            except ProjectApiError as exc:
                self._send_api_error(exc)
                return
            is_project_path = _is_project_api_post_path(project_path)
            is_skill_path = _is_skill_api_post_path(project_path)
            normalized_path = project_path
            if normalized_path.startswith("/api/"):
                normalized_path = normalized_path.removeprefix("/api")
            is_execution_path = bool(
                re.fullmatch(r"/executions/[^/]+/(pr|ci|review-workbench/decision|correction/start|correction/stop|correction/prepare-worker)", normalized_path)
            )

            if not is_project_path and not is_skill_path and not is_execution_path and normalized_path not in {
                "/report-bug", "/context-estimate", "/context-generate",
                "/project-config", "/project-delete", "/open-path",
                "/fs/choose",
                "/fs/mkdir", "/git-init",
            }:
                self.send_error(404)
                return
            if not _request_origin_is_same_origin(
                origin=str(self.headers.get("Origin", "")),
                host=str(self.headers.get("Host", "")),
            ):
                self._send_api_error(
                    ProjectApiError(
                        "same_origin_required",
                        "Cross-origin POST requests are not allowed.",
                        403,
                        recovery=["retry_from_goodteam_origin"],
                    )
                )
                return
            if re.fullmatch(r"/executions/[^/]+/(review-workbench/decision|correction/start|correction/stop|correction/prepare-worker)", normalized_path):
                if not _protected_execution_origin_is_trusted(
                    origin=str(self.headers.get("Origin", "")), request_host=str(self.headers.get("Host", "")),
                    server_host=host, server_port=self.server.server_port,
                    fetch_site=str(self.headers.get("Sec-Fetch-Site", "")),
                ):
                    self._send_api_error(ProjectApiError("same_origin_required",
                        "Protected execution records require the configured GoodTeam origin.", 403,
                        recovery=["retry_from_goodteam_origin"]))
                    return
                if str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower() != "application/json":
                    self._send_api_error(ProjectApiError("json_content_type_required",
                        "Protected execution requests require application/json.", 415,
                        recovery=["send_json_request"]))
                    return
            if is_skill_path:
                try:
                    client = ipaddress.ip_address(str(self.client_address[0]))
                except ValueError:
                    client = None
                if (
                    client is None
                    or not client.is_loopback
                    or not _request_host_is_loopback(str(self.headers.get("Host", "")))
                ):
                    self._send_api_error(
                        ProjectApiError(
                            "local_skill_import_requires_loopback",
                            "Local Skill import is available only from a loopback GoodTeam client.",
                            403,
                            recovery=["use_git_skill_source", "retry_from_local_goodteam"],
                        )
                    )
                    return
            if normalized_path == "/fs/choose" and not _native_picker_request_targets_local_server(
                request_host=str(self.headers.get("Host", "")),
                port=port,
            ):
                self._send_api_error(
                    ProjectApiError(
                        "same_origin_required",
                        "Native picker requires the local GoodTeam origin.",
                        403,
                        recovery=["retry_from_goodteam_origin"],
                    )
                )
                return
            if normalized_path == "/fs/choose" and not _native_picker_origin_is_trusted(
                origin=str(self.headers.get("Origin", "")),
                request_host=str(self.headers.get("Host", "")),
                port=port,
            ):
                self._send_api_error(
                    ProjectApiError(
                        "same_origin_required",
                        "Native picker requires the local GoodTeam origin.",
                        403,
                        recovery=["retry_from_goodteam_origin"],
                    )
                )
                return
            try:
                raw_body = read_request_body(self)
                if is_project_path or is_skill_path:
                    request = _parse_project_api_request(raw_body)
                    project_response = (
                        handle_project_api_post(workspace, project_path, request, **runtime_kwargs, **correction_kwargs)
                        if is_project_path
                        else handle_skill_api_post(workspace, project_path, request)
                    )
                    assert project_response is not None
                    status, result = project_response
                    self._send_json(status, result)
                    return
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if is_execution_path:
                    execution_response = handle_execution_post(normalized_path, request)
                    if execution_response is None:
                        self.send_error(404)
                        return
                    status, result = execution_response
                    self._send_json(status, result)
                    return
                if normalized_path == "/fs/choose":
                    mode = str(request.get("mode", "")).strip().lower()
                    if mode not in {"file", "directory"}:
                        raise ValueError("mode must be file or directory")
                    result = choose_native_path(
                        mode,
                        str(request.get("initial_path", "")),
                        host=host,
                    )
                    self._send_json(500 if result.get("status") == "error" else 200, result)
                    return
                if normalized_path == "/fs/mkdir":
                    try:
                        result = create_subdirectory(
                            parent_value=request.get("parent", ""),
                            name_value=request.get("name", ""),
                        )
                    except FsMkdirConflictError as exc:
                        self._send_api_error(exc)
                        return
                    except FsListNotFoundError as exc:
                        self._send_api_error(exc)
                        return
                    except FsListBadRequestError as exc:
                        self._send_api_error(exc)
                        return
                elif normalized_path == "/report-bug":
                    result = report_bug(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        description=str(request.get("description", "")),
                        title=str(request.get("title", "")),
                        severity=str(request.get("severity", "p2")),
                        run_id=str(request.get("run_id", "")),
                    )
                elif normalized_path == "/context-estimate":
                    result = context_estimate_for_request(workspace, request)
                elif normalized_path == "/context-generate":
                    result = context_generate_for_request(workspace, request)
                elif normalized_path == "/project-delete":
                    result = delete_project(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/open-path":
                    result = open_path_in_file_manager(
                        workspace=workspace,
                        relative_path=str(request.get("path", "")),
                    )
                elif normalized_path == "/git-init":
                    result = initialize_project_git_for_request(workspace, request)
                # `/project-config` carries two operations: the worker-backends
                # panel sends `project` + `workers` for an existing project; the
                # create-config form sends neither. Route both explicitly so an
                # unrelated path can never silently fall through to create-config.
                elif normalized_path == "/project-config" and (
                    request.get("action") == "update_worker_backends"
                    or (
                        ("project" in request or "project_key" in request)
                        and "workers" in request
                        and "project_name" not in request
                    )
                ):
                    result = update_project_worker_backend_settings(
                        workspace=workspace,
                        request=request,
                    )
                elif normalized_path == "/project-config":
                    result = create_local_project_config(
                        workspace=workspace,
                        project_name=str(request.get("project_name", "")),
                        project_key=str(request.get("project_key", "")),
                        project_root=str(request.get("project_root", "")),
                        docs_path=str(request.get("docs_path", "")),
                        backend_path=str(request.get("backend_path", "")),
                        frontend_path=str(request.get("frontend_path", "")),
                        task_sync=[str(value) for value in request.get("task_sync", [])],
                        plane_api_host_url=str(request.get("plane_api_host_url", "")),
                        plane_workspace_slug=str(request.get("plane_workspace_slug", "")),
                        plane_project_id=str(request.get("plane_project_id", "")),
                        plane_api_key_env=str(request.get("plane_api_key_env", "")),
                        plane_mcp_server=str(request.get("plane_mcp_server", "")),
                        worker_backend_mode=str(request.get("worker_backend_mode", "codex")),
                        workers=dict(request.get("workers", {})),
                        deployment_backend_mode=str(request.get("deployment_backend_mode", "")),
                        staging_ssh_target=str(request.get("staging_ssh_target", "")),
                        staging_remote_dir=str(request.get("staging_remote_dir", "")),
                        staging_git_ref=str(request.get("staging_git_ref", "")),
                        staging_public_url=str(request.get("staging_public_url", "")),
                        staging_deploy_command=str(request.get("staging_deploy_command", "")),
                        staging_healthcheck_command=str(request.get("staging_healthcheck_command", "")),
                        staging_smoke_command=str(request.get("staging_smoke_command", "")),
                        delivery_contract_required=bool(request.get("delivery_contract_required", False)),
                        delivery_contract_minimum_evidence=str(request.get("delivery_contract_minimum_evidence", "infrastructure")),
                        delivery_contract_fatal_patterns=str(request.get("delivery_contract_fatal_patterns", "")),
                        delivery_contract_acceptance=str(request.get("delivery_contract_acceptance", "")),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "init_project")),
                        delivery_standards=str(request.get("delivery_standards", "")),
                        role_model_overrides=dict(request.get("role_model_overrides", {})),
                        worker_type_settings=dict(request.get("worker_type_settings", {})),
                        init_git=_request_bool(request.get("init_git"), True),
                        initial_files=_initial_git_files(request.get("initial_files")),
                        create_remote=_request_bool(request.get("create_remote"), False),
                        remote_private=_request_bool(request.get("remote_private"), True),
                    )
                self._send_json(200, result)
            except PermissionError as exc:
                self._send_api_error(exc)
            except Exception as exc:
                self._send_api_error(exc)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            _send_cors_headers(self, host=host, port=port)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                _send_cors_headers(self, host=host, port=port)
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _send_api_error(self, error: Exception) -> None:
            status, payload = _project_api_error_response(error)
            self._send_json(status, payload)

        def _send_html(self, status: int, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _send_cors_headers(self, host=host, port=port)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def _send_text(self, payload: str) -> None:
            body = payload.encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    if heartbeat_callback is not None:
        parent_service_actions = getattr(server, "service_actions", None)

        def heartbeat_service_actions() -> None:
            heartbeat_callback()
            if parent_service_actions is not None:
                parent_service_actions()

        server.service_actions = heartbeat_service_actions
    goal_service_stop = None
    previous_signals = {}
    stopping = False

    def request_server_stop(_signum, _frame):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        raise SystemExit(0)

    try:
        # The CLI/supervisor web process runs serve on its main thread. A TERM
        # must unwind through finally rather than kill a claimed Goal tick.
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_signals[signum] = signal.getsignal(signum)
                signal.signal(signum, request_server_stop)
        # Start only after binding succeeds. Persisted explicit Goal authorization
        # is the sole source of continuation; GET handlers remain projections.
        from goodteam_os.goal_continuation import start_goal_service

        goal_service_stop = start_goal_service(workspace)
        if heartbeat_callback is not None:
            heartbeat_callback()
        if ready_file is not None:
            ready_file.write_text(ready_token, encoding="utf-8")
        server.serve_forever()
    finally:
        stopping = True
        if goal_service_stop is not None:
            goal_service_stop.set()
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
        try:
            close_server = getattr(server, "server_close", None)
            if close_server is not None:
                close_server()
            if goal_service_stop is not None:
                goal_service_stop.join()
        finally:
            for signum, previous in previous_signals.items():
                signal.signal(signum, previous)
