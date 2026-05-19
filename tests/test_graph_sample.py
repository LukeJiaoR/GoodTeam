from goodteam_os.graph import run_until_next_gate
from goodteam_os.state import create_initial_state


def test_graph_reaches_prd_gate(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")

    result = run_until_next_gate(state=state, workspace=tmp_path)

    assert result.current_node == "prd_review_gate"
    assert result.next_node == "await_prd_approval"
    assert "prd" in result.artifacts
