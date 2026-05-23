import json
from pathlib import Path

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import (
    build_action_items,
    build_project_overview_items,
    build_role_observations,
    build_approval_reviews,
    build_start_console_context,
    build_workflow_steps,
    decide_approval,
    discover_project_configs,
    discover_project_directories,
    create_local_project_config,
    delete_project,
    read_artifact,
    read_artifact_preview,
    render_markdown_preview,
    render_dashboard,
    render_project_overview,
    maybe_send_favicon_response,
    retry_blocked_workflow,
    terminate_project_run,
    resolve_dashboard_state,
    save_artifact,
    is_active_run,
    start_workflow,
)


def test_render_dashboard_shows_current_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"
    state.context_packet_path = "/tmp/project/.goodteam/memory/project-memory.md"

    html = render_dashboard(state)

    assert "GoodTeam AI Team OS" in html
    assert "await_prd_approval" in html
    assert "PRD" in html
    assert "/tmp/prd.md" in html
    assert "Role Observability" in html
    assert "Product / Requirement" in html
    assert "Waiting for approval" in html
    assert "Architect" in html
    assert "Model" in html
    assert "Start Workflow" not in html
    assert "Start Run" not in html
    assert "Copy Command" not in html
    assert "Enter the real workflow goal" not in html
    assert "Start a workflow run to populate this dashboard" not in html
    assert "Overview" in html
    assert "Roles" in html
    assert "Artifacts" in html
    assert "Actions" in html
    assert "Approvals" in html
    assert "System" in html
    assert "Worker Jobs" in html
    assert "Role Summary" in html
    assert "Workflow Waterfall" in html
    assert "Needs review" in html
    assert "Review PRD" in html
    assert "PRD Review" in html
    assert "Approve" in html
    assert "Request Changes" in html
    assert "Reject" in html
    assert "Edit Artifact" in html
    assert "Save Markdown" in html
    assert "Worker Model" in html
    assert "gpt-5.5" in html
    assert "Build AI Team OS" in html
    assert "artifact-preview" in html
    assert "Project Target" in html
    assert "/tmp/project/.goodteam/memory/project-memory.md" in html
    assert "Local Task Ledger" in html
    assert "Task Backend" in html
    assert "Worker Backend" in html
    assert "Active run in progress" not in html


def test_render_dashboard_shows_worker_blockers():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.blockers = [
        "implementation worker failed: Codex CLI exited with code 1.",
        "qa worker failed: Codex CLI exited with code 1.",
    ]

    html = render_dashboard(state)

    assert "Blocked" in html
    assert "implementation worker failed" in html
    assert "qa worker failed" in html
    assert "Retry blocked workers" in html
    assert "/retry" in html
    assert "await_release_approval" not in html


def test_render_dashboard_shows_complete_status_when_run_is_closed():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "complete"
    state.next_node = ""

    html = render_dashboard(state)

    assert '<div class="status">complete</div>' in html
    assert "<div class=\"signal\"><span>Next</span>complete</div>" in html


def test_render_dashboard_prioritizes_completion_summary_for_closed_run():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "complete"
    state.next_node = ""

    html = render_dashboard(state)

    assert '<link rel="icon" href="data:,">' in html
    assert "Run Complete" in html
    assert "Open project overview" in html
    assert '<section class="hero wide">' in html
    assert '<details class="start-workflow-drawer">' in html
    assert '<h2>Start Workflow</h2>' not in html


def test_maybe_send_favicon_response_returns_204_without_network_server():
    class FakeHandler:
        def __init__(self):
            self.calls = []

        def send_response(self, status):
            self.calls.append(("response", status))

        def send_header(self, name, value):
            self.calls.append(("header", name, value))

        def end_headers(self):
            self.calls.append(("end",))

    handler = FakeHandler()

    handled = maybe_send_favicon_response(handler, "/favicon.ico")

    assert handled is True
    assert handler.calls == [
        ("response", 204),
        ("header", "Content-Length", "0"),
        ("end",),
    ]
    assert maybe_send_favicon_response(handler, "/") is False


