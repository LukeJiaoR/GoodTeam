from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from goodteam_os.state import AgileDevState, StateStore, create_initial_state


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


def render_dashboard(state: AgileDevState) -> str:
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
      dl {{ grid-template-columns: 1fr; }}
      li {{ display: block; }}
      code {{ display: block; text-align: left; margin-top: 6px; }}
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
    </div>
  </main>
</body>
</html>"""


def serve(workspace: Path, host: str = "127.0.0.1", port: int = 8788) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            body = render_dashboard(resolve_dashboard_state(workspace)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()
