from pathlib import Path

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import (
    build_role_observations,
    build_start_console_context,
    render_dashboard,
    resolve_dashboard_state,
    is_active_run,
    start_workflow,
)


def test_render_dashboard_shows_current_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"

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
    assert "Start Workflow" in html
    assert "Start Run" in html
    assert "Copy Command" not in html
    assert "Enter the real workflow goal" in html
    assert "Start a workflow run to populate this dashboard" not in html
    assert "Overview" in html
    assert "Roles" in html
    assert "Artifacts" in html
    assert "Approvals" in html
    assert "System" in html
    assert "Role Summary" in html
    assert "Project Target" in html
    assert "Active run in progress" in html


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
