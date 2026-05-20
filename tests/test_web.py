from pathlib import Path

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import (
    build_role_observations,
    build_start_console_context,
    render_dashboard,
    resolve_dashboard_state,
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
    assert "Copy Command" in html
    assert "--dry-run" in html
    assert "Overview" in html
    assert "Roles" in html
    assert "Artifacts" in html
    assert "Approvals" in html
    assert "System" in html
    assert "Role Summary" in html
    assert "Project Target" in html


def test_resolve_dashboard_state_reads_existing_state(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    resolved = resolve_dashboard_state(tmp_path)

    assert resolved.run_id == "run-1"
    assert resolved.next_node == "await_prd_approval"


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
    assert architect["status"] == "Queued"
    assert architect["next_action"] == "Wait for approved PRD before drafting RFC"
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
