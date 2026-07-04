from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class LedgerEvent(_JsonModel):
    event_id: str
    execution_id: str
    task_id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: str

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
