"""Explicit, exact-text future work intake. Selecting a draft never starts it."""

from datetime import UTC, datetime
from uuid import uuid4

from goodteam_os.assistant_drafts import archive_terminal_proposal
from goodteam_os.assistant_requests import conversation_lock, response
from goodteam_os.state import StateStore
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


QUEUE_ACTIONS = {"queue_task", "edit_queued_task", "discard_queued_task", "select_queued_task"}


def active_project_run(workspace, project_key):
    state = StateStore(workspace).read_project_current(project_key)
    return state if state and state.current_node not in {"complete", "rejected"} and state.next_node != "rejected" else None


def handle_queue_action(service, *, project_key, root, request):
    store = TaskAssistantSessionStore(service.workspace, project_key)
    action = request.get("action")
    if action not in QUEUE_ACTIONS:
        raise ValueError("Unknown task queue action")
    with conversation_lock(store):
        if action == "select_queued_task":
            if active_project_run(service.workspace, project_key):
                raise ValueError("Finish or cancel the active task before preparing another task.")
            archive_terminal_proposal(store, workspace=service.workspace)
        store._mutate(lambda session: _apply(session, request))
        return response(service, project_key, root)


def _apply(session, request):
    action = request["action"]
    queue = session.setdefault("queued_task_proposals", [])
    timeline = session["timeline"]
    timestamp = datetime.now(UTC).isoformat()
    if action in {"queue_task", "edit_queued_task"}:
        text, message_id = request.get("text"), request.get("message_id")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000 or not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("A task of 1–8000 characters and a message ID are required.")
        identity = {key: request.get(key) for key in ("action", "proposal_id", "expected_revision")}
        previous = next((item for item in timeline if item.get("message_id") == message_id), None)
        if previous:
            if previous.get("content") == text and previous.get("details", {}).get("queue_action") == identity:
                return session
            raise ValueError("message_id is already used with different content or context")
    if action == "queue_task":
        proposal = {"id": uuid4().hex, "exact_request": text, "revision": 1,
                    "source_message_ids": [message_id], "status": "queued", "confirmed": False,
                    "run_id": "", "created_at": timestamp}
        queue.append(proposal)
    else:
        proposal = next((item for item in queue if item["id"] == request.get("proposal_id")), None)
        if proposal is None or type(request.get("expected_revision")) is not int or proposal["revision"] != request["expected_revision"]:
            raise ValueError("Queued task changed. Reload the latest revision.")
        if action == "select_queued_task" and proposal["status"] == "selected":
            if (session.get("proposed_task") or {}).get("id") == proposal["id"]:
                return session
        if action == "discard_queued_task" and proposal["status"] == "cancelled":
            return session
        if proposal["status"] != "queued":
            raise ValueError("Queued task is no longer available.")
        if action == "edit_queued_task":
            proposal.update(exact_request=text, revision=proposal["revision"] + 1,
                            source_message_ids=[*proposal["source_message_ids"], message_id])
        elif action == "discard_queued_task":
            proposal["status"] = "cancelled"
        else:
            current = session.get("proposed_task") or {}
            if current and current.get("status") != "cancelled":
                raise ValueError("Resolve the current draft before selecting another task.")
            session["proposed_task"] = {**proposal, "status": "draft"}
            session["status"] = "waiting_for_human"
            proposal["status"] = "selected"
    if action in {"queue_task", "edit_queued_task"}:
        timeline.append({"message_id": message_id, "actor": "user", "content": text, "created_at": timestamp,
                         "event": "queued_task_request", "details": {"queue_action": identity, "proposal_id": proposal["id"]}})
    timeline.append({"message_id": f"queue:{proposal['id']}:{proposal['revision']}:{action}", "actor": "system",
                     "event": {"queue_task": "task_queued", "edit_queued_task": "queued_task_edited",
                               "discard_queued_task": "queued_task_discarded", "select_queued_task": "queued_task_selected"}[action],
                     "created_at": timestamp, "content": "", "details": {"proposal_id": proposal["id"]}})
    return session
