"""Explicit, exact-text future work intake. Selecting a draft never starts it."""

from datetime import UTC, datetime
from hashlib import sha256
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
            queued = next(
                (item for item in store.read().get("queued_task_proposals", [])
                 if item.get("id") == request.get("proposal_id")), None
            )
            if queued is not None and queued.get("status") == "queued":
                require_queued_project_task(root, project_key, queued)
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
            # Editing the Manager's exact task removes its source binding.
            # The result is ordinary human-authored work, not a prior decision.
            for field in ("project_task_id", "project_task_digest"):
                proposal.pop(field, None)
            proposal["source"] = "human_edited"
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


def require_no_active_project_goal(workspace, project_key):
    """Do not silently launch an unrelated Manager task inside a bounded Goal."""
    from pathlib import Path

    if not (Path(workspace) / "state" / "goals.sqlite3").is_file():
        return
    from goodteam_os.goals import GoalStore

    if GoalStore(Path(workspace), project_key).list_goals(active_only=True):
        raise ValueError("Finish or explicitly stop the active Goal before a separate project task.")


def require_queued_project_task(root, project_key, proposal):
    """Revalidate an original Manager-selected task before the existing Workflow.

    Ordinary human-typed drafts have no project_task_id and remain unchanged.
    A model recommendation is never an independent execution authorization.
    """
    task_id = proposal.get("project_task_id")
    if not task_id:
        return None
    from goodteam_os.tasks import LocalTaskStore, task_evidence_digest
    from goodteam_os.project_iteration import _internal

    store = LocalTaskStore(root, project_key)
    tasks = store.list_tasks()
    matched = [task for task in tasks if task.get("id") == task_id]
    if len(matched) != 1:
        raise ValueError("The Manager-selected project task is no longer unique or available.")
    task = matched[0]
    if (_internal(task) or task.get("status") != "todo"
            or task.get("run_id")
            or task_evidence_digest(task) != proposal.get("project_task_digest")):
        raise ValueError("The Manager-selected task changed; obtain a new project recommendation.")
    known = {item.get("id"): item for item in tasks}
    if any(known.get(dep, {}).get("status") != "done" for dep in task.get("dependencies", [])):
        raise ValueError("The Manager-selected task has unfinished dependencies.")
    if task.get("execution_policy") == "read_only":
        raise ValueError("Read-only Manager work cannot enter a code-writing Workflow.")
    return task


def _project_task_request(task):
    """Carry exact existing backlog work into the normal human-reviewable draft."""
    parts = [f"Project task {task['id']}: {task['title']}"]
    if task.get("description"):
        parts.append(str(task["description"]))
    criteria = task.get("acceptance_criteria") or []
    if criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(f"- {item}" for item in criteria))
    domains = task.get("write_domains") or []
    if domains:
        parts.append("Declared write scope (must be independently enforced): " + ", ".join(domains))
    result = "\n\n".join(parts)
    if not 1 <= len(result) <= 8000:
        raise ValueError("Project task exceeds the existing Task Assistant draft limit.")
    return result


def queue_manager_iteration_choice(*, workspace, project_key, root, expected_backlog_version):
    """One adapter: saved Manager picks become ordinary Task Assistant queue items.

    It creates NO Worker, Workflow, Goal, or second project-task ledger. Queued
    work is not approved. Existing select -> confirm -> start handles authority.
    """
    from goodteam_os.project_iteration import (
        latest_iteration_choice, rank_project_backlog, validate_iteration_choice,
    )
    from goodteam_os.tasks import LocalTaskStore, task_evidence_digest

    if (not isinstance(expected_backlog_version, str)
            or not expected_backlog_version.startswith("sha256:")
            or len(expected_backlog_version) != 71):
        raise ValueError("The current Manager backlog revision is required.")
    store = TaskAssistantSessionStore(workspace, project_key)
    tasks = LocalTaskStore(root, project_key)
    with conversation_lock(store):
        require_no_active_project_goal(workspace, project_key)
        # Follow the existing Assistant->Task lock order, pinning task facts
        # until the atomic queue write completes. Other tasks may be queued
        # meanwhile; per-task digests later survive unrelated ledger updates.
        with tasks._lock():
            items = tasks._read()["tasks"]
            snapshot = rank_project_backlog(items, project_key, limit=None)
            if snapshot["version"] != expected_backlog_version:
                raise ValueError("The project backlog changed; refresh the Manager recommendation.")
            session = store.read()
            choice = latest_iteration_choice(session, snapshot)
            if choice is None or choice.get("status") != "recommended":
                raise ValueError("No current persisted Manager recommendation exists.")
            validated = validate_iteration_choice(snapshot, choice)
            known = {item["id"]: item for item in items}
            selected = [known[task_id] for task_id in validated["selected_task_ids"]]
            if any(task.get("execution_policy") == "read_only" for task in selected):
                raise ValueError("Read-only work needs a read-only adapter, not a writing task queue.")
            event_id = f"iteration-choice:{snapshot['version']}"
            if not any(
                item.get("message_id") == event_id and item.get("event") == "project_iteration_choice_recorded"
                for item in session.get("timeline", []) if isinstance(item, dict)
            ):
                raise ValueError("The Manager recommendation does not have its original session event.")
            prepared = []
            for task in selected:
                digest = task_evidence_digest(task)
                proposal_id = sha256(
                    f"{project_key}:{snapshot['version']}:{task['id']}".encode("utf-8")
                ).hexdigest()[:32]
                prepared.append({
                    "id": proposal_id,
                    "exact_request": _project_task_request(task),
                    "revision": 1,
                    "source_message_ids": [event_id],
                    "status": "queued",
                    "confirmed": False,
                    "run_id": "",
                    "created_at": datetime.now(UTC).isoformat(),
                    "project_task_id": task["id"],
                    "project_task_digest": digest,
                    "source": "manager_project_iteration",
                })

            def append(session):
                queue = session.setdefault("queued_task_proposals", [])
                existing = {proposal.get("id"): proposal for proposal in queue}
                for proposal in prepared:
                    old = existing.get(proposal["id"])
                    if old is None:
                        queue.append(proposal)
                    elif (old.get("project_task_id") != proposal["project_task_id"]
                          or old.get("project_task_digest") != proposal["project_task_digest"]):
                        raise ValueError("An existing queued task has conflicting Manager evidence.")
                event_key = f"iteration-queued:{snapshot['version']}"
                if not any(item.get("message_id") == event_key for item in session["timeline"]):
                    session["timeline"].append({
                        "message_id": event_key,
                        "actor": "system",
                        "event": "project_iteration_queued",
                        "content": "",
                        "created_at": datetime.now(UTC).isoformat(),
                        "details": {
                            "backlog_version": snapshot["version"],
                            "task_ids": validated["selected_task_ids"],
                            "proposal_ids": [item["id"] for item in prepared],
                            "execution_started": False,
                        },
                    })
                return session

            updated = store._mutate(append)
    return {
        "status": "queued_for_existing_task_workflow",
        "queued_task_ids": validated["selected_task_ids"],
        "queued_proposal_ids": [proposal["id"] for proposal in prepared],
        "workflow_started": False,
        "task_assistant_session": updated,
    }
