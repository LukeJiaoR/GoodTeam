from pathlib import Path

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import (
    build_role_observations,
    build_approval_reviews,
    build_start_console_context,
    build_workflow_steps,
    decide_approval,
    discover_project_directories,
    create_local_project_config,
    read_artifact,
    read_artifact_preview,
    render_dashboard,
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
    assert "Build AI Team OS" in html
    assert "artifact-preview" in html
    assert "Project Target" in html
    assert "/tmp/project/.goodteam/memory/project-memory.md" in html
    assert "Local Task Ledger" in html
    assert "Task Backend" in html
    assert "Worker Backend" in html
    assert "Active run in progress" not in html


def test_resolve_dashboard_state_reads_existing_state(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    resolved = resolve_dashboard_state(tmp_path)

    assert resolved.run_id == "run-1"
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
        "orchestrator": {"primary": "codex:gpt-5.2", "mode": "short-context"},
        "product_requirement": {"primary": "claude:opus", "draft": "deepseek:deepseek-chat"},
        "architect": {"primary": "claude:opusplan"},
        "implementation": {"primary": "codex:gpt-5.2-codex", "mode": "worker-dispatch"},
    }

    observations = build_role_observations(state)
    product = next(role for role in observations if role["name"] == "Product / Requirement")
    architect = next(role for role in observations if role["name"] == "Architect")
    orchestrator = next(role for role in observations if role["name"] == "Orchestrator / PM")
    implementation = next(role for role in observations if role["name"] == "Implementation")

    assert orchestrator["model"] == "codex:gpt-5.2"
    assert orchestrator["mode"] == "short-context"
    assert product["status"] == "Waiting for approval"
    assert product["artifact"] == "prd"
    assert product["model"] == "claude:opus"
    assert architect["status"] == "No activity"
    assert implementation["model"] == "codex:gpt-5.2-codex"


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
    state = create_initial_state("run-1", "EBC", "Take over existing project")
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


def test_read_artifact_preview_truncates_long_content(tmp_path):
    artifact = tmp_path / "long.md"
    artifact.write_text("a" * 20, encoding="utf-8")

    preview = read_artifact_preview(str(artifact), max_chars=5)

    assert preview == "aaaaa\n\n[Preview truncated]"


def test_read_artifact_reads_current_run_artifact(tmp_path):
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Takeover\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "EBC", "Take over")
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
    state = create_initial_state("run-1", "EBC", "Take over")
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD\n\nAfter")

    assert payload["artifact"] == "prd"
    assert payload["bytes"] == len("# PRD\n\nAfter".encode("utf-8"))
    assert artifact.read_text(encoding="utf-8") == "# PRD\n\nAfter"
    edit_log = tmp_path / "state" / "edit-log.jsonl"
    assert edit_log.is_file()
    assert '"artifact": "prd"' in edit_log.read_text(encoding="utf-8")


def test_save_artifact_rejects_unregistered_artifact(tmp_path):
    StateStore(tmp_path).write_current(create_initial_state("run-1", "EBC", "Take over"))

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "Artifact is not registered" in str(exc)
    else:
        raise AssertionError("Expected unregistered artifact to be rejected")


def test_save_artifact_rejects_path_outside_current_run(tmp_path):
    outside = tmp_path / "notes.md"
    outside.write_text("# Outside", encoding="utf-8")
    state = create_initial_state("run-1", "EBC", "Take over")
    state.artifacts["prd"] = str(outside)
    StateStore(tmp_path).write_current(state)

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "current run artifacts directory" in str(exc)
    else:
        raise AssertionError("Expected outside path to be rejected")


def test_build_start_console_context_reads_project_and_model_config():
    context = build_start_console_context(
        workspace=Path("/tmp/goodteam"),
        model_config=Path("models.yaml"),
        project_config=Path("projects/eastern-birth-chart.example.yaml"),
    )

    assert context["project_key"] == "EBC"
    assert context["project_name"] == "Eastern Birth Chart"
    assert "balanced" in context["profiles"]
    assert context["project_config"] == "projects/eastern-birth-chart.example.yaml"
    assert context["task_backend"] == {"mode": "local", "sync": []}
    assert context["worker_backend"] == {"mode": "manual"}


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
    docs = tmp_path / "docs"
    backend = tmp_path / "api"
    frontend = tmp_path / "ui"
    for path in [docs, backend, frontend]:
        path.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="Existing Product",
        project_key="EBC",
        docs_path=str(docs),
        backend_path=str(backend),
        frontend_path=str(frontend),
        task_sync=["plane"],
        plane_api_host_url="https://api.plane.so/",
        plane_workspace_slug="leader_kanban",
        plane_project_id="project-123",
        plane_api_key_env="PLANE_API_KEY",
        plane_mcp_server="plane",
        worker_backend_mode="manual",
    )

    config = Path(payload["project_config"])
    assert config == tmp_path / "projects" / "ebc.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "name: Existing Product" in content
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
    assert "  mode: manual" in content
    assert payload["worker_backend"] == {"mode": "manual"}


