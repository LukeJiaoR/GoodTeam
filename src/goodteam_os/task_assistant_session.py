from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from goodteam_os.state import normalize_project_key


SCHEMA_VERSION = "task_assistant_session.v2"
_LEGACY_SCHEMA_VERSIONS = {"task_assistant_session.v1"}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class TaskAssistantSessionStore:
    def __init__(self, workspace: Path, project_key: str):
        self.workspace = Path(workspace)
        self.project_key = normalize_project_key(project_key)
        self.path = self.workspace / "state" / "projects" / self.project_key / "task-assistant-session.json"
        self.lock_path = self.path.with_suffix(".lock")
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path), threading.Lock())

    def read(self) -> dict[str, object]:
        if not self.path.is_file():
            return self._empty()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Task Assistant session is invalid")
        version = str(payload.get("schema_version", ""))
        if version in _LEGACY_SCHEMA_VERSIONS:
            return _upgrade_session_v1(payload)
        if version != SCHEMA_VERSION:
            raise ValueError("Task Assistant session is invalid")
        payload.setdefault("archived_task_proposals", [])
        from goodteam_os.skills.task_context import workflow_skill_context

        for proposal in [payload.get("proposed_task"), *payload["archived_task_proposals"]]:
            if isinstance(proposal, dict) and "task_skill_context" in proposal:
                workflow_skill_context(proposal["task_skill_context"],
                                       run_id=f"task-{proposal['id']}", project_key=self.project_key)
        return payload

    def append_message(
        self,
        message_id: str,
        actor: str,
        content: str,
        run_id: str = "",
    ) -> dict[str, object]:
        identifier = str(message_id).strip()
        if not identifier or actor not in {"user", "task_assistant", "system"} or not str(content).strip():
            raise ValueError("Task Assistant message is invalid")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            timeline = list(session["timeline"])
            existing = next((item for item in timeline if item.get("message_id") == identifier), None)
            expected = {"actor": actor, "content": str(content), "run_id": str(run_id)}
            if existing is not None:
                if all(existing.get(key) == value for key, value in expected.items()):
                    return session
                raise ValueError("message_id is already used with different content")
            timeline.append(
                {
                    "message_id": identifier,
                    **expected,
                    "created_at": _now(),
                }
            )
            session["timeline"] = timeline
            return session

        return self._mutate(mutate)

    def initialize_context(self, mode_hint: dict[str, object]) -> dict[str, object]:
        mode = str(mode_hint.get("mode", "unconfirmed"))
        if mode not in {"new_project", "existing_project", "unconfirmed"}:
            raise ValueError("Project mode hint is invalid")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            if session["timeline"]:
                return session
            return {**session, **initial_task_assistant_session(self.project_key, mode_hint, created_at=_now())}

        return self._mutate(mutate)

    def append_event(
        self,
        message_id: str,
        event: str,
        *,
        actor: str = "task_assistant",
        run_id: str = "",
        content: str = "",
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        identifier = str(message_id).strip()
        event_name = str(event).strip()
        if not identifier or not event_name or actor not in {"task_assistant", "system"}:
            raise ValueError("Task Assistant event is invalid")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            timeline = list(session["timeline"])
            existing = next((item for item in timeline if item.get("message_id") == identifier), None)
            expected = {
                "actor": actor,
                "event": event_name,
                "content": str(content),
                "run_id": str(run_id),
                "details": dict(details or {}),
            }
            if existing is not None:
                if all(existing.get(key) == value for key, value in expected.items()):
                    return session
                raise ValueError("message_id is already used with different content")
            timeline.append({"message_id": identifier, **expected, "created_at": _now()})
            session["timeline"] = timeline
            return session

        return self._mutate(mutate)

    def set_attention(self, attention: dict[str, object] | None) -> dict[str, object]:
        return self._mutate(lambda session: {**session, "attention_request": attention})

    def propose_team_decision(
        self,
        *,
        commands: list[dict[str, object]],
        reason: str,
        source_message_id: str,
        expected_team_revision: int | None = None,
        preview: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Record a Manager-proposed team decision awaiting user approval.

        The proposal itself mutates no team state; the control plane executes
        only after the user confirms.
        """
        cleaned_commands = [dict(item) for item in commands if isinstance(item, dict)]
        if not cleaned_commands:
            raise ValueError("Team decision proposal requires at least one command")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            return {
                **session,
                "pending_team_decision": {
                    "id": uuid4().hex,
                    "commands": cleaned_commands,
                    "reason": str(reason),
                    "source_message_id": str(source_message_id),
                    "expected_team_revision": expected_team_revision,
                    "preview": preview,
                    "status": "pending",
                    "created_at": _now(),
                },
            }

        return self._mutate(mutate)

    def take_pending_team_decision(self, proposal_id: str) -> dict[str, object]:
        """Pop the pending team decision after it was applied or dismissed."""

        def mutate(session: dict[str, object]) -> dict[str, object]:
            pending = dict(session.get("pending_team_decision") or {})
            if pending.get("id") != str(proposal_id) or pending.get("status") not in {"pending", "failed"}:
                raise ValueError("Team decision proposal is unavailable")
            session["pending_team_decision"] = None
            return session

        return self._mutate(mutate)

    def set_pending_team_decision_status(self, proposal_id: str, status: str) -> dict[str, object]:
        def mutate(session: dict[str, object]) -> dict[str, object]:
            pending = dict(session.get("pending_team_decision") or {})
            if pending.get("id") != str(proposal_id):
                raise ValueError("Team decision proposal is unavailable")
            pending["status"] = str(status)
            session["pending_team_decision"] = pending
            return session

        return self._mutate(mutate)

    def propose_task(self, source_message_ids: list[str], exact_request: str) -> dict[str, object]:
        request = str(exact_request)
        if not request.strip() or not source_message_ids:
            raise ValueError("Task proposal requires source messages and exact request")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            known_ids = {str(item.get("message_id")) for item in session["timeline"]}
            if any(str(identifier) not in known_ids for identifier in source_message_ids):
                raise ValueError("Task proposal references an unknown message")
            return {
                **session,
                "proposed_task": {
                    "id": uuid4().hex,
                    "source_message_ids": [str(item) for item in source_message_ids],
                    "exact_request": request,
                    "revision": 1,
                    "confirmed": False,
                    "start_claim_id": "",
                    "start_run_id": "",
                    "start_owner_pid": 0,
                    "start_claimed_at": "",
                    "start_status": "",
                    "start_error": "",
                    "run_id": "",
                },
                "status": "waiting_for_human",
            }

        return self._mutate(mutate)

    def archive_task_proposal(
        self,
        proposal_id: str,
        run_id: str,
        terminal_status: str,
    ) -> dict[str, object]:
        if terminal_status not in {"complete", "rejected", "archived"}:
            raise ValueError("Task proposal can only be archived with terminal run evidence")
        if not str(run_id).strip():
            raise ValueError("Task proposal archive requires a run ID")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != str(proposal_id) or proposal.get("run_id") != str(run_id):
                return session

            archived = {
                **proposal,
                "status": "archived",
                "terminal_status": terminal_status,
                "archived_at": _now(),
            }
            history = list(session.get("archived_task_proposals") or [])
            if not any(item.get("id") == archived["id"] for item in history):
                history.append(archived)
            session["archived_task_proposals"] = history
            session["proposed_task"] = None
            session["status"] = "ready_for_request"
            event_id = f"task-proposal-archived:{proposal_id}"
            if not any(item.get("message_id") == event_id for item in session["timeline"]):
                session["timeline"].append({
                    "message_id": event_id,
                    "actor": "system",
                    "event": "task_proposal_archived",
                    "content": "",
                    "run_id": str(run_id),
                    "created_at": _now(),
                    "details": {
                        "proposal_id": str(proposal_id),
                        "terminal_status": terminal_status,
                    },
                })
            return session

        return self._mutate(mutate)

    def revise_task(self, proposal_id: str, exact_request: str, *, expected_revision=None, source_message_ids=None) -> dict[str, object]:
        request = str(exact_request)
        if not request.strip():
            raise ValueError("Task proposal request must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or proposal.get("run_id") or proposal.get("confirmed") or proposal.get("status") == "cancelled":
                raise ValueError("Task proposal is unavailable")
            if expected_revision is not None and (type(expected_revision) is not int or proposal.get("revision") != expected_revision):
                raise ValueError("Task proposal changed. Reload the latest draft.")
            if source_message_ids is not None:
                known = {item.get("message_id") for item in session["timeline"] if item.get("actor") == "user"}
                if not source_message_ids or any(item not in known for item in source_message_ids):
                    raise ValueError("Task proposal references an unknown user message")
                proposal["source_message_ids"] = list(source_message_ids)
            proposal["exact_request"] = request
            proposal.pop("needs_clarification", None)
            proposal["revision"] = int(proposal.get("revision", 0)) + 1
            session["proposed_task"] = proposal
            return session

        return self._mutate(mutate)

    def confirm_task(self, proposal_id: str, *, expected_revision=None, task_skill_context=None) -> dict[str, object]:
        from goodteam_os.skills.task_context import workflow_skill_context

        context = None if task_skill_context is None else workflow_skill_context(
            task_skill_context, run_id=f"task-{proposal_id}", project_key=self.project_key,
        )
        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or proposal.get("status") == "cancelled":
                raise ValueError("Task proposal is unavailable")
            if expected_revision is not None and (type(expected_revision) is not int or proposal.get("revision") != expected_revision):
                raise ValueError("Task proposal changed. Reload the latest draft before confirming.")
            if proposal.get("pending_change"):
                raise ValueError("Resolve the pending draft change before confirming.")
            if proposal.get("needs_clarification"):
                raise ValueError("Clarify the task scope and acceptance criteria or edit the draft before confirming.")
            if proposal.get("run_id") or proposal.get("confirmed"):
                if context is not None and proposal.get("task_skill_context", {}) != context:
                    raise ValueError("Confirmed task methods cannot be replaced during retry.")
                return session
            proposal["task_skill_context"] = context if context is not None else {}
            proposal["confirmed"] = True
            session["proposed_task"] = proposal
            session["status"] = "preparing_to_start"
            return session

        return self._mutate(mutate)

    def claim_workflow_start(
        self,
        proposal_id: str,
        claim_id: str,
        run_id: str,
        *,
        owner_pid: int | None = None,
    ) -> dict[str, object]:
        identifier = str(claim_id).strip()
        requested_run_id = str(run_id).strip()
        if not identifier or not requested_run_id:
            raise ValueError("Workflow start claim and run ID must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or not proposal.get("confirmed"):
                raise ValueError("Task proposal is unavailable")
            if proposal.get("run_id") or proposal.get("start_claim_id"):
                return session
            proposal["start_claim_id"] = identifier
            proposal["start_run_id"] = requested_run_id
            proposal["start_owner_pid"] = int(owner_pid if owner_pid is not None else os.getpid())
            proposal["start_claimed_at"] = _now()
            proposal["start_status"] = "claimed"
            proposal["start_error"] = ""
            session["proposed_task"] = proposal
            session["status"] = "workflow_starting"
            return session

        return self._mutate(mutate)

    def mark_workflow_start_indeterminate(
        self,
        proposal_id: str,
        *,
        claim_id: str = "",
        reason: str = "",
    ) -> dict[str, object]:
        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or proposal.get("run_id"):
                return session
            active_claim = str(proposal.get("start_claim_id", ""))
            owns_claim = bool(claim_id) and active_claim == str(claim_id)
            owner_is_dead = bool(active_claim) and not _pid_alive(int(proposal.get("start_owner_pid", 0) or 0))
            if not owns_claim and not owner_is_dead:
                return session
            proposal["start_status"] = "indeterminate"
            proposal["start_error"] = str(reason).strip()
            session["proposed_task"] = proposal
            session["status"] = "workflow_start_indeterminate"
            return session

        return self._mutate(mutate)

    def release_workflow_start(self, proposal_id: str, claim_id: str) -> dict[str, object]:
        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id:
                raise ValueError("Task proposal is unavailable")
            if proposal.get("run_id") or proposal.get("start_claim_id") != str(claim_id):
                return session
            proposal["start_claim_id"] = ""
            proposal["start_run_id"] = ""
            proposal["start_owner_pid"] = 0
            proposal["start_claimed_at"] = ""
            session["proposed_task"] = proposal
            session["status"] = "preparing_to_start"
            return session

        return self._mutate(mutate)

    def bind_workflow(self, proposal_id: str, run_id: str, *, claim_id: str = "") -> dict[str, object]:
        if not str(run_id).strip():
            raise ValueError("Workflow run_id must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id:
                raise ValueError("Task proposal is unavailable")
            active_claim = str(proposal.get("start_claim_id", ""))
            if active_claim and active_claim != str(claim_id):
                raise ValueError("Workflow start claim is owned by another request")
            start_run_id = str(proposal.get("start_run_id", ""))
            if start_run_id and start_run_id != str(run_id):
                raise ValueError("Workflow run does not match the claimed operation")
            bound_run = str(proposal.get("run_id", ""))
            if bound_run and bound_run != run_id:
                raise ValueError("Task proposal is already bound to another run")
            proposal["run_id"] = str(run_id)
            proposal["start_status"] = "completed"
            proposal["start_error"] = ""
            session["proposed_task"] = proposal
            session["status"] = "workflow_active"
            return session

        return self._mutate(mutate)

    def reconcile_workflow(self, proposal_id: str, run_id: str) -> dict[str, object]:
        if not str(run_id).strip():
            raise ValueError("Workflow run_id must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or proposal.get("start_run_id") != str(run_id):
                raise ValueError("Workflow does not match the claimed task proposal")
            bound_run = str(proposal.get("run_id", ""))
            if bound_run and bound_run != str(run_id):
                raise ValueError("Task proposal is already bound to another run")
            proposal["run_id"] = str(run_id)
            proposal["start_status"] = "completed"
            proposal["start_error"] = ""
            session["proposed_task"] = proposal
            session["status"] = "workflow_active"
            return session

        return self._mutate(mutate)

    def _mutate(self, operation):
        with self._lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                session = operation(self.read())
                from goodteam_os.context_continuity import compact_conversation

                # Derived and persisted under the same atomic write as its
                # exact source timeline. Never discard or rewrite source text.
                session["conversation_continuity"] = compact_conversation(session)
                self._write(session)
                return session
            finally:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                os.close(lock_descriptor)

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _empty(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "project_key": self.project_key,
            "status": "ready",
            "timeline": [],
            "attention_request": None,
            "proposed_task": None,
            "archived_task_proposals": [],
            "pending_team_decision": None,
            "project_mode_hint": None,
            "suggestions": [],
        }


def _upgrade_session_v1(payload: dict[str, object]) -> dict[str, object]:
    """Lazy in-memory upgrade: v1 sessions gain the team-decision slot."""
    return {
        **payload,
        "schema_version": SCHEMA_VERSION,
        "archived_task_proposals": list(payload.get("archived_task_proposals") or []),
        "pending_team_decision": payload.get("pending_team_decision"),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _opening_presentation(mode: str) -> tuple[str, tuple[str, ...]]:
    if mode == "existing_project":
        return "welcome_existing_project", (
            "understand-project",
            "find-priorities",
            "implement-feature",
            "fix-problem",
        )
    if mode == "new_project":
        return "welcome_new_project", (
            "clarify-idea",
            "draft-plan",
            "create-foundation",
            "custom-request",
        )
    return "welcome_ambiguous_project", (
        "confirm-new-project",
        "confirm-existing-project",
        "inspect-directory",
        "custom-request",
    )


def initial_task_assistant_session(
    project_key: str,
    mode_hint: dict[str, object],
    *,
    created_at: str = "",
) -> dict[str, object]:
    key = normalize_project_key(project_key)
    mode = str(mode_hint.get("mode", "unconfirmed"))
    event, suggestions = _opening_presentation(mode)
    return {
        "schema_version": SCHEMA_VERSION,
        "project_key": key,
        "status": "onboarding",
        "timeline": [
            {
                "message_id": f"welcome:{key}",
                "actor": "task_assistant",
                "event": event,
                "content": "",
                "run_id": "",
                "created_at": created_at,
            }
        ],
        "attention_request": None,
        "proposed_task": None,
        "archived_task_proposals": [],
        "pending_team_decision": None,
        "project_mode_hint": dict(mode_hint),
        "suggestions": [{"id": item} for item in suggestions],
    }
