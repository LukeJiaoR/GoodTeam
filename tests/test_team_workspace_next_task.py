"""Current project conversations remain usable after a run becomes history."""

from goodteam_os.state import create_initial_state
from goodteam_os.team_workspace import build_team_workspace


def test_completed_current_project_accepts_next_task_but_history_stays_read_only():
    state = create_initial_state("first-run", "DEMO", "First task")
    state.current_node, state.next_node = "complete", ""
    session = {
        "project_key": "DEMO", "status": "ready_for_request", "timeline": [],
        "proposed_task": {"id": "next-draft", "exact_request": "Second task", "confirmed": False},
    }
    current = build_team_workspace(
        state=state, workflow_active=False, project_root=None, task_assistant_session=session,
    )
    history = build_team_workspace(
        state=state, workflow_active=False, project_root=None, task_assistant_session=session, read_only=True,
    )

    assert "task_assistant" == current["mission"]["source"]
    assert False is current["mission"]["read_only"]
    assert session["proposed_task"] == current["proposed_task"]
    assert "first-run" == history["mission"]["run_id"]
    assert True is history["mission"]["read_only"]
    assert "proposed_task" not in history
    assert [] == history["capabilities"]


def test_active_workflow_keeps_authority_over_the_assistant_conversation():
    state = create_initial_state("active-run", "DEMO", "Current task")
    state.current_node, state.next_node = "prd_review_gate", "await_prd_approval"
    projection = build_team_workspace(
        state=state, workflow_active=True, project_root=None,
        task_assistant_session={"project_key": "DEMO", "timeline": []},
    )

    assert "active-run" == projection["mission"]["run_id"]
    assert "workflow" == projection["mission"]["source"]
