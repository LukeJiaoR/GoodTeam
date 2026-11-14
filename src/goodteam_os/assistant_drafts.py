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
        for field in ("project_task_id", "project_task_digest", "source_event_id"):
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
            for field in ("project_task_id", "project_task_digest", "source_event_id"):
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
            confirm_mode(service, store, root=root, mode=request.get("mode"))
        return response(service, project_key, root)


def confirm_mode(service, store, *, root, mode):
    if mode not in {"new_project", "existing_project"}:
        raise ValueError("Choose new_project or existing_project.")
    state = service._readiness_state(store.project_key, root)
    if state.get("project_mode") not in {"unconfirmed", mode}:
        raise ValueError("Project mode changed. Reload before continuing.")
    if state.get("project_mode") == "unconfirmed":
        service.confirm_mode(service.workspace, store.project_key, root, mode)


def start_selected_project_task(service, *, project_key, root, request):
    """A selected Role task uses the queue's existing batch, even when alone.

    Free-form requests enter as ordinary Planner tasks until a role assignment
    exists. Previously started Workflow identities retain their original route.
    """
    from goodteam_os.assistant_queue import require_queued_project_task
    from goodteam_os.project_iteration_execution import run_queued_iteration_batch
    from goodteam_os.tasks import LocalTaskStore, task_evidence_digest

    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store):
        session = store.read()
        proposal = session.get("proposed_task") or {}
        proposal_id = str(request.get("proposal_id", ""))
        # Preparation submits an input against the selected draft, not a new
        # task. Keep its existing identity even when the UI omits proposal_id.
        if request.get("action") == "continue_preparation" and not proposal_id:
            proposal_id = str(proposal.get("id", ""))
        queued = next((item for item in session.get("queued_task_proposals", [])
                       if item.get("id") == proposal_id), None)
        if (proposal.get("id") == proposal_id
                and (proposal.get("run_id") or proposal.get("start_claim_id") or proposal.get("start_run_id"))):
            return None  # Retain the legacy Workflow's original ownership.
        revision = request.get("expected_revision")
        if request.get("action") == "continue_preparation":
            revision = proposal.get("revision")
        if (proposal.get("id") == proposal_id and proposal.get("project_task_id")
                and (not queued or not queued.get("project_task_id"))):
            if (type(revision) is not int or proposal.get("revision") != revision
                    or proposal.get("status") == "cancelled" or proposal.get("pending_change")
                    or proposal.get("needs_clarification")):
                raise ValueError("Confirm the current task draft with no unresolved changes.")
            # Recover queue publication, not a second card for an already bound
            # draft. Its original digest remains the admission evidence.
            queued = {**proposal, "status": "selected", "confirmed": False, "run_id": ""}
            def restore_queue(current):
                current["queued_task_proposals"] = [item for item in current.get("queued_task_proposals", [])
                                                     if item["id"] != proposal_id] + [queued]
                return current
            store._mutate(restore_queue)
        if not queued or not queued.get("project_task_id") or (
                proposal.get("id") == proposal_id and not proposal.get("project_task_id")):
            if (proposal.get("id") != proposal_id or type(revision) is not int
                    or proposal.get("revision") != revision or proposal.get("status") == "cancelled"
                    or proposal.get("pending_change") or proposal.get("needs_clarification")):
                raise ValueError("Confirm the current task draft with no unresolved changes.")
            text = str(proposal.get("exact_request") or "")
            if not text.strip():
                raise ValueError("A task request is required.")
            assignment = proposal.get("assignment") or {}
            reference = f"assistant-draft:{proposal_id}:{revision}"
            fields = dict(title=text.strip().splitlines()[0], description=text,
                          role=assignment.get("role", "planner"),
                          execution_policy=assignment.get("execution_policy", "read_only"),
                          acceptance_criteria=assignment.get("acceptance_criteria", []),
                          dependencies=assignment.get("dependencies", []),
                          external_refs=[reference])
            for name in ("write_domains", "worker_id", "delegation", "web_search"):
                if name in assignment:
                    fields[name] = assignment[name]
            if fields.get("write_domains") is not None:
                from goodteam_os.project_iteration import normalize_write_domains
                domains = normalize_write_domains(fields["write_domains"])
                if domains is None:
                    raise ValueError("The task assignment has invalid write domains.")
                fields["write_domains"] = domains
            if fields.get("delegation") and "source_messages" not in fields["delegation"]:
                sources = {item.get("message_id"): item for item in session.get("timeline", [])}
                fields["delegation"] = {**fields["delegation"], "source_messages": [
                    {"message_id": identifier, "content": sources[identifier]["content"]}
                    for identifier in proposal.get("source_message_ids", [])]}
            tasks = LocalTaskStore(root, project_key)
            matches = [task for task in tasks.list_tasks() if reference in task.get("external_refs", [])]
            if len(matches) > 1:
                raise ValueError("The original draft refers to multiple task cards.")
            task = matches[0] if matches else tasks.create_task(**fields)
            if any(task.get(name, "workspace_write" if name == "execution_policy" else
                            [] if name in {"dependencies", "acceptance_criteria", "write_domains"} else None)
                   != value for name, value in fields.items() if name != "external_refs"):
                raise ValueError("The draft differs from its original task card.")
            proposal = {**proposal, "project_task_id": task["id"],
                        "project_task_digest": task_evidence_digest(task)}
            queued = {**proposal, "status": "selected", "confirmed": False, "run_id": ""}
            # Preserve the prior Workflow capture as historical evidence. Batch
            # Workers capture their own task methods; this is not that context.
            if proposal.get("task_skill_context"):
                proposal["previous_workflow_skill_context"] = proposal["task_skill_context"]
            def bind(current):
                current["proposed_task"] = proposal
                current["queued_task_proposals"] = [item for item in current.get("queued_task_proposals", [])
                                                     if item["id"] != proposal_id] + [queued]
                return current
            store._mutate(bind)
        if type(revision) is not int or revision != queued.get("revision"):
            raise ValueError("Confirm the latest selected task revision.")
        expected_team_revision = request.get("expected_team_revision")
        batch_run_id = queued.get("batch_run_id")
        if batch_run_id:
            batch = session.get("project_task_batches", {}).get(batch_run_id, {})
            if batch.get("selected_proposal_id") != proposal_id:
                raise ValueError("The selected draft does not own this batch.")
            saved_revision = batch.get("expected_team_revision")
            if expected_team_revision is not None and expected_team_revision != saved_revision:
                raise ValueError("The selected batch owns a different Team confirmation.")
            expected_team_revision = saved_revision
        else:
            if (proposal.get("id") != proposal_id or proposal.get("revision") != revision
                    or proposal.get("status") == "cancelled" or proposal.get("pending_change")
                    or proposal.get("needs_clarification") or queued.get("status") != "selected"):
                raise ValueError("Confirm the current selected task with no unresolved changes.")
            if proposal.get("confirmed") and "selected_team_revision" in proposal:
                saved_revision = proposal.get("selected_team_revision")
                if expected_team_revision is not None and expected_team_revision != saved_revision:
                    raise ValueError("The selected task owns a different Team confirmation.")
                expected_team_revision = saved_revision
            if expected_team_revision is not None:
                from goodteam_os.skills.models import SkillConflict, SkillError
                from goodteam_os.team_control_plane import TeamControlPlaneStore

                if type(expected_team_revision) is not int or expected_team_revision < 0:
                    raise SkillError("invalid_revision", "Task confirmation requires a valid Team revision.")
                team_store = TeamControlPlaneStore(service.workspace, project_key)
                with team_store._locked():
                    team = team_store.read()
                    if team is None:
                        raise SkillError("team_unavailable", "The confirmed project Team is unavailable.")
                    if team["revision"] != expected_team_revision:
                        raise SkillConflict("team_revision_changed", "Team changed after confirmation; review it before starting.")
            require_queued_project_task(root, project_key, proposal, workflow=False)
            if request.get("action") == "confirm_task":
                store.confirm_task(proposal_id, expected_revision=revision)
                store._mutate(lambda value: {**value, "proposed_task": {**value["proposed_task"],
                    "selected_team_revision": expected_team_revision}})
            else:
                if not proposal.get("confirmed"):
                    raise ValueError("Confirm this selected task before continuing preparation.")
                values = request.get("values", {})
                if not isinstance(values, dict):
                    raise ValueError("Project preparation values must be a JSON object.")
                state = service.readiness.submit_input(project_key, root, str(request.get("step_id", "")),
                                                       values, background=False)
    # Admission rechecks the exact selected task, ledger and execution slot
    # under its own locks, then atomically consumes the draft into the batch.
    return run_queued_iteration_batch(workspace=service.workspace, project_key=project_key, root=root,
        project_config=service.project_config, proposal_ids=[proposal_id], expected_revisions={proposal_id: revision},
        confirmed=True, background=True, selected_proposal_id=proposal_id, expected_team_revision=expected_team_revision)
