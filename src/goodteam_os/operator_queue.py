from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None


QUEUE_SCHEMA_VERSION = 1
ITEM_STATUSES = {
    "queued",
    "active",
    "waiting",
    "paused",
    "completed",
    "failed",
    "rejected",
}
ACTIVE_STATUSES = {"active", "waiting", "paused"}
TERMINAL_STATUSES = {"completed", "failed", "rejected"}
LEGAL_STATUS_TRANSITIONS = {
    "queued": {"active", "failed"},
    "active": {"waiting", "paused", "completed", "failed", "rejected"},
    "waiting": {"active", "paused", "failed", "rejected"},
    "paused": {"active", "failed", "rejected"},
    "completed": set(),
    "failed": set(),
    "rejected": set(),
}


class QueueError(RuntimeError):
    pass


class QueueCorruptionError(QueueError):
    pass


class IllegalQueueTransition(QueueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class DurableOperatorQueue:
    """Local, process-safe FIFO queue with append-only transition evidence.

    The JSON snapshot is only the current projection. ``events.ndjson`` is the
    durable audit record and is appended before the projection is replaced.
    Callers must never repair malformed state implicitly: ambiguity fails
    closed until an operator deliberately reconciles it.
    """

    def __init__(self, state_dir: Path, *, clock=utc_now):
        self.root = Path(state_dir) / "operator"
        self.state_path = self.root / "queue.json"
        self.events_path = self.root / "events.ndjson"
        self.lock_path = self.root / "queue.lock"
        self.clock = clock

    def enqueue(
        self,
        *,
        idempotency_key: str,
        source_url: str,
        source_revision: str,
        project_key: str,
        repository_identity: str,
        source_type: str = "github_issue",
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        required = {
            "idempotency_key": idempotency_key,
            "source_url": source_url,
            "project_key": project_key,
            "repository_identity": repository_identity,
            "source_type": source_type,
        }
        for field, value in required.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must not be empty")

        with self._locked():
            state = self._read_state_unlocked()
            for existing in state["items"]:
                if existing["idempotency_key"] == idempotency_key.strip():
                    return dict(existing), False

            timestamp = self.clock()
            item = {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "id": f"task-{secrets.token_hex(8)}",
                "idempotency_key": idempotency_key.strip(),
                "source_type": source_type.strip(),
                "source_url": source_url.strip(),
                "source_revision": str(source_revision or "").strip(),
                "project_key": project_key.strip().upper(),
                "repository_identity": repository_identity.strip(),
                "status": "queued",
                "phase": "queued",
                "execution_id": "",
                "attempt": 0,
                "retry_count": 0,
                "last_failure_class": "",
                "created_at": timestamp,
                "updated_at": timestamp,
                "operations": {},
                "metadata": dict(metadata or {}),
            }
            state["items"].append(item)
            self._commit_unlocked(
                state,
                item=item,
                event="enqueued",
                payload={"source_revision": item["source_revision"]},
            )
            return dict(item), True

    def list_items(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        with self._locked():
            items = self._read_state_unlocked()["items"]
            if not include_terminal:
                items = [item for item in items if item["status"] not in TERMINAL_STATUSES]
            return [dict(item) for item in items]

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._locked():
            state = self._read_state_unlocked()
            item = self._find_item(state, item_id, required=False)
            return dict(item) if item is not None else None

    def active_item(self) -> dict[str, Any] | None:
        with self._locked():
            active = self._active_items(self._read_state_unlocked())
            return dict(active[0]) if active else None

    def claim_next(self) -> dict[str, Any] | None:
        with self._locked():
            state = self._read_state_unlocked()
            if state["desired_state"] != "running":
                return None
            if self._active_items(state):
                return None
            item = next((candidate for candidate in state["items"] if candidate["status"] == "queued"), None)
            if item is None:
                return None
            item["status"] = "active"
            item["phase"] = "hydrating"
            item["attempt"] = max(1, int(item["attempt"]) + 1)
            item["updated_at"] = self.clock()
            self._commit_unlocked(state, item=item, event="claimed", payload={"phase": "hydrating"})
            return dict(item)

    def transition(
        self,
        item_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        expected_phase: str | set[str] | None = None,
        updates: Mapping[str, Any] | None = None,
        event: str = "transitioned",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            state = self._read_state_unlocked()
            item = self._find_item(state, item_id)
            current_phase = str(item["phase"])
            expected = {expected_phase} if isinstance(expected_phase, str) else expected_phase
            if expected is not None and current_phase not in expected:
                raise IllegalQueueTransition(
                    f"item {item_id} is in phase {current_phase}, expected one of {sorted(expected)}"
                )
            if status is not None and status != item["status"]:
                if status not in ITEM_STATUSES:
                    raise IllegalQueueTransition(f"unknown queue status: {status}")
                allowed = LEGAL_STATUS_TRANSITIONS[str(item["status"])]
                if status not in allowed:
                    raise IllegalQueueTransition(f"illegal queue status transition: {item['status']} -> {status}")
                item["status"] = status
            previous_phase = current_phase
            if phase is not None:
                item["phase"] = phase
            for key, value in dict(updates or {}).items():
                if key in {"id", "idempotency_key", "created_at", "schema_version", "operations"}:
                    raise ValueError(f"queue field is immutable: {key}")
                item[key] = value
            item["updated_at"] = self.clock()
            self._commit_unlocked(
                state,
                item=item,
                event=event,
                payload={
                    "previous_phase": previous_phase,
                    "phase": item["phase"],
                    "status": item["status"],
                    **dict(payload or {}),
                },
            )
            return dict(item)

    def record_operation_intent(
        self,
        item_id: str,
        operation_id: str,
        *,
        kind: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._locked():
            state = self._read_state_unlocked()
            item = self._find_item(state, item_id)
            operations = item["operations"]
            existing = operations.get(operation_id)
            if existing is not None:
                return dict(existing), False
            operation = {
                "operation_id": operation_id,
                "kind": kind,
                "state": "intent",
                "payload": dict(payload or {}),
                "result": {},
                "created_at": self.clock(),
                "updated_at": self.clock(),
            }
            operations[operation_id] = operation
            item["updated_at"] = self.clock()
            self._commit_unlocked(
                state,
                item=item,
                event="operation_intent",
                payload={"operation_id": operation_id, "kind": kind},
            )
            return dict(operation), True

    def record_operation_result(
        self,
        item_id: str,
        operation_id: str,
        *,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._locked():
            state = self._read_state_unlocked()
            item = self._find_item(state, item_id)
            operation = item["operations"].get(operation_id)
            if operation is None:
                raise IllegalQueueTransition(f"operation has no persisted intent: {operation_id}")
            if operation["state"] == "result":
                if operation["result"] != dict(result):
                    raise QueueCorruptionError(f"conflicting result for operation: {operation_id}")
                return dict(operation)
            operation["state"] = "result"
            operation["result"] = dict(result)
            operation["updated_at"] = self.clock()
            item["updated_at"] = self.clock()
            self._commit_unlocked(
                state,
                item=item,
                event="operation_result",
                payload={"operation_id": operation_id, "kind": operation["kind"]},
            )
            return dict(operation)

    def set_desired_state(self, desired_state: str) -> dict[str, Any]:
        if desired_state not in {"running", "draining", "stopped"}:
            raise ValueError("desired_state must be running, draining, or stopped")
        with self._locked():
            state = self._read_state_unlocked()
            if state["desired_state"] == desired_state:
                return dict(state)
            state["desired_state"] = desired_state
            state["updated_at"] = self.clock()
            self._append_event_unlocked(
                {"event": "desired_state_changed", "desired_state": desired_state, "created_at": self.clock()}
            )
            self._write_state_unlocked(state)
            return dict(state)

    def summary(self) -> dict[str, Any]:
        with self._locked():
            state = self._read_state_unlocked()
            active = self._active_items(state)
            pending = [item for item in state["items"] if item["status"] == "queued"]
            return {
                "desired_state": state["desired_state"],
                "depth": len(pending),
                "active": dict(active[0]) if active else None,
                "terminal_count": sum(item["status"] in TERMINAL_STATUSES for item in state["items"]),
            }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "desired_state": "running",
                "items": [],
                "updated_at": self.clock(),
            }
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueueCorruptionError(f"operator queue is malformed: {exc}") from exc
        self._validate_state(value)
        return value

    def _validate_state(self, state: Any) -> None:
        if not isinstance(state, dict) or state.get("schema_version") != QUEUE_SCHEMA_VERSION:
            raise QueueCorruptionError("operator queue has an unsupported schema")
        if state.get("desired_state") not in {"running", "draining", "stopped"}:
            raise QueueCorruptionError("operator queue has an invalid desired_state")
        items = state.get("items")
        if not isinstance(items, list):
            raise QueueCorruptionError("operator queue items must be a list")
        ids: set[str] = set()
        idempotency_keys: set[str] = set()
        for item in items:
            self._validate_item(item)
            if item["id"] in ids or item["idempotency_key"] in idempotency_keys:
                raise QueueCorruptionError("operator queue contains duplicate identities")
            ids.add(item["id"])
            idempotency_keys.add(item["idempotency_key"])
        if len(self._active_items(state)) > 1:
            raise QueueCorruptionError("operator queue contains multiple active items")

    @staticmethod
    def _validate_item(item: Any) -> None:
        required = {
            "schema_version",
            "id",
            "idempotency_key",
            "source_type",
            "source_url",
            "source_revision",
            "project_key",
            "repository_identity",
            "status",
            "phase",
            "execution_id",
            "attempt",
            "retry_count",
            "last_failure_class",
            "created_at",
            "updated_at",
            "operations",
            "metadata",
        }
        if not isinstance(item, dict) or required - set(item):
            raise QueueCorruptionError("operator queue item is missing required fields")
        if item["schema_version"] != QUEUE_SCHEMA_VERSION or item["status"] not in ITEM_STATUSES:
            raise QueueCorruptionError("operator queue item has invalid schema or status")
        if not isinstance(item["operations"], dict) or not isinstance(item["metadata"], dict):
            raise QueueCorruptionError("operator queue item operations and metadata must be objects")

    @staticmethod
    def _find_item(state: dict[str, Any], item_id: str, *, required: bool = True) -> dict[str, Any] | None:
        item = next((candidate for candidate in state["items"] if candidate["id"] == item_id), None)
        if item is None and required:
            raise KeyError(f"operator queue item not found: {item_id}")
        return item

    @staticmethod
    def _active_items(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in state["items"] if item["status"] in ACTIVE_STATUSES]

    def _commit_unlocked(
        self,
        state: dict[str, Any],
        *,
        item: dict[str, Any],
        event: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._validate_state(state)
        self._append_event_unlocked(
            {
                "event": event,
                "item_id": item["id"],
                "idempotency_key": item["idempotency_key"],
                "status": item["status"],
                "phase": item["phase"],
                "attempt": item["attempt"],
                "payload": dict(payload),
                "created_at": self.clock(),
            }
        )
        state["updated_at"] = self.clock()
        self._write_state_unlocked(state)

    def _append_event_unlocked(self, event: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_state_unlocked(self, state: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f"{self.state_path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.state_path)

