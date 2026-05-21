import json
from dataclasses import asdict
from datetime import datetime, UTC
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from goodteam_os.graph import run_adoption_until_next_gate, run_until_next_gate
from goodteam_os.models import apply_role_overrides, list_model_profiles, load_model_profile
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore


ROLE_DEFINITIONS = [
    {
        "name": "Orchestrator / PM",
        "key": "orchestrator",
        "artifact": "state",
        "description": "Owns graph routing, checkpoints, approvals, and handoff discipline.",
        "next_action": "Keep workflow paused until the current gate changes.",
    },
    {
        "name": "Project Intake",
        "key": "project_intake",
        "artifact": "takeover",
        "description": "Reads existing project config, context files, ownership boundaries, and local-only constraints before PRD.",
        "next_action": "Generate or review takeover-context.md before product work.",
    },
    {
        "name": "Research",
        "key": "research",
        "artifact": "research_report",
        "description": "Finds market, user, competitor, and risk context when the task needs discovery.",
        "next_action": "Run only when the idea lacks enough product context.",
    },
    {
        "name": "Product / Requirement",
        "key": "product_requirement",
        "artifact": "prd",
        "description": "Turns the idea into PRD, scope, user stories, and acceptance criteria.",
        "next_action": "Request approval or changes on the generated PRD.",
    },
    {
        "name": "Architect",
        "key": "architect",
        "artifact": "rfc",
        "description": "Produces RFC, API contract, data model, migration, rollback, and testing strategy.",
        "next_action": "Wait for approved PRD before drafting RFC",
    },
    {
        "name": "Review",
        "key": "review",
        "artifact": "review_report",
        "description": "Checks product, architecture, security, UX, and contract risk.",
        "next_action": "Review RFC after architecture output exists.",
    },
    {
        "name": "Planner",
        "key": "planner",
        "artifact": "task_breakdown",
        "description": "Splits approved design into 0.5 to 2 day GoodTeam local tasks, with optional external tracker sync.",
        "next_action": "Wait for approved RFC and review report.",
    },
    {
        "name": "Implementation",
        "key": "implementation",
        "artifact": "implementation_handoff",
        "description": "Hands scoped work to FE, BE, Codex, Agy, or Claude workers.",
        "next_action": "Wait for planner output before dispatching workers.",
    },
    {
        "name": "QA / Release / Memory",
        "key": "qa",
        "artifact": "qa_report",
        "description": "Owns test plan, QA report, release checklist, rollback note, and memory update.",
        "next_action": "Wait for implementation result artifacts.",
    },
]


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
    worker_backend_mode: str = "manual",
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
    lines = [
        f"name: {name}",
        f"key: {key}",
        "repos:",
    ]
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
    worker_mode = worker_backend_mode.strip() or "manual"
    lines.extend(["worker_backend:", f"  mode: {worker_mode}"])
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
    }