def test_resolve_dashboard_state_reads_existing_state(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    resolved = resolve_dashboard_state(tmp_path)

    assert resolved.run_id == "run-1"
    assert resolved.next_node == "await_prd_approval"


def test_resolve_dashboard_state_reads_project_scoped_state(tmp_path):
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for cross-platform post production.",
    )
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    resolved = resolve_dashboard_state(tmp_path, project_key="CONTENTOPS")

    assert resolved.run_id == "run-1"
    assert resolved.project_key == "CONTENTOPS"
    assert resolved.next_node == "await_prd_approval"


def test_resolve_dashboard_state_returns_none_without_current_state(tmp_path):
    assert resolve_dashboard_state(tmp_path) is None


def test_is_active_run_detects_current_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.next_node = "await_prd_approval"

    assert is_active_run(state) is True
    assert is_active_run(None) is False


def test_render_dashboard_without_state_shows_only_real_empty_status():
    html = render_dashboard(None)

    assert "no_active_run" in html
    assert "No active run" in html
    assert "Start New Project" in html
    assert "Adopt Existing Project" in html
    assert "Start new project" in html
    assert "Start adoption" in html
    assert "Enter the real workflow goal" in html
    assert "no-run-yet" not in html
    assert "Start a workflow run to populate this dashboard" not in html
    assert "Approve PRD" not in html


def test_build_role_observations_maps_artifacts_to_role_statuses():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"
    state.role_models = {
        "orchestrator": {"primary": "codex:gpt-5.5", "mode": "short-context"},
        "product_requirement": {"primary": "codex:gpt-5.5", "draft": "codex:gpt-5.5"},
        "architect": {"primary": "codex:gpt-5.5"},
        "implementation": {"primary": "codex:gpt-5.5", "mode": "worker-dispatch"},
    }

    observations = build_role_observations(state)
    product = next(role for role in observations if role["name"] == "Product / Requirement")
    architect = next(role for role in observations if role["name"] == "Architect")
    orchestrator = next(role for role in observations if role["name"] == "Orchestrator / PM")
    implementation = next(role for role in observations if role["name"] == "Implementation")

    assert orchestrator["model"] == "codex:gpt-5.5"
    assert orchestrator["mode"] == "short-context"
    assert product["status"] == "Waiting for approval"
    assert product["artifact"] == "prd"
    assert product["model"] == "codex:gpt-5.5"
    assert architect["status"] == "No activity"
    assert implementation["model"] == "codex:gpt-5.5"


def test_build_role_observations_marks_next_role_ready():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"

    observations = build_role_observations(state)
    architect = next(role for role in observations if role["name"] == "Architect")

    assert architect["status"] == "Ready"


def test_build_workflow_steps_marks_current_review_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"

    steps = build_workflow_steps(state)
    product = next(step for step in steps if step["key"] == "product_requirement")
    prd_review = next(step for step in steps if step["key"] == "prd_review")
    architecture = next(step for step in steps if step["key"] == "architecture")

    assert product["status"] == "done"
    assert product["artifact_path"] == "/tmp/prd.md"
    assert prd_review["status"] == "review-needed"
    assert prd_review["action_label"] == "Review PRD"
    assert prd_review["approval"] == "prd"
    assert architecture["status"] == "not-started"


def test_build_workflow_steps_for_existing_project_starts_with_takeover_review():
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.source_type = "existing_project"
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.artifacts["takeover"] = "/tmp/takeover-context.md"
    state.approvals["takeover"] = "pending"

    steps = build_workflow_steps(state)
    takeover = steps[0]
    prd = next(step for step in steps if step["key"] == "product_requirement")

    assert takeover["key"] == "takeover_review"
    assert takeover["status"] == "review-needed"
    assert takeover["action_label"] == "Review TAKEOVER"
    assert takeover["artifact_path"] == "/tmp/takeover-context.md"
    assert prd["status"] == "not-started"


def test_build_workflow_steps_marks_next_step_ready():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"
    state.artifacts["prd"] = "/tmp/prd.md"

    steps = build_workflow_steps(state)
    architecture = next(step for step in steps if step["key"] == "architecture")

    assert architecture["status"] == "ready"
    assert architecture["status_label"] == "Ready"
    assert "no worker execution" in architecture["feedback"]


def test_build_workflow_steps_without_state_is_not_started():
    steps = build_workflow_steps(None)

    assert steps[0]["status"] == "not-started"
    assert all(step["artifact_path"] == "" for step in steps)


