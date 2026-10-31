import ipaddress
import json
import hmac
import hashlib
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, replace
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
from goodteam_os.worker_capabilities import REASONING_DEPTHS
from goodteam_os.debate import default_debate_dispatch, run_gate_debate
from goodteam_os.dispatch import (
    dispatch_tracks,
    ensure_dispatch_project_root_is_safe,
    parse_dispatch_track_spec,
    parse_dispatch_verify_spec,
)
from goodteam_os.execution import ExecutionAPI
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.frontend_assets import FrontendAssetsNotFound, resolve_frontend_dist
from goodteam_os.graph import (
    accept_refinement,
    advance_approved_workflow,
    artifact_mirror_root,
    discard_refinement,
    extract_open_questions,
    generate_rfc,
    generate_prd,
    refine_artifact,
    render_downstream_artifact_markdown,
    resolve_project_runtime_root,
    resolve_role_adapter,
    run_architecture_until_gate,
    run_adoption_until_next_gate,
    run_fix_until_next_gate,
    run_until_next_gate,
    should_generate_artifact_with_worker,
    sync_project_stage_tasks,
)
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.intent_commit import IntentCommitError, IntentCommitStore
from goodteam_os.manager_execution import ManagerExecutionError, run_manager_interpretation
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.assistant_drafts import confirm_and_prepare, handle_draft_action
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_control_plane import (
    TeamControlPlaneError,
    TeamControlPlaneStore,
    execute_team_commands,
)
from goodteam_os.models import list_model_profiles, resolve_project_model_profile
from goodteam_os.multi_reviewer_projection import (
    build_multi_reviewer_projection,
    projection_supports_approval,
    reviewer_statuses_for_artifact,
)
from goodteam_os.native_picker import choose_native_path
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import DELIVERY_MODES, check_project_target, load_project_target, update_worker_backend_config
from goodteam_os.project_reviewer_profiles import normalize_reviewer_profile_config
from goodteam_os.project_import import (
    ProjectImportError,
    confirm_project_mode,
    import_project,
    read_bound_project_readiness,
)
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities  # noqa: F401
from goodteam_os.project_readiness_executor import (
    ProjectReadinessExecutor,
    ReadinessExecutionError,
)
from goodteam_os.reviewer_evidence_snapshot import build_reviewer_evidence_snapshot
from goodteam_os.repository_access import AnchoredRepository, canonical_relative_path
from goodteam_os.repository_snapshot import RepositorySnapshotError, inspect_repository_git
from goodteam_os._trusted_system_executable import (
    TrustedExecutableError,
    open_verified_executable,
    resolve_trusted_system_git,
)
from goodteam_os.artifact_revisions import invalidate_artifact
from goodteam_os.reviewer_dispatch import (
    artifact_version_for_path,
    retry_failed_reviewer_jobs_for_gate,
    run_queued_reviewer_jobs_for_gate,
    select_failed_reviewer_jobs_for_gate,
    select_queued_reviewer_jobs_for_gate,
)
from goodteam_os.resources import (
    ResourceCapability,
    ResourceKind,
    ResourceScope,
    register_markdown_artifact,
    resource_to_dict,
)
from goodteam_os.service import service_state_dir
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.review_provenance import (
    build_reviewer_provenance,
    latest_reviewer_status_for_approval,
    reviewer_phase_for_approval,
    reviewer_profile_for_approval,
)
from goodteam_os.review_policy import evaluate_review_evidence_policy
from goodteam_os.roles import (
    artifact_contract, approval_artifact_for_gate, approval_artifact_map,
    list_role_definitions, workflow_steps_for_source,
)
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import read_messages
from goodteam_os.staging import (
    deployment_backend_config,
    delivery_contract_report,
    run_staging_backend,
)
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore, create_initial_state, normalize_project_key
from goodteam_os.tasks import (
    LocalTaskStore,
    build_stage_task_snapshot,
    build_task_board,
    serialize_task_board,
)
from goodteam_os.team_workspace import build_team_workspace
from goodteam_os.assistant_workspace import build_project_assistant
from goodteam_os.assistant_queue import QUEUE_ACTIONS, handle_queue_action
from goodteam_os.telegram import send_approval_notification
from goodteam_os.workflow_table import (
    GATE_TRANSITIONS,
    WORKFLOW_NODES,
    advisors_for_gate,
    approved_next_for_gate,
    resolve_profile,
    workflow_execution_plan,
    workflow_execution_plan_v2,
)
from goodteam_os.workers import BUILTIN_WORKER_MODES, CLI_BACKENDS, SESSION_BACKENDS, WorkerJobStore, discover_installed_agents
from goodteam_os.cross_project_memory import retrieve_report as retrieve_cross_project_memory
from goodteam_os.workflow_mutations import (
    WorkflowBusyError, begin_transition, finish_transition, interrupt_transition,
    recover_transition, replay_approval, serialized_workflow, workflow_guard,
)

if TYPE_CHECKING:
    from goodteam_os.skills.codex_runtime import CodexReviewAdmission


PRD_REVIEW_ROLES = [
    "Product / Requirement",
    "Architect",
    "Review / QA",
]

_PREFERRED_WORKER_BACKEND_ORDER = ("codex", "claude", "gemini", "agy", "opencode", "pi", "fake", "noop", "human")
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
SERVER_ACTION_TIMEOUT_SECONDS = 600
FS_LIST_MAX_ENTRIES = 500
ARTIFACT_PREVIEW_MAX_CHARS = 8000
FS_LIST_IGNORED_NAMES = {".git", ".github", ".venv", "__pycache__", "node_modules", ".next", "dist", "build"}
DISPATCH_TRACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

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


def append_action(
    workspace: Path,
    *,
    run_id: str,
    actor: str,
    action: str,
    project_key: str = "",
    gate: str = "",
    decision: str = "",
    before_node: str = "",
    after_node: str = "",
    artifact: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
    debate_ref: str = "",
    note: str = "",
    decision_origin: str = "",
    actor_ref: str = "",
    review_mode: str = "",
    review_session_id: str = "",
    client_context: dict[str, object] | None = None,
    evidence_snapshot: dict[str, object] | None = None,
    reviewer_evidence_snapshot: dict[str, object] | None = None,
    review_items: list[dict[str, object]] | None = None,
    workflow_operation_id: str = "",
) -> None:
    action_log = _action_log_path(workspace, project_key)
    action_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "project_key": normalize_project_key(project_key) if project_key else "",
        "run_id": run_id,
        "actor": actor,
        "action": action,
        "gate": gate,
        "decision": decision,
        "before_node": before_node,
        "after_node": after_node,
        "artifact": artifact,
        "artifact_path": artifact_path,
    }
    if artifact_version:
        event["artifact_version"] = artifact_version
    if workflow_operation_id:
        event["workflow_operation_id"] = workflow_operation_id
    if debate_ref:
        event["debate_ref"] = debate_ref
    if note:
        event["note"] = note
    if decision_origin:
        event["decision_origin"] = decision_origin
    if actor_ref:
        event["actor_ref"] = actor_ref
    if review_mode:
        event["review_mode"] = review_mode
    if review_session_id:
        event["review_session_id"] = review_session_id
    if client_context:
        event["client_context"] = client_context
    if evidence_snapshot:
        event["evidence_snapshot"] = evidence_snapshot
    if reviewer_evidence_snapshot:
        event["reviewer_evidence_snapshot"] = reviewer_evidence_snapshot
    if review_items:
        event["review_items"] = review_items
    with action_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


_DEFAULT_TG_NOTIFY_GATES: frozenset[str] = frozenset(
    {"prd", "rfc", "security", "staging", "release", "fix_plan", "code_review", "qa"}
)

def _approval_artifact_for_gate(gate: str) -> str:
    """Resolve the artifact key for a gate, overlaying the fix-profile gates."""
    return approval_artifact_for_gate(gate)


def _tg_notify_gate_set() -> frozenset[str]:
    """Return the set of gates that trigger a Telegram notification card."""
    env = os.getenv("GOODTEAM_TG_NOTIFY_GATES", "")
    if env.strip():
        return frozenset(g.strip() for g in env.split(",") if g.strip())
    return _DEFAULT_TG_NOTIFY_GATES


def notify_current_approval_gate(state: AgileDevState | None, workspace: Path = Path(".")) -> dict[str, object]:
    gate = current_approval_gate(state)
    if state is None or not gate:
        return {"status": "skipped", "reason": "no approval gate"}
    if gate not in _tg_notify_gate_set():
        return {"status": "skipped", "reason": "gate_not_in_notify_set"}
    artifact_key = _approval_artifact_for_gate(gate)
    try:
        return send_approval_notification(
            project_key=state.project_key,
            run_id=state.run_id,
            gate=gate,
            artifact_path=state.artifacts.get(artifact_key, ""),
            summary=f"GoodTeam is waiting for {gate.upper()} approval on {state.project_key}.",
            workspace=workspace,
        )
    except Exception as exc:
        return {"status": "failed", "error": exc.__class__.__name__}


def current_approval_gate(state: AgileDevState | None) -> str:
    if state is None:
        return ""
    match = re.fullmatch(r"await_(.+)_approval", state.next_node)
    if not match:
        return ""
    gate = match.group(1)
    if str(state.approvals.get(gate, "")) != ApprovalDecision.PENDING:
        return ""
    return gate


def run_queued_reviewer_jobs_action(
    workspace: Path, request: Mapping[str, object], *, codex_review_admission: "CodexReviewAdmission | None" = None,
) -> tuple[int, dict[str, object]]:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)
    project_key = normalize_project_key(str(request.get("project_key") or request.get("project") or ""))
    if not project_key:
        return 400, {"error": "project_key is required"}
    try:
        with workflow_guard(workspace, project_key):
            return _run_queued_reviewer_jobs_for_current_run(workspace, request, project_key, runtime_kwargs)
    except WorkflowBusyError as exc:
        return 409, {"error": str(exc), "code": "workflow_busy"}


def _run_queued_reviewer_jobs_for_current_run(
    workspace: Path, request: Mapping[str, object], project_key: str, runtime_kwargs: dict[str, object],
) -> tuple[int, dict[str, object]]:
    # The caller owns the guard through execution. Recover without reacquiring it.
    state = interrupt_transition(workspace, project_key)
    if state is None:
        return 404, {"error": f"Unknown project or no active run: {project_key}"}

    requested_run_id = str(request.get("run_id", "") or "").strip()
    if requested_run_id and requested_run_id != state.run_id:
        return 409, {"error": "run_id does not match the active project run"}

    current_gate = current_approval_gate(state)
    requested_gate = str(request.get("approval") or request.get("gate") or current_gate).strip()
    if not current_gate or requested_gate != current_gate:
        return 409, {"error": f"Approval gate is not currently actionable: {requested_gate}"}

    artifact_key = _approval_artifact_for_gate(current_gate)
    requested_artifact_key = str(request.get("artifact_key", "") or "").strip()
    if requested_artifact_key and requested_artifact_key != artifact_key:
        return 409, {"error": "artifact_key does not match the current approval gate"}
    artifact_path = str(state.artifacts.get(artifact_key, "") or "")
    profile_id = str(request.get("profile_id", "") or "").strip()
    current_artifact_version = artifact_version_for_path(artifact_path)
    artifact_version = str(request.get("artifact_version", "") or "").strip() or current_artifact_version
    if artifact_version != current_artifact_version:
        return 409, {"error": "artifact_version does not match the current approval artifact"}
    requested_job_id = str(request.get("job_id", "") or "").strip()
    target = _reviewer_target_for_state(workspace=workspace, project_key=project_key)
    profile_order = _reviewer_profile_order_for_gate(target, current_gate)

    aggregate = {
        "selected_count": 0,
        "ran_count": 0,
        "skipped_count": 0,
        "results": [],
        "errors": [],
    }
    roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
    for root in roots:
        store = WorkerJobStore(root, **runtime_kwargs)
        jobs = store.list_jobs()
        from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission

        admission = runtime_kwargs.get("codex_review_admission")
        if isinstance(admission, TrustedCodexReviewAdmission) and not state.task_skill_context and not requested_job_id:
            # Default workflows have no Skill lock. At the explicit guarded Run
            # action, produce a separate task-bound review using the actual Team
            # Worker/profile and artifact, leaving other workflow roles intact.
            from goodteam_os.graph import worker_context
            from goodteam_os.reviewer_dispatch import dispatch_prepared_reviewer_jobs, reviewer_job_specs_for_gate, reviewer_phase_for_approval
            from goodteam_os.skills.models import SkillError
            from goodteam_os.workers import _codex_model_name

            if reviewer_phase_for_approval(current_gate) != "implementation":
                return 409, {"error": "trusted_codex_task_mismatch", "message": "Native self-use is limited to Code Review."}
            try:
                specs = reviewer_job_specs_for_gate(state=state, target=target, approval=current_gate,
                    artifact_key=artifact_key, artifact_path=artifact_path, context=worker_context(state))
                if len(specs) == 1 and specs[0].get("adapter") == "codex" and specs[0].get("model"):
                    # Skill publication preserves the request verbatim. Validate
                    # and publish the same canonical model, not a prefix alias.
                    specs[0]["model"] = _codex_model_name(str(specs[0]["model"]))
                if (len(specs) != 1 or specs[0].get("adapter") != "codex" or not specs[0].get("model")
                        or specs[0]["model"] != admission.policy["model"]):
                    return 409, {"error": "trusted_codex_reviewer_selection",
                                 "message": "Choose one Codex Reviewer profile with the privately authorized model."}
                review_context = admission.capture_workflow_review(store, workspace=workspace, state=state)
                jobs = dispatch_prepared_reviewer_jobs(store=store, state=state, specs=specs,
                                                       code_review_context=review_context)
            except (SkillError, OSError) as exc:
                return 409, {"error": exc.code if isinstance(exc, SkillError) else "review_start_unavailable",
                             "message": "The current task could not start its authorized native review."}
        result = run_queued_reviewer_jobs_for_gate(
            store=store,
            jobs=[job for job in jobs if not requested_job_id or job.get("id") == requested_job_id],
            run_id=state.run_id,
            approval=current_gate,
            artifact_key=artifact_key,
            artifact_path=artifact_path,
            artifact_version=artifact_version,
            profile_id=profile_id,
            profile_order=profile_order,
        )
        aggregate["selected_count"] = int(aggregate["selected_count"]) + int(result.get("selected_count", 0) or 0)
        aggregate["ran_count"] = int(aggregate["ran_count"]) + int(result.get("ran_count", 0) or 0)
        aggregate["skipped_count"] = int(aggregate["skipped_count"]) + int(result.get("skipped_count", 0) or 0)
        aggregate["results"] = [*list(aggregate["results"]), *list(result.get("results", []))]
        aggregate["errors"] = [*list(aggregate["errors"]), *list(result.get("errors", []))]

    return 200, {
        **aggregate,
        "project_key": project_key,
        "run_id": state.run_id,
        "approval": current_gate,
        "artifact_key": artifact_key,
        "artifact_path": artifact_path,
        "artifact_version": artifact_version,
        "advisory_only": True,
        "approval_unchanged": True,
        "operator_message": "Reviewer evidence remains advisory. Human operator decides.",
    }


def retry_failed_reviewer_jobs_action(
    workspace: Path, request: Mapping[str, object], *, codex_review_admission: "CodexReviewAdmission | None" = None,
) -> tuple[int, dict[str, object]]:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)
    project_key = normalize_project_key(str(request.get("project_key") or request.get("project") or ""))
    if not project_key:
        return 400, {"error": "project_key is required"}
    try:
        with workflow_guard(workspace, project_key):
            return _retry_failed_reviewer_jobs_for_current_run(workspace, request, project_key, runtime_kwargs)
    except WorkflowBusyError as exc:
        return 409, {"error": str(exc), "code": "workflow_busy"}


def _retry_failed_reviewer_jobs_for_current_run(
    workspace: Path, request: Mapping[str, object], project_key: str, runtime_kwargs: dict[str, object],
) -> tuple[int, dict[str, object]]:
    # The caller owns the guard through retry creation. No nested dashboard recovery.
    state = interrupt_transition(workspace, project_key)
    if state is None:
        return 404, {"error": f"Unknown project or no active run: {project_key}"}

    requested_run_id = str(request.get("run_id", "") or "").strip()
    if requested_run_id and requested_run_id != state.run_id:
        return 409, {"error": "run_id does not match the active project run"}

    current_gate = current_approval_gate(state)
    requested_gate = str(request.get("approval") or request.get("gate") or current_gate).strip()
    if not current_gate or requested_gate != current_gate:
        return 409, {"error": f"Approval gate is not currently actionable: {requested_gate}"}

    artifact_key = _approval_artifact_for_gate(current_gate)
    requested_artifact_key = str(request.get("artifact_key", "") or "").strip()
    if requested_artifact_key and requested_artifact_key != artifact_key:
        return 409, {"error": "artifact_key does not match the current approval gate"}
    artifact_path = str(state.artifacts.get(artifact_key, "") or "")
    current_artifact_version = artifact_version_for_path(artifact_path)
    requested_artifact_version = str(request.get("artifact_version", "") or "").strip()
    if requested_artifact_version and requested_artifact_version != current_artifact_version:
        return 409, {"error": "artifact_version does not match the current approval artifact"}

    profile_id = str(request.get("profile_id", "") or "").strip()
    target = _reviewer_target_for_state(workspace=workspace, project_key=project_key)
    profile_order = _reviewer_profile_order_for_gate(target, current_gate)
    aggregate = {
        "selected_count": 0,
        "retried_count": 0,
        "skipped_count": 0,
        "results": [],
        "errors": [],
    }
    roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
    for root in roots:
        store = WorkerJobStore(root, **runtime_kwargs)
        result = retry_failed_reviewer_jobs_for_gate(
            store=store,
            jobs=store.list_jobs(),
            run_id=state.run_id,
            approval=current_gate,
            artifact_key=artifact_key,
            artifact_path=artifact_path,
            artifact_version=current_artifact_version,
            profile_id=profile_id,
            profile_order=profile_order,
        )
        aggregate["selected_count"] = int(aggregate["selected_count"]) + int(result.get("selected_count", 0) or 0)
        aggregate["retried_count"] = int(aggregate["retried_count"]) + int(result.get("retried_count", 0) or 0)
        aggregate["skipped_count"] = int(aggregate["skipped_count"]) + int(result.get("skipped_count", 0) or 0)
        aggregate["results"] = [*list(aggregate["results"]), *list(result.get("results", []))]
        aggregate["errors"] = [*list(aggregate["errors"]), *list(result.get("errors", []))]

    return 200, {
        **aggregate,
        "project_key": project_key,
        "run_id": state.run_id,
        "approval": current_gate,
        "artifact_key": artifact_key,
        "artifact_path": artifact_path,
        "artifact_version": current_artifact_version,
        "advisory_only": True,
        "approval_unchanged": True,
        "operator_message": "Retry queued reviewer-dispatch jobs only. Approval remains human-operated.",
    }


def _reviewer_profile_order_for_gate(target: Mapping[str, object], gate: str) -> list[str]:
    phase = reviewer_phase_for_approval(gate)
    if phase is None:
        return []
    try:
        configured = normalize_reviewer_profile_config(target)
    except ValueError:
        return []
    return list(configured.get(phase, ()))


def _mask_advisory_text(text: object) -> str:
    try:
        from goodteam_os.telegram import mask_sensitive_data

        return mask_sensitive_data(str(text))
    except Exception:
        return str(text)


def _project_runtime_roots_for_state(*, state: AgileDevState | object | None, workspace: Path, project_key: str) -> list[Path]:
    project_roots: list[Path] = []
    source_ref = str(getattr(state, "source_ref", "") or "") if state is not None else ""
    if source_ref and source_ref != "manual":
        source_path = Path(source_ref)
        if source_path.is_file():
            try:
                target = load_project_target(source_path)
                repos = target.get("repos", {})
                if isinstance(repos, dict) and repos:
                    project_roots.append(resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()}))
                else:
                    project_roots.append(source_path.parent)
            except Exception:
                project_roots.append(source_path.parent)
    try:
        config_path = _project_config_for_key(workspace, project_key)
        target = load_project_target(config_path)
        repos = target.get("repos", {})
        if isinstance(repos, dict) and repos:
            project_roots.append(resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()}))
        else:
            project_roots.append(config_path.parent)
    except Exception:
        project_roots.append(workspace)

    roots: list[Path] = []
    seen_roots: set[Path] = set()
    for root in project_roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        roots.append(resolved)
    return roots


class ProjectConfigNotFoundError(ValueError):
    """No local project config exists; distinct from a broken configured project."""


def _reviewer_target_for_state(*, workspace: Path, project_key: str) -> dict[str, object]:
    try:
        return dict(load_project_target(_project_config_for_key(workspace, project_key)))
    except ProjectConfigNotFoundError:
        return {}
    except Exception:
        return {"_reviewer_configuration_unavailable": True}


def _review_evidence_policy_config(target: Mapping[str, object]) -> object:
    return target.get("review_evidence_policy", {})


def _reviewer_statuses_for_state(
    *,
    state: AgileDevState | object | None,
    workspace: Path,
    project_key: str,
) -> list[dict[str, object]]:
    run_id = str(getattr(state, "run_id", "") or "") if state is not None else ""
    if not run_id:
        return []
    statuses: list[dict[str, object]] = []
    try:
        roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        for root in roots:
            for job in WorkerJobStore(root).list_jobs():
                if str(job.get("run_id", "")) == run_id:
                    statuses.append(dict(job))
    except Exception:
        return []
    return statuses


