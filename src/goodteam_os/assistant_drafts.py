"""Exact-text draft changes and explicit preparation gates for Assistant chat."""

import re
from datetime import UTC, datetime

from goodteam_os.assistant_requests import conversation_lock, response
from goodteam_os.state import StateStore
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


_APPEND = re.compile(r"^(also\b|additionally\b|in addition\b|另外|此外|补充|再加|并且)", re.I)
_CANCEL = re.compile(r"\b(cancel|withdraw|discard) (the |this |my )?(task|draft)\b|撤回草稿|取消任务|取消这个任务", re.I)
_DISCUSSION = re.compile(
    r"acknowledge .*only|only acknowledge|do not (start|inspect|execute)|don't (start|execute)"
    r"|只需确认收到|不要开始|不要执行|先不执行|只是讨论", re.I,
)


def is_conversation_only(text):
    return bool(_CANCEL.search(text) or _DISCUSSION.search(text))


def archive_terminal_proposal(store, *, workspace, root=None):
    proposal = dict(store.read().get("proposed_task") or {})
    run_id = str(proposal.get("run_id", "")).strip()
    if not proposal or not run_id:
        return False

    terminal_status = _terminal_run_status(StateStore(workspace), store.project_key, run_id)
    if not terminal_status:
        return False
    if proposal.get("project_task_id"):
        if root is None:
            raise ValueError("Cannot reconcile a Manager task without its original project root.")
        from goodteam_os.tasks import LocalTaskStore

        LocalTaskStore(root, store.project_key).settle_project_workflow(
            str(proposal["project_task_id"]), run_id=run_id, terminal_status=terminal_status,
        )
    store.archive_task_proposal(proposal["id"], run_id, terminal_status)
    return True


def _terminal_run_status(state_store, project_key, run_id):
    current = state_store.read_project_current(project_key)
    if current is not None and current.run_id == run_id:
        # A final node published before the approval transaction commits is
        # not terminal evidence. Never archive or settle during that window.
        if current.pending_transition.get("status") in {"recording", "executing", "interrupted"}:
            return ""
        if current.current_node == "complete" and not current.blockers and current.next_node != "rejected":
            return "complete"
        if current.current_node == "rejected" or current.next_node == "rejected":
            return "rejected"
        return ""
    if any(state.run_id == run_id for _path, state in state_store.list_project_archived(project_key)):
        return "archived"
    return ""


def stage_draft(store, *, text, message_id, source_messages=None):
    proposal = dict(store.read().get("proposed_task") or {})
    if proposal.get("confirmed") or proposal.get("run_id"):
        return "task_draft_locked"
    if _CANCEL.search(text):
        if proposal and proposal.get("status") != "cancelled":
            _stage_change(store, proposal, text=text, message_id=message_id, kind="withdraw")
            return "task_withdrawal_requested"
        return "intent_clarification_requested"
    if _DISCUSSION.search(text):
        return "intent_clarification_requested"
    sources = source_messages or [{"message_id": message_id, "content": text}]
    source_ids = [item["message_id"] for item in sources]
    exact_text = "\n\n".join(item["content"] for item in sources)
    if not proposal or proposal.get("status") == "cancelled":
        store.propose_task(source_ids, exact_text)
        return "task_proposed"
    if message_id in proposal.get("source_message_ids", []):
        return "task_proposed"
    if _APPEND.search(text.strip()) and not proposal.get("pending_change"):
        store.revise_task(proposal["id"], proposal["exact_request"] + "\n\n" + exact_text,
                          expected_revision=proposal["revision"], source_message_ids=[*proposal["source_message_ids"], *source_ids])
        return "task_revised"
    _stage_change(store, proposal, text=exact_text, message_id=message_id, kind="resolve", source_message_ids=source_ids)
    return "task_change_requested"


def _stage_change(store, proposal, *, text, message_id, kind, source_message_ids=None):
    def mutate(session):
        current = _editable(session, proposal["id"], proposal["revision"])
        if current.get("pending_change"):
            raise ValueError("Resolve the pending draft change before sending another task request.")
        current["pending_change"] = {"text": text, "message_id": message_id, "kind": kind,
                                     "source_message_ids": source_message_ids or [message_id]}
        current["revision"] += 1
        session["proposed_task"] = current
        return session
    store._mutate(mutate)