def test_build_approval_reviews_reads_prd_artifact(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    reviews = build_approval_reviews(state)
    prd_review = next(review for review in reviews if review["approval"] == "prd")

    assert prd_review["artifact"] == "prd"
    assert prd_review["artifact_path"] == str(prd)
    assert "# PRD" in prd_review["content"]
    assert prd_review["is_pending"] is True
    assert prd_review["is_actionable"] is True
    rfc_review = next(review for review in reviews if review["approval"] == "rfc")
    assert rfc_review["is_actionable"] is False


def test_build_approval_reviews_includes_multi_role_prd_reviewers(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    reviews = build_approval_reviews(state)
    prd_review = next(review for review in reviews if review["approval"] == "prd")

    assert prd_review["review_roles"] == [
        "Product / Requirement",
        "Architect",
        "Review / QA",
    ]


def test_render_dashboard_renders_markdown_artifact_preview(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview **me**", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    html = render_dashboard(state)

    assert '<div class="artifact-preview markdown-preview">' in html
    assert '<h1 id="prd">PRD</h1>' in html
    assert "<p>Review <strong>me</strong></p>" in html


def test_read_artifact_preview_truncates_long_content(tmp_path):
    artifact = tmp_path / "long.md"
    artifact.write_text("a" * 20, encoding="utf-8")

    preview = read_artifact_preview(str(artifact), max_chars=5)

    assert preview == "aaaaa\n\n[Preview truncated]"


def test_render_markdown_preview_outputs_readable_safe_html():
    html = render_markdown_preview(
        "# PRD\n\nReview **bold** text.\n\n- One\n- Two\n\n```html\n<script>alert(1)</script>\n```"
    )

    assert '<h1 id="prd">PRD</h1>' in html
    assert "<p>Review <strong>bold</strong> text.</p>" in html
    assert "<ul>" in html
    assert "<li>One</li>" in html
    assert "<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>" in html
    assert "<script>" not in html


def test_read_artifact_reads_current_run_artifact(tmp_path):
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Takeover\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["takeover"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = read_artifact(workspace=tmp_path, artifact_key="takeover")

    assert payload["artifact"] == "takeover"
    assert payload["path"] == str(artifact)
    assert payload["content"] == "# Takeover\n\nReview me"


def test_save_artifact_updates_current_run_artifact_and_logs_edit(tmp_path):
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# PRD\n\nBefore", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD\n\nAfter")

    assert payload["artifact"] == "prd"
    assert payload["bytes"] == len("# PRD\n\nAfter".encode("utf-8"))
    assert artifact.read_text(encoding="utf-8") == "# PRD\n\nAfter"
    edit_log = tmp_path / "state" / "edit-log.jsonl"
    assert edit_log.is_file()
    assert '"artifact": "prd"' in edit_log.read_text(encoding="utf-8")
    action_log = tmp_path / "state" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    assert actions[-1]["action"] == "artifact_saved"
    assert actions[-1]["artifact"] == "prd"


def test_save_artifact_rejects_unregistered_artifact(tmp_path):
    StateStore(tmp_path).write_current(create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio"))

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "Artifact is not registered" in str(exc)
    else:
        raise AssertionError("Expected unregistered artifact to be rejected")


def test_save_artifact_rejects_path_outside_current_run(tmp_path):
    outside = tmp_path / "notes.md"
    outside.write_text("# Outside", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["prd"] = str(outside)
    StateStore(tmp_path).write_current(state)

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "current run artifacts directory" in str(exc)
    else:
        raise AssertionError("Expected outside path to be rejected")


def test_build_start_console_context_reads_project_and_model_config(tmp_path):
    project_config = tmp_path / "real-project.local.yaml"
    project_config.write_text(
        "name: Real Product\nkey: REAL\nmodel_profile: cheap\ndelivery_mode: multi_service\nrole_model_overrides:\n  architect: codex:gpt-5.5\n",
        encoding="utf-8",
    )
    context = build_start_console_context(
        workspace=tmp_path / "goodteam",
        model_config=Path("models.yaml"),
        project_config=project_config,
    )

    assert context["project_key"] == "REAL"
    assert context["project_name"] == "Real Product"
    assert "balanced" in context["profiles"]
    assert context["project_config"] == str(project_config)
    assert context["task_backend"] == {"mode": "local", "sync": []}
    assert context["worker_backend"] == {"mode": "codex"}
    assert context["model_profile"] == "cheap"
    assert context["delivery_mode"] == "multi_service"
    assert context["role_model_overrides"]["architect"] == "codex:gpt-5.5"
def test_build_start_console_context_scopes_directory_candidates_to_project_repos(tmp_path):
    project_root = tmp_path / "contentops"
    docs = project_root / "docs"
    backend = project_root / "backend"
    frontend = project_root / "frontend"
    unrelated = tmp_path / "eastern-birth-chart"
    for path in [docs, backend, frontend, unrelated]:
        path.mkdir(parents=True)
    project_config = tmp_path / "workspace" / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {docs}",
                f"  backend: {backend}",
                f"  frontend: {frontend}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    context = build_start_console_context(
        workspace=tmp_path / "workspace",
        model_config=Path("models.yaml"),
        project_config=project_config,
        project_base=tmp_path,
    )

    candidate_paths = {item["path"] for item in context["candidate_dirs"]}
    assert context["project_base"] == str(project_root)
    assert str(docs) in candidate_paths
    assert str(backend) in candidate_paths
    assert str(frontend) in candidate_paths
    assert str(unrelated) not in candidate_paths


def test_discover_project_configs_lists_only_local_configs(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "real.local.yaml").write_text("name: Real Product\nkey: REAL\n", encoding="utf-8")
    (projects / "ignored.example.yaml").write_text("name: Ignored Config\nkey: IGNORED\n", encoding="utf-8")

    configs = discover_project_configs(tmp_path)

    assert configs == [projects / "real.local.yaml"]


def test_build_project_overview_items_lists_project_configs_with_run_status(tmp_path):
    content_ops_config = tmp_path / "projects" / "contentops.local.yaml"
    gto_config = tmp_path / "projects" / "gto.local.yaml"
    content_ops_config.parent.mkdir()
    content_ops_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\nmodel_profile: cheap\n", encoding="utf-8")
    gto_config.write_text("name: GoodTeam\nkey: GTO\nmodel_profile: max_quality\n", encoding="utf-8")
    active = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio that turns research into posts for TikTok, Reddit, X, and LinkedIn.",
    )
    active.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(active)

    items = build_project_overview_items(tmp_path, model_config=Path("models.yaml"))

    by_key = {item["project_key"]: item for item in items}
    assert by_key["CONTENTOPS"]["status"] == "active"
    assert by_key["CONTENTOPS"]["next_node"] == "await_prd_approval"
    assert by_key["CONTENTOPS"]["model_profile"] == "cheap"
    assert by_key["GTO"]["status"] == "idle"
    assert by_key["GTO"]["model_profile"] == "max_quality"


def test_discover_project_directories_lists_immediate_children(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("nope", encoding="utf-8")

    dirs = discover_project_directories(tmp_path)

    assert dirs == [
        {"name": "api", "path": str(tmp_path / "api")},
        {"name": "docs", "path": str(tmp_path / "docs")},
    ]


def test_discover_project_directories_lists_nested_project_repos(tmp_path):
    project = tmp_path / "product"
    docs = project / "product-docs"
    api = project / "product-api"
    ignored = project / "product-api" / "node_modules"
    for path in [docs, api, ignored]:
        path.mkdir(parents=True)

    dirs = discover_project_directories(tmp_path)
    paths = [item["path"] for item in dirs]

    assert str(project) in paths
    assert str(docs) in paths
    assert str(api) in paths
    assert str(ignored) not in paths


def test_create_local_project_config_writes_selected_directories(tmp_path):
    docs = tmp_path / "content-research"
    backend = tmp_path / "content-pipeline-api"
    frontend = tmp_path / "publishing-dashboard"
    for path in [docs, backend, frontend]:
        path.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        docs_path=str(docs),
        backend_path=str(backend),
        frontend_path=str(frontend),
        task_sync=["plane"],
        plane_api_host_url="https://api.plane.so/",
        plane_workspace_slug="leader_kanban",
        plane_project_id="project-123",
        plane_api_key_env="PLANE_API_KEY",
        plane_mcp_server="plane",
        worker_backend_mode="codex",
        model_profile="cheap",
        delivery_mode="multi_service",
        role_model_overrides={"architect": "codex:gpt-5.5"},
    )

    config = Path(payload["project_config"])
    assert config == tmp_path / "projects" / "contentops.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "name: AI Content Studio" in content
    assert f"  docs: {docs}" in content
    assert f"  backend: {backend}" in content
    assert f"  frontend: {frontend}" in content
    assert "task_backend:" in content
    assert "  mode: local" in content
    assert "  sync: plane" in content
    assert "  plane:" in content
    assert "    api_host_url: https://api.plane.so/" in content
    assert "    workspace_slug: leader_kanban" in content
    assert "    project_id: project-123" in content
    assert "    api_key_env: PLANE_API_KEY" in content
    assert "    mcp_server: plane" in content
    assert "api_key:" not in content
    assert payload["task_backend"]["plane"]["workspace_slug"] == "leader_kanban"
    assert "worker_backend:" in content
    assert "  mode: codex" in content
    assert "model_profile: cheap" in content
    assert "delivery_mode: multi_service" in content
    assert "role_model_overrides:" in content
    assert "  architect: codex:gpt-5.5" in content
    assert payload["worker_backend"] == {"mode": "codex"}
    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "multi_service"
    assert payload["role_model_overrides"] == {"architect": "codex:gpt-5.5"}


def test_create_local_project_config_defaults_to_local_only_task_backend(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        docs_path=str(docs),
    )

    content = Path(payload["project_config"]).read_text(encoding="utf-8")
    assert "task_backend:" in content
    assert "  mode: local" in content
    assert "  sync:" in content
    assert "delivery_mode: init_project" in content
    assert payload["task_backend"] == {"mode": "local", "sync": []}
    assert payload["delivery_mode"] == "init_project"


def test_start_workflow_writes_state_and_checkpoint(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Build an AI content studio that collects source material and drafts posts for TikTok, Reddit, X, and LinkedIn.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "CONTENTOPS"
    assert payload["delivery_mode"] == "init_project"
    assert payload["next_node"] == "await_prd_approval"
    assert payload["dry_run"] is False
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "current.json").is_file()
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / payload["run_id"] / "prd_review_gate.json").is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    assert [action["action"] for action in actions] == ["workflow_started", "prd_generated"]
    assert actions[0]["actor"] == "human"
    assert actions[1]["actor"] == "system"
    assert actions[1]["artifact"] == "prd"


def test_start_workflow_can_adopt_existing_project_first(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "codex.md").write_text(
        "# Memory\n- The product collects source material and drafts platform-specific AI usage posts.\n",
        encoding="utf-8",
    )
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=tmp_path,
        idea="Take over the AI content studio and prepare cross-platform publishing workflows.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="existing_project",
        project_config=project_config,
    )

    assert payload["source_type"] == "existing_project"
    assert payload["next_node"] == "await_takeover_approval"
    assert payload["dry_run"] is False
    assert Path(payload["artifacts"]["takeover"]).is_file()
    assert "prd" not in payload["artifacts"]
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / payload["run_id"] / "takeover_review_gate.json").is_file()


def test_start_workflow_rejects_new_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(existing)

    try:
        start_workflow(
            workspace=tmp_path,
            idea="Start another AI content studio run",
            project_key="CONTENTOPS",
            model_config=Path("models.yaml"),
            model_profile="balanced",
        )
    except ValueError as exc:
        assert "active run already exists" in str(exc)
    else:
        raise AssertionError("Expected active run guard to reject a new run")


def test_start_workflow_dry_run_does_not_write_state(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Preview AI content studio planning without writing state",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="cheap",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert payload["model_profile"] == "cheap"
    assert "planned_artifacts" in payload
    assert not (tmp_path / "state" / "projects" / "CONTENTOPS" / "current.json").exists()


def test_start_workflow_allows_dry_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(existing)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Preview another AI content studio run",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert StateStore(tmp_path).read_project_current("CONTENTOPS").run_id == "run-1"


def test_start_workflow_allows_different_projects_to_run_in_parallel(tmp_path):
    content_ops = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    content_ops.current_node = "prd_review_gate"
    content_ops.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(content_ops)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Start GoodTeam run",
        project_key="GTO",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "GTO"
    assert StateStore(tmp_path).read_project_current("CONTENTOPS").run_id == "run-1"
    assert StateStore(tmp_path).read_project_current("GTO").run_id == payload["run_id"]


def test_start_workflow_uses_project_model_profile_and_overrides(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: cheap",
                "delivery_mode: mvp",
                "role_model_overrides:",
                "  architect: codex:gpt-5.5",
                "  frontend: codex:gpt-5.5",
            ]
        ),
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=tmp_path,
        idea="Use project models for the AI content studio",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="",
        project_config=project_config,
    )

    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "mvp"
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.5"
    assert payload["role_models"]["architect"]["primary"] == "codex:gpt-5.5"
    assert payload["role_models"]["frontend"]["primary"] == "codex:gpt-5.5"


