from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import render_dashboard, resolve_dashboard_state


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


def test_resolve_dashboard_state_reads_existing_state(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    resolved = resolve_dashboard_state(tmp_path)

    assert resolved.run_id == "run-1"
    assert resolved.next_node == "await_prd_approval"