def _safe_reviewer_evidence_snapshot_for_decision(
    *,
    state: AgileDevState,
    workspace: Path,
    project_key: str,
    approval: str,
) -> dict[str, object]:
    reviewer_statuses = _reviewer_statuses_for_state(state=state, workspace=workspace, project_key=project_key)
    reviewer_target = _reviewer_target_for_state(workspace=workspace, project_key=project_key)
    try:
        return build_reviewer_evidence_snapshot(
            state=state,
            approval=approval,
            workspace=workspace,
            worker_jobs=reviewer_statuses,
            reviewer_statuses=reviewer_statuses,
            reviewer_target=reviewer_target,
        )
    except Exception as exc:
        artifact = _approval_artifact_for_gate(approval)
        artifact_path = state.artifacts.get(artifact, "")
        return {
            "schema": "reviewer_evidence_snapshot.v1",
            "run_id": state.run_id,
            "approval": approval,
            "artifact": {
                "key": artifact,
                "path": artifact_path,
                "version": artifact_version_for_path(artifact_path),
            },
            "reviewer_dispatch": {
                "mode": "unknown",
                "parallel_requested": False,
                "parallel_enabled": False,
                "advisory_only": True,
            },
            "reviewers": [],
            "jobs": {
                "queued_total": 0,
                "failed_total": 0,
                "queued_by_profile": {},
                "failed_by_profile": {},
            },
            "aggregate": {
                "reviewer_count": 0,
                "completed": 0,
                "failed": 0,
                "queued": 0,
                "missing": 0,
                "policy_blockers": 0,
                "policy_warnings": 0,
            },
            "operator_message": "Reviewer evidence is advisory. Human operator remains authoritative.",
            "errors": [str(exc)],
        }