WORKFLOW_STEPS = [
    {
        "key": "product_requirement",
        "title": "Product / Requirement",
        "roles": "Product, Requirement",
        "artifact": "prd",
        "node": "product_requirement",
        "summary": "Create PRD, scope, user stories, acceptance criteria.",
    },
    {
        "key": "prd_review",
        "title": "PRD Review",
        "roles": "Human, Review",
        "approval": "prd",
        "node": "prd_review_gate",
        "summary": "Review generated PRD and decide approve, request changes, or reject.",
    },
    {
        "key": "architecture",
        "title": "Architecture",
        "roles": "Architect",
        "artifact": "rfc",
        "node": "architect",
        "summary": "Draft RFC, data model, API contract, migration and rollback plan.",
    },
    {
        "key": "architecture_review",
        "title": "Architecture Review",
        "roles": "Human, Review, Security",
        "approval": "rfc",
        "node": "rfc_review_gate",
        "summary": "Review architecture, security posture, contract and implementation risks.",
    },
    {
        "key": "planning",
        "title": "Planning",
        "roles": "Planner",
        "artifact": "task_breakdown",
        "node": "planner",
        "summary": "Split approved design into 0.5 to 2 day GoodTeam local tasks.",
    },
    {
        "key": "ui_design",
        "title": "UI Design",
        "roles": "UI",
        "artifact": "ui_spec",
        "node": "ui",
        "summary": "Define page structure, interactions, responsive rules, states, accessibility.",
    },
    {
        "key": "implementation",
        "title": "Implementation",
        "roles": "FE, BE",
        "artifact": "implementation_handoff",
        "node": "implementation",
        "summary": "Dispatch scoped frontend and backend work to the configured worker CLIs.",
    },
    {
        "key": "qa",
        "title": "QA",
        "roles": "QA",
        "artifact": "qa_report",
        "node": "qa",
        "summary": "Run test plan, regression, E2E and edge-case checks.",
    },
    {
        "key": "code_review",
        "title": "Code Review",
        "roles": "Code Review",
        "artifact": "code_review",
        "node": "code_review",
        "summary": "Check quality, security, performance, tests, RFC and API contract alignment.",
    },
    {
        "key": "release",
        "title": "Release",
        "roles": "Release",
        "approval": "release",
        "artifact": "release_notes",
        "node": "release",
        "summary": "Prepare release notes, deployment checklist, feature flags and rollback plan.",
    },
    {
        "key": "retro_memory",
        "title": "Retro / Memory",
        "roles": "Retro / Memory",
        "artifact": "retro_memory",
        "node": "retro_memory",
        "summary": "Update lessons learned, ADRs, standards, memory and postmortem notes.",
    },
]

TAKEOVER_WORKFLOW_STEP = {
    "key": "takeover_review",
    "title": "Takeover Context",
    "roles": "Orchestrator, Product, Human",
    "approval": "takeover",
    "artifact": "takeover",
    "node": "takeover_review_gate",
    "summary": "Read configured project context before PRD, then review whether the team understood the existing project.",
}


