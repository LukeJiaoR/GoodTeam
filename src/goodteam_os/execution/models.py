from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
class TaskContract(_JsonModel):
    source_intent: dict[str, Any] = field(default_factory=dict)
    task_identity: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    instructions: str = ""
    constraints: list[str] = field(default_factory=list)
    expected_artifacts: list[dict[str, Any]] = field(default_factory=list)
    path_scope: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_expectations: list[str] = field(default_factory=list)
    review_expectations: list[str] = field(default_factory=list)
    runtime_hints: dict[str, Any] = field(default_factory=dict)
    pr_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "task_contract.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "task_contract.v1":
            raise ValueError("TaskContract.schema_version must be task_contract.v1")
        object.__setattr__(self, "source_intent", _dict_copy(self.source_intent, "source_intent"))
        object.__setattr__(self, "task_identity", _dict_copy(self.task_identity, "task_identity"))
        object.__setattr__(self, "summary", str(self.summary))
        object.__setattr__(self, "instructions", str(self.instructions))
        object.__setattr__(self, "constraints", _string_list(self.constraints))
        object.__setattr__(
            self,
            "expected_artifacts",
            _dict_list(self.expected_artifacts, "expected_artifacts"),
        )
        object.__setattr__(self, "path_scope", _path_scope(self.path_scope))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _string_list(self.acceptance_criteria),
        )
        object.__setattr__(
            self,
            "verification_expectations",
            _string_list(self.verification_expectations),
        )
        object.__setattr__(
            self,
            "review_expectations",
            _string_list(self.review_expectations),
        )
        object.__setattr__(self, "runtime_hints", _dict_copy(self.runtime_hints, "runtime_hints"))
        object.__setattr__(self, "pr_metadata", _dict_copy(self.pr_metadata, "pr_metadata"))
        object.__setattr__(self, "metadata", _dict_copy(self.metadata, "metadata"))


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
    task_contract: TaskContract | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _coerce_enum(ExecutionTaskStatus, self.status, "ExecutionTask.status"),
        )
        if self.task_contract is not None and not isinstance(self.task_contract, TaskContract):
            if not isinstance(self.task_contract, dict):
                raise ValueError("ExecutionTask.task_contract must be an object")
            object.__setattr__(
                self,
                "task_contract",
                TaskContract.from_dict(self.task_contract),
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.task_contract is None:
            payload.pop("task_contract", None)
        return payload


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


def _dict_copy(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"TaskContract.{field_name} must be an object")
    return dict(value)


def _dict_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise ValueError(f"TaskContract.{field_name} must be a list")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"TaskContract.{field_name} items must be objects")
        result.append(dict(item))
    return result


def _path_scope(value: Any) -> dict[str, Any]:
    path_scope = _dict_copy(value, "path_scope")
    normalized: dict[str, Any] = {}
    for key, item in path_scope.items():
        if key in {"focus_paths", "allowed_paths", "excluded_paths"}:
            normalized[key] = _string_list(item)
        else:
            normalized[str(key)] = item
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise ValueError("TaskContract list fields must be lists")
    return [str(item) for item in value]
