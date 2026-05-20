import json
from dataclasses import asdict
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from goodteam_os.graph import run_until_next_gate
from goodteam_os.models import apply_role_overrides, list_model_profiles, load_model_profile
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore, create_initial_state


ROLE_DEFINITIONS = [
    {
        "name": "Orchestrator / PM",
        "key": "orchestrator",
        "artifact": "state",
        "description": "Owns graph routing, checkpoints, approvals, and handoff discipline.",
        "next_action": "Keep workflow paused until the current gate changes.",
    },
    {
        "name": "Research",
        "key": "research",
        "artifact": "research_report",
        "description": "Finds market, user, competitor, and risk context when the ticket needs discovery.",
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
        "description": "Splits approved design into 0.5 to 2 day Plane-ready work items.",
        "next_action": "Wait for approved RFC and review report.",
    },
    {
        "name": "Implementation",
        "key": "implementation",
        "artifact": "implementation_handoff",
        "description": "Hands scoped work to FE, BE, Codex, Gemini, or Claude workers.",
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
        "summary": "Split approved design into 0.5 to 2 day Plane-ready tickets.",
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
    dry_run: bool = False,
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("Workflow goal is required.")
    if not dry_run and is_active_run(resolve_dashboard_state(workspace)):
        raise ValueError("An active run already exists. Finish, archive, or resume it before starting another run.")

    state = create_initial_state(
        run_id=str(uuid4()),
        project_key=project_key,
        idea=idea.strip(),
    )
    state.model_profile = model_profile
    state.role_models = apply_role_overrides(
        load_model_profile(model_config, model_profile),
        [],
    )

    if dry_run:
        planned_prd = workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md"
        state.current_node = "prd_review_gate"
        state.next_node = "await_prd_approval"
        payload = asdict(state)
        payload["dry_run"] = True
        payload["planned_artifacts"] = {"prd": str(planned_prd)}
        return payload

    result = run_until_next_gate(state=state, workspace=workspace)
    state_store = StateStore(root=workspace)
    state_store.write_current(result)
    state_store.write_checkpoint(result)
    payload = asdict(result)
    payload["dry_run"] = False
    return payload


def decide_approval(*, workspace: Path, approval: str, decision: str) -> dict[str, object]:
    approved_next = {
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
    for index, step in enumerate(WORKFLOW_STEPS, start=1):
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


def build_approval_reviews(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is None:
        return []

    reviews = []
    approval_artifacts = {
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
) -> dict[str, object]:
    profiles = list_model_profiles(model_config) if model_config.is_file() else ["balanced"]
    project_report: dict[str, object] = {}
    if project_config.is_file():
        project_report = check_project_target(load_project_target(project_config))
    project = dict(project_report.get("project", {}))
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
        "deepseek_configured": bool(deepseek.get("configured", False)),
    }


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
    current_run_html = (
        f"""
        <p class="idea">{escape(state.idea)}</p>
        <div class="signal-row">
          <div class="signal"><span>Run</span>{escape(state.run_id)}</div>
          <div class="signal"><span>Project</span>{escape(state.project_key)}</div>
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
          <div class="console-grid">
            <label>
            Goal
              <textarea id="start-idea" placeholder="Enter the real workflow goal..."></textarea>
            </label>
            <div>
              <label>
                Model Profile
                <select id="model-profile">{profile_options}</select>
              </label>
              <div class="actions">
                <label class="checkbox-label">
                  <input id="dry-run" type="checkbox" checked>
                  Dry run
                </label>
                <button class="primary" id="start-run" type="button">Start Run</button>
              </div>
            </div>
          </div>
          <div class="signal-row">
            <div class="signal"><span>Project</span>{escape(str(start_context["project_name"]))} / {escape(str(start_context["project_key"]))}</div>
            <div class="signal"><span>Config</span><code>{escape(str(start_context["project_config"]))}</code></div>
            <div class="signal"><span>DeepSeek</span>{'configured' if start_context["deepseek_configured"] else 'not configured'}</div>
          </div>
          <code class="command-box" id="start-result">Enter a workflow goal, then start the run.</code>
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

    function updateStartState() {{
      const idea = document.getElementById("start-idea").value.trim();
      const dryRun = document.getElementById("dry-run").checked;
      document.getElementById("start-run").textContent = dryRun ? "Preview Run" : "Start Run";
      if (!idea) {{
        document.getElementById("start-result").textContent = "Enter a workflow goal, then start the run.";
        return;
      }}
      document.getElementById("start-result").textContent = dryRun
        ? "Ready to preview. No state or artifacts will be written."
        : "Ready to start. This will write state, artifacts, and a checkpoint.";
    }}

    document.getElementById("start-idea").addEventListener("input", updateStartState);
    document.getElementById("model-profile").addEventListener("change", updateStartState);
    document.getElementById("dry-run").addEventListener("change", updateStartState);
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
            project_key: projectKey,
            model_config: modelConfig,
            model_profile: document.getElementById("model-profile").value,
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
  </script>
</body>
</html>"""


def serve(workspace: Path, host: str = "127.0.0.1", port: int = 8788) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
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
            if self.path not in {"/start", "/approve"}:
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
                        dry_run=bool(request.get("dry_run", False)),
                    )
                else:
                    result = decide_approval(
                        workspace=workspace,
                        approval=str(request.get("approval", "")),
                        decision=str(request.get("decision", "")),
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