def _editable(session, proposal_id, expected_revision):
    proposal = dict(session.get("proposed_task") or {})
    if proposal.get("id") != proposal_id or proposal.get("status") == "cancelled":
        raise ValueError("Task proposal is unavailable")
    if type(expected_revision) is not int or expected_revision != proposal.get("revision"):
        raise ValueError("Task proposal changed. Reload the latest draft before confirming.")
    if proposal.get("confirmed") or proposal.get("run_id"):
        raise ValueError("The confirmed task cannot be edited. Use the running task change controls.")
    return proposal


def edit_draft(store, request):
    """Atomically persist the human edit and its revision, or apply no change."""
    text = request.get("text")
    message_id = str(request.get("message_id", "")).strip()
    if not isinstance(text, str) or not text.strip() or not message_id:
        raise ValueError("A nonblank task and edit message_id are required.")

    def mutate(session):
        proposal = _editable(session, str(request.get("proposal_id", "")), request.get("expected_revision"))
        if any(item.get("message_id") == message_id for item in session["timeline"]):
            raise ValueError("Edit message_id is already used. Reload the latest draft.")
        session["timeline"].append({"message_id": message_id, "actor": "user", "content": text, "created_at": datetime.now(UTC).isoformat(),
                                    "event": "task_draft_edited", "details": {"proposal_id": proposal["id"]}})
        proposal.update(exact_request=text, revision=proposal["revision"] + 1,
                        source_message_ids=[*proposal["source_message_ids"], message_id], pending_change=None)
        proposal.pop("needs_clarification", None)
        # A human edit is a new task, not the Manager-selected source task.
        for field in ("project_task_id", "project_task_digest"):
            proposal.pop(field, None)
        proposal["source"] = "human_edited"
        session["proposed_task"] = proposal
        return session
    return store._mutate(mutate)


def resolve_draft(store, request):
    choice = str(request.get("choice", ""))
    if choice not in {"append", "replace", "discard", "withdraw"}:
        raise ValueError("Choose append, replace, discard, or withdraw.")

    def mutate(session):
        proposal = _editable(session, str(request.get("proposal_id", "")), request.get("expected_revision"))
        change = proposal.get("pending_change") or {}
        if choice == "withdraw":
            proposal["status"] = "cancelled"
            session["status"] = "ready_for_request"
        elif not change or change.get("kind") == "withdraw" and choice != "discard":
            raise ValueError("Draft change is unavailable")
        elif choice != "discard":
            proposal["exact_request"] = (proposal["exact_request"] + "\n\n" if choice == "append" else "") + change["text"]
            proposal["source_message_ids"] = (([*proposal["source_message_ids"]] if choice == "append" else [])
                                              + change.get("source_message_ids", [change["message_id"]]))
            proposal.pop("needs_clarification", None)
            for field in ("project_task_id", "project_task_digest"):
                proposal.pop(field, None)
            proposal["source"] = "human_edited"
        proposal.update(revision=proposal["revision"] + 1, pending_change=None)
        session["proposed_task"] = proposal
        session["timeline"].append({"message_id": f"draft:{proposal['id']}:{proposal['revision']}", "actor": "system",
                                    "created_at": datetime.now(UTC).isoformat(),
                                    "event": "task_withdrawn" if choice == "withdraw" else "task_revised",
                                    "details": {"choice": choice, "proposal_id": proposal["id"], "revision": proposal["revision"]}})
        return session
    return store._mutate(mutate)


def handle_draft_action(service, *, project_key, root, request):
    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store):
        if request["action"] == "edit_task":
            edit_draft(store, request)
        elif request["action"] == "resolve_draft":
            resolve_draft(store, request)
        else:
            confirm_mode_and_resume(service, store, root=root, mode=request.get("mode"))
        return response(service, project_key, root)