def workflow_steps_for_state(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is not None and state.source_type == "existing_project":
        return [TAKEOVER_WORKFLOW_STEP, *WORKFLOW_STEPS]
    return WORKFLOW_STEPS


def resolve_dashboard_state(workspace: Path) -> AgileDevState | None:
    return StateStore(workspace).read_current()


def is_active_run(state: AgileDevState | None) -> bool:
    return state is not None and bool(state.next_node)


def start_workflow(
    *,
    workspace: Path,
    idea: str,
    project_key: str,
    model_config: Path,
    model_profile: str,
    source_type: str = "new_project",
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
    dry_run: bool = False,
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("Workflow goal is required.")
    if not dry_run and is_active_run(resolve_dashboard_state(workspace)):
        raise ValueError("An active run already exists. Finish, archive, or resume it before starting another run.")

    if source_type not in {"new_project", "existing_project"}:
        raise ValueError(f"Unknown workflow source type: {source_type}")

    state = create_initial_state(
        run_id=str(uuid4()),
        project_key=project_key,
        idea=idea.strip(),
    )
    state.source_type = source_type
    state.source_ref = str(project_config) if source_type == "existing_project" else "manual"
    state.model_profile = model_profile
    state.role_models = apply_role_overrides(
        load_model_profile(model_config, model_profile),
        [],
    )

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
    state_store.write_current(result)
    state_store.write_checkpoint(result)
    payload = asdict(result)
    payload["dry_run"] = False
    return payload


def decide_approval(*, workspace: Path, approval: str, decision: str) -> dict[str, object]:
    approved_next = {
        "takeover": "product_requirement",
        "prd": "architect",
        "rfc": "planner",
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
    state = state_store.read_current()
    if state is None:
        raise ValueError("No active run to approve.")
    if approval not in state.approvals:
        raise ValueError(f"Approval gate is not present in current state: {approval}")

    state.approvals[approval] = approval_decision
    state.current_node = f"{approval}_review_gate"
    state.next_node = gate_next_node(state, gate=approval, approved_next=approved_next[approval])
    if approval == "takeover" and approval_decision == ApprovalDecision.APPROVED:
        state_store.write_current(state)
        state_store.write_checkpoint(state)
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else Path("projects/eastern-birth-chart.local.yaml")
        state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    state_store.write_current(state)
    state_store.write_checkpoint(state)
    payload = asdict(state)
    payload["decision"] = str(approval_decision)
    return payload


def build_role_observations(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is None:
        return []

    observations = []
    for role in ROLE_DEFINITIONS:
        artifact = role["artifact"]
        model_config = state.role_models.get(role["key"], {})
        status = "No activity"
        if artifact == "state":
            status = "Active"
        elif artifact in state.artifacts:
            status = "Artifact ready"
        if role["name"] == "Project Intake" and state.next_node == "await_takeover_approval":
            status = "Waiting for approval"
        if role["name"] == "Product / Requirement" and state.next_node == "await_prd_approval":
            status = "Waiting for approval"
        if role["name"] == "Architect" and state.current_node == "architect":
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


def _resolve_registered_artifact(workspace: Path, artifact_key: str) -> tuple[AgileDevState, Path]:
    state = StateStore(workspace).read_current()
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


def read_artifact(*, workspace: Path, artifact_key: str) -> dict[str, object]:
    _, path = _resolve_registered_artifact(workspace, artifact_key)
    return {
        "artifact": artifact_key,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def save_artifact(*, workspace: Path, artifact_key: str, content: str) -> dict[str, object]:
    state, path = _resolve_registered_artifact(workspace, artifact_key)
    path.write_text(content, encoding="utf-8")

    edit_log = workspace / "state" / "edit-log.jsonl"
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
    approval_artifacts = {
        "takeover": "takeover",
        "prd": "prd",
        "rfc": "rfc",
        "security": "review_report",
        "release": "release_notes",
    }
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
            }
        )
    return reviews


def build_start_console_context(
    workspace: Path,
    model_config: Path = Path("models.yaml"),
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
    project_base: Path = Path("/Users/luke/WorkSpace/codeAi"),
) -> dict[str, object]:
    profiles = list_model_profiles(model_config) if model_config.is_file() else ["balanced"]
    project_report: dict[str, object] = {}
    if project_config.is_file():
        project_report = check_project_target(load_project_target(project_config))
    project = dict(project_report.get("project", {}))
    target = load_project_target(project_config) if project_config.is_file() else {}
    providers = dict(project_report.get("providers", {}))
    deepseek = dict(providers.get("deepseek", {}))
    return {
        "workspace": str(workspace),
        "model_config": str(model_config),
        "project_config": str(project_config),
        "project_report": project_report,
        "profiles": profiles,
        "project_key": str(project.get("key", "GTO") or "GTO"),
        "project_name": str(project.get("name", "GoodTeam") or "GoodTeam"),
        "task_backend": normalize_task_backend(target.get("task_backend", {"mode": "local", "sync": []})),
        "worker_backend": normalize_worker_backend(target.get("worker_backend", {"mode": "manual"})),
        "deepseek_configured": bool(deepseek.get("configured", False)),
        "project_base": str(project_base),
        "candidate_dirs": discover_project_directories(project_base),
    }


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
    return {"mode": str(backend.get("mode", "manual") or "manual")}


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
            }
        )
    return items


def render_dashboard(state: AgileDevState | None, start_context: dict[str, object] | None = None) -> str:
    start_context = start_context or build_start_console_context(Path("."))
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
          {f'<pre class="artifact-preview">{escape(review["content"])}</pre>' if review["content"] else '<div class="empty-state">No artifact content available for this approval yet.</div>'}
        </article>
        """
        for review in build_approval_reviews(state)
    )
    if not approval_review_cards:
        approval_review_cards = '<div class="empty-state">No approval review items available.</div>'
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
    worker_backend = normalize_worker_backend(start_context.get("worker_backend", {"mode": "manual"}))
    worker_backend_mode = str(worker_backend.get("mode", "manual"))
    project_root = Path(state.context_packet_path).parents[2] if state is not None and state.context_packet_path else None
    worker_job_items = "\n".join(
        f"<li><span>{escape(item['role'])} / {escape(item['adapter'])}</span><code>{escape(item['status'])} · {escape(item['id'])}</code></li>"
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
    profile_options = "\n".join(
        f'<option value="{escape(profile)}"{" selected" if profile == "balanced" else ""}>{escape(profile)}</option>'
        for profile in profiles
    )
    candidate_dirs = [dict(item) for item in start_context.get("candidate_dirs", [])]
    directory_options = "\n".join(
        f'<option value="{escape(str(item["path"]))}">{escape(str(item["name"]))}</option>'
        for item in candidate_dirs
    )
    directory_options_with_empty = f'<option value="">Not selected</option>{directory_options}'
    current_run_html = (
        f"""
        <p class="idea">{escape(state.idea)}</p>
        <div class="signal-row">
          <div class="signal"><span>Run</span>{escape(state.run_id)}</div>
          <div class="signal"><span>Project</span>{escape(state.project_key)}</div>
          <div class="signal"><span>Mode</span>{escape(state.source_type)}</div>
          <div class="signal"><span>Next</span>{escape(state.next_node)}</div>
        </div>
        """
        if state is not None
        else '<div class="empty-state">No active run. Start a workflow to create state/current.json.</div>'
    )
    state_panel_html = (
        f"""
        <dl>
          <dt>Run</dt><dd>{escape(state.run_id)}</dd>
          <dt>Project</dt><dd>{escape(state.project_key)}</dd>
          <dt>Current Node</dt><dd>{escape(state.current_node)}</dd>
          <dt>Next Node</dt><dd>{escape(state.next_node)}</dd>
          <dt>Model Profile</dt><dd>{escape(state.model_profile)}</dd>
          <dt>Source</dt><dd>{escape(state.source_type)} / {escape(state.source_ref)}</dd>
          <dt>Project Memory</dt><dd>{escape(state.context_packet_path or "not initialized")}</dd>
          <dt>Local Task Ledger</dt><dd>{escape(state.artifacts.get("task_ledger", "not initialized"))}</dd>
        </dl>
        """
        if state is not None
        else '<div class="empty-state">state/current.json not found.</div>'
    )
    header_status = state.next_node if state is not None else "no_active_run"
    start_workflow_section = (
        ""
        if active_run
        else f"""
        <section>
          <h2>Start Workflow</h2>
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
                  <option value="manual"{" selected" if worker_backend_mode == "manual" else ""}>Manual / human adapter</option>
                  <option value="noop"{" selected" if worker_backend_mode == "noop" else ""}>Noop / protocol dry run</option>
                  <option value="supervised"{" selected" if worker_backend_mode == "supervised" else ""}>Supervised CLI adapters</option>
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
            <div class="signal"><span>DeepSeek</span>{'configured' if start_context["deepseek_configured"] else 'not configured'}</div>
            <div class="signal"><span>Directory Base</span><code>{escape(str(start_context["project_base"]))}</code></div>
            <div class="signal"><span>Task Backend</span>{escape(task_sync_label)}</div>
            <div class="signal"><span>Worker Backend</span>{escape(worker_backend_mode)}</div>
          </div>
          <code class="command-box" id="start-result">Adopt mode reads the project config first and generates takeover-context.md before PRD.</code>
        </section>
        """
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
    document.getElementById("dry-run").addEventListener("change", updateStartState);
    function updateAdapterConfigVisibility() {{
      document.getElementById("plane-config").hidden = document.getElementById("task-sync").value !== "plane";
    }}
    document.getElementById("task-sync").addEventListener("change", updateAdapterConfigVisibility);
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
            worker_backend_mode: document.getElementById("worker-backend-mode").value
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
            source_type: startMode,
            project_config: document.getElementById("project-config").value,
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

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GoodTeam AI Team OS</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101113;
      --panel: #181a1f;
      --panel-2: #20242b;
      --text: #f4f0e8;
      --muted: #a7adba;
      --line: #343a46;
      --accent: #d6ff63;
      --blue: #7dd3fc;
      --red: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
      background: var(--bg);
      color: var(--text);
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
      background: #151a15;
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
    .wide {{
      grid-column: 1 / -1;
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
      background: #151a15;
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
      background: #0f1115;
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
      min-height: 42px;
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
      background: #0d0f13;
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
      background: #101318;
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
    .flow-card {{
      display: grid;
      grid-template-columns: 40px 1fr;
      gap: 12px;
      border: 1px solid var(--line);
      background: #111318;
      padding: 14px;
      min-height: 172px;
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
      background: #171a10;
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
      background: #111318;
      padding: 14px;
      min-height: 190px;
    }}
    .approval-card {{
      border: 1px solid var(--line);
      background: #111318;
      padding: 14px;
      margin-top: 12px;
    }}
    .artifact-preview {{
      margin: 14px 0 0;
      padding: 14px;
      border: 1px solid var(--line);
      background: #0d0f13;
      color: var(--text);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 520px;
      overflow: auto;
      line-height: 1.45;
      font: inherit;
    }}
    .artifact-editor {{
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #0d0f13;
      padding: 14px;
    }}
    .artifact-editor textarea {{
      min-height: 420px;
      line-height: 1.45;
      white-space: pre;
      overflow: auto;
      resize: vertical;
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
    button {{
      border: 1px solid var(--line);
      background: #0f1115;
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
    }}
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
      <button class="tab-button" data-tab="workers" type="button">Workers</button>
      <button class="tab-button" data-tab="approvals" type="button">Approvals</button>
      <button class="tab-button" data-tab="system" type="button">System</button>
    </nav>
    <div class="tab-panel active" id="tab-overview">
      <div class="grid">
        <section class="hero">
          <h2>Current Run</h2>
          {current_run_html}
        </section>
        {start_workflow_section}
        <section>
          <h2>Role Summary</h2>
          <div class="signal-row compact">{role_summary_items}</div>
        </section>
        {current_work_html}
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
            <dt>Task Backend</dt><dd>{escape(task_sync_label)}</dd>
            <dt>Worker Backend</dt><dd>{escape(worker_backend_mode)}</dd>
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
          const response = await fetch("/artifact?key=" + encodeURIComponent(artifact));
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
            body: JSON.stringify({{ artifact, content: textarea.value }})
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
  </script>
</body>
</html>"""


def serve(workspace: Path, host: str = "127.0.0.1", port: int = 8788) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/artifact":
                try:
                    artifact_key = parse_qs(parsed.query).get("key", [""])[0]
                    self._send_json(200, read_artifact(workspace=workspace, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            start_context = build_start_console_context(workspace)
            body = render_dashboard(resolve_dashboard_state(workspace), start_context).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path not in {"/start", "/approve", "/artifact", "/project-config"}:
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
                        source_type=str(request.get("source_type", "new_project")),
                        project_config=Path(str(request.get("project_config", "projects/eastern-birth-chart.local.yaml"))),
                        dry_run=bool(request.get("dry_run", False)),
                    )
                elif self.path == "/approve":
                    result = decide_approval(
                        workspace=workspace,
                        approval=str(request.get("approval", "")),
                        decision=str(request.get("decision", "")),
                    )
                elif self.path == "/artifact":
                    result = save_artifact(
                        workspace=workspace,
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
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
                        worker_backend_mode=str(request.get("worker_backend_mode", "manual")),
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