def build_provenance_refs(*, state: AgileDevState | object | None, workspace: Path, project_key: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    try:
        roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        seen_manifests: set[Path] = set()
        for root in roots:
            provenance_root = root / ".goodteam" / "provenance"
            if not provenance_root.is_dir():
                continue
            for manifest_path in sorted(provenance_root.glob("pr-*/manifest.json")):
                try:
                    resolved_manifest = manifest_path.resolve()
                except Exception:
                    resolved_manifest = manifest_path
                if resolved_manifest in seen_manifests:
                    continue
                seen_manifests.add(resolved_manifest)
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(manifest, dict):
                    continue
                archive_paths = manifest.get("archive_paths", {})
                archive_paths = archive_paths if isinstance(archive_paths, dict) else {}
                pr_ref = manifest.get("pr", manifest.get("pr_number", manifest.get("number", "")))
                if not pr_ref:
                    pr_ref = manifest_path.parent.name.removeprefix("pr-")
                refs.append(
                    {
                        "pr": _mask_advisory_text(pr_ref),
                        "branch": _mask_advisory_text(manifest.get("branch", "")),
                        "ci_status": _mask_advisory_text(manifest.get("ci_status", "")),
                        "merge_sha": _mask_advisory_text(
                            manifest.get("merge_sha", manifest.get("squash_merge_sha", manifest.get("squash_sha", "")))
                        ),
                        "job_ref": _mask_advisory_text(manifest.get("job_ref", archive_paths.get("job", ""))),
                        "contract_ref": _mask_advisory_text(
                            manifest.get("contract_ref", archive_paths.get("contract", archive_paths.get("task_contract", "")))
                        ),
                        "pre_review_ref": _mask_advisory_text(
                            manifest.get(
                                "pre_review_ref",
                                manifest.get("pre-review_ref", archive_paths.get("pre_review", archive_paths.get("pre-review", ""))),
                            )
                        ),
                    }
                )
    except Exception:
        return []
    return refs


def build_approval_recommendation(
    *,
    state: AgileDevState | object | None,
    review: dict[str, object] | object,
    evidence_snapshot: dict[str, object] | object,
    workspace: Path,
) -> dict[str, object]:
    del workspace
    recommendation: dict[str, object] = {
        "verdict": "caution",
        "confidence": "low",
        "blocking_count": 0,
        "reasons": [],
    }
    try:
        review_dict = review if isinstance(review, dict) else {}
        evidence = evidence_snapshot if isinstance(evidence_snapshot, dict) else {}
        reasons: list[dict[str, str]] = []
        blockers = getattr(state, "blockers", []) if state is not None else []
        blockers = blockers if isinstance(blockers, list) else []
        for blocker in blockers:
            reasons.append({"kind": "blocker", "text": _mask_advisory_text(blocker)})

        evidence_blocker_count = 0
        try:
            evidence_blocker_count = int(evidence.get("blocker_count", 0) or 0)
        except Exception:
            evidence_blocker_count = 0
        approval = str(review_dict.get("approval", "") or "")
        status_values = [
            str(evidence.get("ci_status", "unknown") or "unknown"),
            str(evidence.get("smoke_status", "unknown") or "unknown"),
            str(evidence.get("qa_status", "unknown") or "unknown"),
        ]
        unknown_count = sum(1 for status in status_values if status == "unknown")
        release_stage = approval in {"staging", "release"}
        if evidence_blocker_count > 0 and not blockers:
            reasons.append({"kind": "missing_evidence", "text": "Evidence snapshot reports blockers without blocker details."})
        if release_stage and unknown_count:
            reasons.append({"kind": "missing_evidence", "text": "Release-stage gate has unknown CI, smoke, or QA evidence."})

        # Legacy callers may omit both fields. Missing metadata is unknown,
        # never a successful review inferred from non-empty artifact content.
        provenance = review_dict.get("reviewer_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        derived_policy = evaluate_review_evidence_policy(provenance, gate=approval)
        policy = review_dict.get("review_evidence_policy")
        inconsistent_policy = "review_evidence_policy" in review_dict and (
            not isinstance(policy, dict) or policy.get("state") != "ok"
            or policy.get("gate", approval) != approval
            or policy.get("evidence", derived_policy["evidence"]) != derived_policy["evidence"]
        )
        if derived_policy["state"] != "ok" or inconsistent_policy:
            reasons.append({"kind": "missing_evidence", "text": "Reviewer evidence is missing, simulated or unsuccessful. Human review is required."})
        if "multi_reviewer_projection" in review_dict and not projection_supports_approval(
            review_dict["multi_reviewer_projection"], approval=approval,
            run_id=str(getattr(state, "run_id", "") or ""),
            artifact_key=str(review_dict.get("artifact", "") or ""),
            artifact_path=str(review_dict.get("artifact_path", "") or ""),
            artifact_version=str(review_dict.get("artifact_version", "") or ""),
        ):
            projection = review_dict["multi_reviewer_projection"]
            configuration_error = projection.get("configuration_error") if isinstance(projection, Mapping) else None
            text = (configuration_error if isinstance(configuration_error, str) and configuration_error else
                    "Current reviewer evidence is incomplete or inconsistent across the review team. Human review is required.")
            reasons.append({"kind": "missing_evidence", "text": _mask_advisory_text(text)})

        messages = {}
        raw_messages = {}
        committee = review_dict.get("committee", {})
        committee = committee if isinstance(committee, dict) else {}
        message_list = committee.get("messages", [])
        message_list = message_list if isinstance(message_list, list) else []
        for index, raw_message in enumerate(message_list):
            if not isinstance(raw_message, dict):
                continue
            message_id = str(raw_message.get("id", "") or "")
            if not message_id:
                continue
            messages[message_id] = index
            raw_messages[message_id] = raw_message
        for message_id, raw_message in raw_messages.items():
            if str(raw_message.get("kind", "")) not in {"ask", "reject"}:
                continue
            message_index = messages.get(message_id, -1)
            has_owner_reply = False
            for later in message_list[message_index + 1 :]:
                if not isinstance(later, dict):
                    continue
                if str(later.get("in_reply_to", "")) == message_id and str(later.get("kind", "")) in {"assert", "propose"}:
                    has_owner_reply = True
                    break
            if not has_owner_reply:
                body = str(raw_message.get("body", "") or "Committee objection is still unresolved.")
                reasons.append({"kind": "open_objection", "text": _mask_advisory_text(body)})

        content = str(review_dict.get("content", "") or "")
        blocking_count = len(blockers)
        if blockers:
            verdict = "request_changes"
        elif any(reason["kind"] in {"missing_evidence", "open_objection"} for reason in reasons):
            verdict = "caution"
        elif content.strip():
            verdict = "approve"
            reasons.append({"kind": "clean", "text": "Artifact content is present and no blocking signals were found."})
        else:
            verdict = "caution"
            reasons.append({"kind": "missing_evidence", "text": "No artifact content is available for review."})

        if blockers:
            confidence = "low"
        elif len(reasons) > 1 and any(reason["kind"] != "clean" for reason in reasons):
            confidence = "low"
        elif any(reason["kind"] in {"missing_evidence", "open_objection"} for reason in reasons):
            confidence = "medium"
        elif unknown_count:
            confidence = "medium"
        else:
            confidence = "high"

        recommendation = {
            "verdict": verdict,
            "confidence": confidence,
            "blocking_count": blocking_count,
            "reasons": reasons,
        }
    except Exception:
        return recommendation
    return recommendation


def _latest_smoke_report(workspace: Path) -> tuple[Path, dict[str, object], dict[str, object]] | None:
    root = workspace / "state" / "smoke-runs"
    if not root.is_dir():
        return None
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            wrapper = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(wrapper, dict):
            continue
        nested = wrapper.get("report")
        report = nested if isinstance(nested, dict) and nested else wrapper
        if isinstance(report.get("runs", []), list):
            return path, report, wrapper
    return None


def _smoke_command_refs(commands: object) -> list[dict[str, object]]:
    if not isinstance(commands, list):
        return []
    refs: list[dict[str, object]] = []
    for raw in commands:
        if not isinstance(raw, dict):
            continue
        refs.append(
            {
                "name": str(raw.get("name", "") or ""),
                "command": str(raw.get("command", "") or ""),
                "cwd": str(raw.get("cwd", "") or ""),
                "exit_code": raw.get("exit_code", ""),
            }
        )
    return refs


def _build_smoke_refs(workspace: Path, project_key: str) -> list[dict[str, object]]:
    latest = _latest_smoke_report(workspace)
    if latest is None:
        return []
    report_path, report, wrapper = latest
    report_status = str(report.get("status", wrapper.get("status", "")) or "")
    refs: list[dict[str, object]] = []
    for raw in report.get("runs", []):
        if not isinstance(raw, dict):
            continue
        run_project = str(raw.get("project_key", "") or "")
        if project_key and run_project and run_project != project_key:
            continue
        evidence = raw.get("evidence", {})
        evidence = evidence if isinstance(evidence, dict) else {}
        smoke_report_path = str(evidence.get("smoke_report", "") or report_path)
        refs.append(
            {
                "report_path": smoke_report_path,
                "report_status": report_status,
                "project_key": run_project,
                "suite": str(raw.get("suite", "") or ""),
                "cycle": raw.get("cycle", ""),
                "status": str(raw.get("status", "") or ""),
                "classification": str(raw.get("classification", "") or ""),
                "run_id": str(raw.get("run_id", "") or ""),
                "worker_job_ids": [str(item) for item in raw.get("worker_job_ids", []) if str(item)],
                "worker_result_paths": [str(item) for item in evidence.get("worker_result_paths", []) if str(item)],
                "commands": _smoke_command_refs(raw.get("commands", [])),
            }
        )
    return refs


def _derive_smoke_status_from_refs(smoke_refs: list[dict[str, object]]) -> str:
    statuses: list[str] = []
    for ref in smoke_refs:
        statuses.append(str(ref.get("status", "") or "").lower())
        statuses.append(str(ref.get("report_status", "") or "").lower())
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "passed" for status in statuses):
        return "passed"
    return "unknown"


def _derive_qa_status(state: AgileDevState | object | None) -> str:
    """Map the run-scoped QA gate decision to a verdict; defaults to "unknown"."""
    try:
        approvals = getattr(state, "approvals", {}) if state is not None else {}
        if not isinstance(approvals, dict):
            return "unknown"
        decision = str(approvals.get("qa", "") or "").lower()
        if decision == "approved":
            return "passed"
        if decision == "rejected":
            return "failed"
        return "unknown"
    except Exception:
        return "unknown"


def _map_manifest_ci_status(raw_status: object) -> str:
    """Map a provenance manifest's ci_status string to passed/failed/unknown."""
    status = str(raw_status or "").strip().lower()
    if status in {"success", "passed", "pass", "succeeded"}:
        return "passed"
    if status in {"failure", "failed", "fail", "error"}:
        return "failed"
    return "unknown"


def _derive_ci_status(
    *,
    state: AgileDevState | object | None,
    workspace: Path,
    project_key: str,
) -> str:
    """Read the run's provenance manifest ci_status from disk; defaults to "unknown"."""
    try:
        run_id = str(getattr(state, "run_id", "") or "") if state is not None else ""
        roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        seen_manifests: set[Path] = set()
        candidates: list[tuple[float, str]] = []
        run_match_status: str | None = None
        for root in roots:
            provenance_root = root / ".goodteam" / "provenance"
            if not provenance_root.is_dir():
                continue
            for manifest_path in provenance_root.glob("pr-*/manifest.json"):
                try:
                    resolved_manifest = manifest_path.resolve()
                except Exception:
                    resolved_manifest = manifest_path
                if resolved_manifest in seen_manifests:
                    continue
                seen_manifests.add(resolved_manifest)
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(manifest, dict):
                    continue
                ci_status = _map_manifest_ci_status(manifest.get("ci_status", ""))
                manifest_run_id = str(manifest.get("run_id", "") or "")
                if run_id and manifest_run_id and manifest_run_id == run_id:
                    if ci_status != "unknown":
                        return ci_status
                    run_match_status = run_match_status or ci_status
                try:
                    mtime = manifest_path.stat().st_mtime
                except Exception:
                    mtime = 0.0
                candidates.append((mtime, ci_status))
        if run_match_status is not None:
            return run_match_status
        if not candidates:
            return "unknown"
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    except Exception:
        return "unknown"


def build_approval_evidence_snapshot(
    *,
    state: AgileDevState | object | None,
    workspace: Path,
    project_key: str,
    debate_ref: str = "",
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "artifact_refs": [],
        "worker_job_refs": [],
        "ci_status": "unknown",
        "smoke_status": "unknown",
        "qa_status": "unknown",
        "screenshot_refs": [],
        "debate_refs": [debate_ref] if debate_ref else [],
        "blocker_count": 0,
        "provenance_refs": [],
        "smoke_refs": [],
    }
    if getattr(state, "delivery_items", None):
        from goodteam_os.delivery_outcomes import _artifact_item

        snapshot["delivery_refs"] = [_artifact_item(workspace, state, key)
                                     for key in getattr(state, "delivery_items", {})]
    try:
        artifacts = getattr(state, "artifacts", {}) if state is not None else {}
        approvals = getattr(state, "approvals", {}) if state is not None else {}
        artifact_refs: set[str] = set()
        if isinstance(artifacts, dict):
            artifact_refs.update(str(key) for key, value in artifacts.items() if str(key).strip() and str(value).strip())
        if isinstance(approvals, dict):
            approval_map = approval_artifact_map()
            artifact_refs.update(
                str(approval_map.get(str(gate), str(gate)))
                for gate in approvals
                if isinstance(artifacts, dict) and str(approval_map.get(str(gate), str(gate))) in artifacts
            )
        snapshot["artifact_refs"] = sorted(artifact_refs)
    except Exception:
        snapshot["artifact_refs"] = []

    try:
        blockers = getattr(state, "blockers", []) if state is not None else []
        snapshot["blocker_count"] = len(blockers) if isinstance(blockers, list) else 0
    except Exception:
        snapshot["blocker_count"] = 0

    try:
        snapshot["provenance_refs"] = build_provenance_refs(state=state, workspace=workspace, project_key=project_key)
    except Exception:
        snapshot["provenance_refs"] = []

    try:
        snapshot["smoke_refs"] = _build_smoke_refs(workspace, project_key)
        snapshot["smoke_status"] = _derive_smoke_status_from_refs(list(snapshot["smoke_refs"]))
    except Exception:
        snapshot["smoke_refs"] = []
        snapshot["smoke_status"] = "unknown"

    try:
        snapshot["qa_status"] = _derive_qa_status(state)
    except Exception:
        snapshot["qa_status"] = "unknown"

    try:
        snapshot["ci_status"] = _derive_ci_status(state=state, workspace=workspace, project_key=project_key)
    except Exception:
        snapshot["ci_status"] = "unknown"

    try:
        run_id = str(getattr(state, "run_id", "") or "")
        if not run_id:
            return snapshot
        project_roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        worker_refs: set[str] = set()
        for root in project_roots:
            for job in WorkerJobStore(root).list_jobs():
                if str(job.get("run_id", "")) != run_id:
                    continue
                job_id = str(job.get("id", "") or job.get("job_id", ""))
                if job_id:
                    worker_refs.add(job_id)
        snapshot["worker_job_refs"] = sorted(worker_refs)
    except Exception:
        snapshot["worker_job_refs"] = []

    return snapshot


def _action_log_path(workspace: Path, project_key: str = "") -> Path:
    if project_key:
        return StateStore(workspace).project_state_dir(project_key) / "action-log.jsonl"
    return workspace / "state" / "action-log.jsonl"


def build_action_title(action: dict[str, str]) -> str:
    actor = action.get("actor", "").title() or "System"
    action_type = action.get("action", "")
    artifact = action.get("artifact", "")
    gate = action.get("gate", "")
    decision = action.get("decision", "")
    if action_type == "workflow_started":
        return f"{actor} started workflow"
    if action_type.endswith("_generated") and artifact:
        return f"{actor} generated {artifact.upper()}"
    if action_type.endswith("_regenerated") and gate:
        return f"{actor} regenerated {gate.upper()}"
    if action_type == "artifact_saved" and artifact:
        return f"{actor} saved {artifact.upper()} artifact"
    if action_type == "approval_decided" and gate and decision:
        verb = {
            "approved": "approved",
            "changes_requested": "requested changes on",
            "rejected": "rejected",
        }.get(decision, decision.replace("_", " "))
        return f"{actor} {verb} {gate.upper()}"
    return f"{actor} {action_type.replace('_', ' ')}".strip()


def build_action_items(workspace: Path, project_key: str = "") -> list[dict[str, object]]:
    action_log = _action_log_path(workspace, project_key)
    if not action_log.is_file():
        return []
    items = []
    for line in action_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = {
            key: value if key in {"client_context", "evidence_snapshot", "review_items"} else str(value)
            for key, value in raw.items()
        }
        before_node = action.get("before_node", "")
        after_node = action.get("after_node", "")
        action["title"] = build_action_title(action)
        action["node_transition"] = f"{before_node} -> {after_node}" if before_node or after_node else ""
        items.append(action)
    return list(reversed(items))


def build_smoke_run_items(workspace: Path) -> list[dict[str, str]]:
    root = workspace / "state" / "smoke-runs"
    if not root.is_dir():
        return []
    items: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        nested_report = report.get("report")
        display_report = dict(nested_report) if isinstance(nested_report, dict) and nested_report else report
        for run in display_report.get("runs", []):
            raw = dict(run)
            raw["report_path"] = str(path)
            raw["report_status"] = str(display_report.get("status", report.get("status", "")))
            items.append({key: str(value) for key, value in raw.items()})
    return items[:12]


def _run_readonly_command(command: list[str], workspace: Path, warnings: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=workspace,
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


def _dispatch_worktree_path(workspace: Path, name: str) -> Path:
    return workspace.resolve().parent / f"goodteam-dev-{name}"


def _ensure_dispatch_worktree(workspace: Path, *, name: str, branch: str) -> Path:
    worktree = _dispatch_worktree_path(workspace, name)
    workspace_root = workspace.resolve()
    resolved_worktree = worktree.resolve() if worktree.exists() else worktree.absolute().resolve(strict=False)
    if resolved_worktree == workspace_root or workspace_root in resolved_worktree.parents:
        raise ValueError("dispatch worktree must be outside the live repo")
    if not worktree.exists():
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", branch, "main"],
            cwd=workspace,
            check=True,
        )
    _link_dispatch_worktree_venv(workspace, worktree)
    return worktree


def _link_dispatch_worktree_venv(workspace: Path, worktree: Path) -> None:
    workspace_root = workspace.resolve()
    worktree_root = worktree.resolve() if worktree.exists() else worktree.absolute().resolve(strict=False)
    source_bin = workspace_root / ".venv" / "bin"
    if not source_bin.exists():
        return
    venv_dir = worktree_root / ".venv"
    bin_link = venv_dir / "bin"
    if venv_dir.exists() or venv_dir.is_symlink():
        return
    venv_dir.mkdir(parents=True, exist_ok=True)
    bin_link.symlink_to(source_bin)


def _prepare_dispatch_track_inputs(
    worktree: Path,
    *,
    run_id: str,
    task: str,
    constraints: str,
) -> tuple[Path, Path | None]:
    input_dir = worktree / ".goodteam" / "dispatch-inputs" / run_id
    input_dir.mkdir(parents=True, exist_ok=True)
    task_file = input_dir / "task.md"
    task_file.write_text(task, encoding="utf-8")
    constraints_file = None
    if constraints.strip():
        constraints_file = input_dir / "constraints.md"
        constraints_file.write_text(constraints, encoding="utf-8")
    return task_file, constraints_file


def _dispatch_track_status_path(workspace: Path, run_id: str) -> Path:
    return workspace / "state" / "dispatch-tracks" / f"dispatch-track-{run_id}.json"


def _smoke_run_status_path(workspace: Path, run_id: str) -> Path:
    return workspace / "state" / "smoke-runs" / f"smoke-run-{run_id}.json"


def _write_dispatch_track_status(
    workspace: Path,
    *,
    run_id: str,
    name: str,
    worktree: Path,
    status: str,
    error: str = "",
    report: dict[str, object] | None = None,
) -> None:
    status_path = _dispatch_track_status_path(workspace, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "name": name,
        "worktree": str(worktree),
        "status": status,
        "error": error,
        **({"report": report} if report is not None else {}),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_smoke_run_status(
    workspace: Path,
    *,
    run_id: str,
    suites: list[str],
    cycles: int,
    dry_run: bool,
    worker_mode: str,
    status: str,
    report: dict[str, object] | None = None,
    error: str = "",
) -> None:
    status_path = _smoke_run_status_path(workspace, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "suites": suites,
        "cycles": cycles,
        "dry_run": dry_run,
        "worker_mode": worker_mode,
        "status": status,
        "report": report or {},
        "error": error,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _start_dispatch_track_background(
    *,
    workspace: Path,
    run_id: str,
    name: str,
    worktree: Path,
    tracks: list[dict[str, object]],
    verify_commands: dict[str, str],
    constraints_file: Path | None,
    response_written: threading.Event | None = None,
) -> None:
    projects_dir = workspace / "projects"

    def run_dispatch_track() -> None:
        if response_written is not None:
            response_written.wait()
        _write_dispatch_track_status(
            workspace,
            run_id=run_id,
            name=name,
            worktree=worktree,
            status="started",
        )
        try:
            report = dispatch_tracks(
                project_root=worktree,
                tracks=tracks,
                verify_commands=verify_commands,
                constraints_file=constraints_file,
                projects_dir=projects_dir,
                logical_task_id=run_id,
            )
        except Exception as exc:
            _write_dispatch_track_status(
                workspace,
                run_id=run_id,
                name=name,
                worktree=worktree,
                status="failed",
                error=str(exc),
            )
            return
        _write_dispatch_track_status(
            workspace,
            run_id=run_id,
            name=name,
            worktree=worktree,
            status="completed" if report.get("all_ok") else "failed",
            report=report,
            error="" if report.get("all_ok") else "Dispatch requires attention; inspect the retained report and candidate.",
        )

    thread = threading.Thread(
        target=run_dispatch_track,
        daemon=True,
    )
    thread.start()


def run_smoke_suite(**kwargs) -> dict[str, object]:
    from goodteam_os.smoke import run_smoke_suite as _run_smoke_suite

    return _run_smoke_suite(**kwargs)


def _start_smoke_run_background(
    *,
    workspace: Path,
    run_id: str,
    suites: list[str],
    cycles: int,
    dry_run: bool,
    worker_mode: str,
) -> None:
    def run_smoke() -> None:
        _write_smoke_run_status(
            workspace,
            run_id=run_id,
            suites=suites,
            cycles=cycles,
            dry_run=dry_run,
            worker_mode=worker_mode,
            status="started",
        )
        try:
            report = run_smoke_suite(
                workspace=workspace,
                suites=suites,
                cycles=cycles,
                autonomy="full",
                dry_run=dry_run,
                worker_mode=worker_mode,
            )
        except Exception as exc:
            _write_smoke_run_status(
                workspace,
                run_id=run_id,
                suites=suites,
                cycles=cycles,
                dry_run=dry_run,
                worker_mode=worker_mode,
                status="failed",
                error=str(exc),
            )
            return
        status = "failed" if str(report.get("status", "")) == "failed" else "completed"
        _write_smoke_run_status(
            workspace,
            run_id=run_id,
            suites=suites,
            cycles=cycles,
            dry_run=dry_run,
            worker_mode=worker_mode,
            status=status,
            report=report,
        )

    threading.Thread(target=run_smoke, daemon=True).start()


def start_smoke_run(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    raw_suite = request.get("suite", request.get("suites", "self"))
    if isinstance(raw_suite, list):
        suites = [str(value).strip() for value in raw_suite if str(value).strip()]
    else:
        suites = [part.strip() for part in str(raw_suite or "self").split(",") if part.strip()]
    suites = suites or ["self"]
    invalid_suites = [suite for suite in suites if suite not in {"contentops", "self"}]
    if invalid_suites:
        raise ValueError(f"Unknown smoke suite: {', '.join(invalid_suites)}")
    cycles = int(request.get("cycles", 1) or 1)
    if cycles < 1 or cycles > 5:
        raise ValueError("cycles must be between 1 and 5")
    worker_mode = str(request.get("worker_mode", "fake") or "fake").strip()
    if worker_mode not in {"fake", "noop", "codex"}:
        raise ValueError("worker_mode must be one of: fake, noop, codex")
    # The web control plane starts in dry-run mode by default. Real smoke can be
    # enabled explicitly once the operator wants the workflow-mutating path.
    dry_run = bool(request.get("dry_run", True))
    run_id = f"smoke-{uuid4()}"
    _start_smoke_run_background(
        workspace=workspace,
        run_id=run_id,
        suites=suites,
        cycles=cycles,
        dry_run=dry_run,
        worker_mode=worker_mode,
    )
    return {
        "run_id": run_id,
        "suites": suites,
        "cycles": cycles,
        "dry_run": dry_run,
        "worker_mode": worker_mode,
        "status": "started",
    }


def start_dispatch_track(
    workspace: Path,
    request: dict[str, object],
    *,
    response_written: threading.Event | None = None,
) -> dict[str, object]:
    from goodteam_os.skills.models import SkillError
    from goodteam_os.skills.worker_requests import has_skill_fields, subject_for_role

    if has_skill_fields(request):
        raise SkillError("skill_dispatch_track_not_connected", "Worktree dispatch does not accept Skill task references; no fallback was started.")
    selected_key = str(request.get("project_key", "") or "")
    if selected_key:
        from goodteam_os.skills.task_start import workflow_job_fields
        from goodteam_os.team_control_plane import TeamControlPlaneStore

        existing = StateStore(workspace).read_project_current(selected_key)
        if existing is not None:
            workflow_job_fields(existing, role=str(request.get("role", "")), command_key="worktree-dispatch-reference-check")
            if existing.task_skill_context:
                raise SkillError("skill_dispatch_track_not_connected", "A method-bound task cannot enter the legacy worktree dispatcher.")
        # This helper creates a NEW dispatch, not a retry of the existing run.
        # An old legacy run must not hide the selected Role's current methods.
        team = TeamControlPlaneStore(workspace, selected_key).read()
        if team and team.get("skill_bindings", {}).get(subject_for_role(str(request.get("role", ""))), {}).get("mode") == "custom":
            raise SkillError("skill_dispatch_track_not_connected", "Custom-method worktree dispatch requires a supported bounded task start.")
    else:
        from goodteam_os.service import service_state_dir

        if (Path(service_state_dir()).expanduser().resolve() / "skills" / "task-locks").exists():
            raise SkillError("project_scope_unknown", "Skill-aware worktree dispatch requires explicit project scope before creating a branch.")
    name = str(request.get("name", "")).strip()
    role = str(request.get("role", "")).strip()
    adapter = str(request.get("adapter", "")).strip()
    task = str(request.get("task", ""))
    constraints = str(request.get("constraints", ""))
    verify = str(request.get("verify", "")).strip()
    if not DISPATCH_TRACK_NAME_RE.fullmatch(name):
        raise ValueError("name must be a slug using lowercase letters, numbers, and hyphens")
    if not role:
        raise ValueError("role is required")
    if not adapter:
        raise ValueError("adapter is required")
    if not task.strip():
        raise ValueError("task is required")

    branch = f"feat/{name}"
    worktree = _dispatch_worktree_path(workspace, name)
    safety = ensure_dispatch_project_root_is_safe(worktree, projects_dir=workspace / "projects")
    if not safety.get("safe"):
        raise ValueError(str(safety.get("reason", "unsafe dispatch target")))
    worktree = _ensure_dispatch_worktree(workspace, name=name, branch=branch)

    run_id = f"dispatch-{uuid4()}"
    task_file, constraints_file = _prepare_dispatch_track_inputs(
        worktree,
        run_id=run_id,
        task=task,
        constraints=constraints,
    )
    tracks = [parse_dispatch_track_spec(f"{role}:{adapter}:{task_file}", base_dir=worktree)]
    verify_commands = {}
    if verify:
        verify_commands = dict([parse_dispatch_verify_spec(f"{role}:{verify}")])

    _start_dispatch_track_background(
        workspace=workspace,
        run_id=run_id,
        name=name,
        worktree=worktree,
        tracks=tracks,
        verify_commands=verify_commands,
        constraints_file=constraints_file,
        response_written=response_written,
    )
    return {
        "run_id": run_id,
        "name": name,
        "branch": branch,
        "worktree": str(worktree),
        "status": "started",
    }


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


def _land_command_for_pull_request(
    number: int,
    head_branch: str,
    worktrees: list[dict[str, str]],
    *,
    review_rationale_path: str = "",
) -> str:
    matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
    command = f"scripts/land-pr.sh {number}"
    if matching_worktree is None:
        return command
    command = f"{command} --worktree {matching_worktree['path']}"
    if review_rationale_path:
        command = f"{command} --review-rationale {review_rationale_path}"
    return command


def _review_rationale_path(root: Path, number: int) -> Path:
    return root / ".goodteam" / "provenance" / f"pr-{number}" / "review-rationale.md"


def _non_empty_file_exists(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _server_actions_enabled() -> bool:
    return os.environ.get("GOODTEAM_SERVER_ACTIONS", "").strip() == "1"


def _server_action_nonce() -> str:
    return os.environ.get("GOODTEAM_SERVER_ACTION_NONCE", "").strip()


def _server_action_audit_dir(root: Path) -> Path:
    return root / ".goodteam" / "server-actions"


def _tail_text(value: str, *, max_chars: int = 2000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _decode_subprocess_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _optional_absolute_path(value: object, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return str(path)


def _git_safe_ref(value: object, field: str = "ref") -> str:
    ref = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{7,80}", ref):
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    if ref.startswith(("-", "/", ".")) or ref.endswith(("/", ".")):
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    if ".." in ref or "//" in ref or "@{" in ref:
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    for component in ref.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.startswith(("-", "."))
            or component.endswith(".lock")
        ):
            raise ValueError(f"{field} must be a git-safe ref or SHA")
    return ref


def _git_safe_tag(value: object, field: str = "tag") -> str:
    tag = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", tag):
        raise ValueError(f"{field} must be a git-safe tag")
    if tag.startswith(("-", "/", ".")) or tag.endswith(("/", ".")):
        raise ValueError(f"{field} must be a git-safe tag")
    if ".." in tag or "//" in tag or "@{" in tag:
        raise ValueError(f"{field} must be a git-safe tag")
    for component in tag.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.startswith(("-", "."))
            or component.endswith(".lock")
        ):
            raise ValueError(f"{field} must be a git-safe tag")
    return tag


def _tag_release_safety_report() -> dict[str, object]:
    return {
        "server_execution": "report_only",
        "blocked_by": [
            "tag_name_policy",
            "signed_tag_policy",
            "remote_push_policy",
            "rollback_policy",
            "human_release_decision",
        ],
        "future_enablement_requires": [
            "GOODTEAM_SERVER_ACTIONS=1",
            "GOODTEAM_SERVER_ACTION_NONCE",
            "typed confirmation: TAG RELEASE <tag>",
            "argv allowlist with shell=False",
            "audit record before any remote push",
            "human-reviewed release notes and rollback plan",
        ],
    }


def _server_action_expected_confirmation(action: str, request: dict[str, object]) -> str:
    if action == "land_pr":
        pr_number = _positive_int(request.get("pr"), "pr")
        return f"LAND PR {pr_number}"
    if action == "graduate_release":
        ref = _git_safe_ref(request.get("ref", ""))
        return f"GRADUATE RELEASE {ref}"
    if action == "tag_release":
        tag = _git_safe_tag(request.get("tag", ""), "tag")
        return f"TAG RELEASE {tag}"
    raise ValueError(f"Unsupported server action: {action}")


def _land_pr_action_argv(root: Path, request: dict[str, object]) -> list[str]:
    pr_number = _positive_int(request.get("pr"), "pr")
    dev_loop = build_dev_loop(root)
    readiness = dev_loop.get("land_tag_readiness", {})
    candidates = readiness.get("pull_requests", []) if isinstance(readiness, dict) else []
    matching = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and int(item.get("pr", 0) or 0) == pr_number
        ),
        None,
    )
    if matching is None:
        raise ValueError(f"PR {pr_number} is not present in local land readiness")
    if not matching.get("ready"):
        missing = matching.get("missing", [])
        missing_text = ", ".join(str(item) for item in missing) if isinstance(missing, list) else str(missing)
        raise ValueError(f"PR {pr_number} is not ready to land: {missing_text}")
    worktree = _optional_absolute_path(matching.get("worktree", ""), "worktree")
    rationale = _optional_absolute_path(matching.get("review_rationale_path", ""), "review_rationale_path")
    if not worktree or not rationale:
        raise ValueError(f"PR {pr_number} is missing trusted land paths")
    return [
        "scripts/land-pr.sh",
        str(pr_number),
        "--worktree",
        worktree,
        "--review-rationale",
        rationale,
    ]


def _server_action_argv(root: Path, action: str, request: dict[str, object]) -> list[str]:
    if action == "land_pr":
        return _land_pr_action_argv(root, request)
    if action == "graduate_release":
        ref = _git_safe_ref(request.get("ref", ""))
        return ["scripts/graduate-release.sh", "--ref", ref, "--yes"]
    if action == "tag_release":
        raise ValueError("tag_release is report-only until tag safety design is complete")
    raise ValueError(f"Unsupported server action: {action}")


def run_gated_server_action(
    root: Path,
    request: dict[str, object],
    *,
    origin: str = "",
    actor: str = "local-dashboard",
) -> dict[str, object]:
    if not _server_actions_enabled():
        raise PermissionError("server action execution is disabled")
    expected_nonce = _server_action_nonce()
    supplied_nonce = str(request.get("nonce", ""))
    if not expected_nonce or not hmac.compare_digest(supplied_nonce, expected_nonce):
        raise PermissionError("server action nonce is missing or invalid")

    action = str(request.get("action", "")).strip()
    expected_confirmation = _server_action_expected_confirmation(action, request)
    typed_confirmation = str(request.get("confirm", "")).strip()
    if typed_confirmation != expected_confirmation:
        raise ValueError(f"typed confirmation must exactly match: {expected_confirmation}")
    argv = _server_action_argv(root, action, request)

    started_at = datetime.now(UTC).isoformat()
    timed_out = False
    returncode: int | None
    stdout = ""
    stderr = ""
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=SERVER_ACTION_TIMEOUT_SECONDS,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = _decode_subprocess_text(exc.output)
        stderr = _decode_subprocess_text(exc.stderr)
    finished_at = datetime.now(UTC).isoformat()
    audit = {
        "id": f"server-action-{uuid4()}",
        "timestamp": finished_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "actor": actor,
        "origin": origin,
        "action": action,
        "argv": argv,
        "typed_confirmation": typed_confirmation,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_tail": _tail_text(stdout),
        "stderr_tail": _tail_text(stderr),
        "rollback_hint": "Use GitHub PR/release rollback guidance from the audit context; do not rerun blindly.",
    }
    audit_dir = _server_action_audit_dir(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{audit['id']}.json"
    audit["audit_path"] = str(audit_path)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "completed" if returncode == 0 else "failed",
        "action": action,
        "argv": argv,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_tail": audit["stdout_tail"],
        "stderr_tail": audit["stderr_tail"],
        "audit_path": str(audit_path),
    }


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


def _worktree_status_for_path(path: str, root: Path, warnings: list[str]) -> str:
    if not path:
        return "missing"
    result = _run_readonly_command(["git", "-C", path, "status", "--short"], root, warnings)
    if result is None:
        return "unknown"
    if result.returncode != 0:
        warnings.append(f"git status failed for worktree {path}")
        return "unknown"
    return "dirty" if result.stdout.strip() else "clean"


def build_land_tag_readiness(
    pull_requests: list[dict[str, object]],
    pre_review_presence_by_pr: dict[int, bool],
    worktrees: list[dict[str, str]],
    worktree_status_by_path: dict[str, str],
    root: Path,
) -> dict[str, object]:
    readiness_items: list[dict[str, object]] = []
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        head_branch = str(pull_request.get("head_branch", ""))
        ci = str(pull_request.get("ci", "unknown"))
        pre_review = pre_review_presence_by_pr.get(number, False)
        matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
        worktree_path = str(matching_worktree.get("path", "")) if matching_worktree else ""
        worktree_status = worktree_status_by_path.get(worktree_path, "missing") if worktree_path else "missing"
        rationale_path = _review_rationale_path(root, number)
        review_rationale_present = _non_empty_file_exists(rationale_path)
        missing: list[str] = []
        if ci != "pass":
            missing.append("ci_not_green")
        if not pre_review:
            missing.append("missing_pre_review")
        if matching_worktree is None:
            missing.append("missing_worktree")
        elif worktree_status == "dirty":
            missing.append("dirty_worktree")
        elif worktree_status != "clean":
            missing.append("worktree_status_unknown")
        if not review_rationale_present:
            missing.append("missing_rationale")
        readiness_items.append(
            {
                "pr": number,
                "ready": not missing,
                "missing": missing,
                "command": _land_command_for_pull_request(
                    number,
                    head_branch,
                    worktrees,
                    review_rationale_path=str(rationale_path) if review_rationale_present else "",
                ),
                "ci": ci,
                "pre_review": pre_review,
                "head_branch": head_branch,
                "worktree": worktree_path,
                "worktree_status": worktree_status,
                "review_rationale": review_rationale_present,
                "review_rationale_path": str(rationale_path),
            }
        )
    return {
        "server_execution": "disabled_by_default",
        "pull_requests": readiness_items,
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


def _server_action_capability(
    *,
    available: bool,
    mode: str,
    reason: str = "",
    command: str = "",
    readiness: dict[str, object] | None = None,
) -> dict[str, object]:
    capability: dict[str, object] = {
        "available": available,
        "mode": mode,
        "reason": reason,
    }
    if command:
        capability["command"] = command
    if readiness is not None:
        capability["readiness"] = readiness
    return capability


def build_server_action_execution_policy(root: Path | None) -> dict[str, object]:
    env_gate_configured = _server_actions_enabled()
    nonce_configured = bool(_server_action_nonce())
    destructive_actions_available = bool(root is not None and env_gate_configured and nonce_configured)
    audit_dir = str(_server_action_audit_dir(root.resolve())) if root is not None else ""
    blocked_actions: dict[str, list[str]] = {
        "tag_release": ["tag_release_safety_design_required"],
    }
    enabled_actions: list[str] = []
    if destructive_actions_available:
        enabled_actions = ["land_pr", "graduate_release"]
    else:
        blockers = []
        if root is None:
            blockers.append("no_project_selected")
        if not env_gate_configured:
            blockers.append("disabled_by_default")
        if not nonce_configured:
            blockers.append("missing_nonce")
        blocked_actions["land_pr"] = blockers
        blocked_actions["graduate_release"] = blockers
    return {
        "status": "guarded_execute" if destructive_actions_available else "disabled",
        "destructive_actions_available": destructive_actions_available,
        "env_gate_configured": env_gate_configured,
        "nonce_configured": nonce_configured,
        "same_origin_required": True,
        "typed_confirmation_required": True,
        "argv_allowlist_required": True,
        "audit_required": True,
        "audit_dir": audit_dir,
        "timeout_seconds": SERVER_ACTION_TIMEOUT_SECONDS,
        "enabled_actions": enabled_actions,
        "blocked_actions": blocked_actions,
    }


def build_server_action_capabilities(
    root: Path | None,
    *,
    release_readiness: dict[str, object] | None = None,
    land_tag_readiness: dict[str, object] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    capability_warnings: list[str] = [] if warnings is None else warnings
    if release_readiness is None:
        release_readiness = (
            build_release_readiness(root, capability_warnings) if root is not None else {"available": False}
        )
    graduate_command = str(release_readiness.get("graduate_command", ""))
    destructive_actions_available = bool(root is not None and _server_actions_enabled() and _server_action_nonce())
    destructive_reason = "" if destructive_actions_available else "disabled_by_default"
    destructive_mode = "guarded_execute" if destructive_actions_available else "human_command_only"
    return {
        "actions": {
            "dispatch_track": _server_action_capability(
                available=True,
                mode="execute",
            ),
            "dry_smoke": _server_action_capability(
                available=True,
                mode="execute",
            ),
            "land_pr": _server_action_capability(
                available=destructive_actions_available,
                mode=destructive_mode,
                reason=destructive_reason,
                readiness=land_tag_readiness,
            ),
            "tag_release": _server_action_capability(
                available=False,
                mode="report_only",
                reason="tag_release_safety_design_required",
                readiness=_tag_release_safety_report(),
            ),
            "graduate_release": _server_action_capability(
                available=destructive_actions_available,
                mode=destructive_mode,
                reason=destructive_reason,
                command=graduate_command,
            ),
        },
        "execution_policy": build_server_action_execution_policy(root),
        "release_readiness": release_readiness,
        "warnings": capability_warnings,
        "generated_at": datetime.now(UTC).isoformat(),
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
    worktree_status_by_path = {
        worktree["path"]: _worktree_status_for_path(worktree["path"], root, warnings)
        for worktree in worktrees
        if worktree.get("path")
    }

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
    pre_review_presence_by_pr: dict[int, bool] = {}
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        report_path = PRE_REVIEW_REPORT_DIR / f"pre-review-{number}-report.md"
        present = report_path.is_file()
        pre_review_presence_by_pr[number] = present
        pre_reviews.append(
            {
                "pr": number,
                "present": present,
                "report_path": str(report_path),
            }
        )
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        head_branch = str(pull_request["head_branch"])
        matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
        worktree_path = str(matching_worktree.get("path", "")) if matching_worktree else ""
        rationale_path = _review_rationale_path(root, number)
        rationale_present = _non_empty_file_exists(rationale_path)
        worktree_status = worktree_status_by_path.get(worktree_path, "missing") if worktree_path else "missing"
        pull_request["land_ready"] = (
            pull_request["ci"] == "pass"
            and pre_review_presence_by_pr.get(number, False)
            and worktree_status == "clean"
            and rationale_present
        )
        pull_request["land_command"] = _land_command_for_pull_request(
            number,
            head_branch,
            worktrees,
            review_rationale_path=str(rationale_path) if rationale_present else "",
        )

    release_readiness = build_release_readiness(root, warnings)
    land_tag_readiness = build_land_tag_readiness(
        pull_requests,
        pre_review_presence_by_pr,
        worktrees,
        worktree_status_by_path,
        root,
    )
    return {
        "worktrees": worktrees,
        "pull_requests": pull_requests,
        "pre_reviews": pre_reviews,
        "is_git_repo": True,
        "land_tag_readiness": land_tag_readiness,
        "release_readiness": release_readiness,
        "server_actions": build_server_action_capabilities(
            root,
            release_readiness=release_readiness,
            land_tag_readiness=land_tag_readiness,
        ),
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_agile_board(state: AgileDevState | None) -> dict[str, object]:
    ledger = _task_ledger_from_state(state)
    if ledger is None:
        return serialize_task_board(build_task_board([]))
    project_key = str(getattr(state, "project_key", "") or "")
    return serialize_task_board(build_task_board(LocalTaskStore(ledger.parents[2], project_key=project_key).list_tasks()))


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
    if not (workspace / "state" / "goals.sqlite3").is_file():
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
    from goodteam_os.delivery_outcomes import build_delivery_summary

    project_config = _project_config_for_key(workspace, project_key)
    start_context = build_start_console_context(workspace, project_config=project_config)
    retry_state = resolve_dashboard_state(workspace, project_key=project_key, include_display_hints=False)
    state = _dashboard_state_view(retry_state)
    project_root = resolve_project_runtime_root(dict(load_project_target(project_config).get("repos", {})))
    task_assistant_session = (
        TaskAssistantService(workspace, project_config=project_config).session(project_key, project_root, **({"goal_limit": goal_limit} if goal_limit is not None else {}))
        if project_root is not None
        else None
    )
    resources = build_resource_items(state)
    approval_reviews = build_approval_reviews(state, workspace=workspace)
    action_items = build_action_items(workspace, project_key=project_key)
    agile_board = build_agile_board(state)
    from goodteam_os.project_iteration import project_iteration_backlog
    iteration_backlog = project_iteration_backlog(project_root, project_key)
    worker_job_records = WorkerJobStore(project_root).list_jobs() if project_root is not None else []
    worker_jobs = _serialize_worker_job_items(worker_job_records)
    task_rows = [
        task
        for column in dict(agile_board.get("columns", {})).values()
        for task in (column if isinstance(column, list) else [])
        if isinstance(task, dict)
    ]
    active_run = is_active_run(state)
    intent_commits = IntentCommitStore(workspace, project_key).list_intents()
    team_payload = None
    try:
        team_payload = build_team_view_payload(workspace, project_key)["team"]
    except (ProjectApiError, ValueError):
        team_payload = None
    team_workspace = build_team_workspace(
        state=state,
        workflow_active=active_run,
        project_root=project_root,
        project_key=project_key,
        operator_state_dir=service_state_dir(),
        tasks=task_rows,
        worker_jobs=worker_job_records,
        approval_reviews=approval_reviews,
        action_items=action_items,
        intent_commits=intent_commits,
        task_assistant_session=task_assistant_session,
    )
    return {
        "type": "project",
        "state": asdict(state) if state is not None else None,
        "resources": resources,
        "start_context": start_context,
        "team": team_payload,
        "active_run": active_run,
        "task_assistant_session": task_assistant_session,
        "role_observations": build_role_observations(state),
        "workflow_steps": build_workflow_steps(state),
        "approval_reviews": approval_reviews,
        "action_items": action_items,
        "smoke_run_items": build_smoke_run_items(workspace),
        "agile_board": agile_board,
        "iteration_backlog": iteration_backlog,
        "worker_jobs": worker_jobs,
        # Team projection can migrate configuration; version its final config
        # against the same persisted state, not the display-only revision hint.
        "retry_target": workflow_retry_target(workspace, retry_state),
        "delivery_summary": build_delivery_summary(workspace, state=state, jobs=worker_job_records, worker_root=project_root),
        "team_workspace": team_workspace,
        "project_assistant": build_project_assistant(
            task_assistant_session, team_workspace, project_key=project_key,
            workflow_active=active_run, jobs=worker_job_records, intent_commits=intent_commits,
        ),
    }


def _task_ledger_from_state(state: AgileDevState | None) -> Path | None:
    if state is None:
        return None
    path = ""
    for resource in state.resources.values():
        if ResourceCapability.BOARD_SOURCE in resource.capabilities and resource.path:
            path = resource.path
            break
    if not path:
        path = str(state.artifacts.get("task_ledger", "")).strip()
    if not path:
        return None
    ledger = Path(path)
    if ledger.name != "tasks.json" or not ledger.is_file():
        return None
    return ledger


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
                env=env,
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
    project_name = str(target.get("name", "") or target.get("key", "") or project_key)
    return _initialize_project_git(
        root_path=root_path,
        project_name=project_name,
        project_key=project_key,
        init_git=True,
        create_remote=_request_bool(request.get("create_remote"), False),
        remote_private=_request_bool(request.get("remote_private"), True),
        initial_files=_initial_git_files(request.get("initial_files")),
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


def workflow_steps_for_state(state: AgileDevState | None) -> list[dict[str, str]]:
    source_type = state.source_type if state is not None else "new_project"
    return workflow_steps_for_source(source_type)


def _dashboard_state_view(state):
    if state is not None and state.next_node.startswith("revise_") and not state.blockers:
        return replace(state, blockers=["Changes requested; retry to apply the recorded revision."])
    return state


def resolve_dashboard_state(workspace: Path, project_key: str = "", *, include_display_hints: bool = True) -> AgileDevState | None:
    store = StateStore(workspace)
    if project_key:
        state = store.read_project_current(project_key)
        if state is not None and state.pending_transition.get("status") in {"recording", "executing"}:
            return recover_transition(workspace, project_key)
        return _dashboard_state_view(state) if include_display_hints else state
    return store.read_current()


def build_workflow_graph(workspace: Path, project_key: str = "") -> dict[str, object]:
    def _map_to_graph_node(node: str) -> str:
        if node.endswith("_review_gate"):
            return node.removesuffix("_review_gate")
        if node.startswith("await_") and node.endswith("_approval"):
            return node.removeprefix("await_").removesuffix("_approval")
        return node

    key = ""
    if project_key.strip():
        try:
            key = normalize_project_key(project_key)
        except ValueError:
            key = ""

    edge_fields = (
        ("on_approved_next", "approved"),
        ("on_changes_requested", "changes_requested"),
        ("on_rejected", "rejected"),
    )
    edges: list[dict[str, str]] = []
    identifiers = set(WORKFLOW_NODES) | set(GATE_TRANSITIONS)
    for gate, transition in GATE_TRANSITIONS.items():
        identifiers.add(gate)
        identifiers.add(transition.gate)
        for field, edge_type in edge_fields:
            target = getattr(transition, field, None)
            if target:
                edges.append({"from": transition.gate, "to": target, "type": edge_type})
                identifiers.add(target)
    for node in WORKFLOW_NODES.values():
        for dependency in getattr(node, "depends_on", ()):
            edges.append({"from": dependency, "to": node.name, "type": "dependency"})
            identifiers.add(node.name)
            identifiers.add(dependency)
        for field, edge_type in (("on_error", "error"), ("on_blocked", "blocked")):
            target = getattr(node, field, "")
            if target:
                edges.append({"from": node.name, "to": target, "type": edge_type})
                identifiers.add(node.name)
                identifiers.add(target)

    nodes = []
    for identifier in sorted(identifiers):
        workflow_node = WORKFLOW_NODES.get(identifier)
        if workflow_node is not None:
            nodes.append(
                {
                    "name": workflow_node.name,
                    "gate": workflow_node.run_guard_gate,
                    "runner": workflow_node.runner_fn,
                    "kind": "stage",
                }
            )
        elif identifier in GATE_TRANSITIONS:
            nodes.append({"name": identifier, "kind": "gate"})
        elif identifier in {"rejected", "blocked"}:
            nodes.append({"name": identifier, "kind": "terminal"})
        elif identifier.startswith("revise_"):
            nodes.append({"name": identifier, "kind": "revise"})
        else:
            nodes.append({"name": identifier, "kind": "role"})

    run = None
    if key:
        state = resolve_dashboard_state(workspace, project_key=key)
        if is_active_run(state):
            run = {
                "run_id": state.run_id,
                "current_node": state.current_node,
                "next_node": state.next_node,
                "current_graph_node": _map_to_graph_node(state.current_node),
                "next_graph_node": _map_to_graph_node(state.next_node),
                "approvals": {gate: str(decision) for gate, decision in state.approvals.items()},
            }

    return {
        "project_key": key,
        "nodes": nodes,
        "edges": edges,
        "execution_plan": _workflow_execution_plan_payload(),
        "workflow_execution_plan_v2": _workflow_execution_plan_v2_payload(),
        "run": run,
    }


def _workflow_execution_plan_payload() -> dict[str, object]:
    try:
        return workflow_execution_plan(WORKFLOW_NODES, gate_transitions=GATE_TRANSITIONS).to_dict()
    except ValueError as exc:
        return {
            "scheduler": "unavailable",
            "parallel_level_execution": "deferred",
            "levels": [],
            "error": str(exc),
        }


def _workflow_execution_plan_v2_payload() -> dict[str, object]:
    return workflow_execution_plan_v2(WORKFLOW_NODES, gate_transitions=GATE_TRANSITIONS)


def is_active_run(state: AgileDevState | None) -> bool:
    return state is not None and bool(state.next_node) and state.next_node != "rejected"


def _latest_archived_run_summary(workspace: Path, project_key: str) -> dict[str, str]:
    key = normalize_project_key(project_key)
    archive_dir = StateStore(workspace).project_state_dir(key) / "archived"
    if not archive_dir.is_dir():
        return {}
    archived_paths = sorted(
        archive_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in archived_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return {
            "run_id": str(data.get("run_id", "") or ""),
            "idea": str(data.get("idea", "") or ""),
            "source_type": str(data.get("source_type", "") or ""),
            "archive_path": str(path),
        }
    return {}


def _run_projection(
    state: AgileDevState,
    *,
    status: str,
    updated_at: str,
    reason: str = "",
) -> dict[str, object]:
    return {
        "run_id": state.run_id,
        "project_key": state.project_key,
        "idea": state.idea,
        "source_type": state.source_type,
        "status": status,
        "current_node": state.current_node,
        "next_node": state.next_node,
        "updated_at": updated_at,
        "reason": reason,
        "task_skill_context": state.task_skill_context,
    }


def build_project_run_history(workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    store = StateStore(workspace)
    runs: list[dict[str, object]] = []
    current = store.read_project_current(key)
    current_path = store.project_state_dir(key) / "current.json"
    if current is not None:
        runs.append(
            _run_projection(
                current,
                status="active" if is_active_run(current) else "complete",
                updated_at=datetime.fromtimestamp(
                    current_path.stat().st_mtime,
                    tz=UTC,
                ).isoformat(),
            )
        )
    for path, state in store.list_project_archived(key):
        reason = path.stem.removeprefix(f"{state.run_id}-")
        runs.append(
            _run_projection(
                state,
                status="archived",
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
                reason=reason,
            )
        )
    return {"project_key": key, "runs": runs}


def build_project_run_detail(
    workspace: Path,
    project_key: str,
    run_id: str,
) -> dict[str, object]:
    from goodteam_os.delivery_outcomes import build_delivery_summary

    key = normalize_project_key(project_key)
    store = StateStore(workspace)
    current = store.read_project_current(key)
    if current is not None and current.run_id == run_id:
        current_path = store.project_state_dir(key) / "current.json"
        status = "active" if is_active_run(current) else "complete"
        state = current
        summary = _run_projection(
            state,
            status=status,
            updated_at=datetime.fromtimestamp(current_path.stat().st_mtime, tz=UTC).isoformat(),
        )
    else:
        archived = next(
            (
                (path, candidate)
                for path, candidate in store.list_project_archived(key)
                if candidate.run_id == run_id
            ),
            None,
        )
        if archived is None:
            raise ProjectApiError(
                "run_not_found",
                f"Run not found for project {key}: {run_id}",
                404,
                details={"project_key": key, "run_id": run_id},
                recovery=["return_to_project_work", "choose_another_run"],
            )
        path, state = archived
        summary = _run_projection(
            state,
            status="archived",
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
            reason=path.stem.removeprefix(f"{state.run_id}-"),
        )
    project_config = _project_config_for_key(workspace, key)
    project_root = resolve_project_runtime_root(dict(load_project_target(project_config).get("repos", {})))
    agile_board = build_agile_board(state)
    task_rows = [
        task
        for column in dict(agile_board.get("columns", {})).values()
        for task in (column if isinstance(column, list) else [])
        if isinstance(task, dict)
    ]
    worker_jobs = WorkerJobStore(project_root).list_jobs() if project_root is not None else []
    approval_reviews = build_approval_reviews(state, workspace=workspace)
    run_tasks = [task for task in task_rows if str(task.get("run_id", "")) == state.run_id]
    if not run_tasks:
        run_tasks = build_stage_task_snapshot(
            state,
            updated_at=str(summary.get("updated_at", "")),
        )
    team_workspace = build_team_workspace(
        state=state,
        workflow_active=False,
        project_root=project_root,
        project_key=key,
        # Prefer explicitly run-bound rows. Older runs predate that identity,
        # so their canonical stage rows are reconstructed from the archived
        # state rather than leaking the current shared board into history.
        tasks=run_tasks,
        worker_jobs=worker_jobs,
        approval_reviews=approval_reviews,
        action_items=build_action_items(workspace, project_key=key),
        intent_commits=IntentCommitStore(workspace, key).list_intents(mission_id=state.run_id),
        read_only=True,
        historical_reason=str(summary.get("reason", "")),
    )
    return {
        "project_key": key,
        "run": summary,
        "state": asdict(state),
        "resources": build_resource_items(state),
        "workflow_steps": build_workflow_steps(state),
        "agile_board": agile_board,
        "approval_reviews": approval_reviews,
        "team_workspace": team_workspace,
        "delivery_summary": build_delivery_summary(
            workspace, state=state, jobs=worker_jobs, historical_reason=str(summary.get("reason", "")),
            worker_root=project_root,
        ),
    }


def _normalize_start_delivery_standards(value: list[str] | str) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


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


@serialized_workflow
def start_workflow(
    *,
    workspace: Path,
    idea: str,
    project_key: str,
    model_config: Path,
    model_profile: str,
    delivery_mode: str = "",
    delivery_standards: list[str] | str = "",
    source_type: str = "new_project",
    project_config: Path | None = None,
    role_model_overrides: list[str] | None = None,
    load_profile: str = "greenfield",
    dry_run: bool = False,
    run_id: str = "",
    expected_team_revision: int | None = None,
    task_skill_context: dict[str, object] | None = None,
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("Workflow goal is required.")
    key = normalize_project_key(project_key)
    requested_run_id = str(run_id).strip()
    if not dry_run and is_active_run(resolve_dashboard_state(workspace, project_key=key)):
        raise ValueError("An active run already exists. Finish, archive, or resume it before starting another run.")

    if source_type not in {"new_project", "existing_project"}:
        raise ValueError(f"Unknown workflow source type: {source_type}")
    if source_type == "existing_project" and project_config is None:
        raise ValueError("Project config is required for existing project adoption.")

    if project_config is not None:
        project_config = project_config.expanduser()
        if project_config.is_file():
            project_config = project_config.resolve()
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    if not dry_run and project_config is not None and project_config.is_file():
        project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
        if project_root is not None:
            try:
                readiness = read_bound_project_readiness(workspace, key, project_root)
            except ValueError as exc:
                if not str(exc).startswith("No readiness found for project:"):
                    raise
            else:
                if not build_capabilities(readiness)["start_workflow"]:
                    raise ValueError("Project readiness must be complete before starting a workflow.")
    resolved_profile, role_models = resolve_project_model_profile(
        resolve_runtime_model_config(model_config),
        target,
        explicit_profile=model_profile,
        role_overrides=role_model_overrides or [],
    )
    state = create_initial_state(
        run_id=requested_run_id or str(uuid4()),
        project_key=key,
        idea=idea.strip(),
    )
    state.source_type = source_type
    state.source_ref = str(project_config) if project_config is not None and project_config.is_file() else "manual"
    state.model_profile = resolved_profile
    state.delivery_mode = normalize_delivery_mode(delivery_mode or str(target.get("delivery_mode", "") or "init_project"))
    state.delivery_standards = _normalize_start_delivery_standards(delivery_standards) if state.delivery_mode == "custom" else []
    state.role_models = role_models
    if not dry_run and project_config is not None and project_config.is_file():
        # Bind the immutable Team execution snapshot for this durable run so
        # later Team changes never rewrite historical execution truth. A
        # workflow start is an explicit team touchpoint and may migrate once.
        from goodteam_os.team_control_plane import build_team_execution_snapshot, ensure_team_state

        team_state = ensure_team_state(workspace, key, project_config)
        if team_state is not None:
            state.team_snapshot = build_team_execution_snapshot(team_state)
    if not dry_run and target:
        from goodteam_os.workflow_workspace import adapter_requires_workflow_workspace, prepare_workflow_workspace

        runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
        from goodteam_os.reviewer_profiles import get_reviewer_profile

        roles = ("project_intake", "product_requirement", "architect", "ui", "implementation", "qa", "release")
        needs_repository = any(adapter_requires_workflow_workspace(resolve_role_adapter(target, role)) for role in roles)
        needs_repository = needs_repository or any(
            adapter_requires_workflow_workspace(get_reviewer_profile(profile_id).adapter)
            for profiles in normalize_reviewer_profile_config(target).values() for profile_id in profiles
        )
        from goodteam_os.staging import staging_backend_enabled

        needs_repository = needs_repository or staging_backend_enabled(source_type=source_type, project_config=project_config)
        if needs_repository:
            if runtime_root is None:
                raise ValueError("A real workflow requires its configured Git repository baseline.")
            state.repository_baseline = prepare_workflow_workspace(runtime_root, project_key=key, run_id=state.run_id)
    if not dry_run:
        from goodteam_os.skills.task_context import workflow_skill_context
        from goodteam_os.skills.task_locks import TaskSkillIdentity
        from goodteam_os.skills.task_start import capture_task_start

        runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
        if task_skill_context is not None:
            state.task_skill_context = workflow_skill_context(task_skill_context, run_id=state.run_id, project_key=key)
            if not state.task_skill_context:
                from goodteam_os.skills.task_start import reject_missing_task_context

                reject_missing_task_context(TaskSkillIdentity("workflow_run", key, state.run_id))
            if state.task_skill_context:
                from goodteam_os.skills.task_service import ProjectTaskSkillService

                if runtime_root is None:
                    raise ValueError("Task Skill execution requires its bound project runtime root.")
                lock = ProjectTaskSkillService(workspace, key, worker_roots=(runtime_root,)).read(state.task_skill_context)
                confirmation = lock.get("start_input", {}).get("request", {}).get("confirmed_input", {})
                if str(confirmation.get("exact_request", "")).strip() != state.idea:
                    raise ValueError("Workflow input differs from its original task confirmation.")
                binding = confirmation.get("producer_binding", {})
                if binding != {"workspace": str(Path(workspace).expanduser().resolve()),
                               "worker_root": str(Path(runtime_root).expanduser().resolve())}:
                    raise ValueError("Workflow root differs from its original task confirmation.")
        elif runtime_root is not None:
            state.task_skill_context = capture_task_start(
                workspace=workspace, project_key=key, worker_root=runtime_root,
                task=TaskSkillIdentity("workflow_run", key, state.run_id),
                command_key=f"workflow-start:{key}:{state.run_id}",
                confirmed_input={"exact_request": idea, "source_type": source_type,
                                 "delivery_mode": state.delivery_mode, "delivery_standards": state.delivery_standards},
                expected_team_revision=expected_team_revision,
            )
    profile = resolve_profile(load_profile or "greenfield")
    state.load_profile = profile.name
    use_fix_profile = profile.name == "fix" and source_type != "existing_project"

    if dry_run:
        if source_type == "existing_project":
            planned_artifacts = {"takeover": str(workspace / "artifacts" / "runs" / state.run_id / "takeover" / "takeover-brief.md")}
            state.current_node = "takeover_review_gate"
            state.next_node = "await_takeover_approval"
            state.approvals["takeover"] = ApprovalDecision.PENDING
        elif use_fix_profile:
            planned_artifacts = {"fix_plan": str(workspace / "artifacts" / "runs" / state.run_id / "fix_plan" / "fix-plan.md")}
            state.current_node = "fix_plan_review_gate"
            state.next_node = "await_fix_plan_approval"
            state.approvals["fix_plan"] = ApprovalDecision.PENDING
        else:
            planned_artifacts = {"prd": str(workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md")}
            state.current_node = "prd_review_gate"
            state.next_node = "await_prd_approval"
        payload = asdict(state)
        payload["dry_run"] = True
        payload["planned_artifacts"] = planned_artifacts
        return payload

    previous = StateStore(workspace).read_project_current(key)
    if previous is not None and not is_active_run(previous):
        StateStore(workspace).archive_project_current(key, reason="rejected" if previous.next_node == "rejected" else "complete")
    if state.task_skill_context or state.repository_baseline:
        # Lock ownership and the portable reference are durable BEFORE any graph
        # node may dispatch. Failure leaves an inspectable blocked task, not a
        # template-generated success or a recapture from current methods.
        StateStore(workspace).write_project_current(state)
        StateStore(workspace).write_project_checkpoint(state)
    from goodteam_os.skills.models import SkillError

    try:
        if source_type == "existing_project":
            result = run_adoption_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        elif use_fix_profile:
            result = run_fix_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        else:
            result = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    except SkillError as exc:
        if not state.task_skill_context:
            raise
        state.blockers = [*state.blockers, f"{exc.code}: Task methods are locked but execution is blocked."]
        result = state
    state_store = StateStore(root=workspace)
    state_store.write_project_current(result)
    state_store.write_project_checkpoint(result)
    append_action(
        workspace,
        run_id=result.run_id,
        actor="human",
        action="workflow_started",
        project_key=result.project_key,
        before_node="",
        after_node=result.next_node,
    )
    for artifact, artifact_path in result.artifacts.items():
        if artifact in {"task_ledger"}:
            continue
        append_action(
            workspace,
            run_id=result.run_id,
            actor="system",
            action=f"{artifact}_generated",
            project_key=result.project_key,
            before_node="",
            after_node=result.next_node,
            artifact=artifact,
            artifact_path=artifact_path,
        )
    notify_current_approval_gate(result, workspace=workspace)
    payload = asdict(result)
    payload["dry_run"] = False
    return payload


def report_bug(
    *,
    workspace: Path,
    project_key: str,
    description: str,
    title: str = "",
    severity: str = "p2",
) -> dict[str, object]:
    """Start a lightweight `fix` workflow run from a human-reported bug.

    Builds the defect context (`[BUG] ...`) into the run idea and dispatches the
    `fix` load profile, which stops at the `await_fix_plan_approval` gate with a
    `fix_plan` artifact. The active-run guard in `start_workflow` still applies;
    its ValueError propagates so the route can map it to a 400.
    """
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

    result = start_workflow(
        workspace=workspace,
        idea=idea,
        project_key=project_key,
        model_config=Path("models.yaml"),
        model_profile=model_profile,
        source_type="new_project",
        project_config=project_config,
        load_profile="fix",
    )
    result["severity"] = severity
    result["title"] = title
    return result


@serialized_workflow
def decide_approval(
    *,
    workspace: Path,
    approval: str,
    decision: str,
    project_key: str = "",
    debate_ref: str = "",
    note: str = "",
    decision_origin: str = "web",
    actor_ref: str = "",
    review_mode: str = "",
    review_session_id: str = "",
    client_context: dict[str, object] | None = None,
    review_items: list[dict[str, object]] | None = None,
    expected_run_id: str = "",
    expected_target_type: str = "",
    expected_target_id: str = "",
    expected_artifact_key: str = "",
    expected_artifact_version: str = "",
) -> dict[str, object]:
    try:
        approval_decision = ApprovalDecision(decision)
    except ValueError as exc:
        raise ValueError(f"Unknown approval decision: {decision}") from exc

    if not isinstance(note, str):
        raise ProjectApiError("invalid_approval_note", "Approval note must be text.", 400)

    if approval_decision in (ApprovalDecision.CHANGES_REQUESTED, ApprovalDecision.REJECTED):
        if not note.strip():
            raise ValueError(f"Approval decision reason is required for {approval_decision.value}")

    state_store = StateStore(workspace)
    state = state_store.read_project_current(project_key) if project_key else state_store.read_current()
    if state is None:
        raise ValueError("No active run to approve.")
    key = normalize_project_key(project_key or state.project_key)

    # Resolve the approved-next target through the run's load profile. Greenfield
    # keeps the historical module-function call path (so monkeypatch-based tests
    # that spy on web.approved_next_for_gate stay valid, including its
    # "Unknown approval gate" raise ordering); other profiles (e.g. fix) resolve
    # against their own captured gate-transition table.
    profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield")
    if profile.name == "greenfield":
        approved_next = approved_next_for_gate(approval)
    else:
        approved_next = profile.approved_next_for_gate(approval)

    if approval not in state.approvals:
        raise ValueError(f"Approval gate is not present in current state: {approval}")
    if state.next_node != f"await_{approval}_approval":
        if replay_approval(state, approval=approval, decision=decision, expected={
            "run_id": expected_run_id, "target_type": expected_target_type,
            "target_id": expected_target_id, "artifact_key": expected_artifact_key,
            "artifact_version": expected_artifact_version,
        }):
            return {**asdict(state), "decision": decision, "replayed": True}
        raise ValueError(f"Approval gate is not currently actionable: {approval}")

    approval_target = _validate_approval_target(
        state=state,
        workspace=workspace,
        approval=approval,
        expected_run_id=expected_run_id,
        expected_target_type=expected_target_type,
        expected_target_id=expected_target_id,
        expected_artifact_key=expected_artifact_key,
        expected_artifact_version=expected_artifact_version,
    )

    resolved_actor_ref = actor_ref
    if not resolved_actor_ref and decision_origin == "web":
        resolved_actor_ref = os.getenv("GOODTEAM_OPERATOR_REF") or "web:local"
    evidence_snapshot = build_approval_evidence_snapshot(
        state=state,
        workspace=workspace,
        project_key=key,
        debate_ref=debate_ref,
    )
    reviewer_evidence_snapshot = _safe_reviewer_evidence_snapshot_for_decision(
        state=state,
        workspace=workspace,
        project_key=key,
        approval=approval,
    )
    before_node = state.next_node
    state.approvals[approval] = approval_decision
    state.current_node = f"{approval}_review_gate"
    state.next_node = gate_next_node(state, gate=approval, approved_next=approved_next)
    if approval_decision == ApprovalDecision.CHANGES_REQUESTED:
        state.change_requests[approval] = note if decision_origin == "manager_intent_commit" else note.strip()
    begin_transition(workspace, state, dict(
        run_id=state.run_id,
        actor="human",
        action="approval_decided",
        project_key=key,
        gate=approval,
        decision=str(approval_decision),
        before_node=before_node,
        after_node=state.next_node,
        artifact=_approval_artifact_for_gate(approval),
        artifact_path=state.artifacts.get(_approval_artifact_for_gate(approval), ""),
        artifact_version=str(approval_target["artifact_version"]),
        debate_ref=debate_ref,
        note=note if decision_origin == "manager_intent_commit" else note.strip(),
        decision_origin=decision_origin,
        actor_ref=resolved_actor_ref,
        review_mode=review_mode,
        review_session_id=review_session_id,
        client_context=client_context,
        evidence_snapshot=evidence_snapshot,
        reviewer_evidence_snapshot=reviewer_evidence_snapshot,
        review_items=review_items,
    ))
    if approval == "takeover" and approval_decision == ApprovalDecision.APPROVED:
        regenerate_before_node = state.next_node
        state_store.write_project_current(state, key)
        state_store.write_project_checkpoint(state, key)
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action="prd_generated",
            project_key=key,
            gate="prd",
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact="prd",
            artifact_path=state.artifacts.get("prd", ""),
        )
    elif approval_decision == ApprovalDecision.APPROVED:
        generated_before = set(state.artifacts)
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        state = advance_approved_workflow(state=state, workspace=workspace, project_config=project_config)
        for artifact in sorted(set(state.artifacts) - generated_before):
            append_action(
                workspace,
                run_id=state.run_id,
                actor="system",
                action=f"{artifact}_generated",
                project_key=key,
                gate=approval,
                before_node=before_node,
                after_node=state.next_node,
                artifact=artifact,
                artifact_path=state.artifacts.get(artifact, ""),
            )
    elif approval in {"prd", "rfc"} and approval_decision == ApprovalDecision.REJECTED:
        regenerate_before_node = state.next_node
        state.approvals[approval] = ApprovalDecision.PENDING
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        if approval == "prd":
            state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        else:
            state = regenerate_rfc_review_gate(state=state, workspace=workspace, project_config=project_config)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{approval}_regenerated",
            project_key=key,
            gate=approval,
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact=approval_artifact_map().get(approval, approval),
            artifact_path=state.artifacts.get(approval_artifact_map().get(approval, approval), ""),
        )
    elif approval == "takeover" and approval_decision == ApprovalDecision.CHANGES_REQUESTED:
        revision_note = note if decision_origin == "manager_intent_commit" else note.strip()
        regenerate_before_node = state.next_node
        state.change_requests[approval] = revision_note
        state.approvals[approval] = ApprovalDecision.PENDING
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        state = run_adoption_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action="takeover_revised",
            project_key=key,
            gate=approval,
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact="takeover",
            artifact_path=state.artifacts.get("takeover", ""),
            note=revision_note,
        )
    elif approval in {"prd", "rfc"} and approval_decision == ApprovalDecision.CHANGES_REQUESTED:
        revision_note = note if decision_origin == "manager_intent_commit" else note.strip()
        regenerate_before_node = state.next_node
        state.change_requests[approval] = revision_note
        state.approvals[approval] = ApprovalDecision.PENDING
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        revision_role = "product_requirement" if approval == "prd" else "architect"
        uses_worker_revision = should_generate_artifact_with_worker(project_config, revision_role)
        if approval == "prd":
            state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        else:
            state = regenerate_rfc_review_gate(state=state, workspace=workspace, project_config=project_config)
        if not uses_worker_revision:
            state.change_requests.pop(approval, None)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{approval}_revised",
            project_key=key,
            gate=approval,
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact=approval_artifact_map().get(approval, approval),
            artifact_path=state.artifacts.get(approval_artifact_map().get(approval, approval), ""),
            note=revision_note,
        )
    elif approval_decision == ApprovalDecision.CHANGES_REQUESTED:
        from goodteam_os.workflow_revisions import revise_workflow_gate

        state = revise_workflow_gate(state, workspace, approval=approval)
    finish_transition(state)
    project_config_for_sync = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config_for_sync is None or not project_config_for_sync.is_file():
        try:
            project_config_for_sync = _project_config_for_key(workspace, key)
        except ValueError:
            project_config_for_sync = None
    sync_project_stage_tasks(state, project_config_for_sync)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)
    notify_current_approval_gate(state, workspace=workspace)

    try:
        from goodteam_os.telegram import update_active_tg_review_on_web_decision
        update_active_tg_review_on_web_decision(
            workspace=workspace,
            project_key=key,
            gate=approval,
            decision=decision,
        )
    except Exception:
        pass

    payload = asdict(state)
    payload["decision"] = str(approval_decision)
    return payload


def _validate_approval_target(
    *,
    state: AgileDevState,
    workspace: Path,
    approval: str,
    expected_run_id: str = "",
    expected_target_type: str = "",
    expected_target_id: str = "",
    expected_artifact_key: str = "",
    expected_artifact_version: str = "",
) -> dict[str, str]:
    """Resolve the current actionable review and reject stale UI bindings."""

    actionable = next(
        (
            review
            for review in build_approval_reviews(state, workspace=workspace)
            if str(review.get("approval", "")) == approval and bool(review.get("is_actionable"))
        ),
        None,
    )
    current = {
        "run_id": str(state.run_id),
        "target_type": "review",
        "target_id": approval,
        "artifact_key": str(actionable.get("artifact", "")) if actionable else "",
        "artifact_version": str(actionable.get("artifact_version", "")) if actionable else "",
    }
    expected = {
        "run_id": expected_run_id.strip(),
        "target_type": expected_target_type.strip(),
        "target_id": expected_target_id.strip(),
        "artifact_key": expected_artifact_key.strip(),
        "artifact_version": expected_artifact_version.strip(),
    }
    mismatches = [
        field
        for field, expected_value in expected.items()
        if expected_value and expected_value != current[field]
    ]
    if actionable is None:
        mismatches.append("actionable_review")
    if mismatches:
        raise ProjectApiError(
            "stale_approval",
            "This approval changed after the brief was loaded. Refresh the brief and review the current version before approving.",
            409,
            details={
                "mismatches": list(dict.fromkeys(mismatches)),
                "expected": expected,
                "current": current,
            },
            recovery=["refresh_approval_brief"],
        )
    return current


def start_request_changes_intent(
    *,
    workspace: Path,
    project_key: str,
    approval: str,
    expected_run_id: str,
    expected_target_type: str,
    expected_target_id: str,
    expected_artifact_key: str,
    expected_artifact_version: str,
    expected_task_id: str = "",
    expected_head_sha: str = "",
    initial_text: str = "",
) -> dict[str, object]:
    """Open a durable Manager discussion without deciding the approval."""

    key = normalize_project_key(project_key)
    state = StateStore(workspace).read_project_current(key)
    if state is None:
        raise ProjectApiError(
            "intent_mission_not_found",
            "This Task Assistant mission is no longer active.",
            409,
            recovery=["refresh_approval_brief"],
        )
    target = _validate_intent_binding(
        state=state,
        workspace=workspace,
        approval=approval,
        expected_run_id=expected_run_id,
        expected_target_type=expected_target_type,
        expected_target_id=expected_target_id,
        expected_artifact_key=expected_artifact_key,
        expected_artifact_version=expected_artifact_version,
        expected_task_id=expected_task_id,
        expected_head_sha=expected_head_sha,
    )
    try:
        intent, created = IntentCommitStore(workspace, key).open_request_changes(
            mission={
                "id": state.run_id,
                "run_id": state.run_id,
                "execution_id": "",
                "source": "workflow",
            },
            target=target,
            initial_text=initial_text,
        )
    except IntentCommitError as exc:
        raise ProjectApiError("intent_commit_invalid", str(exc), 409) from exc
    return {"intent_commit": intent, "created": created}


def discuss_request_changes_intent(
    *,
    workspace: Path,
    project_key: str,
    intent_id: str,
    text: str,
) -> dict[str, object]:
    store, intent = _load_current_request_changes_intent(
        workspace=workspace,
        project_key=project_key,
        intent_id=intent_id,
    )
    _validate_stored_intent_target(workspace=workspace, store=store, intent=intent)
    state = StateStore(workspace).read_project_current(normalize_project_key(project_key))
    project_config = (
        Path(state.source_ref)
        if state is not None and state.source_ref and state.source_ref != "manual"
        else None
    )
    interpretation = None
    if (project_config is not None and project_config.is_file()
            and resolve_role_adapter(load_project_target(project_config), "orchestrator") not in BUILTIN_WORKER_MODES):
        target = dict(intent.get("target") or {})
        try:
            interpretation = run_manager_interpretation(
                project_config=project_config,
                run_id=str(dict(intent.get("mission") or {}).get("run_id", "")),
                project_key=normalize_project_key(project_key),
                intent_id=intent_id,
                logical_intent_id=next((str(event["id"]) for event in reversed(intent.get("events", []))
                                        if event.get("event") in {"change_discussion_started", "change_discussion_continued"}), intent_id),
                change_request=text,
                context=(
                    f"Approval: {target.get('approval', '')}\n"
                    f"Artifact: {target.get('artifact_key', '')}\n"
                    f"Target: {target.get('target_type', '')}:{target.get('target_id', '')}"
                ),
            )
        except ManagerExecutionError as exc:
            raise ProjectApiError("manager_execution_failed", str(exc), 502) from exc
    try:
        updated = store.discuss(intent_id, text, interpretation=interpretation)
    except IntentCommitError as exc:
        raise ProjectApiError("intent_discussion_invalid", str(exc), 400) from exc
    return {"intent_commit": updated}


def keep_discussing_request_changes_intent(
    *,
    workspace: Path,
    project_key: str,
    intent_id: str,
) -> dict[str, object]:
    store, intent = _load_current_request_changes_intent(
        workspace=workspace,
        project_key=project_key,
        intent_id=intent_id,
    )
    _validate_stored_intent_target(workspace=workspace, store=store, intent=intent)
    try:
        updated = store.keep_discussing(intent_id)
    except IntentCommitError as exc:
        raise ProjectApiError("intent_transition_invalid", str(exc), 409) from exc
    return {"intent_commit": updated}


def confirm_request_changes_intent(
    *,
    workspace: Path,
    project_key: str,
    intent_id: str,
) -> dict[str, object]:
    """Commit the confirmed interpretation through the existing authority."""

    store, _ = _load_current_request_changes_intent(
        workspace=workspace,
        project_key=project_key,
        intent_id=intent_id,
    )
    key = normalize_project_key(project_key)
    # Holding the shared per-path lock across authority mutation prevents a
    # concurrent request from mistaking live work for a restart. After a real
    # process restart no holder remains, so the persisted state can reconcile.
    with store.confirmation_guard():
        intent = store.get(intent_id)
        if intent is None:
            raise ProjectApiError(
                "intent_commit_not_found",
                "This change discussion no longer exists.",
                404,
            )
        if intent.get("status") == "committed":
            return _committed_intent_response(intent, idempotent=True)

        if intent.get("status") in {"committing", "ambiguous"}:
            evidence_state, decision_action, evidence_details = _intent_decision_evidence(
                workspace,
                key,
                intent,
            )
            if evidence_state == "matched" and decision_action is not None:
                return _complete_intent_from_decision_action(
                    store,
                    intent_id,
                    decision_action,
                    idempotent=True,
                )
            if evidence_state == "ambiguous":
                store.mark_ambiguous(intent_id, reason="decision_evidence_mismatch")
                raise _ambiguous_intent_commit_error(evidence_details)
            if intent.get("status") == "ambiguous":
                raise _ambiguous_intent_commit_error(
                    {"reason": str(intent.get("ambiguity_reason", "authority_state_unresolved"))}
                )
            try:
                _assert_stored_intent_target(
                    workspace=workspace,
                    store=store,
                    intent=intent,
                )
            except ProjectApiError as exc:
                store.mark_ambiguous(intent_id, reason="authority_state_unresolved")
                raise _ambiguous_intent_commit_error(
                    {
                        "reason": "target_changed_without_decision_evidence",
                        "target_error": exc.code,
                    }
                ) from exc
            # No decision evidence exists and the exact authority target is
            # still pending. The previous process stopped before mutation, so
            # it is safe to restore the confirmed draft and try once now.
            intent = store.recover_unrecorded_confirmation(intent_id)

        _validate_stored_intent_target(workspace=workspace, store=store, intent=intent)
        try:
            confirming, already_committed = store.begin_confirm(intent_id)
        except IntentCommitError as exc:
            raise ProjectApiError("intent_confirmation_invalid", str(exc), 409) from exc
        if already_committed:
            return _committed_intent_response(confirming, idempotent=True)

        interpretation = dict(confirming.get("confirmed_interpretation") or {})
        instruction = str(confirming.get("draft_text", "") or "")
        target = dict(confirming.get("target") or {})
        manager_execution = dict(interpretation.get("manager_execution") or {})
        try:
            decision_result = decide_approval(
                workspace=workspace,
                project_key=key,
                approval=str(target.get("approval", "")),
                decision="changes_requested",
                note=instruction,
                decision_origin="manager_intent_commit",
                review_mode="intent_commit",
                review_session_id=intent_id,
                client_context={
                    "intent_commit_id": intent_id,
                    "interpretation_schema_version": str(
                        interpretation.get("schema_version", "")
                    ),
                    **(
                        {
                            "manager_job_id": str(manager_execution.get("job_id", "")),
                            "manager_adapter": str(manager_execution.get("adapter", "")),
                            "manager_model": str(manager_execution.get("model", "")),
                            "manager_reasoning_depth": str(
                                manager_execution.get("reasoning_depth", "")
                            ),
                        }
                        if manager_execution
                        else {}
                    ),
                },
                expected_run_id=str(
                    target.get("run_id", "")
                    or dict(confirming.get("mission") or {}).get("run_id", "")
                ),
                expected_target_type=str(target.get("target_type", "")),
                expected_target_id=str(target.get("target_id", "")),
                expected_artifact_key=str(target.get("artifact_key", "")),
                expected_artifact_version=str(target.get("artifact_version", "")),
            )
        except (ProjectApiError, ValueError) as exc:
            recovered = _reconcile_intent_decision_after_error(
                workspace=workspace,
                project_key=key,
                store=store,
                intent=confirming,
            )
            if recovered is not None:
                return recovered
            is_stale = isinstance(exc, ProjectApiError) and exc.code == "stale_approval"
            store.fail_confirm(intent_id, stale=is_stale or isinstance(exc, ValueError))
            if is_stale:
                raise _stale_intent_error(exc.details) from exc
            raise
        except Exception:
            recovered = _reconcile_intent_decision_after_error(
                workspace=workspace,
                project_key=key,
                store=store,
                intent=confirming,
            )
            if recovered is not None:
                return recovered
            store.fail_confirm(intent_id)
            raise

        committed = store.complete_commit(
            intent_id,
            decision="changes_requested",
            result={
                "run_id": str(decision_result.get("run_id", "")),
                "approval": str(target.get("approval", "")),
                "next_node": str(decision_result.get("next_node", "")),
            },
        )
        return _committed_intent_response(committed, idempotent=False)


def _validate_intent_binding(
    *,
    state: AgileDevState,
    workspace: Path,
    approval: str,
    expected_run_id: str,
    expected_target_type: str,
    expected_target_id: str,
    expected_artifact_key: str,
    expected_artifact_version: str,
    expected_task_id: str = "",
    expected_head_sha: str = "",
) -> dict[str, str]:
    expected = {
        "run_id": expected_run_id.strip(),
        "target_type": expected_target_type.strip(),
        "target_id": expected_target_id.strip(),
        "artifact_key": expected_artifact_key.strip(),
        "artifact_version": expected_artifact_version.strip(),
    }
    missing = [field for field, value in expected.items() if not value]
    if missing:
        raise ProjectApiError(
            "intent_binding_required",
            "Request changes requires the exact run, review target, artifact key, and artifact version from the current brief.",
            409,
            details={"missing": missing},
            recovery=["refresh_approval_brief"],
        )
    try:
        current = _validate_approval_target(
            state=state,
            workspace=workspace,
            approval=approval,
            expected_run_id=expected["run_id"],
            expected_target_type=expected["target_type"],
            expected_target_id=expected["target_id"],
            expected_artifact_key=expected["artifact_key"],
            expected_artifact_version=expected["artifact_version"],
        )
    except ProjectApiError as exc:
        if exc.code == "stale_approval":
            raise _stale_intent_error(exc.details) from exc
        raise
    return {
        "approval": approval.strip(),
        **current,
        "task_id": expected_task_id.strip(),
        "head_sha": expected_head_sha.strip(),
    }


def _load_current_request_changes_intent(
    *,
    workspace: Path,
    project_key: str,
    intent_id: str,
) -> tuple[IntentCommitStore, dict[str, object]]:
    key = normalize_project_key(project_key)
    store = IntentCommitStore(workspace, key)
    try:
        intent = store.get(intent_id)
    except IntentCommitError as exc:
        raise ProjectApiError("intent_commit_invalid", str(exc), 409) from exc
    if intent is None or intent.get("kind") != "request_changes":
        raise ProjectApiError("intent_commit_not_found", "This change discussion no longer exists.", 404)
    return store, intent


def _validate_stored_intent_target(
    *,
    workspace: Path,
    store: IntentCommitStore,
    intent: Mapping[str, object],
) -> None:
    try:
        _assert_stored_intent_target(
            workspace=workspace,
            store=store,
            intent=intent,
        )
    except ProjectApiError:
        store.mark_stale(str(intent.get("id", "")))
        raise


def _assert_stored_intent_target(
    *,
    workspace: Path,
    store: IntentCommitStore,
    intent: Mapping[str, object],
) -> None:
    """Check the stored binding without changing the Intent Commit status."""

    mission = dict(intent.get("mission") or {})
    target = dict(intent.get("target") or {})
    state = StateStore(workspace).read_project_current(store.project_key)
    if state is None or state.run_id != str(mission.get("run_id", "")):
        raise _stale_intent_error({"reason": "mission_changed"})
    try:
        _validate_approval_target(
            state=state,
            workspace=workspace,
            approval=str(target.get("approval", "")),
            expected_run_id=str(mission.get("run_id", "")),
            expected_target_type=str(target.get("target_type", "")),
            expected_target_id=str(target.get("target_id", "")),
            expected_artifact_key=str(target.get("artifact_key", "")),
            expected_artifact_version=str(target.get("artifact_version", "")),
        )
    except (ProjectApiError, ValueError) as exc:
        details = exc.details if isinstance(exc, ProjectApiError) else {"reason": str(exc)}
        raise _stale_intent_error(details) from exc


def _stale_intent_error(details: Mapping[str, object] | None = None) -> ProjectApiError:
    return ProjectApiError(
        "stale_intent_target",
        "The approval target changed during this discussion. Review the current brief and restart the change discussion.",
        409,
        details=dict(details or {}),
        recovery=["refresh_approval_brief", "restart_change_discussion"],
    )


def _committed_intent_response(
    intent: Mapping[str, object],
    *,
    idempotent: bool,
    reconciled: bool = False,
) -> dict[str, object]:
    response = {
        "intent_commit": dict(intent),
        "decision": "changes_requested",
        "committed": True,
        "idempotent": idempotent,
        **dict(intent.get("commit_result") or {}),
    }
    if reconciled:
        response["reconciled"] = True
    return response


def _complete_intent_from_decision_action(
    store: IntentCommitStore,
    intent_id: str,
    action: Mapping[str, object],
    *,
    idempotent: bool,
) -> dict[str, object]:
    committed = store.complete_commit(
        intent_id,
        decision="changes_requested",
        result={
            "run_id": str(action.get("run_id", "")),
            "approval": str(action.get("gate", "")),
            "next_node": str(action.get("after_node", "")),
        },
    )
    return _committed_intent_response(
        committed,
        idempotent=idempotent,
        reconciled=True,
    )


def _reconcile_intent_decision_after_error(
    *,
    workspace: Path,
    project_key: str,
    store: IntentCommitStore,
    intent: Mapping[str, object],
) -> dict[str, object] | None:
    evidence_state, action, details = _intent_decision_evidence(
        workspace,
        project_key,
        intent,
    )
    intent_id = str(intent.get("id", ""))
    if evidence_state == "matched" and action is not None:
        return _complete_intent_from_decision_action(
            store,
            intent_id,
            action,
            idempotent=False,
        )
    if evidence_state == "ambiguous":
        store.mark_ambiguous(intent_id, reason="decision_evidence_mismatch")
        raise _ambiguous_intent_commit_error(details)
    return None


def _intent_decision_evidence(
    workspace: Path,
    project_key: str,
    intent: Mapping[str, object],
) -> tuple[str, dict[str, object] | None, dict[str, object]]:
    """Classify durable authority evidence for one interrupted confirmation."""

    intent_id = str(intent.get("id", ""))
    candidates = [
        item
        for item in build_action_items(workspace, project_key=project_key)
        if item.get("action") == "approval_decided"
        and item.get("review_mode") == "intent_commit"
        and item.get("review_session_id") == intent_id
    ]
    if not candidates:
        return "none", None, {"reason": "decision_evidence_missing"}

    mission = dict(intent.get("mission") or {})
    target = dict(intent.get("target") or {})
    interpretation = dict(intent.get("confirmed_interpretation") or {})
    summary = str(interpretation.get("summary", "") or "").strip()

    def matches(action: Mapping[str, object]) -> bool:
        client_context = action.get("client_context")
        client_context = client_context if isinstance(client_context, Mapping) else {}
        return all(
            (
                action.get("decision") == "changes_requested",
                action.get("decision_origin") == "manager_intent_commit",
                action.get("run_id") == str(target.get("run_id", "") or mission.get("run_id", "")),
                action.get("gate") == str(target.get("approval", "")),
                action.get("artifact") == str(target.get("artifact_key", "")),
                action.get("artifact_version") == str(target.get("artifact_version", "")),
                action.get("note") == summary,
                client_context.get("intent_commit_id") == intent_id,
                client_context.get("interpretation_schema_version")
                == str(interpretation.get("schema_version", "")),
            )
        )

    matched = [item for item in candidates if matches(item)]
    if len(candidates) == 1 and len(matched) == 1:
        return "matched", matched[0], {"reason": "decision_evidence_matched"}
    return (
        "ambiguous",
        None,
        {
            "reason": "decision_evidence_mismatch",
            "evidence_count": len(candidates),
            "matching_evidence_count": len(matched),
        },
    )


def _ambiguous_intent_commit_error(
    details: Mapping[str, object] | None = None,
) -> ProjectApiError:
    return ProjectApiError(
        "intent_commit_ambiguous",
        "GoodTeam could not prove whether this Intent Commit reached the approval authority. It will not replay the decision.",
        409,
        details=dict(details or {}),
        recovery=["refresh_change_discussion"],
    )


def recover_legacy_takeover_review(
    *,
    workspace: Path,
    project_key: str,
    note: str = "",
) -> dict[str, object]:
    """Turn one persisted pre-brief Takeover revision into a normal review.

    Old Takeover runs could persist a machine-oriented context packet as the
    approval artifact after ``changes_requested``.  Recovery is deliberately
    explicit and limited to that exact legacy state; reads never mutate it.
    """

    key = normalize_project_key(project_key)
    state_store = StateStore(workspace)
    state = state_store.read_project_current(key)
    if state is None:
        raise ValueError(f"No active run for project: {key}")
    if not _is_legacy_takeover_change_request(state, "takeover"):
        raise ValueError("The current Takeover review does not need recovery.")

    revision_note = note.strip() or _latest_takeover_change_request_note(workspace, state)
    if not revision_note:
        raise ValueError("Describe the requested Takeover changes before regenerating the brief.")

    project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config is None or not project_config.is_file():
        try:
            project_config = _project_config_for_key(workspace, key)
        except ValueError:
            project_config = None
    if project_config is None or not project_config.is_file():
        raise ValueError("The configured project source is unavailable, so GoodTeam cannot regenerate the Takeover Brief.")

    before_node = state.next_node
    state.change_requests["takeover"] = revision_note
    state.approvals["takeover"] = ApprovalDecision.PENDING
    state = run_adoption_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="takeover_recovery_reopened",
        project_key=key,
        gate="takeover",
        before_node=before_node,
        after_node=state.next_node,
        artifact="takeover",
        artifact_path=state.artifacts.get("takeover", ""),
        note=revision_note,
    )
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)
    notify_current_approval_gate(state, workspace=workspace)
    payload = asdict(state)
    payload["recovered"] = True
    return payload


def regenerate_rfc_review_gate(
    *,
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    if should_generate_artifact_with_worker(project_config, "architect"):
        return run_architecture_until_gate(state=state, workspace=workspace, project_config=project_config)
    invalidate_artifact(state, "rfc")
    rfc_path = ArtifactStore(root=workspace, mirror_root=artifact_mirror_root(project_config, state),
                             revision_tokens=state.artifact_generations).write_markdown(
        state.run_id,
        "rfc",
        "rfc.md",
        render_downstream_artifact_markdown("rfc", state, project_config),
    )
    register_markdown_artifact(state, "rfc", rfc_path)
    state.current_node = "rfc_review_gate"
    state.next_node = gate_next_node(state, gate="rfc", approved_next="planner")
    sync_project_stage_tasks(state, project_config)
    return state


def _workflow_retry_context(workspace, state):
    from goodteam_os.workflow_revisions import project_config_for_state

    config = project_config_for_state(state, workspace)
    target = load_project_target(config) if config is not None else {}
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    jobs = WorkerJobStore(root).list_jobs() if root is not None else []
    context = {"config": str(config) if config else "", "root": str(root) if root else "",
               "config_digest": hashlib.sha256(config.read_bytes()).hexdigest() if config else ""}
    return config, jobs, context


def workflow_retry_target(workspace, state):
    from goodteam_os.workflow_retry import build_retry_target

    if state is None:
        return None
    _config, jobs, context = _workflow_retry_context(workspace, state)
    return build_retry_target(state, jobs=jobs, actions=build_action_items(workspace, state.project_key),
                              execution_context=context)


def retry_blocked_workflow(*, workspace: Path, project_key: str,
                           expected_run_id: str | None = None,
                           expected_retry_version: str | None = None) -> dict[str, object]:
    from goodteam_os.workflow_retry import build_retry_target

    key = normalize_project_key(project_key)
    if not isinstance(expected_run_id, str) or not expected_run_id.strip() or not isinstance(expected_retry_version, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_retry_version):
        raise ProjectApiError("retry_binding_required", "Refresh and select the exact task attempt to retry.", 400,
                              recovery=["refresh"])
    with workflow_guard(workspace, key):
        state = StateStore(workspace).read_project_current(key)
        # Binding failures must not enter the recovery exception handler: it
        # would interrupt the replacement run's still-active transition.
        if state is None or state.run_id != expected_run_id or state.project_key != key:
            raise ProjectApiError("run_binding_mismatch", "The active task changed. Refresh before retrying.", 409,
                                  recovery=["refresh"])
        config, jobs, context = _workflow_retry_context(workspace, state)
        target = build_retry_target(state, jobs=jobs, actions=build_action_items(workspace, key),
                                    execution_context=context)
        if target["version"] != expected_retry_version:
            raise ProjectApiError("retry_target_changed", "The task stage or attempt changed. Refresh before retrying.", 409,
                                  recovery=["refresh"])
        if not target["available"]:
            code = {"active_attempt": "retry_attempt_active", "attempt_unknown": "retry_attempt_unknown"}.get(target["reason"], "retry_not_available")
            raise ProjectApiError(code, "This task cannot be retried from the displayed attempt. Refresh to inspect its current state.", 409,
                                  recovery=["refresh"])
        append_action(workspace, run_id=state.run_id, project_key=key, actor="human",
                      action="blocked_workflow_retry_started", before_node=state.next_node,
                      workflow_operation_id=uuid4().hex,
                      client_context={"expected_retry_version": expected_retry_version, "retry_mode": target["mode"]})
        try:
            return _retry_bound_workflow(workspace=workspace, key=key, state=state, project_config=config)
        except Exception:
            interrupt_transition(workspace, key)
            raise


def _retry_bound_workflow(*, workspace, key, state, project_config):
    state_store = StateStore(workspace)
    if state.pending_transition.get("status") in {"recording", "executing", "interrupted"}:
        from goodteam_os.workflow_revisions import resume_transition

        return resume_transition(state, workspace)
    if state.next_node.startswith("revise_"):
        from goodteam_os.workflow_revisions import retry_legacy_revision

        return retry_legacy_revision(state, workspace)
    if state.load_profile == "fix" and state.next_node == "blocked":
        from goodteam_os.workflow_revisions import retry_fix_workflow

        return retry_fix_workflow(state, workspace)
    retryable_implementation = state.next_node == "blocked" or (
        state.next_node == "failure_triage" and bool(state.blockers)
    )
    if state.current_node != "implementation" or not retryable_implementation:
        raise ValueError(f"Current run is not blocked at implementation: {key}")

    before_node = state.next_node
    state.role_models = refresh_state_role_models(state, project_config)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)

    generated_before = set(state.artifacts)
    staging_retry = any(blocker.startswith("staging ") for blocker in state.blockers)
    state.blockers = []
    if staging_retry:
        from goodteam_os.workflow_workspace import collect_workflow_workspace, workspace_for_workflow

        execution_root = None
        repository_candidate = {}
        if state.repository_baseline:
            source = Path(str(state.repository_baseline.get("source_root", "")))
            execution_root = workspace_for_workflow(source, state.repository_baseline,
                                                     project_key=state.project_key, run_id=state.run_id)
            repository_candidate = collect_workflow_workspace(source, state.repository_baseline,
                                                               project_key=state.project_key, run_id=state.run_id)
        staging_result = run_staging_backend(project_config=project_config, execution_root=execution_root,
                                             repository_candidate=repository_candidate)
        from goodteam_os.graph import render_staging_report_markdown
        from goodteam_os.artifacts import ArtifactStore

        invalidate_artifact(state, "staging_report")
        store = ArtifactStore(root=workspace, mirror_root=artifact_mirror_root(project_config, state),
                              revision_tokens=state.artifact_generations)
        staging_path = store.write_markdown(
            state.run_id,
            "staging_report",
            "staging-report.md",
            render_staging_report_markdown(state, project_config, staging_result),
        )
        register_markdown_artifact(state, "staging_report", staging_path, reviewable=True)
        if str(staging_result.get("status", "")) != "passed":
            failed_step = str(staging_result.get("failed_step", "") or "deploy")
            state.current_node = "implementation"
            state.next_node = "blocked"
            evidence_gap = str(staging_result.get("evidence_gap", "") or "")
            if failed_step == "acceptance" and evidence_gap:
                state.blockers = [f"staging acceptance failed: {evidence_gap}"]
            else:
                state.blockers = [f"staging {failed_step} failed"]
        else:
            state.current_node = "staging_review_gate"
            state.next_node = "await_staging_approval"
    else:
        state.current_node = "rfc_review_gate"
        state.next_node = "planner"
        state = advance_approved_workflow(state=state, workspace=workspace, project_config=project_config)
    sync_project_stage_tasks(state, project_config)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)

    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="blocked_workflow_retried",
        project_key=key,
        before_node=before_node,
        after_node=state.next_node,
    )
    for artifact in sorted(set(state.artifacts) - generated_before):
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{artifact}_generated",
            project_key=key,
            before_node=before_node,
            after_node=state.next_node,
            artifact=artifact,
            artifact_path=state.artifacts.get(artifact, ""),
        )

    notify_current_approval_gate(state, workspace=workspace)
    payload = asdict(state)
    payload["retried"] = True
    return payload


def refresh_state_role_models(state: AgileDevState, project_config: Path | None) -> dict[str, dict[str, str]]:
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    try:
        _profile, role_models = resolve_project_model_profile(
            Path("models.yaml"),
            target,
            explicit_profile=state.model_profile,
            role_overrides=[],
        )
    except Exception:
        return state.role_models
    return role_models


def terminate_project_run(
    *, workspace: Path, project_key: str, expected_run_id: str | None = None,
) -> dict[str, object]:
    key = normalize_project_key(project_key)
    if expected_run_id is not None and (not isinstance(expected_run_id, str) or not expected_run_id.strip()):
        raise ProjectApiError("run_binding_required", "Refresh and select the task to archive.", 400)
    with workflow_guard(workspace, key):
        state_store = StateStore(workspace)
        state = state_store.read_project_current(key)
        # Reject before recovery or writes: an old tab has no authority over a
        # replacement run, including its interrupted transition evidence.
        if expected_run_id is not None and (state is None or state.run_id != expected_run_id):
            raise ProjectApiError(
                "run_binding_mismatch", "The active task changed. Refresh before archiving it.", 409,
                details={"expected_run_id": expected_run_id, "current_run_id": state.run_id if state else ""},
                recovery=["refresh"],
            )
        if state is None:
            raise ValueError(f"No active run for project: {key}")
        before_node = state.next_node
        try:
            archive_path = state_store.archive_project_current(key, reason="cancelled")
            append_action(
                workspace,
                run_id=state.run_id,
                actor="human",
                action="run_terminated",
                project_key=key,
                before_node=before_node,
                after_node="cancelled",
            )
        except Exception:
            interrupt_transition(workspace, key)
            raise
        return {
            "terminated": True,
            "project_key": key,
            "run_id": state.run_id,
            "archive_path": str(archive_path),
        }


def delete_project(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    config_path = _project_config_for_key(workspace, key)
    projects_dir = (workspace / "projects").resolve()
    resolved_config = config_path.resolve()
    if projects_dir not in resolved_config.parents or not resolved_config.name.endswith(".local.yaml"):
        raise ValueError("Project deletion is limited to local project configs under workspace/projects.")

    state_store = StateStore(workspace)
    state_projects_dir = (workspace / "state" / "projects").resolve()
    state_dir = state_store.project_state_dir(key).resolve()
    if state_projects_dir not in state_dir.parents:
        raise ValueError("Project deletion is limited to project-scoped GoodTeam state.")

    resolved_config.unlink()
    if state_dir.exists():
        shutil.rmtree(state_dir)

    return {
        "deleted": True,
        "project_key": key,
        "project_config": str(config_path),
        "state_dir": str(state_store.project_state_dir(key)),
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



def build_role_observations(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is None:
        return []

    observations = []
    for role in list_role_definitions():
        artifact = role["artifact"]
        model_config = state.role_models.get(role["key"], {})
        status = "No activity"
        if artifact == "state":
            status = "Active"
        elif artifact in state.artifacts:
            status = "Artifact ready"
        if artifact in state.approvals and state.next_node == f"await_{artifact}_approval":
            status = "Waiting for approval"
        if role["key"] == state.current_node:
            status = "Active"
        if role["key"] == state.next_node:
            status = "Ready"

        observations.append(
            {
                "name": role["name"],
                "artifact": artifact,
                "status": status,
                "description": role["description"],
                "artifact_path": state.artifacts.get(artifact, ""),
                "model": model_config.get("primary", "unassigned"),
                "mode": model_config.get("mode", "default"),
            }
        )
    return observations


def build_workflow_steps(state: AgileDevState | None) -> list[dict[str, str]]:
    steps = []
    for index, step in enumerate(workflow_steps_for_state(state), start=1):
        artifact = step.get("artifact", "")
        approval = step.get("approval", "")
        status = "not-started"
        status_label = "Not started"
        action_label = ""
        feedback = "No work recorded yet."

        if state is not None:
            approval_value = str(state.approvals.get(str(approval), "")) if approval else ""
            if artifact and artifact in state.artifacts:
                status = "done"
                status_label = "Done"
                feedback = "Artifact is available."
            if approval and approval_value == "pending" and state.next_node == f"await_{approval}_approval":
                status = "review-needed"
                status_label = "Needs review"
                action_label = f"Review {approval.upper()}"
                feedback = "Human review is required before the workflow can continue."
            elif approval and approval_value == "approved":
                status = "done"
                status_label = "Approved"
                feedback = "Approval completed."
            elif approval and approval_value in {"changes_requested", "rejected"}:
                status = "blocked"
                status_label = approval_value.replace("_", " ").title()
                feedback = "Workflow is blocked by this decision."
            elif step.get("node") == state.current_node:
                status = "active"
                status_label = "Active"
                feedback = "Worker is marked as active in current state."
            elif step.get("node") == state.next_node:
                status = "ready"
                status_label = "Ready"
                feedback = "Ready to run; no worker execution or artifact has been recorded yet."

        steps.append(
            {
                "number": str(index),
                "key": str(step["key"]),
                "title": str(step["title"]),
                "roles": str(step["roles"]),
                "artifact": str(artifact),
                "approval": str(approval),
                "summary": str(step["summary"]),
                "feedback": feedback,
                "status": status,
                "status_label": status_label,
                "action_label": action_label,
                "artifact_path": state.artifacts.get(str(artifact), "") if state is not None and artifact else "",
            }
        )
    return steps


def _review_artifact_snapshot(path: str, max_chars: int = ARTIFACT_PREVIEW_MAX_CHARS) -> tuple[str, str, bool]:
    artifact = Path(path).expanduser() if path else None
    if artifact is None or not artifact.is_file():
        return "", "missing", False
    try:
        raw_content = artifact.read_bytes()
    except OSError:
        return "", "missing", False
    content = raw_content.decode("utf-8")
    truncated = len(content) > max_chars
    preview = content[:max_chars] + "\n\n[Preview truncated]" if truncated else content
    return preview, f"sha256:{hashlib.sha256(raw_content).hexdigest()}", truncated


def _resolve_registered_artifact(workspace: Path, artifact_key: str, project_key: str = "") -> tuple[AgileDevState, Path]:
    store = StateStore(workspace)
    state = store.read_project_current(project_key) if project_key else store.read_current()
    if state is None:
        raise ValueError("No active run state found.")
    resource = state.resources.get(artifact_key)
    if resource is not None:
        if resource.kind not in {ResourceKind.MARKDOWN_ARTIFACT, ResourceKind.PROPOSED_MARKDOWN_ARTIFACT}:
            raise ValueError(f"Resource is not a markdown artifact: {artifact_key}")
        if ResourceCapability.READ not in resource.capabilities:
            raise ValueError(f"Resource does not allow read: {artifact_key}")
        artifact_path = resource.path
    else:
        artifact_path = state.artifacts.get(artifact_key, "")
    if not artifact_path:
        raise ValueError(f"Artifact is not registered in current run: {artifact_key}")

    path = Path(artifact_path)
    run_root = (workspace / "artifacts" / "runs" / state.run_id).resolve()
    if path.is_absolute():
        path = path.resolve()
    else:
        candidates = [(workspace / path).resolve(), path.resolve()]
        path = next(
            (
                candidate
                for candidate in candidates
                if run_root in [candidate, *candidate.parents]
            ),
            candidates[0],
        )
    if resource is None or resource.scope == ResourceScope.RUN:
        if run_root not in [path, *path.parents]:
            raise ValueError("Artifact path must stay inside the current run artifacts directory.")
    elif resource.scope != ResourceScope.PROJECT_RUNTIME:
        raise ValueError("Artifact path must stay inside the current run artifacts directory.")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("Only markdown artifacts can be edited in the dashboard.")
    if not path.is_file():
        raise ValueError(f"Artifact file does not exist: {artifact_key}")
    return state, path


def build_resource_items(state: AgileDevState | None) -> list[dict[str, object]]:
    if state is None:
        return []
    return [
        resource_to_dict(resource)
        for _key, resource in sorted(state.resources.items())
    ]


def read_artifact(*, workspace: Path, artifact_key: str, project_key: str = "") -> dict[str, object]:
    state, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    if artifact_key == "source_preview":
        from goodteam_os.delivery_outcomes import _artifact_bytes

        raw_content = _artifact_bytes(workspace, state, artifact_key)
    else:
        raw_content = path.read_bytes()
    content = raw_content.decode("utf-8")
    return {
        **({"proposal_binding": dict(state.artifact_proposals[artifact_key.removesuffix("_proposed")])}
           if artifact_key.endswith("_proposed") and artifact_key.removesuffix("_proposed") in state.artifact_proposals else {}),
        "artifact": artifact_key,
        "path": str(path),
        "content": content,
        "run_id": state.run_id,
        "artifact_version": f"sha256:{hashlib.sha256(raw_content).hexdigest()}",
        "resource": resource_to_dict(state.resources[artifact_key]) if artifact_key in state.resources else {},
    }


def read_open_questions(*, workspace: Path, artifact_key: str, project_key: str = "") -> dict[str, object]:
    artifact = read_artifact(workspace=workspace, artifact_key=artifact_key, project_key=project_key)
    return {
        "artifact": artifact_key,
        "questions": extract_open_questions(artifact["content"]),
    }


def answer_open_questions(
    *,
    workspace: Path,
    project_key: str,
    artifact_key: str,
    answers: list[object],
    expected_run_id: str = "", expected_artifact_version: str = "",
) -> dict[str, object]:
    artifact = read_artifact(workspace=workspace, artifact_key=artifact_key, project_key=project_key)
    if (not expected_run_id or not expected_artifact_version or expected_run_id != artifact["run_id"]
            or expected_artifact_version != artifact["artifact_version"]):
        raise ProjectApiError("stale_artifact", "Reload the artifact before answering its questions.", 409)
    guidance_parts: list[str] = []
    for raw_answer in answers:
        if not isinstance(raw_answer, dict):
            continue
        answer = str(raw_answer.get("answer", "")).strip()
        if not answer:
            continue
        question = str(raw_answer.get("question", "")).strip()
        guidance_parts.append(f"Q: {question}\nA: {answer}\n\n")
    return refine_artifact(
        workspace=workspace,
        project_key=project_key,
        artifact_key=artifact_key,
        content=artifact["content"],
        guidance="".join(guidance_parts),
        expected_run_id=artifact["run_id"],
        expected_artifact_version=artifact["artifact_version"],
    )


@serialized_workflow
def save_artifact(*, workspace: Path, artifact_key: str, content: str, project_key: str = "",
                  expected_run_id: str = "", expected_artifact_version: str = "") -> dict[str, object]:
    # Enforce this for older persisted registries that still advertise EDIT too.
    if artifact_key == "source_preview":
        raise ProjectApiError("read_only_artifact", "Retained source evidence is read-only.", 403)
    state, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    if (expected_run_id and expected_run_id != state.run_id) or (
        expected_artifact_version and expected_artifact_version != artifact_version_for_path(str(path))
    ):
        raise ProjectApiError("stale_artifact", "This artifact changed. Reload it before saving your edits.", 409)
    if path.read_text(encoding="utf-8") != content:
        from goodteam_os.artifacts import ArtifactStore
        from goodteam_os.graph import _register_written_markdown

        invalidate_artifact(state, artifact_key, rewind=True)
        path = ArtifactStore(workspace, revision_tokens=state.artifact_generations).write_markdown(
            state.run_id, artifact_key, path.name, content)
        _register_written_markdown(state, artifact_key, path)
        store = StateStore(workspace)
        if project_key:
            store.write_project_current(state)
            store.write_project_checkpoint(state)
        else:
            store.write_current(state)
            store.write_checkpoint(state)

    key = normalize_project_key(project_key or state.project_key)
    project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config is None or not project_config.is_file():
        try:
            project_config = _project_config_for_key(workspace, key)
        except ValueError:
            project_config = None
    sync_project_stage_tasks(state, project_config)
    edit_log = StateStore(workspace).project_state_dir(key) / "edit-log.jsonl" if project_key else workspace / "state" / "edit-log.jsonl"
    edit_log.parent.mkdir(parents=True, exist_ok=True)
    edit_event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": state.run_id,
        "artifact": artifact_key,
        "path": str(path),
        "bytes": len(content.encode("utf-8")),
    }
    with edit_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(edit_event) + "\n")

    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="artifact_saved",
        project_key=key if project_key else "",
        before_node=state.next_node,
        after_node=state.next_node,
        artifact=artifact_key,
        artifact_path=str(path),
    )

    return {
        "artifact": artifact_key,
        "path": str(path),
        "bytes": edit_event["bytes"],
        "saved": True,
    }


def _approval_review_title(state: AgileDevState, artifact: str) -> str:
    resource = state.resources.get(artifact)
    if isinstance(resource, Mapping):
        title = str(resource.get("title", "") or "").strip()
    else:
        title = str(getattr(resource, "title", "") or "").strip()
    if title:
        return title
    try:
        contract = artifact_contract(artifact)
    except ValueError:
        return artifact.replace("_", " ").title()
    return str(contract.get("title", "") or "").strip() or artifact.replace("_", " ").title()


def _is_legacy_takeover_change_request(state: AgileDevState, approval: str) -> bool:
    return (
        approval == "takeover"
        and str(state.approvals.get("takeover", "")) == ApprovalDecision.CHANGES_REQUESTED
        and state.current_node == "takeover_review_gate"
        and state.next_node == "revise_takeover"
    )


def _latest_takeover_change_request_note(workspace: Path, state: AgileDevState) -> str:
    for action in build_action_items(workspace, project_key=state.project_key):
        if (
            action.get("run_id") == state.run_id
            and action.get("action") == "approval_decided"
            and action.get("gate") == "takeover"
            and action.get("decision") == ApprovalDecision.CHANGES_REQUESTED
        ):
            return str(action.get("note", "") or "").strip()
    return ""


def build_approval_reviews(state: AgileDevState | None, *, workspace: Path = Path(".")) -> list[dict[str, object]]:
    if state is None:
        return []

    reviews = []
    profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield")
    relevant_gates = None if profile.name == "greenfield" else set(profile.gate_names())
    reviewer_target = _reviewer_target_for_state(workspace=workspace, project_key=state.project_key)
    reviewer_statuses = _reviewer_statuses_for_state(state=state, workspace=workspace, project_key=state.project_key)
    from goodteam_os.legacy_reviewer_reports import build_legacy_reviewer_reports

    reviewer_reports = build_legacy_reviewer_reports(state, _project_runtime_roots_for_state(
        state=state, workspace=workspace, project_key=state.project_key))
    for approval, decision in state.approvals.items():
        if relevant_gates is not None and approval not in relevant_gates:
            continue
        artifact = _approval_artifact_for_gate(approval)
        artifact_path = state.artifacts.get(artifact, "")
        preview, artifact_version, truncated = _review_artifact_snapshot(artifact_path)
        legacy_takeover_recovery = _is_legacy_takeover_change_request(state, approval)
        review = {
            "approval": approval,
            "title": _approval_review_title(state, artifact),
            "decision": str(decision),
            "is_pending": str(decision) == ApprovalDecision.PENDING,
            "is_actionable": str(decision) == ApprovalDecision.PENDING and state.next_node == f"await_{approval}_approval",
            "is_current": state.current_node == f"{approval}_review_gate",
            "artifact": artifact,
            "artifact_path": artifact_path,
            "artifact_version": artifact_version,
            "content": "" if legacy_takeover_recovery else preview,
            "content_truncated": truncated and not legacy_takeover_recovery,
            "presentation": "legacy_takeover_context" if legacy_takeover_recovery else "review_artifact",
            "recovery": {
                "available": legacy_takeover_recovery,
                "action": "regenerate_takeover_brief" if legacy_takeover_recovery else "",
                "note": _latest_takeover_change_request_note(workspace, state) if legacy_takeover_recovery else "",
            },
            "review_roles": PRD_REVIEW_ROLES if approval == "prd" else [],
        }
        current_reviewer_statuses = reviewer_statuses_for_artifact(
            reviewer_statuses, artifact_key=artifact, artifact_path=artifact_path,
            artifact_version=artifact_version, require_exact=True,
        )
        reviewer_profile = reviewer_profile_for_approval(reviewer_target, approval)
        reviewer_status = latest_reviewer_status_for_approval(current_reviewer_statuses, approval)
        review["reviewer_provenance"] = build_reviewer_provenance(reviewer_status, profile=reviewer_profile)
        review["review_evidence_policy"] = evaluate_review_evidence_policy(
            review["reviewer_provenance"],
            gate=approval,
            config=_review_evidence_policy_config(reviewer_target),
        )
        review["multi_reviewer_projection"] = build_multi_reviewer_projection(
            approval=approval,
            reviewer_target=reviewer_target,
            reviewer_statuses=current_reviewer_statuses,
            review_evidence_policy_config=_review_evidence_policy_config(reviewer_target),
            artifact_key=artifact,
            artifact_path=artifact_path,
            artifact_version=artifact_version,
        )
        from goodteam_os.gate_report_projection import project_gate_reports

        review["reviewer_reports"] = project_gate_reports(
            [entry["status"] for entry in review["multi_reviewer_projection"]["reviewers"]
             if isinstance(entry.get("status"), dict)],
            roots=_project_runtime_roots_for_state(state=state, workspace=workspace, project_key=state.project_key),
            run_id=state.run_id, project_key=state.project_key,
        )
        review["queued_reviewer_jobs"] = select_queued_reviewer_jobs_for_gate(
            reviewer_statuses,
            run_id=state.run_id,
            approval=approval,
            artifact_key=artifact,
            artifact_path=artifact_path,
            artifact_version=artifact_version,
            profile_order=_reviewer_profile_order_for_gate(reviewer_target, approval),
        )
        review["legacy_reviewer_jobs"] = [item for item in reviewer_reports if item["approval"] == approval]
        review["failed_reviewer_jobs"] = select_failed_reviewer_jobs_for_gate(
            reviewer_statuses,
            run_id=state.run_id,
            approval=approval,
            artifact_key=artifact,
            artifact_path=artifact_path,
            artifact_version=artifact_version,
            profile_order=_reviewer_profile_order_for_gate(reviewer_target, approval),
        )
        advisors = advisors_for_gate(approval)
        if advisors:
            messages = [
                {
                    "id": str(message.get("id", "")),
                    "from_role": str(message.get("from_role", "")),
                    "to": list(message.get("to", [])) if isinstance(message.get("to", []), list) else [],
                    "kind": str(message.get("kind", "")),
                    "gate": str(message.get("gate", "")),
                    "body": str(message.get("body", "")),
                    "in_reply_to": str(message.get("in_reply_to", "")),
                }
                for message in read_messages(workspace, state.project_key, state.run_id, gate=approval)
            ]
            review["committee"] = {
                "advisors": list(advisors),
                "convened": bool(messages),
                "messages": messages,
            }
        if review["is_actionable"]:
            evidence_snapshot = build_approval_evidence_snapshot(
                state=state,
                workspace=workspace,
                project_key=state.project_key,
            )
            evidence_snapshot["reviewer_reports"] = review["reviewer_reports"]
            review["evidence_snapshot"] = evidence_snapshot
            review["recommendation"] = build_approval_recommendation(
                state=state,
                review=review,
                evidence_snapshot=evidence_snapshot,
                workspace=workspace,
            )
        reviews.append(review)
    return reviews


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
        "latest_archived_run": _latest_archived_run_summary(workspace, str(project.get("key", "GTO") or "GTO")),
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
            "active_run_count": sum(1 for item in overview if item.get("status") == "active"),
            "server_actions_enabled": os.getenv("GOODTEAM_SERVER_ACTIONS", "0") == "1",
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
    paths.extend(_project_configs_from_state_sources(workspace))
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


def _project_configs_from_state_sources(workspace: Path) -> list[Path]:
    state_dirs = [
        workspace / "state" / "projects",
        workspace.parent / "state" / "projects",
    ]
    goodteam_home = os.getenv("GOODTEAM_HOME")
    if goodteam_home:
        state_dirs.append(Path(goodteam_home).expanduser() / "state" / "projects")
    else:
        state_dirs.append(Path.home() / ".goodteam" / "state" / "projects")
    store = StateStore(workspace)
    paths: list[Path] = []
    seen_keys: set[str] = set()
    for state_projects_dir in state_dirs:
        if not state_projects_dir.is_dir():
            continue
        for current_path in state_projects_dir.glob("*/current.json"):
            project_name = current_path.parent.name
            if project_name in seen_keys:
                continue
            seen_keys.add(project_name)
            state = store.read_project_current(project_name)
            if state is None:
                continue
            source_ref = str(state.source_ref or "").strip()
            if not source_ref or source_ref == "manual":
                continue
            source_path = Path(source_ref).expanduser().resolve()
            if source_path.is_file():
                paths.append(source_path)
    return paths


def build_project_overview_items(workspace: Path, model_config: Path = Path("models.yaml")) -> list[dict[str, str]]:
    items = []
    store = StateStore(workspace)
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        project_key = normalize_project_key(str(target.get("key", config_path.stem)))
        state = store.read_project_current(project_key)
        action_items = build_action_items(workspace, project_key=project_key)
        items.append(
            {
                "project_name": str(target.get("name", project_key) or project_key),
                "project_key": project_key,
                "project_config": str(config_path),
                "status": "active" if is_active_run(state) else "idle",
                "run_id": state.run_id if state is not None else "",
                "next_node": state.next_node if state is not None else "",
                "model_profile": str(target.get("model_profile", "balanced") or "balanced"),
                "delivery_mode": normalize_delivery_mode(str(target.get("delivery_mode", "init_project") or "init_project")),
                "last_action": action_items[0].get("timestamp", "") if action_items else "",
            }
        )
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
    state = StateStore(workspace).read_project_current(key)
    worker_preflight = check_project_target(target).get("worker_backend_preflight", {})
    worker_preflight = dict(worker_preflight) if isinstance(worker_preflight, Mapping) else {}
    active_run = (
        {
            "run_id": state.run_id,
            "idea": state.idea,
            "current_node": state.current_node,
            "next_node": state.next_node,
        }
        if is_active_run(state)
        else None
    )
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
            "active_run": active_run,
            "project_goal": state.idea if state is not None else str(readiness.get("inputs", {}).get("goal", "")),
            "blockers": list(state.blockers) if state is not None else [],
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
            "workflow_entry_url": (
                f"/projects/{quote(key, safe='')}/work"
                if build_capabilities(readiness)["start_workflow"]
                else ""
            ),
        },
        "next_action": pending
        or {"id": "shared_readiness_complete", "status": "complete"},
    }


def _project_attention_snapshot(workspace, key, root, readiness, state):
    from goodteam_os.project_attention import build_project_attention

    # Do not call build_project_work_state or TaskAssistantService.session here:
    # those routes may initialize/migrate Team/session state. A cross-project
    # scan must never dispatch, reconcile or write another project's records.
    session = TaskAssistantSessionStore(workspace, key).read()
    if session.get("project_key") != key or (state is not None and state.project_key != key):
        raise ValueError("Attention source belongs to another project")
    workflow = None
    if state is not None and not isinstance(state.pending_transition, Mapping):
        raise ValueError("Workflow transition is invalid")
    if state is not None and state.pending_transition.get("status") in {"recording", "executing"}:
        # Work may reconcile this transition when entered. The overview cannot
        # trigger recovery or claim its pre-transition gate is still current.
        raise ValueError("Workflow transition is still in progress")
    if is_active_run(state):
        state = _dashboard_state_view(state)
        board = build_agile_board(state)
        tasks = [task for column in board.get("columns", {}).values() for task in column]
        workflow = build_team_workspace(
            state=state, workflow_active=True, project_root=root, project_key=key,
            tasks=tasks,
            worker_jobs=WorkerJobStore(root).list_jobs() if root is not None else [],
            approval_reviews=build_approval_reviews(state, workspace=workspace),
            intent_commits=IntentCommitStore(workspace, key).list_intents(),
        )
    return build_project_attention(project_key=key, readiness=readiness, workflow=workflow, session=session)


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
    state_store = StateStore(workspace)
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
        state = state_store.read_project_current(key)
        active_run = (
            {"run_id": state.run_id, "next_node": state.next_node}
            if is_active_run(state)
            else None
        )
        steps = readiness.get("steps", [])
        attention_count = sum(
            1
            for step in steps
            if isinstance(step, dict) and step.get("status") in {"needs_input", "failed"}
        )
        try:
            attention = _project_attention_snapshot(workspace, key, root, readiness, state)
        except (ValueError, OSError, TypeError, KeyError):
            # Unknown is not an empty queue. Keep ordinary project navigation
            # available if an optional attention source cannot be read.
            attention = None
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
                "active_run": active_run,
            }
        )
    return {"projects": projects}


