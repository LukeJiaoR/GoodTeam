from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.state import ApprovalDecision, create_initial_state


def test_gate_pauses_when_pending():
    state = create_initial_state("run-1", "GTO", "idea")

    next_node = gate_next_node(state, gate="prd", approved_next="architect")

    assert next_node == "await_prd_approval"


def test_gate_continues_when_approved():
    state = create_initial_state("run-1", "GTO", "idea")
    state.approvals["prd"] = ApprovalDecision.APPROVED

    next_node = gate_next_node(state, gate="prd", approved_next="architect")

    assert next_node == "architect"
