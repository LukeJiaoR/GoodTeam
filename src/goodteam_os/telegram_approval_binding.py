"""Bind workflow decisions to the material actually displayed by Telegram."""

from goodteam_os.state import normalize_project_key


TARGET_FIELDS = ("run_id", "target_type", "target_id", "artifact_key", "artifact_version")


def capture_target(*, state, workspace, project_key, run_id, gate, displayed_version):
    """Never bind a card to newer bytes than the ones used to render it."""
    from goodteam_os.web import _validate_approval_target

    if (state is None or state.run_id != run_id
            or normalize_project_key(state.project_key) != normalize_project_key(project_key)
            or not displayed_version):
        return {}
    target = _validate_approval_target(state=state, workspace=workspace, approval=gate)
    if target["artifact_version"] != displayed_version or not target["artifact_key"]:
        return {}
    return {"project_key": normalize_project_key(project_key), **target}


def session_decision_binding(session, *, chat_id, message_id=None, project_key=""):
    """Return a stored exact target, or fail closed without filling defaults."""
    from goodteam_os.web import ProjectApiError

    target = session.get("approval_target") if isinstance(session, dict) else None

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    valid = isinstance(target, dict) and all(nonempty(target.get(field)) for field in TARGET_FIELDS)
    if valid:
        session_project = normalize_project_key(str(session.get("project_key") or ""))
        valid = (
            nonempty(target.get("project_key")) and bool(session_project)
            and normalize_project_key(target["project_key"]) == session_project
            and (not project_key or normalize_project_key(project_key) == session_project)
            and target["run_id"] == session.get("run_id")
            and target["target_type"] == "review" and target["target_id"] == session.get("gate")
            and target["artifact_version"] != "missing"
            and not session.get("superseded") and not session.get("decided")
            and str(session.get("chat_id")) == str(chat_id)
            and bool(session.get("message_id"))
            and (message_id is None or str(session["message_id"]) == str(message_id))
        )
    if not valid:
        raise ProjectApiError("stale_approval", "This Telegram card has no current, version-bound review target.",
                              409, recovery=["refresh_approval_brief"])
    return {f"expected_{field}": target[field] for field in TARGET_FIELDS}
