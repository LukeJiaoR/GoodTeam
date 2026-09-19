"""Resume recorded decisions and route requested changes back through evidence."""

from dataclasses import asdict
from pathlib import Path

from goodteam_os.state import ApprovalDecision
from goodteam_os.workflow_mutations import (
    INTERRUPTED_TRANSITION, finish_transition, persist_transition, publish_decision,
)


def project_config_for_state(state, workspace):
    from goodteam_os import web

    path = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if path is not None and path.is_file():
        return path
    try:
        return web._project_config_for_key(workspace, state.project_key)
    except ValueError:
        return None


def reset_gates(state, gates):
    for gate in gates:
        if gate in state.approvals:
            state.approvals[gate] = ApprovalDecision.PENDING


def revise_workflow_gate(state, workspace, *, approval):
    from goodteam_os import graph, web

    config = project_config_for_state(state, workspace)
    if approval in {"takeover", "prd", "rfc", "fix_plan"}:
        state.approvals[approval] = ApprovalDecision.PENDING
        runner = {
            "takeover": web.run_adoption_until_next_gate,
            "prd": web.run_until_next_gate,
            "rfc": web.regenerate_rfc_review_gate,
            "fix_plan": graph.run_fix_until_next_gate,
        }[approval]
        return runner(state=state, workspace=workspace, project_config=config)
    if approval == "security":
        reset_gates(state, ("rfc", "security", "staging", "release"))
        state.change_requests["rfc"] = state.change_requests.get("security", "")
        return web.regenerate_rfc_review_gate(state=state, workspace=workspace, project_config=config)
    if approval in {"staging", "release"}:
        reset_gates(state, ("staging", "release"))
        return graph.run_delivery_until_release_gate(state, workspace, config)
    if approval in {"code_review", "qa"} and state.load_profile == "fix":
        reset_gates(state, ("code_review", "qa"))
        return graph.run_fix_implementation(state, workspace, config)
    raise ValueError(f"No revision path for approval: {approval}")


def _persist_result(state, workspace):
    from goodteam_os import web

    web.sync_project_stage_tasks(state, project_config_for_state(state, workspace))
    finish_transition(state)
    persist_transition(workspace, state)
    web.notify_current_approval_gate(state, workspace=workspace)
    return {**asdict(state), "retried": True}


def resume_transition(state, workspace):
    from goodteam_os import web

    transition = state.pending_transition
    action = transition["action"]
    approval, decision = action["gate"], action["decision"]
    publish_decision(workspace, state)
    state.current_node = transition["current_node"]
    state.next_node = transition["next_node"]
    state.blockers = [item for item in state.blockers if item != INTERRUPTED_TRANSITION]
    config = project_config_for_state(state, workspace)
    if decision == "approved":
        if approval == "takeover":
            state = web.run_until_next_gate(state=state, workspace=workspace, project_config=config)
        else:
            state = web.advance_approved_workflow(state=state, workspace=workspace, project_config=config)
    elif decision == "changes_requested" or (decision == "rejected" and approval in {"prd", "rfc"}):
        state.change_requests[approval] = action.get("note", "")
        state = revise_workflow_gate(state, workspace, approval=approval)
    return _persist_result(state, workspace)


def retry_fix_workflow(state, workspace):
    from goodteam_os import graph

    if state.current_node != "fix_implement":
        raise ValueError("The fix run is not blocked at implementation.")
    state.blockers = []
    state = graph.run_fix_implementation(state, workspace, project_config_for_state(state, workspace))
    return _persist_result(state, workspace)


def retry_legacy_revision(state, workspace):
    from goodteam_os import web

    approval = state.next_node.removeprefix("revise_")
    if not state.change_requests.get(approval):
        action = next((item for item in web.build_action_items(workspace, state.project_key)
                       if item.get("run_id") == state.run_id and item.get("gate") == approval
                       and item.get("decision") == "changes_requested"), {})
        if not action.get("note"):
            raise ValueError("The revision needs its original change request before it can resume.")
        state.change_requests[approval] = action["note"]
    state = revise_workflow_gate(state, workspace, approval=approval)
    return _persist_result(state, workspace)