def test_start_workflow_rejects_empty_goal(tmp_path):
    try:
        start_workflow(
            workspace=tmp_path,
            idea=" ",
            project_key="CONTENTOPS",
            model_config=Path("models.yaml"),
            model_profile="balanced",
        )
    except ValueError as exc:
        assert "Workflow goal is required" in str(exc)
    else:
        raise AssertionError("Expected empty workflow goal to fail")


def test_decide_approval_approves_prd_and_generates_rfc(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="approved",
    )

    assert payload["approvals"]["prd"] == "approved"
    assert payload["current_node"] == "rfc_review_gate"
    assert payload["next_node"] == "await_rfc_approval"
    assert "rfc" in payload["artifacts"]
    assert Path(payload["artifacts"]["rfc"]).is_file()
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / "run-1" / "rfc_review_gate.json").is_file()


def test_decide_approval_approves_rfc_and_runs_to_release_gate(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    state.approvals["prd"] = "approved"
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="approved",
    )

    assert payload["approvals"]["rfc"] == "approved"
    assert payload["current_node"] == "release_review_gate"
    assert payload["next_node"] == "await_release_approval"
    for artifact in ["review_report", "task_breakdown", "ui_spec", "implementation_handoff", "qa_report", "code_review", "release_notes"]:
        assert artifact in payload["artifacts"]
        assert Path(payload["artifacts"][artifact]).is_file()


