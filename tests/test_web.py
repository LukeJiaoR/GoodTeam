from pathlib import Path

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import (
    build_role_observations,
    build_approval_reviews,
    build_start_console_context,
    build_workflow_steps,
    decide_approval,
    read_artifact_preview,
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
    assert "Role Summary" in html
    assert "Workflow Waterfall" in html
    assert "Needs review" in html
    assert "Review PRD" in html
    assert "PRD Review" in html
    assert "Approve" in html
    assert "Request Changes" in html
    assert "Reject" in html
    assert "Build AI Team OS" in html
    assert "artifact-preview" in html
    assert "Project Target" in html
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
    assert "Start Workflow" in html
    assert "Start Run" in html
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
