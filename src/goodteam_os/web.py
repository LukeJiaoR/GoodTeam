import json
import os
import re
import shutil
from dataclasses import asdict
from datetime import datetime, UTC
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from goodteam_os.graph import (
    advance_approved_workflow,
    render_downstream_artifact_markdown,
    run_adoption_until_next_gate,
    run_until_next_gate,
)
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.models import list_model_profiles, resolve_project_model_profile
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.roles import approval_artifact_map, list_role_definitions, workflow_steps_for_source
from goodteam_os.staging import (
    deployment_backend_config,
    delivery_contract_config as load_delivery_contract_config,
    delivery_contract_report,
    run_staging_backend,
)
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore, create_initial_state, normalize_project_key
from goodteam_os.tasks import LocalTaskStore, build_task_board, serialize_task_board
from goodteam_os.telegram import send_approval_notification
from goodteam_os.workers import WorkerJobStore
from goodteam_os.workers import DEFAULT_CODEX_MODEL

PRD_REVIEW_ROLES = [
    "Product / Requirement",
    "Architect",
    "Review / QA",
]


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
) -> None:
    action_log = _action_log_path(workspace, project_key)
    action_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
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
    with action_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def notify_current_approval_gate(state: AgileDevState | None) -> dict[str, object]:
    gate = current_approval_gate(state)
    if state is None or not gate:
        return {"status": "skipped", "reason": "no approval gate"}
    artifact_key = approval_artifact_map().get(gate, gate)
    try:
        return send_approval_notification(
            project_key=state.project_key,
            run_id=state.run_id,
            gate=gate,
            artifact_path=state.artifacts.get(artifact_key, ""),
            summary=f"GoodTeam is waiting for {gate.upper()} approval on {state.project_key}.",
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


def build_action_items(workspace: Path, project_key: str = "") -> list[dict[str, str]]:
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
        action = {key: str(value) for key, value in raw.items()}
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
        for run in report.get("runs", []):
            raw = dict(run)
            raw["report_path"] = str(path)
            raw["report_status"] = str(report.get("status", ""))
            items.append({key: str(value) for key, value in raw.items()})
    return items[:12]


def build_agile_board(state: AgileDevState | None) -> dict[str, object]:
    ledger = _task_ledger_from_state(state)
    if ledger is None:
        return serialize_task_board(build_task_board([]))
    return serialize_task_board(build_task_board(LocalTaskStore(ledger.parents[2]).list_tasks()))


def _task_ledger_from_state(state: AgileDevState | None) -> Path | None:
    if state is None:
        return None
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


def discover_project_directories(base_dir: Path = Path("/Users/luke/WorkSpace/codeAi")) -> list[dict[str, str]]:
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


def create_local_project_config(
    *,
    workspace: Path,
    project_name: str,
    project_key: str,
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
    model_profile: str = "balanced",
    delivery_mode: str = "init_project",
    role_model_overrides: dict[str, str] | None = None,
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
) -> dict[str, object]:
    name = project_name.strip()
    key = project_key.strip().upper()
    if not name:
        raise ValueError("Project name is required.")
    if not key:
        raise ValueError("Project key is required.")

    repos = []
    if docs_path.strip():
        repos.append(("docs", _validate_directory_path("Docs", docs_path)))
    if backend_path.strip():
        repos.append(("backend", _validate_directory_path("Backend", backend_path)))
    if frontend_path.strip():
        repos.append(("frontend", _validate_directory_path("Frontend", frontend_path)))
    if not repos:
        raise ValueError("Select at least one project directory.")

    path = workspace / "projects" / f"{_slug(key)}.local.yaml"
    profile = model_profile.strip() or "balanced"
    mode = normalize_delivery_mode(delivery_mode)
    overrides = {
        str(role).strip(): str(model).strip()
        for role, model in (role_model_overrides or {}).items()
        if str(role).strip() and str(model).strip()
    }
    lines = [
        f"name: {name}",
        f"key: {key}",
        f"model_profile: {profile}",
        f"delivery_mode: {mode}",
    ]
    if overrides:
        lines.append("role_model_overrides:")
        lines.extend(f"  {role}: {model}" for role, model in sorted(overrides.items()))
    lines.extend([
        "repos:",
    ])
    lines.extend(f"  {repo}: {repo_path}" for repo, repo_path in repos)
    sync_values = [value for value in (task_sync or []) if value]
    lines.extend(["task_backend:", "  mode: local", "  sync:"])
    if sync_values:
        lines[-1] = "  sync: " + ",".join(sync_values)
    else:
        lines[-1] = "  sync: "
    plane_config = {
        "api_host_url": plane_api_host_url.strip(),
        "workspace_slug": plane_workspace_slug.strip(),
        "project_id": plane_project_id.strip(),
        "api_key_env": plane_api_key_env.strip() or "PLANE_API_KEY",
        "mcp_server": plane_mcp_server.strip() or "plane",
    }
    if "plane" in sync_values:
        lines.append("  plane:")
        lines.extend(f"    {key}: {value}" for key, value in plane_config.items() if value)
    worker_mode = worker_backend_mode.strip() or "codex"
    lines.extend(["worker_backend:", f"  mode: {worker_mode}"])
    deployment_backend = {
        "mode": deployment_backend_mode.strip(),
        "staging_ssh_target": staging_ssh_target.strip(),
        "staging_remote_dir": staging_remote_dir.strip(),
        "staging_git_ref": staging_git_ref.strip(),
        "staging_public_url": staging_public_url.strip(),
        "staging_deploy_command": staging_deploy_command.strip(),
        "staging_healthcheck_command": staging_healthcheck_command.strip(),
        "staging_smoke_command": staging_smoke_command.strip(),
    }
    if deployment_backend["mode"]:
        lines.extend(["deployment_backend:", f"  mode: {deployment_backend['mode']}"])
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
                lines.append(f"  {config_key}: {value}")
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
        "role_model_overrides": overrides,
    }


def _delivery_contract_lines(
    *,
    required: bool,
    minimum_evidence: str,
    fatal_patterns: str,
    acceptance: str,
) -> list[str]:
    pattern_values = [line.strip() for line in fatal_patterns.splitlines() if line.strip()]
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
        checks.append((check_id, check_type, command))
    if not required and not pattern_values and not checks:
        return []
    evidence = minimum_evidence.strip() or ("functional" if required else "infrastructure")
    lines = [
        "delivery_contract:",
        f"  required: {'true' if required else 'false'}",
        f"  minimum_evidence: {evidence}",
    ]
    if pattern_values:
        lines.append("  fatal_output_patterns:")
        for index, pattern in enumerate(pattern_values, start=1):
            lines.append(f"    pattern_{index}: {pattern}")
    if checks:
        lines.append("  staging_acceptance:")
        for check_id, check_type, command in checks:
            lines.extend(
                [
                    f"    {check_id}:",
                    f"      type: {check_type}",
                    f"      command: {command}",
                ]
            )
    return lines


def workflow_steps_for_state(state: AgileDevState | None) -> list[dict[str, str]]:
    source_type = state.source_type if state is not None else "new_project"
    return workflow_steps_for_source(source_type)


def resolve_dashboard_state(workspace: Path, project_key: str = "") -> AgileDevState | None:
    store = StateStore(workspace)
    if project_key:
        return store.read_project_current(project_key)
    return store.read_current()


def is_active_run(state: AgileDevState | None) -> bool:
    return state is not None and bool(state.next_node)


