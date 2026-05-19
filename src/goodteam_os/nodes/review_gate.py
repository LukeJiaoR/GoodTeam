from goodteam_os.state import AgileDevState, ApprovalDecision


def gate_next_node(state: AgileDevState, gate: str, approved_next: str) -> str:
    decision = state.approvals.get(gate, ApprovalDecision.PENDING)
    if decision == ApprovalDecision.APPROVED:
        return approved_next
    if decision == ApprovalDecision.CHANGES_REQUESTED:
        return f"revise_{gate}"
    if decision == ApprovalDecision.REJECTED:
        return "rejected"
    return f"await_{gate}_approval"
