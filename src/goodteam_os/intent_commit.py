from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from goodteam_os.state import StateStore, normalize_project_key


INTENT_COMMIT_SCHEMA_VERSION = "intent_commit.v1"
INTENT_COMMIT_STORE_SCHEMA_VERSION = "intent_commit_store.v1"
INTENT_INTERPRETATION_SCHEMA_VERSION = "intent_interpretation.v1"
MAX_CHANGE_REQUEST_LENGTH = 8_000

_ACTIVE_STATUSES = frozenset({"discussing", "ready_to_confirm", "committing", "stale"})
_ALL_STATUSES = frozenset({*_ACTIVE_STATUSES, "committed", "cancelled"})
_TARGET_IDENTITY_FIELDS = (
    "approval",
    "run_id",
    "target_type",
    "target_id",
    "artifact_key",
    "artifact_version",
    "head_sha",
)
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class IntentCommitError(ValueError):
    """Raised when an Intent Commit transition would weaken its boundary."""


class IntentCommitStore:
    """Persist Manager intent discussions beside the existing project state.

    This store is a small GoodTeam-owned projection, not a second approval
    authority. Confirmation still delegates to the existing approval mutation
    after the server revalidates the stored exact target.
    """

    def __init__(self, workspace: Path, project_key: str) -> None:
        self.workspace = Path(workspace)
        self.project_key = normalize_project_key(project_key)
        self.path = (
            StateStore(self.workspace).project_state_dir(self.project_key)
            / "intent-commits.json"
        )
        self._lock = _lock_for_path(self.path)

    def list_intents(self, *, mission_id: str = "") -> list[dict[str, object]]:
        with self._lock:
            records = list(self._read_payload()["intents"].values())
        wanted = str(mission_id or "").strip()
        if wanted:
            records = [
                record
                for record in records
                if _text(_mapping(record.get("mission")).get("id")) == wanted
            ]
        return sorted(
            (deepcopy(record) for record in records),
            key=lambda record: (_text(record.get("created_at")), _text(record.get("id"))),
        )

    def get(self, intent_id: str) -> dict[str, object] | None:
        identifier = _required_identifier(intent_id)
        with self._lock:
            record = self._read_payload()["intents"].get(identifier)
        return deepcopy(record) if isinstance(record, dict) else None

    def open_request_changes(
        self,
        *,
        mission: Mapping[str, object],
        target: Mapping[str, object],
    ) -> tuple[dict[str, object], bool]:
        normalized_mission = _normalize_mission(mission)
        normalized_target = _normalize_target(target)
        if normalized_mission["run_id"] != normalized_target["run_id"]:
            raise IntentCommitError("Intent mission and approval target run identities must match.")

        with self._lock:
            payload = self._read_payload()
            records = payload["intents"]
            for record in reversed(list(records.values())):
                if (
                    _text(record.get("kind")) == "request_changes"
                    and _text(record.get("status")) in {"discussing", "ready_to_confirm", "committing"}
                    and _same_mission(_mapping(record.get("mission")), normalized_mission)
                    and _same_target(_mapping(record.get("target")), normalized_target)
                ):
                    return deepcopy(record), False

            timestamp = _now()
            for record in records.values():
                stored_target = _mapping(record.get("target"))
                if (
                    _text(record.get("kind")) == "request_changes"
                    and _text(record.get("status")) in _ACTIVE_STATUSES
                    and _same_mission(_mapping(record.get("mission")), normalized_mission)
                    and _text(stored_target.get("approval")) == normalized_target["approval"]
                ):
                    record["status"] = "cancelled"
                    record["cancel_reason"] = "target_replaced"
                    record["cancelled_at"] = timestamp
                    record["updated_at"] = timestamp

            intent_id = f"intent-{uuid4().hex}"
            record: dict[str, object] = {
                "schema_version": INTENT_COMMIT_SCHEMA_VERSION,
                "id": intent_id,
                "kind": "request_changes",
                "status": "discussing",
                "project_key": self.project_key,
                "mission": normalized_mission,
                "target": normalized_target,
                "draft_text": "",
                "interpretation": None,
                "events": [
                    _event(
                        intent_id,
                        1,
                        actor="user",
                        event="change_request_started",
                        timestamp=timestamp,
                    ),
                    _event(
                        intent_id,
                        2,
                        actor="manager",
                        event="change_discussion_started",
                        timestamp=timestamp,
                    )
                ],
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            records[intent_id] = record
            self._write_payload(payload)
            return deepcopy(record), True

    def discuss(self, intent_id: str, text: str) -> dict[str, object]:
        change_request = _normalize_change_request(text)

        def update(record: dict[str, object]) -> None:
            _require_status(record, "discussing")
            timestamp = _now()
            interpretation = deterministic_interpretation(change_request)
            record["draft_text"] = change_request
            record["interpretation"] = interpretation
            record["status"] = "ready_to_confirm"
            record["updated_at"] = timestamp
            _append_event(
                record,
                actor="user",
                event="change_request_shared",
                timestamp=timestamp,
                content=change_request,
            )
            _append_event(
                record,
                actor="manager",
                event="change_intent_summarized",
                timestamp=timestamp,
                content=str(interpretation["summary"]),
            )

        return self._update(intent_id, update)

    def keep_discussing(self, intent_id: str) -> dict[str, object]:
        def update(record: dict[str, object]) -> None:
            _require_status(record, "ready_to_confirm")
            timestamp = _now()
            record["status"] = "discussing"
            record["updated_at"] = timestamp
            _append_event(
                record,
                actor="manager",
                event="change_discussion_continued",
                timestamp=timestamp,
            )

        return self._update(intent_id, update)

    def begin_confirm(self, intent_id: str) -> tuple[dict[str, object], bool]:
        identifier = _required_identifier(intent_id)
        with self._lock:
            payload = self._read_payload()
            record = _require_record(payload, identifier)
            if _text(record.get("status")) == "committed":
                return deepcopy(record), True
            if _text(record.get("status")) == "committing":
                raise IntentCommitError("This confirmed change request is already being committed.")
            _require_status(record, "ready_to_confirm")
            interpretation = _mapping(record.get("interpretation"))
            summary = _text(interpretation.get("summary"))
            expected_interpretation = deterministic_interpretation(summary) if summary else {}
            if (
                not summary
                or _text(record.get("draft_text")) != summary
                or dict(interpretation) != expected_interpretation
            ):
                raise IntentCommitError("The Manager does not have a safe interpretation to confirm.")
            timestamp = _now()
            record["status"] = "committing"
            record["confirmation_started_at"] = timestamp
            record["updated_at"] = timestamp
            record["confirmed_interpretation"] = deepcopy(expected_interpretation)
            self._write_payload(payload)
            return deepcopy(record), False

    def fail_confirm(self, intent_id: str, *, stale: bool = False) -> dict[str, object]:
        def update(record: dict[str, object]) -> None:
            if _text(record.get("status")) == "committed":
                return
            if _text(record.get("status")) != "committing":
                raise IntentCommitError("This change request is not being committed.")
            timestamp = _now()
            record["status"] = "stale" if stale else "ready_to_confirm"
            record["updated_at"] = timestamp
            record["confirmation_failed_at"] = timestamp
            if stale:
                record["stale_at"] = timestamp
                _append_event(
                    record,
                    actor="manager",
                    event="change_intent_stale",
                    timestamp=timestamp,
                )

        return self._update(intent_id, update)

    def complete_commit(
        self,
        intent_id: str,
        *,
        decision: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        def update(record: dict[str, object]) -> None:
            if _text(record.get("status")) == "committed":
                return
            _require_status(record, "committing")
            interpretation = _mapping(record.get("confirmed_interpretation"))
            summary = _text(interpretation.get("summary"))
            if not summary:
                raise IntentCommitError("The confirmed interpretation is missing.")
            timestamp = _now()
            record["status"] = "committed"
            record["decision"] = _text(decision)
            record["commit_result"] = {
                str(key): value
                for key, value in result.items()
                if str(key) in {"run_id", "approval", "next_node"}
            }
            record["confirmed_at"] = timestamp
            record["committed_at"] = timestamp
            record["updated_at"] = timestamp
            _append_event(
                record,
                actor="user",
                event="change_intent_confirmed",
                timestamp=timestamp,
                content=summary,
            )
            _append_event(
                record,
                actor="manager",
                event="change_request_committed",
                timestamp=timestamp,
                content=summary,
            )

        return self._update(intent_id, update)

    def mark_stale(self, intent_id: str) -> dict[str, object]:
        def update(record: dict[str, object]) -> None:
            if _text(record.get("status")) in {"committed", "cancelled", "stale"}:
                return
            timestamp = _now()
            record["status"] = "stale"
            record["stale_at"] = timestamp
            record["updated_at"] = timestamp
            _append_event(
                record,
                actor="manager",
                event="change_intent_stale",
                timestamp=timestamp,
            )

        return self._update(intent_id, update)

    def _update(
        self,
        intent_id: str,
        update: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        identifier = _required_identifier(intent_id)
        with self._lock:
            payload = self._read_payload()
            record = _require_record(payload, identifier)
            update(record)
            self._write_payload(payload)
            return deepcopy(record)

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": INTENT_COMMIT_STORE_SCHEMA_VERSION,
                "project_key": self.project_key,
                "intents": {},
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntentCommitError("The durable Intent Commit state cannot be read safely.") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != INTENT_COMMIT_STORE_SCHEMA_VERSION:
            raise IntentCommitError("The durable Intent Commit state has an unsupported schema.")
        if _text(payload.get("project_key")) != self.project_key:
            raise IntentCommitError("The durable Intent Commit state belongs to another project.")
        raw_intents = payload.get("intents")
        if not isinstance(raw_intents, dict) or any(not isinstance(value, dict) for value in raw_intents.values()):
            raise IntentCommitError("The durable Intent Commit state is malformed.")
        for intent_id, record in raw_intents.items():
            _validate_record(str(intent_id), record, self.project_key)
        return payload

    def _write_payload(self, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def deterministic_interpretation(change_request: str) -> dict[str, object]:
    change_request = _normalize_change_request(change_request)
    lines = [line.strip() for line in change_request.splitlines() if line.strip()]
    explicit_items: list[str] = []
    for line in lines:
        text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if text and text not in explicit_items:
            explicit_items.append(text)
    if not explicit_items:
        raise IntentCommitError("The Manager could not form an explicit interpretation safely.")
    return {
        "schema_version": INTENT_INTERPRETATION_SCHEMA_VERSION,
        "source": "deterministic_explicit_request",
        "summary": change_request,
        "items": [
            {"kind": "explicit_request", "text": item}
            for item in explicit_items
        ],
    }


def _normalize_change_request(text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines()).strip()
    if not normalized:
        raise IntentCommitError("Describe the complete change you want the Manager to interpret.")
    if len(normalized) > MAX_CHANGE_REQUEST_LENGTH:
        raise IntentCommitError(
            f"Change requests must be {MAX_CHANGE_REQUEST_LENGTH} characters or fewer."
        )
    return normalized


def _normalize_mission(mission: Mapping[str, object]) -> dict[str, str]:
    normalized = {
        "id": _text(mission.get("id")),
        "run_id": _text(mission.get("run_id")),
        "execution_id": _text(mission.get("execution_id")),
        "source": _text(mission.get("source")),
    }
    if not normalized["id"] or not normalized["run_id"] or normalized["source"] != "workflow":
        raise IntentCommitError("Request changes requires an active workflow mission identity.")
    return normalized


def _normalize_target(target: Mapping[str, object]) -> dict[str, str]:
    normalized = {field: _text(target.get(field)) for field in _TARGET_IDENTITY_FIELDS}
    normalized["task_id"] = _text(target.get("task_id"))
    required = (
        "approval",
        "run_id",
        "target_type",
        "target_id",
        "artifact_key",
        "artifact_version",
    )
    missing = [field for field in required if not normalized[field]]
    if missing:
        raise IntentCommitError(f"Intent target is missing exact identity: {', '.join(missing)}.")
    if normalized["target_type"] != "review" or normalized["target_id"] != normalized["approval"]:
        raise IntentCommitError("Intent target does not identify the requested approval review.")
    return normalized


def _same_mission(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    fields = ("id", "run_id", "execution_id", "source")
    return all(_text(left.get(field)) == _text(right.get(field)) for field in fields)


def _same_target(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(_text(left.get(field)) == _text(right.get(field)) for field in _TARGET_IDENTITY_FIELDS)


def _require_record(payload: Mapping[str, object], intent_id: str) -> dict[str, object]:
    intents = payload.get("intents")
    record = intents.get(intent_id) if isinstance(intents, dict) else None
    if not isinstance(record, dict):
        raise IntentCommitError("This change discussion no longer exists.")
    return record


def _validate_record(intent_id: str, record: Mapping[str, object], project_key: str) -> None:
    if (
        _text(record.get("id")) != intent_id
        or _text(record.get("schema_version")) != INTENT_COMMIT_SCHEMA_VERSION
        or _text(record.get("project_key")) != project_key
        or _text(record.get("kind")) != "request_changes"
        or _text(record.get("status")) not in _ALL_STATUSES
        or not isinstance(record.get("mission"), Mapping)
        or not isinstance(record.get("target"), Mapping)
        or not isinstance(record.get("events"), list)
        or any(not isinstance(event, Mapping) for event in _list(record.get("events")))
    ):
        raise IntentCommitError("The durable Intent Commit state contains an invalid record.")


def _require_status(record: Mapping[str, object], expected: str) -> None:
    current = _text(record.get("status"))
    if current != expected:
        raise IntentCommitError(
            f"Intent Commit must be {expected} for this action; current status is {current or 'unknown'}."
        )


def _append_event(
    record: dict[str, object],
    *,
    actor: str,
    event: str,
    timestamp: str,
    content: str = "",
) -> None:
    events = record.setdefault("events", [])
    if not isinstance(events, list):
        raise IntentCommitError("The Intent Commit event history is malformed.")
    events.append(
        _event(
            _text(record.get("id")),
            len(events) + 1,
            actor=actor,
            event=event,
            timestamp=timestamp,
            content=content,
        )
    )


def _event(
    intent_id: str,
    sequence: int,
    *,
    actor: str,
    event: str,
    timestamp: str,
    content: str = "",
) -> dict[str, str]:
    return {
        "id": f"{intent_id}:event:{sequence}",
        "actor": actor,
        "event": event,
        "timestamp": timestamp,
        "content": content,
    }


def _lock_for_path(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


def _required_identifier(value: str) -> str:
    identifier = str(value or "").strip()
    if not identifier or identifier in {".", ".."} or Path(identifier).name != identifier:
        raise IntentCommitError("A valid Intent Commit id is required.")
    return identifier


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(UTC).isoformat()
