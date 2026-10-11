"""Immutable application input, separate from the mutable legacy DTO and model view.

This is an initial-capture contract, not a task-state revision/CAS implementation.
GitHub content is captured external data; freezing it grants no execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from .idempotency import execution_id_from_task, task_id_from_github_issue
from .models import ExecutionTask

SNAPSHOT_SCHEMA = "task_execution_snapshot.v1"
CONTEXT_POLICY = "task_contract_context.v1"
MAX_SNAPSHOT_BYTES = 256 * 1024
INPUT_JOB_PREFIX = "job-input-"


class TaskInputError(ValueError):
    """Only stable codes, never source content or private paths, reach callers."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise TaskInputError("task_input_invalid") from exc


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def execution_key(execution_id: str) -> str:
    return digest(execution_id.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class TaskExecutionSnapshot:
    """Canonical bytes are the sole state; every decoded view is defensive."""

    data: bytes

    def __post_init__(self) -> None:
        if type(self.data) is not bytes or len(self.data) > MAX_SNAPSHOT_BYTES:
            raise TaskInputError("task_input_size_limit")
        try:
            payload = json.loads(self.data)
            if canonical_bytes(payload) != self.data:
                raise TaskInputError("task_input_invalid")
            expected = {"schema_version", "scope", "execution_id", "contract_revision", "operation_id",
                        "context_policy", "source_trust", "source_revision", "role", "adapter", "task"}
            if not isinstance(payload, dict) or set(payload) != expected:
                raise TaskInputError("task_input_invalid")
            if (payload["schema_version"] != SNAPSHOT_SCHEMA or payload["context_policy"] != CONTEXT_POLICY
                    or type(payload["contract_revision"]) is not int or payload["contract_revision"] != 1
                    or payload["source_trust"] != "captured_external_source"
                    or not re.fullmatch(r"[a-f0-9]{64}", payload["scope"])):
                raise TaskInputError("task_input_invalid")
            for key in ("execution_id", "operation_id", "source_revision", "role", "adapter"):
                if not isinstance(payload[key], str) or not payload[key].strip():
                    raise TaskInputError("task_input_invalid")
            task = ExecutionTask.from_dict(payload["task"])
            if (task.task_contract is None or task.source_type != "github_issue"
                    or task.task_id != task_id_from_github_issue(task.repo, task.issue_number)
                    or payload["execution_id"] != execution_id_from_task(task.task_id)
                    or payload["operation_id"] != "issue-input:" + payload["execution_id"]
                    or canonical_bytes(task.to_dict()) != canonical_bytes(payload["task"])):
                raise TaskInputError("task_input_invalid")
            source = task.task_contract.source_intent
            identity = task.task_contract.task_identity
            if (payload["source_revision"] != str(source.get("source_revision") or "unknown")
                    or source.get("source_type") != "github_issue" or source.get("repo") != task.repo
                    or source.get("issue_number") != task.issue_number
                    or identity.get("task_id") != task.task_id or identity.get("repo") != task.repo
                    or identity.get("issue_number") != task.issue_number):
                raise TaskInputError("task_input_invalid")
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, TaskInputError):
                raise
            raise TaskInputError("task_input_invalid") from exc

    @classmethod
    def capture(cls, task: ExecutionTask, *, scope: str, role: str, adapter: str) -> TaskExecutionSnapshot:
        if task.task_contract is None:
            raise TaskInputError("task_input_contract_missing")
        execution_id = execution_id_from_task(task.task_id)
        return cls(canonical_bytes({
            "schema_version": SNAPSHOT_SCHEMA, "scope": scope, "execution_id": execution_id,
            "contract_revision": 1, "operation_id": "issue-input:" + execution_id,
            "context_policy": CONTEXT_POLICY, "source_trust": "captured_external_source",
            "source_revision": str(task.task_contract.source_intent.get("source_revision") or "unknown"),
            "role": role, "adapter": adapter, "task": task.to_dict(),
        }))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.data)

    @property
    def payload_digest(self) -> str:
        return digest(self.data)

    @property
    def execution_id(self) -> str:
        return self.to_dict()["execution_id"]

    @property
    def job_id(self) -> str:
        return INPUT_JOB_PREFIX + execution_key(self.execution_id)

    @property
    def task(self) -> ExecutionTask:
        return ExecutionTask.from_dict(self.to_dict()["task"])

    @property
    def reference(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {key: payload[key] for key in ("schema_version", "scope", "execution_id", "contract_revision", "context_policy")} | {
            "snapshot_id": "input:" + self.payload_digest, "payload_digest": self.payload_digest,
        }

    def same_request(self, other: TaskExecutionSnapshot) -> bool:
        # Capture timestamps are host observations, not changes to the request.
        first, second = self.to_dict(), other.to_dict()
        for value in (first, second):
            value["task"].pop("created_at")
            value["task"].pop("updated_at")
        return canonical_bytes(first) == canonical_bytes(second)