def start_workflow(
    *,
    workspace: Path,
    idea: str,
    project_key: str,
    model_config: Path,
    model_profile: str,
    delivery_mode: str = "",
    source_type: str = "new_project",
    project_config: Path | None = None,
    role_model_overrides: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("Workflow goal is required.")
    key = normalize_project_key(project_key)
    if not dry_run and is_active_run(resolve_dashboard_state(workspace, project_key=key)):
        raise ValueError("An active run already exists. Finish, archive, or resume it before starting another run.")

    if source_type not in {"new_project", "existing_project"}:
        raise ValueError(f"Unknown workflow source type: {source_type}")
    if source_type == "existing_project" and project_config is None:
        raise ValueError("Project config is required for existing project adoption.")

    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    resolved_profile, role_models = resolve_project_model_profile(
        model_config,
        target,
        explicit_profile=model_profile,
        role_overrides=role_model_overrides or [],
    )
    state = create_initial_state(
        run_id=str(uuid4()),
        project_key=key,
        idea=idea.strip(),
    )
    state.source_type = source_type
    state.source_ref = str(project_config) if source_type == "existing_project" and project_config is not None else "manual"
    state.model_profile = resolved_profile
    state.delivery_mode = normalize_delivery_mode(delivery_mode or str(target.get("delivery_mode", "") or "init_project"))
    state.role_models = role_models

    if dry_run:
        if source_type == "existing_project":
            planned_artifacts = {"takeover": str(workspace / "artifacts" / "runs" / state.run_id / "takeover" / "takeover-context.md")}
            state.current_node = "takeover_review_gate"
            state.next_node = "await_takeover_approval"
            state.approvals["takeover"] = ApprovalDecision.PENDING
        else:
            planned_artifacts = {"prd": str(workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md")}
            state.current_node = "prd_review_gate"
            state.next_node = "await_prd_approval"
        payload = asdict(state)
        payload["dry_run"] = True
        payload["planned_artifacts"] = planned_artifacts
        return payload

    if source_type == "existing_project":
        result = run_adoption_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    else:
        result = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
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
    notify_current_approval_gate(result)
    payload = asdict(result)
    payload["dry_run"] = False
    return payload


def decide_approval(*, workspace: Path, approval: str, decision: str, project_key: str = "") -> dict[str, object]:
    approved_next = {
        "takeover": "product_requirement",
        "prd": "architect",
        "rfc": "planner",
        "staging": "release",
        "security": "planner",
        "release": "retro_memory",
    }
    if approval not in approved_next:
        raise ValueError(f"Unknown approval gate: {approval}")

    try:
        approval_decision = ApprovalDecision(decision)
    except ValueError as exc:
        raise ValueError(f"Unknown approval decision: {decision}") from exc

    state_store = StateStore(workspace)
    state = state_store.read_project_current(project_key) if project_key else state_store.read_current()
    if state is None:
        raise ValueError("No active run to approve.")
    key = normalize_project_key(project_key or state.project_key)
    if approval not in state.approvals:
        raise ValueError(f"Approval gate is not present in current state: {approval}")
    if state.next_node != f"await_{approval}_approval":
        raise ValueError(f"Approval gate is not currently actionable: {approval}")

    before_node = state.next_node
    state.approvals[approval] = approval_decision
    state.current_node = f"{approval}_review_gate"
    state.next_node = gate_next_node(state, gate=approval, approved_next=approved_next[approval])
    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="approval_decided",
        project_key=key,
        gate=approval,
        decision=str(approval_decision),
        before_node=before_node,
        after_node=state.next_node,
        artifact=approval_artifact_map().get(approval, approval),
        artifact_path=state.artifacts.get(approval_artifact_map().get(approval, approval), ""),
    )
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
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)
    notify_current_approval_gate(state)
    payload = asdict(state)
    payload["decision"] = str(approval_decision)
    return payload


def regenerate_rfc_review_gate(
    *,
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    rfc_path = ArtifactStore(root=workspace).write_markdown(
        state.run_id,
        "rfc",
        "rfc.md",
        render_downstream_artifact_markdown("rfc", state, project_config),
    )
    state.artifacts["rfc"] = str(rfc_path)
    state.current_node = "rfc_review_gate"
    state.next_node = gate_next_node(state, gate="rfc", approved_next="planner")
    return state


def retry_blocked_workflow(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    state_store = StateStore(workspace)
    state = state_store.read_project_current(key)
    if state is None:
        raise ValueError(f"No active run for project: {key}")
    if state.current_node != "implementation" or state.next_node != "blocked":
        raise ValueError(f"Current run is not blocked at implementation: {key}")

    before_node = state.next_node
    project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config is None or not project_config.is_file():
        project_config = _project_config_for_key(workspace, key)
    state.role_models = refresh_state_role_models(state, project_config)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)

    generated_before = set(state.artifacts)
    staging_retry = any(blocker.startswith("staging ") for blocker in state.blockers)
    state.blockers = []
    if staging_retry:
        staging_result = run_staging_backend(project_config=project_config)
        from goodteam_os.graph import render_staging_report_markdown
        from goodteam_os.artifacts import ArtifactStore

        store = ArtifactStore(root=workspace)
        staging_path = store.write_markdown(
            state.run_id,
            "staging_report",
            "staging-report.md",
            render_staging_report_markdown(state, project_config, staging_result),
        )
        state.artifacts["staging_report"] = str(staging_path)
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

    notify_current_approval_gate(state)
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


def worker_model_label(state: AgileDevState | None, start_context: dict[str, object] | None = None) -> str:
    if state is not None:
        model = str(dict(state.role_models.get("implementation", {})).get("primary", "")).strip()
        if model:
            return model.removeprefix("codex:")
    overrides = dict((start_context or {}).get("role_model_overrides", {}) or {})
    model = str(overrides.get("implementation", "") or overrides.get("frontend", "") or overrides.get("backend", "") or "").strip()
    if model:
        return model.removeprefix("codex:")
    return DEFAULT_CODEX_MODEL


def terminate_project_run(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    state_store = StateStore(workspace)
    state = state_store.read_project_current(key)
    if state is None:
        raise ValueError(f"No active run for project: {key}")
    before_node = state.next_node
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


def read_artifact_preview(path: str, max_chars: int = 8000) -> str:
    if not path:
        return ""
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return ""
    content = artifact_path.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[Preview truncated]"


def _slug_heading(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in slug.split("-") if part) or "section"


def _render_inline_markdown(value: str) -> str:
    text = escape(value)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def render_markdown_preview(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_render_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                blocks.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            blocks.append(f'<h{level} id="{_slug_heading(title)}">{_render_inline_markdown(title)}</h{level}>')
            continue
        list_item = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_item:
            flush_paragraph()
            list_items.append(_render_inline_markdown(list_item.group(1)))
            continue
        flush_list()
        paragraph.append(stripped)

    if in_code:
        blocks.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def _resolve_registered_artifact(workspace: Path, artifact_key: str, project_key: str = "") -> tuple[AgileDevState, Path]:
    store = StateStore(workspace)
    state = store.read_project_current(project_key) if project_key else store.read_current()
    if state is None:
        raise ValueError("No active run state found.")
    artifact_path = state.artifacts.get(artifact_key, "")
    if not artifact_path:
        raise ValueError(f"Artifact is not registered in current run: {artifact_key}")

    path = Path(artifact_path)
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    run_root = (workspace / "artifacts" / "runs" / state.run_id).resolve()
    if run_root not in [path, *path.parents]:
        raise ValueError("Artifact path must stay inside the current run artifacts directory.")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("Only markdown artifacts can be edited in the dashboard.")
    if not path.is_file():
        raise ValueError(f"Artifact file does not exist: {artifact_key}")
    return state, path


def read_artifact(*, workspace: Path, artifact_key: str, project_key: str = "") -> dict[str, object]:
    _, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    return {
        "artifact": artifact_key,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def save_artifact(*, workspace: Path, artifact_key: str, content: str, project_key: str = "") -> dict[str, object]:
    state, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    path.write_text(content, encoding="utf-8")

    key = normalize_project_key(project_key or state.project_key)
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


def build_approval_reviews(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is None:
        return []

    reviews = []
    approval_artifacts = approval_artifact_map()
    for approval, decision in state.approvals.items():
        artifact = approval_artifacts.get(approval, approval)
        artifact_path = state.artifacts.get(artifact, "")
        reviews.append(
            {
                "approval": approval,
                "decision": str(decision),
                "is_pending": str(decision) == ApprovalDecision.PENDING,
                "is_actionable": str(decision) == ApprovalDecision.PENDING and state.next_node == f"await_{approval}_approval",
                "artifact": artifact,
                "artifact_path": artifact_path,
                "content": read_artifact_preview(artifact_path),
                "review_roles": PRD_REVIEW_ROLES if approval == "prd" else [],
            }
        )
    return reviews


def build_start_console_context(
    workspace: Path,
    model_config: Path = Path("models.yaml"),
    project_config: Path | None = None,
    project_base: Path = Path("/Users/luke/WorkSpace/codeAi"),
) -> dict[str, object]:
    profiles = list_model_profiles(model_config) if model_config.is_file() else ["balanced"]
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
        "task_backend": normalize_task_backend(target.get("task_backend", {"mode": "local", "sync": []})),
        "worker_backend": normalize_worker_backend(target.get("worker_backend", {"mode": "codex"})),
        "deployment_backend": deployment_backend,
        "delivery_contract": delivery_contract,
        "project_base": str(directory_base),
        "candidate_dirs": discover_project_directories(directory_base),
    }


def discover_project_configs(workspace: Path) -> list[Path]:
    project_dir = workspace / "projects"
    if not project_dir.is_dir():
        return []
    paths = [*project_dir.glob("*.local.yaml")]
    unique = sorted({path.resolve(): path for path in paths}.values(), key=lambda item: item.name.lower())
    return unique


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
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        if normalize_project_key(str(target.get("key", config_path.stem))) == key:
            return config_path
    fallback = workspace / "projects" / f"{key.lower()}.local.yaml"
    if fallback.is_file():
        return fallback
    raise ValueError(f"No local project config found for project: {key}")


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


def normalize_delivery_mode(value: str) -> str:
    mode = (value or "init_project").strip()
    if mode not in {"mvp", "init_project", "multi_service"}:
        raise ValueError(f"Unknown delivery mode: {mode}")
    return mode


def render_review_roles(review_roles: list[str]) -> str:
    if not review_roles:
        return ""
    items = "".join(f"<li>{escape(role)}</li>" for role in review_roles)
    return f'<div class="review-roles"><span>Review roles</span><ul>{items}</ul></div>'


def render_task_cards(tasks: list[dict[str, object]]) -> str:
    if not tasks:
        return '<div class="empty-state">No tasks in this lane.</div>'
    return "\n".join(
        f"""
        <article class="task-card task-{escape(str(task.get("priority", "")))}">
          <div class="role-topline">
            <h4>{escape(str(task.get("id", "")))} {escape(str(task.get("title", "")))}</h4>
            <span class="role-status">{escape(str(task.get("priority", "")))}</span>
          </div>
          <p>{escape(str(task.get("description", "")))}</p>
          <dl class="role-meta">
            <dt>Role</dt><dd>{escape(str(task.get("role", "")))}</dd>
            <dt>Status</dt><dd>{escape(str(task.get("status", "")))}</dd>
          </dl>
          {render_inline_list("Acceptance", list(task.get("acceptance_criteria", [])))}
          {render_inline_list("Artifacts", list(task.get("artifact_refs", [])), code=True)}
        </article>
        """
        for task in tasks
    )


def render_inline_list(label: str, values: list[object], *, code: bool = False) -> str:
    clean = [str(value) for value in values if str(value).strip()]
    if not clean:
        return ""
    items = "".join(
        f"<li>{f'<code>{escape(value)}</code>' if code else escape(value)}</li>"
        for value in clean
    )
    return f'<div class="task-list"><span>{escape(label)}</span><ul>{items}</ul></div>'


def build_worker_job_items(project_root: Path | None) -> list[dict[str, str]]:
    if project_root is None:
        return []
    items = []
    for job in WorkerJobStore(project_root).list_jobs():
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
            }
        )
    return items


def render_dashboard(state: AgileDevState | None, start_context: dict[str, object] | None = None) -> str:
    if start_context is None:
        start_context = build_start_console_context(Path("."))
    else:
        default_context = build_start_console_context(Path(str(start_context.get("workspace", "."))))
        start_context = {**default_context, **start_context}
    workspace = Path(str(start_context.get("workspace", ".")))
    active_run = is_active_run(state)
    role_observations = build_role_observations(state)
    workflow_steps = build_workflow_steps(state)
    role_status_counts: dict[str, int] = {}
    for role in role_observations:
        role_status_counts[role["status"]] = role_status_counts.get(role["status"], 0) + 1
    role_summary_items = "\n".join(
        f'<div class="signal"><span>{escape(status)}</span>{count}</div>'
        for status, count in sorted(role_status_counts.items())
    )
    if not role_summary_items:
        role_summary_items = '<div class="empty-state">No active run state found.</div>'
    artifact_items = "\n".join(
        f"<li><span>{escape(name.upper())}</span><code>{escape(path)}</code></li>"
        for name, path in (state.artifacts if state is not None else {}).items()
    )
    if not artifact_items:
        artifact_items = '<li><span>No artifacts recorded</span><code>state/current.json has no artifact entries</code></li>'

    approval_items = "\n".join(
        f"<li><span>{escape(name)}</span><strong>{escape(str(decision))}</strong></li>"
        for name, decision in (state.approvals if state is not None else {}).items()
    )
    if not approval_items:
        approval_items = '<li><span>No approvals recorded</span><code>state/current.json has no approval entries</code></li>'
    approval_review_cards = "\n".join(
        f"""
        <article class="approval-card" id="approval-{escape(review["approval"])}">
          <div class="role-topline">
            <h3>{escape(review["approval"].upper())} Review</h3>
            <span class="role-status">{escape(review["decision"])}</span>
          </div>
          <dl class="role-meta">
            <dt>Artifact</dt><dd>{escape(review["artifact"])}</dd>
            <dt>Path</dt><dd>{f'<code>{escape(review["artifact_path"])}</code>' if review["artifact_path"] else 'not available'}</dd>
          </dl>
          {render_review_roles(review["review_roles"])}
          {f'''
          <div class="approval-actions">
            <button class="edit-artifact" data-artifact="{escape(review["artifact"])}" type="button">Edit Artifact</button>
          </div>
          <div class="artifact-editor" id="editor-{escape(review["artifact"])}" hidden>
            <textarea data-editor-for="{escape(review["artifact"])}" spellcheck="false"></textarea>
            <div class="approval-actions">
              <button class="primary save-artifact" data-artifact="{escape(review["artifact"])}" type="button">Save Markdown</button>
              <button class="cancel-edit" data-artifact="{escape(review["artifact"])}" type="button">Cancel</button>
            </div>
            <code class="editor-status" id="editor-status-{escape(review["artifact"])}">Unsaved edits stay in this browser until saved.</code>
          </div>
          ''' if review["artifact_path"] and str(review["artifact_path"]).lower().endswith((".md", ".markdown")) else ''}
          {f'''
          <div class="approval-actions">
            <button class="primary approval-decision" data-approval="{escape(review["approval"])}" data-decision="approved" type="button">Approve</button>
            <button class="approval-decision" data-approval="{escape(review["approval"])}" data-decision="changes_requested" type="button">Request Changes</button>
            <button class="danger approval-decision" data-approval="{escape(review["approval"])}" data-decision="rejected" type="button">Reject</button>
          </div>
          ''' if review["is_actionable"] else ''}
          {f'<div class="artifact-preview markdown-preview">{render_markdown_preview(review["content"])}</div>' if review["content"] else '<div class="empty-state">No artifact content available for this approval yet.</div>'}
        </article>
        """
        for review in build_approval_reviews(state)
    )
    if not approval_review_cards:
        approval_review_cards = '<div class="empty-state">No approval review items available.</div>'
    project_key = state.project_key if state is not None else str(start_context.get("project_key", ""))
    action_cards = "\n".join(
        f"""
        <article class="action-card">
          <div class="role-topline">
            <h3>{escape(action["title"])}</h3>
            <span class="role-status">{escape(action["actor"])}</span>
          </div>
          <dl class="role-meta">
            <dt>Time</dt><dd>{escape(action.get("timestamp", ""))}</dd>
            <dt>Action</dt><dd>{escape(action.get("action", ""))}</dd>
            <dt>Gate</dt><dd>{escape(action.get("gate", "") or "none")}</dd>
            <dt>Decision</dt><dd>{escape(action.get("decision", "") or "none")}</dd>
            <dt>Node</dt><dd>{escape(action.get("node_transition", "") or "none")}</dd>
            <dt>Artifact</dt><dd>{f'<code>{escape(action.get("artifact_path", ""))}</code>' if action.get("artifact_path", "") else escape(action.get("artifact", "") or "none")}</dd>
          </dl>
        </article>
        """
        for action in build_action_items(workspace, project_key=project_key if state is not None else "")
    )
    if not action_cards:
        action_cards = '<div class="empty-state">No actions recorded yet.</div>'
    smoke_cards = "\n".join(
        f"""
        <article class="action-card">
          <div class="role-topline">
            <h3>{escape(item.get("project_key", ""))} cycle {escape(item.get("cycle", ""))}</h3>
            <span class="role-status">{escape(item.get("status", ""))}</span>
          </div>
          <dl class="role-meta">
            <dt>Suite</dt><dd>{escape(item.get("suite", ""))}</dd>
            <dt>Classification</dt><dd>{escape(item.get("classification", "") or "none")}</dd>
            <dt>Next Task</dt><dd>{escape(item.get("next_task", "") or "none")}</dd>
            <dt>Report</dt><dd><code>{escape(item.get("report_path", ""))}</code></dd>
          </dl>
        </article>
        """
        for item in build_smoke_run_items(workspace)
    )
    if not smoke_cards:
        smoke_cards = '<div class="empty-state">No smoke runs recorded yet.</div>'
    agile_board = build_agile_board(state)
    agile_columns = dict(agile_board.get("columns", {}))
    agile_counts = dict(agile_board.get("counts", {}))
    agile_column_labels = {
        "backlog": "Backlog",
        "doing": "Doing",
        "blocked": "Blocked",
        "done": "Done",
    }
    agile_board_cards = "\n".join(
        f"""
        <section class="kanban-column">
          <div class="kanban-column-head">
            <h3>{escape(agile_column_labels[column])}</h3>
            <span>{escape(str(agile_counts.get(column, 0)))}</span>
          </div>
          <div class="task-stack">
            {render_task_cards(list(agile_columns.get(column, [])))}
          </div>
        </section>
        """
        for column in ["backlog", "doing", "blocked", "done"]
    )
    role_cards = "\n".join(
        f"""
        <article class="role-card">
          <div class="role-topline">
            <h3>{escape(role["name"])}</h3>
            <span class="role-status">{escape(role["status"])}</span>
          </div>
          <p>{escape(role["description"])}</p>
          <dl class="role-meta">
            <dt>Model</dt><dd>{escape(role["model"])}</dd>
            <dt>Mode</dt><dd>{escape(role["mode"])}</dd>
            <dt>Artifact</dt><dd>{escape(role["artifact"])}</dd>
          </dl>
          {f'<code>{escape(role["artifact_path"])}</code>' if role["artifact_path"] else ''}
        </article>
        """
        for role in role_observations
    )
    if not role_cards:
        role_cards = '<div class="empty-state">No role state available because there is no active run.</div>'
    current_step = next(
        (step for step in workflow_steps if step["status"] in {"active", "ready", "review-needed", "blocked"}),
        None,
    )
    current_work_html = (
        f"""
        <section>
          <h2>Current Work</h2>
          <dl>
            <dt>Step</dt><dd>{escape(current_step["title"])}</dd>
            <dt>Status</dt><dd>{escape(current_step["status_label"])}</dd>
            <dt>Roles</dt><dd>{escape(current_step["roles"])}</dd>
            <dt>Artifact</dt><dd>{escape(current_step["artifact"] or "none")}</dd>
            <dt>Feedback</dt><dd>{escape(current_step["feedback"])}</dd>
          </dl>
        </section>
        """
        if current_step is not None
        else ""
    )
    waterfall_cards = "\n".join(
        f"""
        <article class="flow-card flow-{escape(step["status"])}">
          <div class="flow-index">{escape(step["number"])}</div>
          <div class="flow-body">
            <div class="flow-topline">
              <h3>{escape(step["title"])}</h3>
              <span class="flow-status">{escape(step["status_label"])}</span>
            </div>
            <p>{escape(step["summary"])}</p>
            <p class="flow-feedback">{escape(step["feedback"])}</p>
            <dl class="role-meta">
              <dt>Roles</dt><dd>{escape(step["roles"])}</dd>
              <dt>Artifact</dt><dd>{escape(step["artifact"] or "none")}</dd>
              {f'<dt>Path</dt><dd><code>{escape(step["artifact_path"])}</code></dd>' if step["artifact_path"] else ''}
            </dl>
            {f'<button class="primary review-link" data-approval="{escape(step["approval"])}" type="button">{escape(step["action_label"])}</button>' if step["action_label"] else ''}
          </div>
        </article>
        """
        for step in workflow_steps
    )
    project_report = dict(start_context.get("project_report", {}))
    task_backend = normalize_task_backend(start_context.get("task_backend", {"mode": "local", "sync": []}))
    task_sync_values = [str(value) for value in task_backend.get("sync", [])]
    task_sync_label = "Local only" if not task_sync_values else "Local + " + ", ".join(task_sync_values)
    plane_config = dict(task_backend.get("plane", {})) if isinstance(task_backend.get("plane", {}), dict) else {}
    worker_backend = normalize_worker_backend(start_context.get("worker_backend", {"mode": "codex"}))
    worker_backend_mode = str(worker_backend.get("mode", "codex"))
    deployment_backend = dict(start_context.get("deployment_backend", {"mode": ""}))
    deployment_mode = str(deployment_backend.get("mode", "") or "")
    delivery_contract = dict(start_context.get("delivery_contract", {}))
    delivery_contract_required = bool(delivery_contract.get("required", False))
    delivery_contract_minimum = str(delivery_contract.get("minimum_evidence", "infrastructure") or "infrastructure")
    delivery_contract_level = str(delivery_contract.get("evidence_level", "infrastructure") or "infrastructure")
    delivery_contract_acceptance_count = str(delivery_contract.get("acceptance_count", 0))
    delivery_contract_configured = bool(delivery_contract.get("configured", False))
    project_root = Path(state.context_packet_path).parents[2] if state is not None and state.context_packet_path else None
    worker_job_items = "\n".join(
        (
            f"<li><span>{escape(item['role'])} / {escape(item['adapter'])}</span>"
            f"<code>{escape(item['status'])} · {escape(item['id'])}"
            f"{' · pid ' + escape(item['pid']) if item['pid'] else ''}"
            f"{' · heartbeat ' + escape(item['heartbeat_at']) if item['heartbeat_at'] else ''}"
            f"{' · ' + escape(item['summary']) if item['summary'] else ''}</code></li>"
        )
        for item in build_worker_job_items(project_root)
    )
    if not worker_job_items:
        worker_job_items = "<li><span>No worker jobs</span><code>Create jobs from planner or worker dispatch.</code></li>"
    workers = dict(project_report.get("workers", {}))
    worker_items = "\n".join(
        f"<li><span>{escape(name)}</span><code>{escape(str(dict(worker).get('path') or dict(worker).get('command') or 'missing'))}</code></li>"
        for name, worker in workers.items()
    )
    if not worker_items:
        worker_items = "<li><span>No worker config</span><code>Project target not loaded</code></li>"
    providers = dict(project_report.get("providers", {}))
    provider_items = "\n".join(
        f"<li><span>{escape(name)}</span><strong>{'configured' if dict(provider).get('configured') else 'not configured'}</strong></li>"
        for name, provider in providers.items()
    )
    if not provider_items:
        provider_items = "<li><span>No providers</span><code>Project target not loaded</code></li>"
    profiles = [str(profile) for profile in start_context.get("profiles", ["balanced"])]
    selected_profile = str(start_context.get("model_profile", "balanced") or "balanced")
    selected_delivery_mode = normalize_delivery_mode(str(start_context.get("delivery_mode", "init_project") or "init_project"))
    profile_options = "\n".join(
        f'<option value="{escape(profile)}"{" selected" if profile == selected_profile else ""}>{escape(profile)}</option>'
        for profile in profiles
    )
    delivery_mode_options = "\n".join(
        f'<option value="{mode}"{" selected" if mode == selected_delivery_mode else ""}>{label}</option>'
        for mode, label in [
            ("mvp", "MVP / validation slice"),
            ("init_project", "Init project / production starter"),
            ("multi_service", "Multi-service product"),
        ]
    )
    role_overrides = dict(start_context.get("role_model_overrides", {}))
    role_overrides_text = "\n".join(f"{role}={model}" for role, model in sorted(role_overrides.items()))
    worker_model = worker_model_label(state, start_context)
    target_for_contract = load_project_target(Path(str(start_context["project_config"]))) if str(start_context.get("project_config", "")) and Path(str(start_context["project_config"])).is_file() else {}
    delivery_contract_config_for_form = load_delivery_contract_config(target_for_contract) if target_for_contract else {"required": False, "minimum_evidence": "infrastructure", "fatal_output_patterns": [], "staging_acceptance": []}
    delivery_contract_patterns_text = "\n".join(str(pattern) for pattern in delivery_contract_config_for_form.get("fatal_output_patterns", []))
    delivery_contract_acceptance_text = "\n".join(
        f"{item.get('id', '')}|{item.get('type', 'ssh_command')}|{item.get('command', '')}"
        for item in delivery_contract_config_for_form.get("staging_acceptance", [])
    )
    candidate_dirs = [dict(item) for item in start_context.get("candidate_dirs", [])]
    directory_options = "\n".join(
        f'<option value="{escape(str(item["path"]))}">{escape(str(item["name"]))}</option>'
        for item in candidate_dirs
    )
    directory_options_with_empty = f'<option value="">Not selected</option>{directory_options}'
    blocker_html = ""
    if state is not None and state.blockers:
        blocker_items = "".join(f"<li>{escape(blocker)}</li>" for blocker in state.blockers)
        blocker_html = f"""
        <div class="empty-state blocker-state"><strong>Blocked</strong><ul>{blocker_items}</ul></div>
        <div class="approval-actions">
          <button class="primary retry-blocked" type="button">Retry blocked workers</button>
        </div>
        """
    closed_run = state is not None and not active_run
    current_run_html = (
        f"""
        <div class="completion-banner">
          <p class="eyebrow-line">Run Complete</p>
          <p class="idea">{escape(state.idea)}</p>
          <div class="completion-actions">
            <a class="button primary" href="/">Open project overview</a>
            <button class="review-link" data-approval="release" type="button">Review release notes</button>
          </div>
        </div>
        <div class="signal-row">
          <div class="signal"><span>Run</span>{escape(state.run_id)}</div>
          <div class="signal"><span>Project</span>{escape(state.project_key)}</div>
          <div class="signal"><span>Mode</span>{escape(state.source_type)}</div>
          <div class="signal"><span>Next</span>{escape(state.next_node or state.current_node)}</div>
          <div class="signal"><span>Model Profile</span>{escape(state.model_profile)}</div>
          <div class="signal"><span>Delivery</span>{escape(state.delivery_mode)}</div>
          <div class="signal"><span>Worker Model</span><code>{escape(worker_model)}</code></div>
        </div>
        {blocker_html}
        """
        if closed_run
        else (
        f"""
        <p class="idea">{escape(state.idea)}</p>
          <div class="signal-row">
            <div class="signal"><span>Run</span>{escape(state.run_id)}</div>
            <div class="signal"><span>Project</span>{escape(state.project_key)}</div>
            <div class="signal"><span>Mode</span>{escape(state.source_type)}</div>
            <div class="signal"><span>Next</span>{escape(state.next_node or state.current_node)}</div>
            <div class="signal"><span>Model Profile</span>{escape(state.model_profile)}</div>
            <div class="signal"><span>Delivery</span>{escape(state.delivery_mode)}</div>
            <div class="signal"><span>Worker Model</span><code>{escape(worker_model)}</code></div>
          </div>
        {blocker_html}
        """
        if state is not None
        else '<div class="empty-state">No active run. Start a workflow to create state/current.json.</div>'
        )
    )
    state_panel_html = (
        f"""
        <dl>
          <dt>Run</dt><dd>{escape(state.run_id)}</dd>
          <dt>Project</dt><dd>{escape(state.project_key)}</dd>
          <dt>Current Node</dt><dd>{escape(state.current_node)}</dd>
          <dt>Next Node</dt><dd>{escape(state.next_node)}</dd>
          <dt>Model Profile</dt><dd>{escape(state.model_profile)}</dd>
          <dt>Delivery Mode</dt><dd>{escape(state.delivery_mode)}</dd>
          <dt>Source</dt><dd>{escape(state.source_type)} / {escape(state.source_ref)}</dd>
          <dt>Project Memory</dt><dd>{escape(state.context_packet_path or "not initialized")}</dd>
          <dt>Local Task Ledger</dt><dd>{escape(state.artifacts.get("task_ledger", "not initialized"))}</dd>
        </dl>
        """
        if state is not None
        else '<div class="empty-state">state/current.json not found.</div>'
    )
    header_status = state.next_node or state.current_node if state is not None else "no_active_run"
    start_workflow_inner = f"""
          <div class="mode-switch" role="tablist" aria-label="Workflow start mode">
            <button class="mode-button active" data-start-mode="existing_project" type="button">Adopt Existing Project</button>
            <button class="mode-button" data-start-mode="new_project" type="button">Start New Project</button>
          </div>
          <div class="console-grid">
            <label>
            Goal
              <textarea id="start-idea" placeholder="Enter the real workflow goal..."></textarea>
            </label>
            <div>
              <label>
                Project Name
                <input id="project-name" value="{escape(str(start_context["project_name"]))}">
              </label>
              <label>
                Project Key
                <input id="project-key" value="{escape(str(start_context["project_key"]))}">
              </label>
              <label>
                Model Profile
                <select id="model-profile">{profile_options}</select>
              </label>
              <label>
                Delivery Mode
                <select id="delivery-mode">{delivery_mode_options}</select>
              </label>
              <label>
                Role Model Overrides
                <textarea id="role-model-overrides" placeholder="architect=codex:gpt-5.5&#10;frontend=codex:gpt-5.3-codex">{escape(role_overrides_text)}</textarea>
              </label>
              <label>
                Docs Directory
                <select id="docs-path">{directory_options_with_empty}</select>
              </label>
              <label>
                Backend Directory
                <select id="backend-path">{directory_options_with_empty}</select>
              </label>
              <label>
                Frontend Directory
                <select id="frontend-path">{directory_options_with_empty}</select>
              </label>
              <label>
                Project Config
                <input id="project-config" value="{escape(str(start_context["project_config"]))}">
              </label>
              <label>
                Task Backend Sync
                <select id="task-sync">
                  <option value="">Local only</option>
                  <option value="plane"{" selected" if "plane" in task_sync_values else ""}>Local + Plane</option>
                  <option value="linear"{" selected" if "linear" in task_sync_values else ""}>Local + Linear</option>
                  <option value="github_issues"{" selected" if "github_issues" in task_sync_values else ""}>Local + GitHub Issues</option>
                </select>
              </label>
              <label>
                Worker Backend
                <select id="worker-backend-mode">
                  <option value="codex"{" selected" if worker_backend_mode == "codex" else ""}>Codex CLI / {escape(worker_model)}</option>
                  <option value="manual"{" selected" if worker_backend_mode == "manual" else ""}>Manual / human adapter</option>
                  <option value="noop"{" selected" if worker_backend_mode == "noop" else ""}>Noop / protocol dry run</option>
                  <option value="supervised"{" selected" if worker_backend_mode == "supervised" else ""}>Supervised CLI adapters</option>
                </select>
              </label>
              <label>
                Deployment Backend
                <select id="deployment-backend-mode">
                  <option value=""{" selected" if deployment_mode == "" else ""}>None</option>
                  <option value="ssh_compose"{" selected" if deployment_mode == "ssh_compose" else ""}>Docker Compose + SSH</option>
                </select>
              </label>
              <div class="adapter-config" id="plane-config">
                <label>
                  Plane API Host
                  <input id="plane-api-host-url" value="{escape(str(plane_config.get("api_host_url", "https://api.plane.so/")))}">
                </label>
                <label>
                  Plane Workspace Slug
                  <input id="plane-workspace-slug" value="{escape(str(plane_config.get("workspace_slug", "")))}" placeholder="leader_kanban">
                </label>
                <label>
                  Plane Project ID
                  <input id="plane-project-id" value="{escape(str(plane_config.get("project_id", "")))}" placeholder="project uuid">
                </label>
                <label>
                  Plane API Key Env
                  <input id="plane-api-key-env" value="{escape(str(plane_config.get("api_key_env", "PLANE_API_KEY")))}">
                </label>
                <label>
                  Plane MCP Server
                  <input id="plane-mcp-server" value="{escape(str(plane_config.get("mcp_server", "plane")))}">
                </label>
              </div>
              <div class="adapter-config" id="deployment-config">
                <label>
                  Staging SSH Target
                  <input id="staging-ssh-target" value="{escape(str(deployment_backend.get("staging_ssh_target", "")))}">
                </label>
                <label>
                  Staging Remote Dir
                  <input id="staging-remote-dir" value="{escape(str(deployment_backend.get("staging_remote_dir", "")))}">
                </label>
                <label>
                  Staging Git Ref
                  <input id="staging-git-ref" value="{escape(str(deployment_backend.get("staging_git_ref", "")))}">
                </label>
                <label>
                  Staging Public URL
                  <input id="staging-public-url" value="{escape(str(deployment_backend.get("staging_public_url", "")))}">
                </label>
                <label>
                  Staging Deploy Command
                  <input id="staging-deploy-command" value="{escape(str(deployment_backend.get("staging_deploy_command", "")))}">
                </label>
                <label>
                  Staging Healthcheck Command
                  <input id="staging-healthcheck-command" value="{escape(str(deployment_backend.get("staging_healthcheck_command", "")))}">
                </label>
                <label>
                  Staging Smoke Command
                  <input id="staging-smoke-command" value="{escape(str(deployment_backend.get("staging_smoke_command", "")))}">
                </label>
                <label class="checkbox-label">
                  <input id="delivery-contract-required" type="checkbox"{" checked" if bool(delivery_contract_config_for_form.get("required", False)) else ""}>
                  Require functional delivery evidence
                </label>
                <label>
                  Minimum Evidence
                  <select id="delivery-contract-minimum-evidence">
                    <option value="infrastructure"{" selected" if str(delivery_contract_config_for_form.get("minimum_evidence", "infrastructure")) == "infrastructure" else ""}>Infrastructure</option>
                    <option value="functional"{" selected" if str(delivery_contract_config_for_form.get("minimum_evidence", "infrastructure")) == "functional" else ""}>Functional</option>
                  </select>
                </label>
                <label>
                  Fatal Output Patterns
                  <textarea id="delivery-contract-fatal-patterns" placeholder="migrate: executable file not found&#10;relation .* does not exist">{escape(delivery_contract_patterns_text)}</textarea>
                </label>
                <label>
                  Staging Acceptance Checks
                  <textarea id="delivery-contract-acceptance" placeholder="critical-user-journey|local_command|./scripts/e2e-critical.sh">{escape(delivery_contract_acceptance_text)}</textarea>
                </label>
              </div>
              <div class="actions">
                <label class="checkbox-label">
                  <input id="dry-run" type="checkbox" checked>
                  Dry run
                </label>
                <button id="create-project-config" type="button">Create Local Config</button>
                <button class="primary" id="start-run" data-adopt-label="Start adoption" data-new-label="Start new project" type="button">Start adoption</button>
              </div>
            </div>
          </div>
          <div class="signal-row">
            <div class="signal"><span>Project</span>{escape(str(start_context["project_name"]))} / {escape(str(start_context["project_key"]))}</div>
            <div class="signal"><span>Config</span><code>{escape(str(start_context["project_config"]))}</code></div>
            <div class="signal"><span>Delivery Mode</span>{escape(selected_delivery_mode)}</div>
            <div class="signal"><span>Worker Model</span><code>{escape(worker_model)}</code></div>
            <div class="signal"><span>Directory Base</span><code>{escape(str(start_context["project_base"]))}</code></div>
            <div class="signal"><span>Task Backend</span>{escape(task_sync_label)}</div>
            <div class="signal"><span>Worker Backend</span>{escape(worker_backend_mode)}</div>
            <div class="signal"><span>Deployment Backend</span>{escape(deployment_mode or "none")}</div>
          </div>
          <code class="command-box" id="start-result">Adopt mode reads the project config first and generates takeover-context.md before PRD.</code>
    """
    start_workflow_section = (
        ""
        if active_run
        else (
            f"""
        <details class="start-workflow-drawer">
          <summary>Start another run for this project</summary>
          <section class="start-workflow-panel">
            <h3>Start Workflow</h3>
            {start_workflow_inner}
          </section>
        </details>
        """
            if closed_run
            else f"""
        <section>
          <h2>Start Workflow</h2>
          {start_workflow_inner}
        </section>
        """
        )
    )
    start_workflow_script = (
        ""
        if active_run
        else f"""
    const workspace = {str(start_context["workspace"])!r};
    const projectKey = {str(start_context["project_key"])!r};
    const modelConfig = {str(start_context["model_config"])!r};
    let startMode = "existing_project";

    function updateStartState() {{
      const idea = document.getElementById("start-idea").value.trim();
      const dryRun = document.getElementById("dry-run").checked;
      const action = startMode === "existing_project" ? "adoption" : "new project";
      document.getElementById("start-run").textContent = dryRun ? "Preview " + action : "Start " + action;
      if (!idea) {{
        document.getElementById("start-result").textContent = startMode === "existing_project"
          ? "Enter a takeover goal. The first artifact will be takeover-context.md."
          : "Enter a product idea. The first artifact will be prd.md.";
        return;
      }}
      document.getElementById("start-result").textContent = dryRun
        ? "Ready to preview. No state or artifacts will be written."
        : "Ready to start. This will write state, artifacts, and a checkpoint.";
    }}

    document.getElementById("start-idea").addEventListener("input", updateStartState);
    document.getElementById("model-profile").addEventListener("change", updateStartState);
    document.getElementById("delivery-mode").addEventListener("change", updateStartState);
    document.getElementById("dry-run").addEventListener("change", updateStartState);
    function updateAdapterConfigVisibility() {{
      document.getElementById("plane-config").hidden = document.getElementById("task-sync").value !== "plane";
      document.getElementById("deployment-config").hidden = document.getElementById("deployment-backend-mode").value !== "ssh_compose";
    }}
    document.getElementById("task-sync").addEventListener("change", updateAdapterConfigVisibility);
    document.getElementById("deployment-backend-mode").addEventListener("change", updateAdapterConfigVisibility);
    document.querySelectorAll(".mode-button").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelectorAll(".mode-button").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        startMode = button.dataset.startMode;
        updateStartState();
      }});
    }});
    document.getElementById("create-project-config").addEventListener("click", async () => {{
      const resultBox = document.getElementById("start-result");
      const button = document.getElementById("create-project-config");
      button.disabled = true;
      resultBox.textContent = "Creating local project config...";
      try {{
        const response = await fetch("/project-config", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            project_name: document.getElementById("project-name").value,
            project_key: document.getElementById("project-key").value,
            docs_path: document.getElementById("docs-path").value,
            backend_path: document.getElementById("backend-path").value,
            frontend_path: document.getElementById("frontend-path").value,
            task_sync: document.getElementById("task-sync").value ? [document.getElementById("task-sync").value] : [],
            plane_api_host_url: document.getElementById("plane-api-host-url").value,
            plane_workspace_slug: document.getElementById("plane-workspace-slug").value,
            plane_project_id: document.getElementById("plane-project-id").value,
            plane_api_key_env: document.getElementById("plane-api-key-env").value,
            plane_mcp_server: document.getElementById("plane-mcp-server").value,
            worker_backend_mode: document.getElementById("worker-backend-mode").value,
            deployment_backend_mode: document.getElementById("deployment-backend-mode").value,
            staging_ssh_target: document.getElementById("staging-ssh-target").value,
            staging_remote_dir: document.getElementById("staging-remote-dir").value,
            staging_git_ref: document.getElementById("staging-git-ref").value,
            staging_public_url: document.getElementById("staging-public-url").value,
            staging_deploy_command: document.getElementById("staging-deploy-command").value,
            staging_healthcheck_command: document.getElementById("staging-healthcheck-command").value,
            staging_smoke_command: document.getElementById("staging-smoke-command").value,
            delivery_contract_required: document.getElementById("delivery-contract-required").checked,
            delivery_contract_minimum_evidence: document.getElementById("delivery-contract-minimum-evidence").value,
            delivery_contract_fatal_patterns: document.getElementById("delivery-contract-fatal-patterns").value,
            delivery_contract_acceptance: document.getElementById("delivery-contract-acceptance").value,
            model_profile: document.getElementById("model-profile").value,
            delivery_mode: document.getElementById("delivery-mode").value,
            role_model_overrides: Object.fromEntries(
              document.getElementById("role-model-overrides").value
                .split("\\n")
                .map((line) => line.trim())
                .filter(Boolean)
                .map((line) => line.split("=", 2))
                .filter((parts) => parts.length === 2 && parts[0] && parts[1])
            )
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "Failed to create local config.");
        }}
        document.getElementById("project-config").value = payload.project_config;
        resultBox.textContent = JSON.stringify(payload, null, 2);
      }} catch (error) {{
        resultBox.textContent = error.message;
      }} finally {{
        button.disabled = false;
      }}
    }});
    updateAdapterConfigVisibility();
    document.getElementById("start-run").addEventListener("click", async () => {{
      const resultBox = document.getElementById("start-result");
      const idea = document.getElementById("start-idea").value.trim();
      const dryRun = document.getElementById("dry-run").checked;
      if (!idea) {{
        resultBox.textContent = "Workflow goal is required.";
        return;
      }}
      document.getElementById("start-run").disabled = true;
      resultBox.textContent = "Starting...";
      try {{
        const response = await fetch("/start", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            idea,
            project_key: document.getElementById("project-key").value || projectKey,
            model_config: modelConfig,
            model_profile: document.getElementById("model-profile").value,
            delivery_mode: document.getElementById("delivery-mode").value,
            source_type: startMode,
            project_config: document.getElementById("project-config").value,
            role_model_overrides: document.getElementById("role-model-overrides").value
              .split("\\n")
              .map((line) => line.trim())
              .filter(Boolean),
            dry_run: dryRun
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "Failed to start workflow.");
        }}
        resultBox.textContent = JSON.stringify(payload, null, 2);
        if (!payload.dry_run) {{
          window.setTimeout(() => window.location.reload(), 600);
        }}
      }} catch (error) {{
        resultBox.textContent = error.message;
      }} finally {{
        document.getElementById("start-run").disabled = false;
      }}
    }});
    updateStartState();
        """
    )
    current_run_section_class = "hero wide" if closed_run else "hero"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>GoodTeam AI Team OS</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7f4;
      --panel: #ffffff;
      --panel-2: #eef3f1;
      --surface: #f9fbfa;
      --ink: #17211d;
      --text: #17211d;
      --muted: #66736e;
      --line: #d9e0dc;
      --accent: #217a62;
      --accent-soft: #e2f2ed;
      --blue: #246b8f;
      --red: #bf3d4d;
      --code-bg: #eef1ef;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", Avenir, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 15px;
    }}
    code, pre, .command-box, .editor-status {{
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    }}
    main {{
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      border-bottom: 1px solid var(--line);
      padding-bottom: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 5vw, 58px);
      line-height: 0.95;
      letter-spacing: 0;
    }}
    .tag {{
      display: inline-block;
      margin-bottom: 12px;
      color: var(--accent);
      font-size: 13px;
      text-transform: uppercase;
    }}
    .status {{
      border: 1px solid var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
      padding: 10px 12px;
      min-width: 220px;
      text-align: center;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 20px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 10px;
    }}
    .tab-button {{
      border-color: var(--line);
      color: var(--muted);
      min-width: 108px;
    }}
    .tab-button.active {{
      border-color: var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
    }}
    .tab-panel {{
      display: none;
    }}
    .tab-panel.active {{
      display: block;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 16px;
      margin-top: 20px;
    }}
    section {{
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 18px;
      min-height: 180px;
      border-radius: 8px;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 14px;
      color: var(--blue);
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .hero {{
      background: linear-gradient(135deg, var(--panel), var(--panel-2));
    }}
    .completion-banner {{
      display: grid;
      gap: 14px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 16px;
    }}
    .eyebrow-line {{
      margin: 0;
      color: var(--accent);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .completion-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .wide {{
      grid-column: 1 / -1;
    }}
    .start-workflow-drawer {{
      grid-column: 1 / -1;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 0;
      overflow: hidden;
    }}
    .start-workflow-drawer summary {{
      cursor: pointer;
      padding: 14px 18px;
      color: var(--accent);
      background: var(--accent-soft);
      list-style-position: inside;
    }}
    .start-workflow-panel {{
      border: 0;
      border-top: 1px solid var(--line);
      border-radius: 0;
      min-height: 0;
    }}
    .console-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .mode-switch {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 14px;
    }}
    .mode-button {{
      min-height: 42px;
      color: var(--muted);
    }}
    .mode-button.active {{
      border-color: var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
    }}
    label {{
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    textarea, select, input {{
      width: 100%;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
      min-height: 42px;
      border-radius: 6px;
    }}
    textarea {{
      min-height: 118px;
      resize: vertical;
      line-height: 1.45;
    }}
    .checkbox-label {{
      display: flex;
      align-items: center;
      gap: 10px;
      text-transform: none;
      color: var(--text);
    }}
    .checkbox-label input {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      accent-color: var(--accent);
    }}
    .command-box {{
      margin-top: 14px;
      border: 1px solid var(--line);
      background: var(--code-bg);
      padding: 14px;
      min-height: 116px;
      color: var(--accent);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      text-align: left;
    }}
    .adapter-config {{
      border: 1px solid var(--line);
      padding: 12px;
      display: grid;
      gap: 10px;
      background: var(--surface);
    }}
    .adapter-config[hidden] {{
      display: none;
    }}
    .signal-row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .signal-row.compact {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .signal {{
      border: 1px solid var(--line);
      padding: 10px;
      min-height: 72px;
    }}
    .signal span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .empty-state {{
      border: 1px dashed var(--line);
      color: var(--muted);
      padding: 14px;
      line-height: 1.45;
    }}
    .roles {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .waterfall {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .agile-board {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      align-items: start;
    }}
    .kanban-column {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
      min-height: 360px;
      padding: 12px;
      border-radius: 8px;
    }}
    .kanban-column-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .kanban-column-head h3 {{
      font-size: 14px;
      margin: 0;
      letter-spacing: 0;
    }}
    .kanban-column-head span {{
      border: 1px solid var(--line);
      color: var(--muted);
      min-width: 28px;
      height: 24px;
      display: grid;
      place-items: center;
      font-size: 12px;
    }}
    .task-stack {{
      display: grid;
      gap: 10px;
    }}
    .task-card {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 12px;
      border-radius: 8px;
    }}
    .task-card h4 {{
      margin: 0;
      font-size: 13px;
      line-height: 1.35;
      letter-spacing: 0;
    }}
    .task-card p {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      margin: 8px 0;
    }}
    .task-high {{
      border-left: 3px solid var(--red);
    }}
    .task-list {{
      margin-top: 8px;
    }}
    .task-list span {{
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
    }}
    .task-list ul {{
      margin: 5px 0 0;
      padding-left: 16px;
      color: var(--text);
      font-size: 12px;
      line-height: 1.45;
    }}
    .flow-card {{
      display: grid;
      grid-template-columns: 40px 1fr;
      gap: 12px;
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 14px;
      min-height: 172px;
      border-radius: 8px;
    }}
    .flow-index {{
      width: 32px;
      height: 32px;
      border: 1px solid var(--line);
      display: grid;
      place-items: center;
      color: var(--muted);
    }}
    .flow-topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    .flow-status {{
      border: 1px solid var(--line);
      color: var(--muted);
      padding: 4px 6px;
      font-size: 11px;
      white-space: nowrap;
    }}
    .flow-card p {{
      color: var(--muted);
      line-height: 1.45;
      margin: 10px 0 12px;
      font-size: 13px;
    }}
    .flow-card .flow-feedback {{
      color: var(--text);
      margin-top: 0;
    }}
    .flow-done {{
      border-color: #4ade80;
    }}
    .flow-done .flow-status {{
      color: #4ade80;
      border-color: #4ade80;
    }}
    .flow-active {{
      border-color: var(--blue);
    }}
    .flow-active .flow-status {{
      color: var(--blue);
      border-color: var(--blue);
    }}
    .flow-ready {{
      border-color: #fbbf24;
    }}
    .flow-ready .flow-status {{
      color: #fbbf24;
      border-color: #fbbf24;
    }}
    .flow-review-needed {{
      border-color: var(--accent);
      background: var(--accent-soft);
    }}
    .flow-review-needed .flow-index,
    .flow-review-needed .flow-status {{
      color: var(--accent);
      border-color: var(--accent);
    }}
    .flow-blocked {{
      border-color: var(--red);
    }}
    .flow-blocked .flow-status {{
      color: var(--red);
      border-color: var(--red);
    }}
    .role-card {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 14px;
      min-height: 190px;
      border-radius: 8px;
    }}
    .approval-card {{
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 14px;
      margin-top: 12px;
      border-radius: 8px;
    }}
    .action-card {{
      border-left: 3px solid var(--accent);
      border-top: 1px solid var(--line);
      background: var(--surface);
      padding: 14px;
    }}
    .action-timeline {{
      display: grid;
      gap: 10px;
    }}
    .artifact-preview {{
      margin: 14px 0 0;
      padding: 22px;
      border: 1px solid var(--line);
      background: #fffefa;
      color: var(--ink);
      overflow-wrap: break-word;
      max-height: 520px;
      overflow: auto;
      line-height: 1.65;
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(23, 33, 29, 0.06);
    }}
    .markdown-preview h1,
    .markdown-preview h2,
    .markdown-preview h3 {{
      color: var(--ink);
      text-transform: none;
      margin: 22px 0 10px;
      line-height: 1.2;
    }}
    .markdown-preview h1:first-child,
    .markdown-preview h2:first-child,
    .markdown-preview h3:first-child {{
      margin-top: 0;
    }}
    .markdown-preview h1 {{
      font-size: 30px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
    }}
    .markdown-preview h2 {{
      font-size: 21px;
      color: var(--blue);
    }}
    .markdown-preview h3 {{
      font-size: 17px;
    }}
    .markdown-preview p {{
      margin: 10px 0;
    }}
    .markdown-preview ul {{
      margin: 10px 0 14px;
      padding-left: 24px;
      display: block;
      list-style: disc;
    }}
    .markdown-preview li {{
      display: list-item;
      border-top: 0;
      padding-top: 0;
      margin: 6px 0;
    }}
    .markdown-preview code {{
      background: var(--code-bg);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 1px 5px;
      font-size: 0.92em;
      color: var(--ink);
      text-align: left;
    }}
    .markdown-preview pre {{
      margin: 14px 0;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #17211d;
      color: #edf7f1;
      overflow: auto;
    }}
    .markdown-preview pre code {{
      background: transparent;
      border: 0;
      color: inherit;
      padding: 0;
    }}
    .artifact-editor {{
      margin-top: 14px;
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 14px;
      border-radius: 8px;
    }}
    .artifact-editor textarea {{
      min-height: 420px;
      line-height: 1.6;
      white-space: pre;
      overflow: auto;
      resize: vertical;
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 14px;
    }}
    .editor-status {{
      display: block;
      text-align: left;
      margin-top: 10px;
    }}
    .role-topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    h3 {{
      margin: 0;
      font-size: 15px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .role-status {{
      border: 1px solid var(--line);
      color: var(--accent);
      padding: 4px 6px;
      font-size: 11px;
      white-space: nowrap;
    }}
    .role-card p {{
      color: var(--muted);
      line-height: 1.45;
      margin: 12px 0;
      font-size: 13px;
    }}
    .role-meta {{
      grid-template-columns: 74px 1fr;
      gap: 7px 10px;
      font-size: 12px;
    }}
    .review-roles {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
    }}
    .review-roles span {{
      display: block;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .review-roles ul {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0;
      padding: 0;
    }}
    .review-roles li {{
      border: 1px solid var(--line);
      padding: 5px 7px;
      color: var(--ink);
      background: var(--panel);
    }}
    .idea {{
      font-size: 20px;
      line-height: 1.45;
      margin: 0;
    }}
    dl {{
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 10px 16px;
      margin: 0;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    ul {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    li {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    code {{
      color: var(--muted);
      overflow-wrap: anywhere;
      text-align: right;
    }}
    strong {{ color: var(--accent); font-weight: 500; }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .approval-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .button,
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
      border-radius: 6px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .button.primary,
    button.primary {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    button.danger {{
      border-color: var(--red);
      color: var(--red);
    }}
    @media (max-width: 760px) {{
      header, .grid {{ display: block; }}
      .status {{ margin-top: 16px; }}
      section {{ margin-top: 16px; }}
      .console-grid, .signal-row {{ grid-template-columns: 1fr; }}
      .waterfall {{ grid-template-columns: 1fr; }}
      .roles {{ grid-template-columns: 1fr; }}
      dl {{ grid-template-columns: 1fr; }}
      li {{ display: block; }}
      code {{ display: block; text-align: left; margin-top: 6px; }}
    }}
    @media (min-width: 761px) and (max-width: 1040px) {{
      .roles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .waterfall {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <script>window.GOODTEAM_PROJECT_KEY = {project_key!r};</script>
    <header>
      <div>
        <span class="tag">Local workflow dashboard</span>
        <h1>GoodTeam<br>AI Team OS</h1>
      </div>
      <div class="status">{escape(header_status)}</div>
    </header>
    <nav class="tabs" aria-label="Dashboard sections">
      <button class="tab-button active" data-tab="overview" type="button">Overview</button>
      <button class="tab-button" data-tab="roles" type="button">Roles</button>
      <button class="tab-button" data-tab="artifacts" type="button">Artifacts</button>
      <button class="tab-button" data-tab="actions" type="button">Actions</button>
      <button class="tab-button" data-tab="board" type="button">Agile Board</button>
      <button class="tab-button" data-tab="smoke" type="button">Smoke Runs</button>
      <button class="tab-button" data-tab="workers" type="button">Workers</button>
      <button class="tab-button" data-tab="approvals" type="button">Approvals</button>
      <button class="tab-button" data-tab="system" type="button">System</button>
    </nav>
    <div class="tab-panel active" id="tab-overview">
      <div class="grid">
        <section class="{current_run_section_class}">
          <h2>Current Run</h2>
          {current_run_html}
        </section>
        <section>
          <h2>Role Summary</h2>
          <div class="signal-row compact">{role_summary_items}</div>
        </section>
        {current_work_html}
        {start_workflow_section}
        <section class="wide">
          <h2>Workflow Waterfall</h2>
          <div class="waterfall">{waterfall_cards}</div>
        </section>
        <section>
          <h2>Recent Artifacts</h2>
          <ul>{artifact_items}</ul>
        </section>
      </div>
    </div>
    <div class="tab-panel" id="tab-roles">
      <section class="wide">
        <h2>Role Observability</h2>
        <div class="roles">{role_cards}</div>
      </section>
    </div>
    <div class="tab-panel" id="tab-artifacts">
      <section>
        <h2>Artifacts</h2>
        <ul>{artifact_items}</ul>
      </section>
    </div>
    <div class="tab-panel" id="tab-actions">
      <section>
        <h2>Actions</h2>
        <div class="action-timeline">{action_cards}</div>
      </section>
    </div>
    <div class="tab-panel" id="tab-board">
      <section class="wide">
        <h2>Agile Board</h2>
        <div class="agile-board">{agile_board_cards}</div>
      </section>
    </div>
    <div class="tab-panel" id="tab-smoke">
      <section>
        <h2>Smoke Runs</h2>
        <div class="action-timeline">{smoke_cards}</div>
      </section>
    </div>
    <div class="tab-panel" id="tab-workers">
      <section>
        <h2>Worker Jobs</h2>
        <ul>{worker_job_items}</ul>
      </section>
    </div>
    <div class="tab-panel" id="tab-approvals">
      <section>
        <h2>Approvals</h2>
        <ul>{approval_items}</ul>
        {approval_review_cards}
      </section>
    </div>
    <div class="tab-panel" id="tab-system">
      <div class="grid">
        <section>
          <h2>State</h2>
          {state_panel_html}
        </section>
        <section>
          <h2>Project Target</h2>
          <dl>
            <dt>Name</dt><dd>{escape(str(start_context["project_name"]))}</dd>
            <dt>Key</dt><dd>{escape(str(start_context["project_key"]))}</dd>
            <dt>Config</dt><dd>{escape(str(start_context["project_config"]))}</dd>
            <dt>Model Config</dt><dd>{escape(str(start_context["model_config"]))}</dd>
            <dt>Delivery Mode</dt><dd>{escape(selected_delivery_mode)}</dd>
            <dt>Task Backend</dt><dd>{escape(task_sync_label)}</dd>
            <dt>Worker Backend</dt><dd>{escape(worker_backend_mode)}</dd>
            <dt>Deployment Backend</dt><dd>{escape(deployment_mode or "none")}</dd>
            <dt>Staging SSH</dt><dd>{escape(str(deployment_backend.get("staging_ssh_target", "")) or "not configured")}</dd>
            <dt>Staging Remote Dir</dt><dd>{escape(str(deployment_backend.get("staging_remote_dir", "")) or "not configured")}</dd>
            <dt>Delivery Contract</dt><dd>{'required' if delivery_contract_required else 'optional'} / {'configured' if delivery_contract_configured else 'insufficient'}</dd>
            <dt>Minimum Evidence</dt><dd>{escape(delivery_contract_minimum)}</dd>
            <dt>Evidence Level</dt><dd>{escape(delivery_contract_level)}</dd>
            <dt>Acceptance Checks</dt><dd>{escape(delivery_contract_acceptance_count)}</dd>
          </dl>
        </section>
        <section>
          <h2>Workers</h2>
          <ul>{worker_items}</ul>
        </section>
        <section>
          <h2>Providers</h2>
          <ul>{provider_items}</ul>
        </section>
      </div>
    </div>
  </main>
  <script>
    {start_workflow_script}
    document.querySelectorAll(".tab-button").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelectorAll(".tab-button").forEach((item) => item.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        document.getElementById("tab-" + button.dataset.tab).classList.add("active");
      }});
    }});
    document.querySelectorAll(".review-link").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelector('[data-tab="approvals"]').click();
        const target = document.getElementById("approval-" + button.dataset.approval);
        if (target) {{
          target.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }}
      }});
    }});
    document.querySelectorAll(".approval-decision").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          const response = await fetch("/approve", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{
              project_key: window.GOODTEAM_PROJECT_KEY,
              approval: button.dataset.approval,
              decision: button.dataset.decision
            }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to update approval.");
          }}
          window.setTimeout(() => window.location.reload(), 300);
        }} catch (error) {{
          button.disabled = false;
          const card = button.closest(".approval-card");
          if (card) {{
            const message = document.createElement("div");
            message.className = "empty-state";
            message.textContent = error.message;
            card.prepend(message);
          }}
        }}
      }});
    }});
    document.querySelectorAll(".edit-artifact").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const artifact = button.dataset.artifact;
        const editor = document.getElementById("editor-" + artifact);
        const textarea = document.querySelector(`[data-editor-for="${{artifact}}"]`);
        const status = document.getElementById("editor-status-" + artifact);
        button.disabled = true;
        status.textContent = "Loading artifact...";
        try {{
          const response = await fetch("/artifact?key=" + encodeURIComponent(artifact) + "&project=" + encodeURIComponent(window.GOODTEAM_PROJECT_KEY));
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to read artifact.");
          }}
          textarea.value = payload.content;
          editor.hidden = false;
          status.textContent = payload.path;
        }} catch (error) {{
          status.textContent = error.message;
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll(".cancel-edit").forEach((button) => {{
      button.addEventListener("click", () => {{
        const editor = document.getElementById("editor-" + button.dataset.artifact);
        if (editor) {{
          editor.hidden = true;
        }}
      }});
    }});
    document.querySelectorAll(".save-artifact").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const artifact = button.dataset.artifact;
        const textarea = document.querySelector(`[data-editor-for="${{artifact}}"]`);
        const status = document.getElementById("editor-status-" + artifact);
        button.disabled = true;
        status.textContent = "Saving...";
        try {{
          const response = await fetch("/artifact", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: window.GOODTEAM_PROJECT_KEY, artifact, content: textarea.value }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to save artifact.");
          }}
          status.textContent = "Saved " + payload.bytes + " bytes. Reloading preview...";
          window.setTimeout(() => window.location.reload(), 350);
        }} catch (error) {{
          status.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll(".retry-blocked").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        button.textContent = "Retrying workers...";
        try {{
          const response = await fetch("/retry", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: window.GOODTEAM_PROJECT_KEY }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to retry blocked workflow.");
          }}
          window.setTimeout(() => window.location.reload(), 300);
        }} catch (error) {{
          button.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
  </script>
</body>
</html>"""


def render_project_overview(workspace: Path, model_config: Path = Path("models.yaml")) -> str:
    project_items = build_project_overview_items(workspace, model_config=model_config)
    legacy_state = StateStore(workspace).read_current()
    first_project_key = project_items[0]["project_key"] if project_items else ""
    open_project_link = (
        f'<a class="button primary" href="/?project={escape(first_project_key)}">Open {escape(first_project_key)}</a>'
        if first_project_key
        else ""
    )
    cards = "\n".join(
        f"""
        <article class="project-card project-{escape(item["status"])}">
          <div>
            <h2>{escape(item["project_name"])}</h2>
            <p>{escape(item["project_key"])} · {escape(item["status"])}</p>
          </div>
          <dl>
            <dt>Model</dt><dd>{escape(item["model_profile"])}</dd>
            <dt>Delivery</dt><dd>{escape(item["delivery_mode"])}</dd>
            <dt>Next</dt><dd>{escape(item["next_node"] or "no active run")}</dd>
            <dt>Config</dt><dd><code>{escape(item["project_config"])}</code></dd>
            <dt>Last Action</dt><dd>{escape(item["last_action"] or "none")}</dd>
          </dl>
          <div class="project-actions">
            <a class="button primary" href="/?project={escape(item["project_key"])}">Open project</a>
            <a class="button" href="/?project={escape(item["project_key"])}">{'Resume' if item["status"] == "active" else 'Start'}</a>
            {f'<button class="danger terminate-project" data-project="{escape(item["project_key"])}" type="button">Terminate run</button>' if item["status"] == "active" else ''}
            <button class="danger delete-project" data-project="{escape(item["project_key"])}" type="button">Delete project</button>
          </div>
        </article>
        """
        for item in project_items
    )
    if not cards:
        cards = '<div class="empty-state">No local project configs found. Create a real project config with the dashboard form or add projects/&lt;project-key&gt;.local.yaml.</div>'
    legacy_notice = (
        f'<section class="legacy"><h2>Legacy active run</h2><p>{escape(legacy_state.project_key)} / {escape(legacy_state.next_node)}</p><code>state/current.json</code></section>'
        if legacy_state is not None
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GoodTeam Projects</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7f4;
      --panel: #ffffff;
      --surface: #f9fbfa;
      --ink: #17211d;
      --muted: #66736e;
      --line: #d9e0dc;
      --accent: #217a62;
      --accent-soft: #e2f2ed;
      --red: #bf3d4d;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Avenir Next", Avenir, "Segoe UI", "PingFang SC", sans-serif; background: var(--bg); color: var(--ink); }}
    main {{ width: min(1120px, calc(100vw - 32px)); margin: 0 auto; padding: 36px 0; }}
    header {{ display: flex; justify-content: space-between; gap: 20px; align-items: end; border-bottom: 1px solid var(--line); padding-bottom: 20px; }}
    h1 {{ margin: 0; font-size: clamp(32px, 5vw, 56px); line-height: 1; letter-spacing: 0; }}
    .tag {{ color: var(--accent); text-transform: uppercase; font-size: 13px; }}
    .projects {{ display: grid; gap: 12px; margin-top: 24px; }}
    .project-card, .legacy {{ display: grid; grid-template-columns: 0.8fr 1.5fr auto; gap: 18px; align-items: center; border: 1px solid var(--line); background: var(--panel); padding: 16px; border-radius: 8px; }}
    .project-active {{ border-color: var(--accent); background: var(--accent-soft); }}
    h2 {{ margin: 0 0 6px; font-size: 19px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); }}
    dl {{ display: grid; grid-template-columns: 80px 1fr; gap: 7px 12px; margin: 0; font-size: 13px; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    code {{ color: var(--muted); }}
    .project-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: end; }}
    .button, button {{ border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 9px 11px; border-radius: 6px; font: inherit; text-decoration: none; }}
    .primary {{ border-color: var(--accent); color: var(--accent); }}
    .danger {{ border-color: var(--red); color: var(--red); }}
    .empty-state {{ border: 1px dashed var(--line); background: var(--panel); color: var(--muted); padding: 16px; }}
    @media (max-width: 780px) {{ header, .project-card, .legacy {{ display: block; }} .project-actions {{ justify-content: start; margin-top: 14px; }} dl {{ margin-top: 14px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="tag">Multi-project control room</div>
        <h1>GoodTeam Projects</h1>
      </div>
      {open_project_link}
    </header>
    {legacy_notice}
    <section class="projects">{cards}</section>
  </main>
  <script>
    document.querySelectorAll(".terminate-project").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          const response = await fetch("/terminate", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: button.dataset.project }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to terminate run.");
          }}
          window.setTimeout(() => window.location.reload(), 250);
        }} catch (error) {{
          button.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll(".delete-project").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const projectKey = button.dataset.project;
        const confirmed = window.confirm(
          "Delete project " + projectKey + " from GoodTeam? This removes the local project config and GoodTeam state only. It will not delete any referenced repos."
        );
        if (!confirmed) {{
          return;
        }}
        button.disabled = true;
        try {{
          const response = await fetch("/project-delete", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: projectKey }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to delete project.");
          }}
          window.setTimeout(() => window.location.reload(), 250);
        }} catch (error) {{
          button.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
  </script>
</body>
</html>"""


def serve(workspace: Path, host: str = "127.0.0.1", port: int = 8788) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            project_key = query.get("project", [""])[0]
            if maybe_send_favicon_response(self, parsed.path):
                return
            if parsed.path == "/artifact":
                try:
                    artifact_key = query.get("key", [""])[0]
                    self._send_json(200, read_artifact(workspace=workspace, project_key=project_key, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            if not project_key:
                body = render_project_overview(workspace).encode("utf-8")
            else:
                project_config = _project_config_for_key(workspace, project_key)
                start_context = build_start_console_context(workspace, project_config=project_config)
                body = render_dashboard(resolve_dashboard_state(workspace, project_key=project_key), start_context).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path not in {"/start", "/approve", "/artifact", "/project-config", "/terminate", "/project-delete", "/retry"}:
                self.send_error(404)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if self.path == "/start":
                    result = start_workflow(
                        workspace=workspace,
                        idea=str(request.get("idea", "")),
                        project_key=str(request.get("project_key", "")),
                        model_config=Path(str(request.get("model_config", "models.yaml"))),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "")),
                        source_type=str(request.get("source_type", "new_project")),
                        project_config=Path(str(request["project_config"])) if str(request.get("project_config", "")).strip() else None,
                        role_model_overrides=[str(value) for value in request.get("role_model_overrides", [])],
                        dry_run=bool(request.get("dry_run", False)),
                    )
                elif self.path == "/approve":
                    result = decide_approval(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        approval=str(request.get("approval", "")),
                        decision=str(request.get("decision", "")),
                    )
                elif self.path == "/artifact":
                    result = save_artifact(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
                    )
                elif self.path == "/terminate":
                    result = terminate_project_run(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif self.path == "/project-delete":
                    result = delete_project(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif self.path == "/retry":
                    result = retry_blocked_workflow(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                else:
                    result = create_local_project_config(
                        workspace=workspace,
                        project_name=str(request.get("project_name", "")),
                        project_key=str(request.get("project_key", "")),
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
                        role_model_overrides=dict(request.get("role_model_overrides", {})),
                    )
                self._send_json(200, result)
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()