def handle_project_api_get(
    workspace: Path,
    path: str,
    *, codex_review_admission: "CodexReviewAdmission | None" = None,
) -> tuple[int, dict[str, object]] | None:
    from goodteam_os.skills.review_api import handle_review_get
    from goodteam_os.task_input_context import handle_task_input_get

    task_input_response = handle_task_input_get(workspace, path)
    if task_input_response is not None:
        return task_input_response
    from goodteam_os.skills.codex_runtime import review_worker_kwargs
    review_response = handle_review_get(workspace, path, **review_worker_kwargs(codex_review_admission))
    if review_response is not None:
        return review_response
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
    runs_match = re.fullmatch(r"/api/projects/([^/]+)/runs", path)
    if runs_match:
        key = _decode_project_api_key(runs_match.group(1))
        _project_config_for_key(workspace, key)
        return 200, build_project_run_history(workspace, key)
    run_match = re.fullmatch(r"/api/projects/([^/]+)/runs/([^/]+)", path)
    if run_match:
        key = _decode_project_api_key(run_match.group(1))
        _project_config_for_key(workspace, key)
        run_id = _decode_project_run_id(run_match.group(2))
        return 200, build_project_run_detail(workspace, key, run_id)
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
) -> tuple[int, dict[str, object]] | None:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs
    from goodteam_os.skills.review_api import handle_review_post

    review_response = handle_review_post(workspace, path, request, **review_worker_kwargs(codex_review_admission))
    if review_response is not None:
        return review_response
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
    task_assistant_message_match = re.fullmatch(
        r"/api/projects/([^/]+)/task-assistant/messages",
        path,
    )
    if task_assistant_message_match:
        key = _decode_project_api_key(task_assistant_message_match.group(1))
        key, _target, root = _project_target_and_root(workspace, key)
        project_config = _project_config_for_key(workspace, key)
        try:
            return 200, TaskAssistantService(
                workspace,
                project_config=project_config,
            ).send_message(
                project_key=key,
                root=root,
                message_id=str(request.get("message_id", "")),
                text=str(request.get("text", "")),
                context_run_id=request.get("context_run_id", ""),
            )
        except ManagerExecutionError as exc:
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
        if action in {"retry_message", "check_runtime"}:
            service = TaskAssistantService(workspace, project_config=_project_config_for_key(workspace, key))
            try:
                if action == "check_runtime":
                    if request.get("confirmed") is not True:
                        raise ValueError("Confirm the potentially billable runtime check first.")
                    return 200, service.check_runtime(project_key=key, root=root, message_id=str(request.get("message_id", "")))
                return 200, service.retry_message(
                    project_key=key, root=root, message_id=str(request.get("message_id", "")),
                    expected_attempt=request.get("expected_attempt"),
                )
            except ManagerExecutionError as exc:
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
        store = TaskAssistantSessionStore(workspace, key)
        executor = ProjectReadinessExecutor(workspace)
        proposal_id = str(request.get("proposal_id", ""))
        if action == "confirm_task":
            service = TaskAssistantService(workspace, readiness_executor=executor,
                                           project_config=_project_config_for_key(workspace, key))
            try:
                session, readiness = confirm_and_prepare(service, project_key=key, root=root, request=request)
            except ValueError as exc:
                raise ProjectApiError("task_draft_unavailable", str(exc), 409, recovery=["reload_project"]) from exc
        else:
            raw_values = request.get("values", {})
            if not isinstance(raw_values, dict):
                raise ProjectApiError(
                    "invalid_request",
                    "Project preparation values must be a JSON object.",
                    400,
                    recovery=["retry_request"],
                )
            readiness = executor.submit_input(
                key,
                root,
                str(request.get("step_id", "")),
                raw_values,
                background=False,
            )
            session = TaskAssistantService(
                workspace,
                readiness_executor=executor,
            ).session(key, root)
        if not build_capabilities(readiness)["start_workflow"]:
            return 202, {
                "task_assistant_session": session,
                "workflow_started": False,
                "status": "preparing_project",
            }
        proposal = dict(session.get("proposed_task") or {})
        if not proposal.get("confirmed"):
            return 200, {
                "task_assistant_session": session,
                "workflow_started": False,
                "status": "ready_for_request",
            }
        proposal_id = str(proposal.get("id", ""))
        claim_id = uuid4().hex
        start_run_id = f"task-{proposal_id}"
        session = store.claim_workflow_start(proposal_id, claim_id, start_run_id)
        proposal = dict(session.get("proposed_task") or {})
        bound_run_id = str(proposal.get("run_id", ""))
        if bound_run_id:
            return 200, {
                "task_assistant_session": session,
                "workflow_started": True,
                "status": "workflow_started",
                "run_id": bound_run_id,
            }
        claimed_run_id = str(proposal.get("start_run_id", ""))
        current_state = StateStore(workspace).read_project_current(key)
        if (
            current_state is not None
            and current_state.run_id == claimed_run_id
            and current_state.current_node != "start"
        ):
            session = store.reconcile_workflow(proposal_id, claimed_run_id)
            return 200, {
                "task_assistant_session": session,
                "workflow_started": True,
                "status": "workflow_started",
                "run_id": claimed_run_id,
            }
        if str(proposal.get("start_status", "")) == "indeterminate":
            raise ProjectApiError(
                "workflow_start_indeterminate",
                "The prior workflow start could not be proven complete; automatic retry is blocked to prevent a duplicate run.",
                409,
                recovery=["inspect_workflow_state", "retry_after_manual_reconciliation"],
            )
        if str(proposal.get("start_claim_id", "")) != claim_id:
            session = store.mark_workflow_start_indeterminate(
                proposal_id,
                reason="The workflow start owner exited before completion was durably recorded.",
            )
            proposal = dict(session.get("proposed_task") or {})
            if str(proposal.get("start_status", "")) == "indeterminate":
                raise ProjectApiError(
                    "workflow_start_indeterminate",
                    "The prior workflow start could not be proven complete; automatic retry is blocked to prevent a duplicate run.",
                    409,
                    recovery=["inspect_workflow_state", "retry_after_manual_reconciliation"],
                )
            return 202, {
                "task_assistant_session": session,
                "workflow_started": False,
                "status": "workflow_starting",
                "run_id": "",
            }
        project_config = _project_config_for_key(workspace, key)
        try:
            result = start_workflow(
                workspace=workspace,
                idea=str(proposal.get("exact_request", "")),
                project_key=key,
                model_config=Path("models.yaml"),
                model_profile=str(target.get("model_profile", "") or "balanced"),
                delivery_mode=str(target.get("delivery_mode", "") or "init_project"),
                source_type=str(readiness.get("project_mode", "") or "existing_project"),
                project_config=project_config,
                load_profile="greenfield",
                run_id=start_run_id,
                task_skill_context=proposal.get("task_skill_context", {}),
            )
        except Exception as exc:
            store.mark_workflow_start_indeterminate(proposal_id, claim_id=claim_id, reason=str(exc))
            raise
        session = store.bind_workflow(
            proposal_id,
            str(result.get("run_id", "")),
            claim_id=claim_id,
        )
        return 200, {
            "task_assistant_session": session,
            "workflow_started": True,
            "status": "workflow_started",
            "run_id": str(result.get("run_id", "")),
        }
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
    workflow_match = re.fullmatch(r"/api/projects/([^/]+)/workflow", path)
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
    if workflow_match:
        key = _decode_project_api_key(workflow_match.group(1))
        key, _target, root = _project_target_and_root(workspace, key)
        readiness = ProjectReadinessExecutor(workspace).status(key, root)
        capabilities = build_capabilities(readiness)
        if not capabilities["start_workflow"]:
            raise ReadinessExecutionError(
                "readiness_not_ready",
                "Complete project readiness before starting a workflow.",
                details={"project_key": key},
                recovery=["continue_readiness", "reload_project"],
            )
        return 200, {
            "project_key": key,
            "workflow_url": f"/projects/{quote(key, safe='')}/work",
            "bridge": "product_project_workflow",
        }
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


