"""Explicit, exact-text future work intake. Selecting a draft never starts it."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from goodteam_os.assistant_drafts import archive_terminal_proposal
from goodteam_os.assistant_requests import conversation_lock, response
from goodteam_os.state import StateStore
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


QUEUE_ACTIONS = {"queue_task", "edit_queued_task", "discard_queued_task", "select_queued_task", "configure_queued_search"}


def handle_queue_action(service, *, project_key, root, request):
    store = TaskAssistantSessionStore(service.workspace, project_key)
    action = request.get("action")
    if action not in QUEUE_ACTIONS:
        raise ValueError("Unknown task queue action")
    with conversation_lock(store):
        if action == "configure_queued_search":
            configure_queued_search(store, root, project_key, request)
            return response(service, project_key, root)
        if action == "select_queued_task":
            queued = next(
                (item for item in store.read().get("queued_task_proposals", [])
                 if item.get("id") == request.get("proposal_id")), None
            )
            if queued is not None and queued.get("status") == "queued":
                require_queued_project_task(root, project_key, queued, workflow=False)
            archive_terminal_proposal(store, workspace=service.workspace, root=root)
        store._mutate(lambda session: _apply(session, request))
        return response(service, project_key, root)


def _apply(session, request):
    action = request["action"]
    queue = session.setdefault("queued_task_proposals", [])
    timeline = session["timeline"]
    timestamp = datetime.now(UTC).isoformat()
    if action in {"queue_task", "edit_queued_task"}:
        text, message_id = request.get("text"), request.get("message_id")
        if not isinstance(text, str) or not text.strip() or not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("Nonempty task text and a message ID are required.")
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
            for field in ("project_task_id", "project_task_digest", "source_event_id"):
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


def require_queued_project_task(root, project_key, proposal, *, workflow=True):
    """Revalidate an original Manager-selected task before the existing Workflow.

    Ordinary human-typed drafts have no project_task_id and remain unchanged.
    A model recommendation is never an independent execution authorization.
    """
    # Edited legacy drafts can retain an exact assignment without a ledger link.
    # Only a single-draft Workflow replaces that target with a Role default.
    assignment = proposal.get("assignment")
    if workflow and isinstance(assignment, dict) and assignment.get("worker_id"):
        raise ValueError("A named Worker task cannot enter a role-replacing Workflow.")
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
    if workflow and task.get("web_search") is not None:
        raise ValueError("Search-authorized project tasks must retain their confirmed batch search contract.")
    if workflow and task.get("worker_id"):
        raise ValueError("A named Worker task cannot enter a role-replacing Workflow.")
    if workflow and task.get("execution_policy") == "read_only":
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
    if task.get("web_search") is not None:
        import json
        parts.append("Explicit Web Search allowance (not an amount budget): " + json.dumps(task["web_search"], sort_keys=True))
    result = "\n\n".join(parts)
    return result


def queue_manager_iteration_choice(*, workspace, project_key, root, expected_backlog_version,
                                   expected_choice_event_id=None, expected_task_ids=None):
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
        # Intake records future work; an active Goal does not own the queue.
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
            if not validated["selected_task_ids"]:
                raise ValueError("A non-dispatch judgment cannot be queued.")
            known = {item["id"]: item for item in items}
            selected = [known[task_id] for task_id in validated["selected_task_ids"]]
            # Batch dispatch preserves read-only policy. The ordinary writing
            # Workflow adapter still rejects it at selection/confirmation.
            event_id = choice.get("source_event_id") or f"iteration-choice:{snapshot['version']}"
            if (expected_choice_event_id is not None and event_id != expected_choice_event_id
                    or expected_task_ids is not None and validated["selected_task_ids"] != expected_task_ids):
                raise ValueError("The Manager recommendation changed before its queue handoff.")
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
                    # Manager evidence is not a user-authored message.
                    # Keep provenance separate so later human draft edits can
                    # use the existing user-message validation unchanged.
                    "source_message_ids": [],
                    "source_event_id": event_id,
                    "status": "queued",
                    "confirmed": False,
                    "run_id": "",
                    "created_at": datetime.now(UTC).isoformat(),
                    "project_task_id": task["id"],
                    "project_task_digest": digest,
                    "web_search": task.get("web_search"),
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


def configure_queued_search(store, root, project_key, request):
    """Explicit user configuration only; retain task identity and invalidate old confirmation.

    Caller owns the conversation lock. A crash between ledger and queue writes
    fails closed at the existing digest check; it never starts a Worker.
    """
    from goodteam_os.tasks import LocalTaskStore, task_evidence_digest
    from goodteam_os.web_search import search_policy
    from goodteam_os.providers import ProviderStore

    if request.get("confirmed") is not True or "web_search" not in request:
        raise ValueError("Explicit search configuration confirmation is required.")
    policy = search_policy(request["web_search"])
    if policy is not None:
        ProviderStore().resolve(policy["provider"])
    tasks = LocalTaskStore(root, project_key)
    with tasks._lock():
        session = store.read()
        proposal = next((item for item in session.get("queued_task_proposals", [])
                         if item["id"] == request.get("proposal_id")), None)
        if (proposal is None or proposal.get("status") != "queued" or proposal.get("confirmed")
                or proposal.get("run_id") or proposal.get("batch_run_id")
                or type(request.get("expected_revision")) is not int
                or proposal["revision"] != request["expected_revision"] or not proposal.get("project_task_id")):
            raise ValueError("Reload an unconfirmed queued project task before configuring search.")
        task = require_queued_project_task(root, project_key, proposal, workflow=False)
        original = tasks._read()
        if policy is not None and not (task.get("execution_policy", "workspace_write") == "workspace_write"
                or task.get("execution_policy") == "read_only" and task.get("role") in {"research", "planner"}):
            raise ValueError("Task Web Search requires an explicitly authorized supported Worker task.")
        updated = {**task, "updated_at": datetime.now(UTC).isoformat()}
        if policy is None:
            updated.pop("web_search", None)
        else:
            updated["web_search"] = policy
        ledger = {**original, "tasks": [updated if item["id"] == task["id"] else item for item in original["tasks"]]}
        tasks._write(ledger)
        try:
            text = (proposal["exact_request"] if proposal.get("source") == "manager_conversation"
                    else _project_task_request(updated))
            def apply(current):
                item = next(item for item in current["queued_task_proposals"] if item["id"] == proposal["id"])
                item.update(project_task_digest=task_evidence_digest(updated), exact_request=text,
                            revision=proposal["revision"] + 1, web_search=policy)
                current["timeline"].append({"message_id": f"search-config:{item['id']}:{item['revision']}",
                    "actor": "user", "event": "queued_search_configured", "content": text,
                    "created_at": datetime.now(UTC).isoformat(),
                    "details": {"proposal_id": item["id"], "web_search": policy, "execution_started": False}})
                return current
            store._mutate(apply)
        except Exception:
            tasks._write(original)
            raise


def queue_project_request(*, workspace, project_key, root, request_id, text,
                          acceptance_criteria=None, runtime_request=None, metadata=None):
    """Adapt an unassigned request into an ordinary Planner task in this queue.

    Planner methods decide the decomposition. This intake neither implements the
    request nor invents Worker assignments from natural-language text.
    """
    from goodteam_os.tasks import LocalTaskStore, task_evidence_digest

    identity = sha256(f"project-request:{project_key}:{request_id}".encode()).hexdigest()
    reference = "project-request:" + identity
    store = TaskAssistantSessionStore(workspace, project_key)
    tasks = LocalTaskStore(root, project_key)
    with conversation_lock(store):
        session = store.read()
        old = next((item for item in session.get("queued_task_proposals", []) if item["id"] == identity), None)
        criteria = list(acceptance_criteria or [])
        runtime = dict(runtime_request or {})
        context = dict(metadata or {})
        if old:
            if (old["exact_request"] != text or old.get("runtime_request", {}) != runtime
                    or old.get("request_metadata", {}) != context or old.get("request_criteria", []) != criteria):
                raise ValueError("This request ID already names different task input.")
            return old
        matches = [task for task in tasks.list_tasks() if reference in task.get("external_refs", [])]
        if len(matches) > 1:
            raise ValueError("The original request task identity is ambiguous.")
        task = matches[0] if matches else tasks.create_task(
            title=text.splitlines()[0][:180], description=text, role="planner",
            execution_policy="read_only", acceptance_criteria=criteria,
            external_refs=[reference],
        )
        if (task["description"] != text or task["acceptance_criteria"] != criteria or task["role"] != "planner"
                or task.get("execution_policy") != "read_only" or task.get("dependencies")
                or task.get("worker_id") or task.get("delegation") or task.get("web_search")
                or task.get("write_domains")):
            raise ValueError("The original request task changed before queue publication.")
        proposal = {"id": identity, "revision": 1, "exact_request": text,
            "source_message_ids": [request_id], "source": "project_request", "status": "queued",
            "confirmed": False, "run_id": "", "created_at": datetime.now(UTC).isoformat(),
            "project_task_id": task["id"], "project_task_digest": task_evidence_digest(task),
            "runtime_request": runtime, "request_metadata": context, "request_criteria": criteria}
        def enqueue(current):
            current.setdefault("queued_task_proposals", []).append(proposal)
            return current
        store._mutate(enqueue)
        return proposal