def test_create_local_project_config_defaults_to_local_only_task_backend(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="Existing Product",
        project_key="EBC",
        docs_path=str(docs),
    )

    content = Path(payload["project_config"]).read_text(encoding="utf-8")
    assert "task_backend:" in content
    assert "  mode: local" in content
    assert "  sync:" in content
    assert payload["task_backend"] == {"mode": "local", "sync": []}


def test_start_workflow_writes_state_and_checkpoint(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Audit real project state",
        project_key="EBC",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "EBC"
    assert payload["next_node"] == "await_prd_approval"
    assert payload["dry_run"] is False
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert (tmp_path / "state" / "current.json").is_file()
    assert (tmp_path / "state" / "checkpoints" / payload["run_id"] / "prd_review_gate.json").is_file()


def test_start_workflow_can_adopt_existing_project_first(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "codex.md").write_text("# Memory\n- Existing project fact.\n", encoding="utf-8")
    project_config.write_text(
        f"name: Existing Product\nkey: EBC\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=tmp_path,
        idea="Take over real project state",
        project_key="EBC",
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
    assert (tmp_path / "state" / "checkpoints" / payload["run_id"] / "takeover_review_gate.json").is_file()


def test_start_workflow_rejects_new_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "EBC", "Existing active run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(existing)

    try:
        start_workflow(
            workspace=tmp_path,
            idea="Start another run",
            project_key="EBC",
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
        idea="Preview real project state",
        project_key="EBC",
        model_config=Path("models.yaml"),
        model_profile="cheap",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert payload["model_profile"] == "cheap"
    assert "planned_artifacts" in payload
    assert not (tmp_path / "state" / "current.json").exists()


def test_start_workflow_allows_dry_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "EBC", "Existing active run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(existing)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Preview another run",
        project_key="EBC",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert StateStore(tmp_path).read_current().run_id == "run-1"


def test_start_workflow_rejects_empty_goal(tmp_path):
    try:
        start_workflow(
            workspace=tmp_path,
            idea=" ",
            project_key="EBC",
            model_config=Path("models.yaml"),
            model_profile="balanced",
        )
    except ValueError as exc:
        assert "Workflow goal is required" in str(exc)
    else:
        raise AssertionError("Expected empty workflow goal to fail")


def test_decide_approval_approves_prd_and_advances_to_architect(tmp_path):
    state = create_initial_state("run-1", "EBC", "Existing active run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        approval="prd",
        decision="approved",
    )

    assert payload["approvals"]["prd"] == "approved"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "architect"
    assert (tmp_path / "state" / "checkpoints" / "run-1" / "prd_review_gate.json").is_file()


def test_decide_approval_approves_takeover_and_generates_prd(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "codex.md").write_text("# Memory\n- Existing project context.\n", encoding="utf-8")
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: Existing Product\nkey: EBC\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "EBC", "Existing active run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    state.artifacts["takeover"] = str(tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md")
    StateStore(tmp_path).write_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        approval="takeover",
        decision="approved",
    )

    assert payload["approvals"]["takeover"] == "approved"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert (tmp_path / "state" / "checkpoints" / "run-1" / "prd_review_gate.json").is_file()


def test_decide_approval_can_request_changes(tmp_path):
    state = create_initial_state("run-1", "EBC", "Existing active run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        approval="prd",
        decision="changes_requested",
    )

    assert payload["approvals"]["prd"] == "changes_requested"
    assert payload["next_node"] == "revise_prd"


def test_decide_approval_rejects_unknown_gate(tmp_path):
    state = create_initial_state("run-1", "EBC", "Existing active run")
    StateStore(tmp_path).write_current(state)

    try:
        decide_approval(workspace=tmp_path, approval="unknown", decision="approved")
    except ValueError as exc:
        assert "Unknown approval gate" in str(exc)
    else:
        raise AssertionError("Expected unknown approval gate to fail")
