from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from goodteam_os.state import normalize_project_key


SCHEMA_VERSION = "task_assistant_session.v1"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class TaskAssistantSessionStore:
    def __init__(self, workspace: Path, project_key: str):
        self.workspace = Path(workspace)
        self.project_key = normalize_project_key(project_key)
        self.path = self.workspace / "state" / "projects" / self.project_key / "task-assistant-session.json"
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path), threading.Lock())

    def read(self) -> dict[str, object]:
        if not self.path.is_file():
            return self._empty()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Task Assistant session is invalid")
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
            event, suggestions = _opening_presentation(mode)
            session["project_mode_hint"] = dict(mode_hint)
            session["timeline"] = [
                {
                    "message_id": f"welcome:{self.project_key}",
                    "actor": "task_assistant",
                    "event": event,
                    "content": "",
                    "run_id": "",
                    "created_at": _now(),
                }
            ]
            session["suggestions"] = [{"id": item} for item in suggestions]
            session["status"] = "onboarding"
            return session

        return self._mutate(mutate)

    def append_event(
        self,
        message_id: str,
        event: str,
        *,
        actor: str = "task_assistant",
        run_id: str = "",
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
                "content": "",
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
                    "run_id": "",
                },
                "status": "waiting_for_human",
            }

        return self._mutate(mutate)

    def revise_task(self, proposal_id: str, exact_request: str) -> dict[str, object]:
        request = str(exact_request)
        if not request.strip():
            raise ValueError("Task proposal request must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id or proposal.get("run_id"):
                raise ValueError("Task proposal is unavailable")
            proposal["exact_request"] = request
            proposal["revision"] = int(proposal.get("revision", 0)) + 1
            session["proposed_task"] = proposal
            return session

        return self._mutate(mutate)

    def bind_workflow(self, proposal_id: str, run_id: str) -> dict[str, object]:
        if not str(run_id).strip():
            raise ValueError("Workflow run_id must not be blank")

        def mutate(session: dict[str, object]) -> dict[str, object]:
            proposal = dict(session.get("proposed_task") or {})
            if proposal.get("id") != proposal_id:
                raise ValueError("Task proposal is unavailable")
            bound_run = str(proposal.get("run_id", ""))
            if bound_run and bound_run != run_id:
                raise ValueError("Task proposal is already bound to another run")
            proposal["run_id"] = str(run_id)
            session["proposed_task"] = proposal
            session["status"] = "workflow_active"
            return session

        return self._mutate(mutate)

    def _mutate(self, operation):
        with self._lock:
            session = operation(self.read())
            self._write(session)
            return session

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
            "project_mode_hint": None,
            "suggestions": [],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