def confirm_mode_and_resume(service, store, *, root, mode):
    if mode not in {"new_project", "existing_project"}:
        raise ValueError("Choose new_project or existing_project.")
    state = service._readiness_state(store.project_key, root)
    if state.get("project_mode") not in {"unconfirmed", mode}:
        raise ValueError("Project mode changed. Reload before continuing.")
    if state.get("project_mode") == "unconfirmed":
        state = service.confirm_mode(service.workspace, store.project_key, root, mode)
    pending = store.read().get("pending_inspection")
    if pending:
        resume_inspection(service, store, root=root, state=state, pending=pending)


def resume_inspection(service, store, *, root, state, pending):
    if state.get("lifecycle") != "ready" and not isinstance(state.get("execution"), dict):
        state = service.readiness.start(store.project_key, root, {}, idempotency_key=f"task-assistant:{pending['message_id']}")
    store.append_event(f"inspection:{pending['message_id']}", "project_inspection_started",
                       details={"manager_execution": pending.get("manager_execution", {})})
    store._mutate(lambda session: {**session, "pending_inspection": None})
    return state


def confirm_and_prepare(service, *, project_key, root, request):
    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store):
        revision = request.get("expected_revision")
        if type(revision) is not int:
            raise ValueError("Confirm the latest task revision.")
        from goodteam_os.assistant_queue import active_project_run

        current = active_project_run(service.workspace, project_key)
        proposal = store.read().get("proposed_task") or {}
        if current and current.run_id not in {proposal.get("run_id"), proposal.get("start_run_id")}:
            raise ValueError("Finish or cancel the active task before starting another task.")
        state = service._readiness_state(project_key, root)
        if state.get("project_mode") == "unconfirmed":
            raise ValueError("Confirm the project mode before starting the task.")
        proposal_id = str(request.get("proposal_id", ""))
        if (proposal.get("id") != proposal_id or proposal.get("revision") != revision
                or proposal.get("status") == "cancelled" or proposal.get("pending_change")
                or proposal.get("needs_clarification")):
            raise ValueError("Confirm the current task draft with no unresolved changes.")
        if proposal.get("confirmed"):
            # Pre-Skill confirmations are explicitly legacy; never upgrade one
            # during preparation/recovery because Team configuration changed.
            context = proposal.get("task_skill_context", {})
            if not context:
                from goodteam_os.skills.task_locks import TaskSkillIdentity
                from goodteam_os.skills.task_start import reject_missing_task_context

                reject_missing_task_context(TaskSkillIdentity("workflow_run", project_key, f"task-{proposal_id}"))
        else:
            from goodteam_os.assistant_queue import require_no_active_project_goal, require_queued_project_task
            if proposal.get("project_task_id"):
                require_no_active_project_goal(service.workspace, project_key)
            require_queued_project_task(root, project_key, proposal)
            from goodteam_os.skills.task_locks import TaskSkillIdentity
            from goodteam_os.skills.task_start import capture_task_start

            context = capture_task_start(
                workspace=service.workspace, project_key=project_key, worker_root=root,
                task=TaskSkillIdentity("workflow_run", project_key, f"task-{proposal_id}"),
                command_key=f"task-confirm:{project_key}:{proposal_id}",
                confirmed_input={"proposal_id": proposal_id, "proposal_revision": revision,
                                 "exact_request": proposal["exact_request"]},
                expected_team_revision=request.get("expected_team_revision"),
            )
        session = store.confirm_task(proposal_id, expected_revision=revision, task_skill_context=context)
        proposal = session["proposed_task"]
        if state.get("lifecycle") != "ready" and not isinstance(state.get("execution"), dict):
            if context:
                from goodteam_os.skills.models import SkillError

                raise SkillError("skill_preparation_not_connected", "Task methods are locked; preparation needs a supported Skill execution path.")
            inputs = {"goal": proposal["exact_request"], "brief": proposal["exact_request"]} if state.get("project_mode") == "new_project" else {}
            state = service.readiness.start(project_key, root, inputs, idempotency_key=f"task-proposal:{proposal['id']}:{revision}")
        return session, state