def test_decide_approval_approves_release_and_completes_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "release_review_gate"
    state.next_node = "await_release_approval"
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "pending"})
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="release",
        decision="approved",
    )

    assert payload["approvals"]["release"] == "approved"
    assert payload["current_node"] == "complete"
    assert payload["next_node"] == ""
    assert "retro_memory" in payload["artifacts"]
    assert Path(payload["artifacts"]["retro_memory"]).is_file()


def test_decide_approval_rejects_gate_that_is_not_actionable(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.blockers = ["implementation worker failed: Codex CLI exited with code 1."]
    StateStore(tmp_path).write_project_current(state)

    try:
        decide_approval(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            approval="release",
            decision="approved",
        )
    except ValueError as exc:
        assert "not currently actionable" in str(exc)
    else:
        raise AssertionError("Expected blocked release approval to fail")


def test_retry_blocked_workflow_reruns_workers_to_release_gate(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "pending"})
    state.blockers = ["implementation worker failed: Codex CLI exited with code 1."]
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["current_node"] == "release_review_gate"
    assert payload["next_node"] == "await_release_approval"
    assert payload["blockers"] == []
    assert "release_notes" in payload["artifacts"]
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / "run-1" / "release_review_gate.json").is_file()


def test_retry_blocked_workflow_rejects_non_blocked_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "release_review_gate"
    state.next_node = "await_release_approval"
    StateStore(tmp_path).write_project_current(state)

    try:
        retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")
    except ValueError as exc:
        assert "not blocked" in str(exc)
    else:
        raise AssertionError("Expected retry to reject non-blocked run")


