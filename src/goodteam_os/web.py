from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from goodteam_os.models import list_model_profiles
from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.state import AgileDevState, StateStore, create_initial_state


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


def resolve_dashboard_state(workspace: Path) -> AgileDevState:
    current = StateStore(workspace).read_current()
    if current is not None:
        return current
    state = create_initial_state(
        run_id="no-run-yet",
        project_key="GTO",
        idea="Start a workflow run to populate this dashboard.",
    )
    state.current_node = "idle"
    state.next_node = "start"
    return state


def build_role_observations(state: AgileDevState) -> list[dict[str, str]]:
    observations = []
    for role in ROLE_DEFINITIONS:
        artifact = role["artifact"]
        model_config = state.role_models.get(role["key"], {})
        status = "Queued"
        if artifact == "state":
            status = "Active" if state.current_node != "idle" else "Idle"
        elif artifact in state.artifacts:
            status = "Artifact ready"
        if role["name"] == "Product / Requirement" and state.next_node == "await_prd_approval":
            status = "Waiting for approval"
        if role["name"] == "Architect" and state.current_node == "architect":
            status = "Active"

        observations.append(
            {
                "name": role["name"],
                "artifact": artifact,
                "status": status,
                "description": role["description"],
                "next_action": role["next_action"],
                "artifact_path": state.artifacts.get(artifact, ""),
                "model": model_config.get("primary", "unassigned"),
                "mode": model_config.get("mode", "default"),
            }
        )
    return observations


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
        "profiles": profiles,
        "project_key": str(project.get("key", "GTO") or "GTO"),
        "project_name": str(project.get("name", "GoodTeam") or "GoodTeam"),
        "deepseek_configured": bool(deepseek.get("configured", False)),
    }


def render_dashboard(state: AgileDevState, start_context: dict[str, object] | None = None) -> str:
    start_context = start_context or build_start_console_context(Path("."))
    artifact_items = "\n".join(
        f"<li><span>{escape(name.upper())}</span><code>{escape(path)}</code></li>"
        for name, path in state.artifacts.items()
    )
    if not artifact_items:
        artifact_items = "<li><span>No artifacts yet</span><code>Run goodteam-os start first</code></li>"

    approval_items = "\n".join(
        f"<li><span>{escape(name)}</span><strong>{escape(str(decision))}</strong></li>"
        for name, decision in state.approvals.items()
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
            <dt>Next</dt><dd>{escape(role["next_action"])}</dd>
          </dl>
          {f'<code>{escape(role["artifact_path"])}</code>' if role["artifact_path"] else ''}
        </article>
        """
        for role in build_role_observations(state)
    )
    profiles = [str(profile) for profile in start_context.get("profiles", ["balanced"])]
    profile_options = "\n".join(
        f'<option value="{escape(profile)}"{" selected" if profile == "balanced" else ""}>{escape(profile)}</option>'
        for profile in profiles
    )
    starter_idea = (
        "Audit Eastern Birth Chart current docs/backend/frontend state, generate takeover report, "
        "and propose next Plane tickets. Do not modify product code."
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
    .roles {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .role-card {{
      border: 1px solid var(--line);
      background: #111318;
      padding: 14px;
      min-height: 190px;
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
      .roles {{ grid-template-columns: 1fr; }}
      dl {{ grid-template-columns: 1fr; }}
      li {{ display: block; }}
      code {{ display: block; text-align: left; margin-top: 6px; }}
    }}
    @media (min-width: 761px) and (max-width: 1040px) {{
      .roles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
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
      <div class="status">{escape(state.next_node)}</div>
    </header>
    <div class="grid">
      <section class="hero">
        <h2>Current Run</h2>
        <p class="idea">{escape(state.idea)}</p>
        <div class="actions">
          <button class="primary" disabled>Approve PRD</button>
          <button disabled>Request Changes</button>
          <button class="danger" disabled>Reject</button>
        </div>
      </section>
      <section>
        <h2>Start Workflow</h2>
        <div class="console-grid">
          <label>
            Goal
            <textarea id="start-idea">{escape(starter_idea)}</textarea>
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
              <button class="primary" id="copy-command" type="button">Copy Command</button>
            </div>
          </div>
        </div>
        <div class="signal-row">
          <div class="signal"><span>Project</span>{escape(str(start_context["project_name"]))} / {escape(str(start_context["project_key"]))}</div>
          <div class="signal"><span>Config</span><code>{escape(str(start_context["project_config"]))}</code></div>
          <div class="signal"><span>DeepSeek</span>{'configured' if start_context["deepseek_configured"] else 'not configured'}</div>
        </div>
        <code class="command-box" id="start-command"></code>
      </section>
      <section>
        <h2>State</h2>
        <dl>
          <dt>Run</dt><dd>{escape(state.run_id)}</dd>
          <dt>Project</dt><dd>{escape(state.project_key)}</dd>
          <dt>Current Node</dt><dd>{escape(state.current_node)}</dd>
          <dt>Next Node</dt><dd>{escape(state.next_node)}</dd>
        </dl>
      </section>
      <section>
        <h2>Artifacts</h2>
        <ul>{artifact_items}</ul>
      </section>
      <section>
        <h2>Approvals</h2>
        <ul>{approval_items}</ul>
      </section>
      <section class="wide">
        <h2>Role Observability</h2>
        <div class="roles">{role_cards}</div>
      </section>
    </div>
  </main>
  <script>
    const workspace = {str(start_context["workspace"])!r};
    const projectKey = {str(start_context["project_key"])!r};
    const modelConfig = {str(start_context["model_config"])!r};

    function shellQuote(value) {{
      return "'" + value.replaceAll("'", "'\\''") + "'";
    }}

    function updateCommand() {{
      const idea = document.getElementById("start-idea").value.trim();
      const profile = document.getElementById("model-profile").value;
      const dryRun = document.getElementById("dry-run").checked;
      const command = [
        "PYTHONPATH=src",
        ".venv/bin/python -m goodteam_os.cli start",
        shellQuote(idea || "Start GoodTeam workflow"),
        "--workspace", shellQuote(workspace),
        "--project-key", shellQuote(projectKey),
        "--model-config", shellQuote(modelConfig),
        "--model-profile", shellQuote(profile),
        dryRun ? "--dry-run" : ""
      ].filter(Boolean).join(" ");
      document.getElementById("start-command").textContent = command;
    }}

    document.getElementById("start-idea").addEventListener("input", updateCommand);
    document.getElementById("model-profile").addEventListener("change", updateCommand);
    document.getElementById("dry-run").addEventListener("change", updateCommand);
    document.getElementById("copy-command").addEventListener("click", async () => {{
      await navigator.clipboard.writeText(document.getElementById("start-command").textContent);
    }});
    updateCommand();
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

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()
