"""[INPUT]: 任务助手会话和 Manager 作业。
[OUTPUT]: 投影队列、草稿、会话和 Manager 活动。
[POS]: 为 Work 提供项目会话状态。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from collections.abc import Mapping

from goodteam_os.conversation_projection import dedupe_timeline


def is_assistant_activity(record: Mapping) -> bool:
    # Older manager turns predate a separate activity kind. Keep their durable
    # evidence, but never turn those calls into business assignments/delivery.
    return record.get("role") == "orchestrator"


def build_project_assistant(session, workspace, *, project_key, jobs=()):
    session = session or {}
    requests = session.get("assistant_requests") or {}
    statuses = {request.get("status") for request in requests.values()}
    status = "running" if "running" in statuses else "needs_attention" if statuses & {"failed", "interrupted"} else "available"
    proposal = session.get("proposed_task") or {}
    return {
        "schema_version": "project_assistant.v1",
        "id": f"project-assistant:{project_key}",
        "project_key": project_key,
        "status": status,
        "timeline": dedupe_timeline(workspace.get("manager_timeline", [])),
        "runtime": session.get("runtime"),
        "assistant_requests": requests,
        "pending_team_decision": session.get("pending_team_decision"),
        "proposed_task": session.get("proposed_task"),
        "queued_tasks": [item for item in session.get("queued_task_proposals", []) if item.get("status") == "queued"],
        "can_select_task": not proposal or proposal.get("status") == "cancelled",
        "suggestions": session.get("suggestions", []),
        "activity": [
            {**{key: job.get(key, "") for key in ("id", "role", "phase", "status", "updated_at")},
             **{key: job[key] for key in ("task_skill_context", "skill_execution_snapshot_ref",
                "skill_preflight_status", "skill_error_code", "skill_runtime_status") if key in job}}
            for job in jobs if is_assistant_activity(job)
        ],
    }