def test_decide_approval_approves_takeover_and_generates_prd(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "codex.md").write_text(
        "# Memory\n- Existing content workflow collects AI usage source material and drafts posts.\n",
        encoding="utf-8",
    )
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    state.artifacts["takeover"] = str(tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md")
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="takeover",
        decision="approved",
    )

    assert payload["approvals"]["takeover"] == "approved"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / "run-1" / "prd_review_gate.json").is_file()


def test_decide_approval_can_request_changes(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
    )

    assert payload["approvals"]["prd"] == "changes_requested"
    assert payload["next_node"] == "revise_prd"


def test_decide_approval_rejects_prd_by_regenerating_for_review(tmp_path):
    state = create_initial_state("run-1", "GTO", "Bad PRD")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True)
    prd.write_text("# Bad PRD\n\nToo vague.", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="GTO",
        approval="prd",
        decision="rejected",
    )

    assert payload["approvals"]["prd"] == "pending"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8").startswith("# Product Roadmap PRD")
    assert "Too vague." not in Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8")
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "GTO" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [action["action"] for action in actions] == ["approval_decided", "prd_regenerated"]
    assert actions[0]["actor"] == "human"
    assert actions[0]["gate"] == "prd"
    assert actions[0]["decision"] == "rejected"
    assert actions[1]["actor"] == "system"
    assert actions[1]["artifact"] == "prd"


