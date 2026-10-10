"""Validate a new approval before any durable decision or execution begins.

The saved current run and its server-built review are authoritative. A client
origin, claimed verdict, or supplied audit context never waives the reason.
"""
from collections.abc import Mapping


def approval_confirmation_error(*, current_run_id, current_node, review, action):
    """Return a stable error code; do not mutate state or manufacture evidence."""
    if not isinstance(review, Mapping) or not review.get("is_actionable"):
        return "stale_approval"
    gate = action.get("gate")
    version = review.get("artifact_version")
    artifact = review.get("artifact")
    if (not isinstance(current_run_id, str) or not current_run_id
            or current_run_id != action.get("run_id")
            or not isinstance(gate, str) or not gate
            or current_node != f"await_{gate}_approval"
            or action.get("before_node") != current_node
            or review.get("approval") != gate
            or not isinstance(artifact, str) or not artifact.strip()
            or artifact != action.get("artifact")
            or not isinstance(version, str) or not version.strip() or version == "missing"
            or version != action.get("artifact_version")):
        return "stale_approval"
    recommendation = review.get("recommendation")
    positive = isinstance(recommendation, Mapping) and recommendation.get("verdict") == "approve"
    note = action.get("note")
    if not positive and (not isinstance(note, str) or not note.strip()):
        return "approval_reason_required"
    return None


def validate_approval_action(workspace, state, action):
    """The caller holds workflow_guard; validation precedes the first write.

    decide_approval has changed its in-memory state at this point, so reload the
    durable pre-decision state instead of evaluating that already-approved copy.
    Completed decision replay and interrupted-transition recovery do not create
    new approvals and do not come through this hook.
    """
    if action.get("action") != "approval_decided" or action.get("decision") != "approved":
        return
    from goodteam_os.state import StateStore, normalize_project_key
    from goodteam_os.web import ProjectApiError, build_approval_reviews

    key = normalize_project_key(state.project_key)
    current = StateStore(workspace).read_project_current(key)
    same_run = (current is not None and current.run_id == state.run_id
                and normalize_project_key(action.get("project_key", "")) == key)
    review = next((item for item in build_approval_reviews(current, workspace=workspace)
                   if item.get("approval") == action.get("gate") and item.get("is_actionable")), None) if same_run else None
    code = approval_confirmation_error(
        current_run_id=current.run_id if same_run else "",
        current_node=current.next_node if same_run else "",
        review=review,
        action=action,
    )
    if code is not None:
        message = (
            "Record why you are continuing despite the current review limitations."
            if code == "approval_reason_required" else
            "This approval changed or has no versioned material. Reload the review before deciding."
        )
        raise ProjectApiError(code, message, 400 if code == "approval_reason_required" else 409,
                              recovery=["refresh_approval_brief"])
