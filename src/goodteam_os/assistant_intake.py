"""[INPUT]: 项目会话消息与草稿事件。
[OUTPUT]: 提取当前草稿的原始用户请求。
[POS]: 限制 Manager 任务输入引用的消息范围。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from goodteam_os.manager_execution import ManagerExecutionError


_INTAKE_BOUNDARIES = {
    "task_proposed", "task_revised", "task_draft_edited", "task_withdrawn",
    "task_confirmed", "queued_task_batch_confirmed",
    "queued_task_selected",
}


def intake_source_messages(session, *, current_message_id=""):
    """Expose complete, bounded source messages, never earlier task contracts."""
    messages = []
    size = 0
    for item in reversed(session.get("timeline", [])):
        if item.get("event") in _INTAKE_BOUNDARIES:
            break
        if item.get("actor") != "user" or item.get("run_id") or item.get("event"):
            continue
        content = item.get("content", "")
        if len(messages) >= 24 or size + len(content) > 32_000:
            break
        messages.append({"message_id": item["message_id"], "content": content})
        size += len(content)
    messages.reverse()
    if current_message_id and not any(item["message_id"] == current_message_id for item in messages):
        current = next((item for item in session.get("timeline", []) if item.get("message_id") == current_message_id
                        and item.get("actor") == "user" and not item.get("run_id") and not item.get("event")), None)
        if current and len(current.get("content", "")) <= 32_000:
            messages.append({"message_id": current_message_id, "content": current["content"]})
    return messages


def resolve_intake_sources(session, message_id, intake):
    """Resolve model-selected IDs to exact human text before any draft mutation."""
    if not intake:
        # Completed legacy model jobs and builtin runtimes have no intake data.
        return None
    selected = intake.get("source_message_ids", [])
    available = intake_source_messages(session, current_message_id=message_id)
    known = {item["message_id"] for item in available}
    if (not isinstance(selected, list) or not all(isinstance(item, str) for item in selected)
            or message_id not in selected or len(set(selected)) != len(selected)
            or not set(selected).issubset(known)):
        raise ManagerExecutionError("The task draft must reference current, visible user messages.")
    return [item for item in available if item["message_id"] in selected]


def mark_draft_needs_clarification(store, intake):
    if not intake or intake.get("ready") is not False:
        return

    def mutate(session):
        proposal = dict(session.get("proposed_task") or {})
        if not proposal or proposal.get("confirmed") or proposal.get("status") == "cancelled":
            return session
        if not proposal.get("needs_clarification"):
            proposal.update(needs_clarification=True, revision=proposal["revision"] + 1)
            session["proposed_task"] = proposal
        return session

    store._mutate(mutate)