def _decode_project_run_id(encoded_run_id: str) -> str:
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", encoded_run_id):
            raise ValueError("malformed percent escape")
        decoded = unquote_to_bytes(encoded_run_id).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectApiError(
            "invalid_run_id",
            "Run ID must be one valid URL segment.",
            400,
            recovery=["return_to_project_work", "choose_another_run"],
        ) from exc
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", decoded):
        raise ProjectApiError(
            "invalid_run_id",
            "Run ID must be one valid URL segment.",
            400,
            details={"run_id": decoded},
            recovery=["return_to_project_work", "choose_another_run"],
        )
    return decoded


def _is_skill_api_post_path(path: str) -> bool:
    return path in {"/api/skills/local/preview", "/api/skills/local/install"}


def _is_project_api_post_path(path: str) -> bool:
    return bool(
        path in {"/api/projects/import", "/api/settings"}
        or re.fullmatch(
            r"/api/projects/[^/]+/(mode|workflow|readiness/(start|input|retry|cancel)|task-assistant/(messages|actions)|team/commands)",
            path,
        )
        or re.fullmatch(r"/api/projects/[^/]+/runs/[^/]+/skill-reviews/job-skill-[0-9a-f]{64}/(?:retry|cancel)", path)
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
        path in {"/", "/legacy", "/projects", "/inbox", "/activity", "/settings"}
        or re.fullmatch(
            r"/projects/[^/]+/(?:home|work|work/runs/[^/]+|review|review/[^/]+|knowledge|team|settings(?:/[^/]+)?)",
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


def build_dashboard_handler(workspace: Path, *, codex_review_admission: "CodexReviewAdmission | None" = None):
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                response = handle_project_api_get(workspace, urlparse(self.path).path, **runtime_kwargs)
                if response is None:
                    self.send_error(404)
                    return
                status, payload = response
                self._send_json(status, payload)
            except Exception as exc:
                self._send_api_error(exc)

        def do_POST(self) -> None:
            project_path = urlparse(self.path).path
            is_project_path = _is_project_api_post_path(project_path)
            is_skill_path = _is_skill_api_post_path(project_path)
            normalized_path = project_path
            if normalized_path.startswith("/api/"):
                normalized_path = normalized_path.removeprefix("/api")
            if not is_project_path and not is_skill_path and normalized_path not in {
                "/context-estimate",
                "/context-generate",
                "/project-config",
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
            try:
                raw_body = read_request_body(self)
                if is_project_path or is_skill_path:
                    request = _parse_project_api_request(raw_body)
                    response = (
                        handle_project_api_post(workspace, project_path, request, **runtime_kwargs)
                        if is_project_path
                        else handle_skill_api_post(workspace, project_path, request)
                    )
                    assert response is not None
                    status, result = response
                    self._send_json(status, result)
                    return
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if normalized_path == "/context-estimate":
                    result = context_estimate_for_request(workspace, request)
                elif normalized_path == "/context-generate":
                    result = context_generate_for_request(workspace, request)
                elif (
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
                else:
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
            except Exception as exc:
                self._send_api_error(exc)

        def _send_api_error(self, error: Exception) -> None:
            status, payload = _project_api_error_response(error)
            self._send_json(status, payload)

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                _send_cors_headers(self, host="127.0.0.1", port=int(self.server.server_port))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


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


def handle_review_workbench_page_get(path: str, query: dict[str, list[str]]) -> tuple[int, str] | None:
    match = re.fullmatch(r"/review-workbench/([^/]+)", path)
    if match is None:
        return None
    execution_id = match.group(1)
    try:
        review = ExecutionAPI(_execution_project_root_from_query(query)).get_review_workbench(execution_id)
        return 200, render_review_workbench_html(review, decision_actions_enabled=True)
    except ValueError as exc:
        status, payload = _execution_error_response(exc)
        return status, f"<h1>Review Workbench unavailable</h1><p>{escape(str(payload['error']))}</p>"


def handle_execution_post(path: str, request: dict[str, object]) -> tuple[int, dict[str, object]] | None:
    try:
        if path == "/executions/github/issues/intake":
            api = ExecutionAPI(_execution_project_root_from_body(request))
            repo = _required_str(request, "repo")
            issue_number = _required_int(request.get("issue_number", request.get("issue")), "issue_number")
            return 200, api.intake_github_issue(
                repo=repo,
                issue_number=issue_number,
                adapter=str(request.get("adapter", "fake")),
                role=str(request.get("role", "implementation")),
            )

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

        match = re.fullmatch(r"/executions/([^/]+)/(run|pr|ci)", path)
        if match is None:
            return None
        execution_id = match.group(1)
        api = ExecutionAPI(_execution_project_root_from_body(request))
        command = match.group(2)
        if command == "run":
            return 200, api.run_execution(execution_id)
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


def _required_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive int")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive int") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive int")
    return parsed


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
        desired_raw = raw_settings.get("desired_instances", 1)
        try:
            if isinstance(desired_raw, bool):
                raise ValueError
            desired_instances = int(desired_raw)
        except (TypeError, ValueError):
            if strict:
                raise ValueError(f"worker_type_settings.{role}.desired_instances must be an integer") from None
            desired_instances = 1
        if role == "orchestrator" and desired_instances != 1:
            if strict:
                raise ValueError("Task Assistant desired_instances must be 1")
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
                "desired_instances": desired_instances,
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
) -> None:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)
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
            query = parse_qs(parsed.query)
            project_key = query.get("project", [""])[0]
            if maybe_send_favicon_response(self, parsed.path):
                return
            if parsed.path in {"/", "/index.html"} and frontend_error is not None:
                self._send_html(503, render_frontend_assets_diagnostic(workspace, frontend_error))
                return
            review_workbench_page = handle_review_workbench_page_get(parsed.path, query)
            if review_workbench_page is not None:
                status, body = review_workbench_page
                self._send_html(status, body)
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
            if parsed.path == "/api/delivery-artifact":
                from goodteam_os.delivery_outcomes import DeliveryArtifactError, read_delivery_artifact
                try:
                    payload = read_delivery_artifact(
                        workspace, project_key=project_key,
                        run_id=_decode_project_run_id(query.get("run", [""])[0]),
                        artifact_key=query.get("key", [""])[0], version=query.get("version", [""])[0],
                    )
                    if query.get("view") == ["text"]:
                        self._send_text(payload["content"])
                    else:
                        self._send_json(200, payload)
                except DeliveryArtifactError as exc:
                    self._send_json(exc.status, {"error": "delivery_artifact_unavailable", "message": str(exc)})
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/reviewer-report":
                from goodteam_os.delivery_outcomes import DeliveryArtifactError, delivery_state
                from goodteam_os.legacy_reviewer_reports import read_legacy_reviewer_report

                try:
                    state = delivery_state(workspace, project_key=project_key,
                        run_id=_decode_project_run_id(query.get("run", [""])[0]))
                    roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=state.project_key)
                    self._send_json(200, read_legacy_reviewer_report(state, roots,
                        job_id=query.get("job", [""])[0], profile_id=query.get("profile", [""])[0],
                        approval=query.get("approval", [""])[0], artifact_key=query.get("artifact", [""])[0],
                        artifact_version=query.get("artifact_version", [""])[0], version=query.get("version", [""])[0]))
                except DeliveryArtifactError as exc:
                    self._send_json(exc.status, {"error": "reviewer_report_unavailable", "message": str(exc)})
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path in {"/artifact", "/api/artifact"}:
                try:
                    artifact_key = query.get("key", [""])[0]
                    self._send_json(200, read_artifact(workspace=workspace, project_key=project_key, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/open-questions":
                try:
                    artifact_key = query.get("artifact", [""])[0]
                    self._send_json(200, read_open_questions(workspace=workspace, project_key=project_key, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_api_error(exc)
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
                    if not project_key:
                        legacy_state = StateStore(workspace).read_current()
                        res_data = {
                            "type": "overview",
                            "project_items": build_project_overview_items(workspace),
                            "legacy_state": asdict(legacy_state) if legacy_state is not None else None,
                            "resources": build_resource_items(legacy_state),
                        }
                    else:
                        res_data = build_project_work_state(workspace, project_key, **({"goal_limit": _goal_page_limit(query)} if "goal_limit" in query else {}))
                    self._send_json(200, res_data)
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/resources":
                try:
                    state = resolve_dashboard_state(workspace, project_key=project_key) if project_key else StateStore(workspace).read_current()
                    self._send_json(
                        200,
                        {
                            "project_key": normalize_project_key(project_key or state.project_key) if state is not None else "",
                            "resources": build_resource_items(state),
                            "legacy_artifacts": dict(state.artifacts) if state is not None else {},
                        },
                    )
                except Exception as exc:
                    self._send_api_error(exc)
                return
            if parsed.path == "/api/workflow/graph":
                try:
                    self._send_json(200, build_workflow_graph(workspace, project_key=project_key))
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
            if parsed.path == "/api/server-actions":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(200, build_server_action_capabilities(project_root))
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
            if parsed.path == "/api/messages":
                try:
                    run_id = query.get("run", [""])[0].strip()
                    gate = query.get("gate", [None])[0]
                    if not project_key or not run_id:
                        raise ValueError("project and run are required.")
                    self._send_json(
                        200,
                        {
                            "messages": read_messages(
                                workspace,
                                project_key,
                                run_id,
                                gate=gate,
                            )
                        },
                    )
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
            is_project_path = _is_project_api_post_path(project_path)
            is_skill_path = _is_skill_api_post_path(project_path)
            normalized_path = project_path
            if normalized_path.startswith("/api/"):
                normalized_path = normalized_path.removeprefix("/api")
            is_execution_path = normalized_path == "/executions/github/issues/intake" or bool(
                re.fullmatch(r"/executions/[^/]+/(run|pr|ci|review-workbench/decision|correction/start|correction/stop|correction/prepare-worker)", normalized_path)
            )

            if not is_project_path and not is_skill_path and not is_execution_path and normalized_path not in {
                "/start", "/report-bug", "/context-estimate", "/context-generate",
                "/intent-commit/start", "/intent-commit/discuss",
                "/intent-commit/keep-discussing", "/intent-commit/confirm",
                "/approve", "/recover-takeover-brief", "/artifact", "/project-config",
                "/terminate", "/project-delete", "/retry", "/generate-prd", "/generate-rfc",
                "/refine-artifact", "/answer-open-questions", "/accept-refinement",
                "/discard-refinement", "/open-path", "/debate", "/dispatch-track",
                "/smoke-run", "/reviewer-jobs/run", "/reviewer-jobs/retry", "/fs/choose",
                "/fs/mkdir", "/server-actions/run", "/git-init",
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
                        handle_project_api_post(workspace, project_path, request, **runtime_kwargs)
                        if is_project_path
                        else handle_skill_api_post(workspace, project_path, request)
                    )
                    assert project_response is not None
                    status, result = project_response
                    self._send_json(status, result)
                    return
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if normalized_path in {"/artifact", "/refine-artifact", "/answer-open-questions"}:
                    if not all(isinstance(request.get(field), str) and request[field].strip()
                               for field in ("expected_run_id", "expected_artifact_version")):
                        raise ProjectApiError("artifact_binding_required",
                            "Reload the current artifact before editing or refining it.", 400)
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
                elif normalized_path == "/start":
                    result = start_workflow(
                        workspace=workspace,
                        idea=str(request.get("idea", "")),
                        project_key=str(request.get("project_key", "")),
                        model_config=Path(str(request.get("model_config", "models.yaml"))),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "")),
                        delivery_standards=request.get("delivery_standards", ""),
                        source_type=str(request.get("source_type", "new_project")),
                        project_config=Path(str(request["project_config"])) if str(request.get("project_config", "")).strip() else None,
                        role_model_overrides=[str(value) for value in request.get("role_model_overrides", [])],
                        dry_run=bool(request.get("dry_run", False)),
                        expected_team_revision=request.get("expected_team_revision"),
                    )
                elif normalized_path == "/report-bug":
                    result = report_bug(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        description=str(request.get("description", "")),
                        title=str(request.get("title", "")),
                        severity=str(request.get("severity", "p2")),
                    )
                elif normalized_path == "/context-estimate":
                    result = context_estimate_for_request(workspace, request)
                elif normalized_path == "/context-generate":
                    result = context_generate_for_request(workspace, request)
                elif normalized_path == "/intent-commit/start":
                    result = start_request_changes_intent(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        approval=str(request.get("approval", "")),
                        expected_run_id=str(request.get("expected_run_id", "") or ""),
                        expected_target_type=str(request.get("expected_target_type", "") or ""),
                        expected_target_id=str(request.get("expected_target_id", "") or ""),
                        expected_artifact_key=str(request.get("expected_artifact_key", "") or ""),
                        expected_artifact_version=str(request.get("expected_artifact_version", "") or ""),
                        expected_task_id=str(request.get("expected_task_id", "") or ""),
                        expected_head_sha=str(request.get("expected_head_sha", "") or ""),
                        initial_text=request.get("initial_text", ""),
                    )
                elif normalized_path == "/intent-commit/discuss":
                    result = discuss_request_changes_intent(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        intent_id=str(request.get("intent_id", "")),
                        text=str(request.get("text", "")),
                    )
                elif normalized_path == "/intent-commit/keep-discussing":
                    result = keep_discussing_request_changes_intent(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        intent_id=str(request.get("intent_id", "")),
                    )
                elif normalized_path == "/intent-commit/confirm":
                    result = confirm_request_changes_intent(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        intent_id=str(request.get("intent_id", "")),
                    )
                elif normalized_path == "/approve":
                    approval_binding = {
                        "expected_run_id": str(request.get("expected_run_id", "") or "").strip(),
                        "expected_target_type": str(request.get("expected_target_type", "") or "").strip(),
                        "expected_target_id": str(request.get("expected_target_id", "") or "").strip(),
                        "expected_artifact_key": str(request.get("expected_artifact_key", "") or "").strip(),
                        "expected_artifact_version": str(
                            request.get("expected_artifact_version", "") or ""
                        ).strip(),
                    }
                    if str(request.get("decision", "")) == "approved":
                        missing_binding = [
                            field
                            for field, value in approval_binding.items()
                            if not value
                        ]
                        if missing_binding:
                            raise ProjectApiError(
                                "approval_binding_required",
                                "Approve requires the exact run, review target, artifact key, and artifact version from the current brief.",
                                409,
                                details={"missing": missing_binding},
                                recovery=["refresh_approval_brief"],
                            )
                    result = decide_approval(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        approval=str(request.get("approval", "")),
                        decision=str(request.get("decision", "")),
                        debate_ref=str(request.get("debate_ref", "")),
                        note=request.get("note", ""),
                        **approval_binding,
                    )
                elif normalized_path == "/recover-takeover-brief":
                    result = recover_legacy_takeover_review(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        note=str(request.get("note", "")),
                    )
                elif normalized_path == "/reviewer-jobs/run":
                    status, result = run_queued_reviewer_jobs_action(workspace, request, **runtime_kwargs)
                    self._send_json(status, result)
                    return
                elif normalized_path == "/reviewer-jobs/retry":
                    status, result = retry_failed_reviewer_jobs_action(workspace, request, **runtime_kwargs)
                    self._send_json(status, result)
                    return
                elif normalized_path == "/debate":
                    project_key = str(request.get("project_key", ""))
                    run_id = str(request.get("run", ""))
                    gate = str(request.get("gate", ""))
                    challenger_role = str(request.get("challenger_role", "qa"))
                    owner_role = str(request.get("owner_role", "architect"))
                    state = resolve_dashboard_state(workspace, project_key=project_key)
                    artifact_key = approval_artifact_map().get(gate, gate)
                    artifact_ref = state.artifacts.get(artifact_key, "") if state is not None else ""
                    result = {
                        "messages": run_gate_debate(
                            workspace,
                            project_key,
                            run_id,
                            gate,
                            challenger_role=challenger_role,
                            owner_role=owner_role,
                            dispatch_fn=default_debate_dispatch(workspace, project_key, run_id),
                            artifact_ref=artifact_ref,
                        )
                    }
                elif normalized_path == "/artifact":
                    result = save_artifact(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
                        expected_run_id=str(request.get("expected_run_id", "")),
                        expected_artifact_version=str(request.get("expected_artifact_version", "")),
                    )
                elif normalized_path == "/terminate":
                    expected_run_id = request.get("expected_run_id")
                    if not isinstance(expected_run_id, str) or not expected_run_id.strip():
                        raise ProjectApiError("run_binding_required", "Refresh and select the task to archive.", 400)
                    result = terminate_project_run(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        expected_run_id=expected_run_id,
                    )
                elif normalized_path == "/project-delete":
                    result = delete_project(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/retry":
                    result = retry_blocked_workflow(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        expected_run_id=request.get("expected_run_id"),
                        expected_retry_version=request.get("expected_retry_version"),
                    )
                elif normalized_path == "/generate-prd":
                    result = generate_prd(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/generate-rfc":
                    result = generate_rfc(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/refine-artifact":
                    result = refine_artifact(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
                        expected_run_id=str(request.get("expected_run_id", "")),
                        expected_artifact_version=str(request.get("expected_artifact_version", "")),
                    )
                elif normalized_path == "/answer-open-questions":
                    raw_answers = request.get("answers", [])
                    result = answer_open_questions(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        answers=raw_answers if isinstance(raw_answers, list) else [],
                        expected_run_id=request.get("expected_run_id", ""),
                        expected_artifact_version=request.get("expected_artifact_version", ""),
                    )
                elif normalized_path == "/accept-refinement":
                    result = accept_refinement(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        expected_run_id=request.get("expected_run_id", ""),
                        expected_artifact_key=request.get("expected_artifact_key", ""),
                        expected_artifact_version=request.get("expected_artifact_version", ""),
                        expected_proposal_id=request.get("expected_proposal_id", ""),
                        expected_proposal_version=request.get("expected_proposal_version", ""),
                    )
                elif normalized_path == "/discard-refinement":
                    result = discard_refinement(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        expected_run_id=request.get("expected_run_id", ""),
                        expected_artifact_key=request.get("expected_artifact_key", ""),
                        expected_artifact_version=request.get("expected_artifact_version", ""),
                        expected_proposal_id=request.get("expected_proposal_id", ""),
                        expected_proposal_version=request.get("expected_proposal_version", ""),
                    )
                elif normalized_path == "/open-path":
                    result = open_path_in_file_manager(
                        workspace=workspace,
                        relative_path=str(request.get("path", "")),
                    )
                elif normalized_path == "/git-init":
                    result = initialize_project_git_for_request(workspace, request)
                elif normalized_path == "/dispatch-track":
                    response_written = threading.Event()
                    result = start_dispatch_track(workspace, request, response_written=response_written)
                    try:
                        self._send_json(202, result)
                    finally:
                        response_written.set()
                    return
                elif normalized_path == "/smoke-run":
                    result = start_smoke_run(workspace, request)
                    self._send_json(202, result)
                    return
                elif normalized_path == "/server-actions/run":
                    project_root = resolve_project_root_for_request(workspace, request)
                    result = run_gated_server_action(
                        project_root,
                        request,
                        origin=str(self.headers.get("Origin", "")),
                    )
                    self._send_json(202, result)
                    return
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
