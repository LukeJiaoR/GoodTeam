"""Project conversation identity and model activity, independent of task lifecycle."""

from collections.abc import Mapping

from goodteam_os.conversation_projection import change_discussion_timeline, dedupe_timeline


def is_assistant_activity(record: Mapping) -> bool:
    # Older manager turns predate a separate activity kind. Keep their durable
    # evidence, but never turn those calls into business assignments/delivery.
    return record.get("role") == "orchestrator"


def build_project_assistant(session, workspace, *, project_key, workflow_active=False, jobs=(), intent_commits=()):
    session = session or {}
    requests = session.get("assistant_requests") or {}
    statuses = {request.get("status") for request in requests.values()}
    status = "running" if "running" in statuses else "needs_attention" if statuses & {"failed", "interrupted"} else "available"
    proposal = session.get("proposed_task") or {}
    editable_draft = proposal and not proposal.get("run_id") and proposal.get("status") != "cancelled"
    timeline = list(workspace.get("manager_timeline", []))
    run_ids = {str((intent.get("mission") or {}).get("id", "")) for intent in intent_commits}
    for run_id in sorted(run_ids - {""}):
        timeline.extend(change_discussion_timeline(run_id, intent_commits))
    active_mission = workspace.get("mission") or {}
    return {
        "schema_version": "project_assistant.v1",
        "id": f"project-assistant:{project_key}",
        "project_key": project_key,
        "status": status,
        "timeline": dedupe_timeline(timeline),
        "runtime": session.get("runtime"),
        "assistant_requests": requests,
        "pending_team_decision": session.get("pending_team_decision"),
        "proposed_task": session.get("proposed_task"),
        "queued_tasks": [item for item in session.get("queued_task_proposals", []) if item.get("status") == "queued"],
        "can_select_task": not workflow_active and not editable_draft and active_mission.get("source") != "operator_queue",
        "suggestions": session.get("suggestions", []) if not workflow_active else [],
        "activity": [
            {key: job.get(key, "") for key in ("id", "role", "phase", "status", "updated_at")}
            for job in jobs if is_assistant_activity(job)
        ],
    }