def test_build_action_items_reads_action_log_newest_first(tmp_path):
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    action_log.parent.mkdir(parents=True)
    action_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-21T10:00:00+00:00",
                        "run_id": "run-1",
                        "actor": "human",
                        "action": "approval_decided",
                        "gate": "prd",
                        "decision": "rejected",
                        "before_node": "await_prd_approval",
                        "after_node": "rejected",
                        "artifact": "prd",
                        "artifact_path": "/tmp/prd.md",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-21T10:00:01+00:00",
                        "run_id": "run-1",
                        "actor": "system",
                        "action": "prd_regenerated",
                        "gate": "prd",
                        "decision": "",
                        "before_node": "rejected",
                        "after_node": "await_prd_approval",
                        "artifact": "prd",
                        "artifact_path": "/tmp/prd.md",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    items = build_action_items(tmp_path, project_key="CONTENTOPS")

    assert [item["action"] for item in items] == ["prd_regenerated", "approval_decided"]
    assert items[0]["title"] == "System regenerated PRD"
    assert items[1]["title"] == "Human rejected PRD"
    assert items[1]["node_transition"] == "await_prd_approval -> rejected"


def test_decide_approval_rejects_unknown_gate(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    StateStore(tmp_path).write_project_current(state)

    try:
        decide_approval(workspace=tmp_path, project_key="CONTENTOPS", approval="unknown", decision="approved")
    except ValueError as exc:
        assert "Unknown approval gate" in str(exc)
    else:
        raise AssertionError("Expected unknown approval gate to fail")


def test_terminate_project_run_archives_current_state_and_clears_active_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = terminate_project_run(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["terminated"] is True
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["run_id"] == "run-1"
    assert Path(payload["archive_path"]).is_file()
    assert StateStore(tmp_path).read_project_current("CONTENTOPS") is None
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert actions[-1]["action"] == "run_terminated"


def test_delete_project_removes_goodteam_config_and_project_state_only(tmp_path):
    repo = tmp_path / "ai-content-studio"
    repo.mkdir()
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {repo}\n",
        encoding="utf-8",
    )
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    StateStore(tmp_path).write_project_current(state)
    state_dir = tmp_path / "state" / "projects" / "CONTENTOPS"
    (state_dir / "action-log.jsonl").write_text("{}\n", encoding="utf-8")

    payload = delete_project(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["deleted"] is True
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["project_config"] == str(project_config)
    assert payload["state_dir"] == str(state_dir)
    assert project_config.exists() is False
    assert state_dir.exists() is False
    assert repo.is_dir()


def test_delete_project_rejects_unknown_project(tmp_path):
    try:
        delete_project(workspace=tmp_path, project_key="UNKNOWN")
    except ValueError as exc:
        assert "No local project config found" in str(exc)
    else:
        raise AssertionError("Expected unknown project delete to fail")


def test_render_project_overview_includes_delete_project_action(tmp_path):
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\n", encoding="utf-8")

    html = render_project_overview(tmp_path)

    assert 'class="danger delete-project"' in html
    assert 'data-project="CONTENTOPS"' in html
    assert "Delete project" in html
    assert "/project-delete" in html


def test_render_project_overview_does_not_link_to_missing_gto_project(tmp_path):
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\n", encoding="utf-8")

    html = render_project_overview(tmp_path)

    assert 'href="/?project=GTO"' not in html
