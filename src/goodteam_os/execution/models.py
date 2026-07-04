from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Self, TypeVar


JsonModelT = TypeVar("JsonModelT", bound="_JsonModel")


class _JsonModel:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls: type[JsonModelT], payload: dict[str, Any]) -> JsonModelT:
        return cls(**payload)

    @classmethod
    def from_json(cls: type[JsonModelT], payload: str) -> JsonModelT:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError(f"{cls.__name__} JSON payload must be an object")
        return cls.from_dict(decoded)


class ExecutionTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LedgerEventType(StrEnum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    BRANCH_CREATED = "branch_created"
    PR_OPENED = "pr_opened"
    REVIEW_RECORDED = "review_recorded"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionActor(StrEnum):
    ORCHESTRATOR = "orchestrator"
    WORKER = "worker"
    GITHUB = "github"
    SYSTEM = "system"


@dataclass(frozen=True)
class ExecutionTask(_JsonModel):
    task_id: str
    source_type: str
    repo: str
    issue_number: int
    title: str
    body: str
    goal: str
    status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _coerce_enum(ExecutionTaskStatus, self.status, "ExecutionTask.status"),
        )


@dataclass(frozen=True)
class ExecutionRun(_JsonModel):
    execution_id: str
    task_id: str
    worker_job_id: str
    adapter: str
    status: str
    branch_name: str
    pr_number: int | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _coerce_enum(ExecutionRunStatus, self.status, "ExecutionRun.status"),
        )


@dataclass(frozen=True)
class LedgerEvent(_JsonModel):
    event_id: str
    execution_id: str
    task_id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_type",
            _coerce_enum(LedgerEventType, self.event_type, "LedgerEvent.event_type"),
        )
        object.__setattr__(
            self,
            "actor",
            _coerce_enum(ExecutionActor, self.actor, "LedgerEvent.actor"),
        )
        if not isinstance(self.payload, dict):
            raise ValueError("LedgerEvent payload must be an object")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Self:
        decoded = dict(payload)
        event_payload = decoded.get("payload")
        if not isinstance(event_payload, dict):
            raise ValueError("LedgerEvent payload must be an object")
        return cls(**decoded)


@dataclass(frozen=True)
class GitHubBinding(_JsonModel):
    repo: str
    issue_number: int
    pr_number: int | None
    branch_name: str
    execution_id: str


def _coerce_enum(enum_type: type[StrEnum], value: object, field_name: str) -> StrEnum:
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc
