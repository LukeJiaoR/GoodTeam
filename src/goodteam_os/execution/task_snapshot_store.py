"""Operator-owned commit-once issue inputs and host-observation receipts.

One capture is committed before publishing its project binding. The independent
required marker detects loss of the capture; protected job IDs cannot downgrade
when either file is missing. Losing all external proof is not tamper detection.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from typing import Any

from goodteam_os.skills.filesystem import directory
from goodteam_os.memory_storage import atomic_write, read_bytes, role_lock

from .task_context import render_task_contract
from .task_snapshot import (
    INPUT_JOB_PREFIX, MAX_SNAPSHOT_BYTES, TaskExecutionSnapshot, TaskInputError,
    canonical_bytes, digest, execution_key,
)

_REQUEST_FIELDS = (
    "id", "run_id", "execution_id", "role", "adapter", "model", "task_source", "constraints_source",
    "research_report_source", "expected_artifacts", "session_policy", "worker_session_ref", "project_key",
    "reasoning_depth", "additional_prompt", "desired_instances", "execution_policy", "pi_path", "pi_provider",
    "pi_model", "pi_thinking_level", "pi_session_dir", "worker_id", "worker_name", "profile_id", "approval",
    "gate", "phase", "artifact_key", "artifact_path", "artifact_version", "dispatch_group_id", "attempt",
    "idempotency_key", "original_failed_job_id", "retry_attempt", "retry_of_idempotency_key", "task_input_snapshot",
)


def _job_binding(snapshot: TaskExecutionSnapshot, job: dict[str, Any]) -> bytes:
    return canonical_bytes({"snapshot": snapshot.reference,
                            "request": {key: job.get(key) for key in _REQUEST_FIELDS}})


def host_return_record(snapshot, dispatch, status, output_digest):
    if status not in {"completed", "failed"} or re.fullmatch(r"[a-f0-9]{64}", output_digest) is None:
        raise TaskInputError("task_input_host_return_invalid")
    value = {"input": snapshot.reference, "dispatch": dispatch, "status": status,
             "output_digest": output_digest, "observation": "host_worker_return", "provider_receipt": "unknown"}
    return {**value, "receipt_id": "host-return:" + digest(canonical_bytes(value))}


def _correction_present(root):
    # Any retained proof means the original transport is no longer fresh. One
    # missing required marker cannot erase the loop, reservation or attempt.
    with directory(root) as fd:
        return any(name.startswith("correction-") for name in os.listdir(fd))


def _project_bucket(project_root: Path) -> Path:
    from goodteam_os.service import service_state_dir

    return Path(service_state_dir()).expanduser().resolve() / "execution-inputs" / digest(
        str(Path(project_root).resolve()).encode("utf-8"))


def _enrolled_project(project_root: Path) -> bool:
    # The trusted caller's project root still identifies its enrollment when the
    # separate registry is lost. Never scan/discover another project's scope.
    with _storage_errors(), role_lock(_project_bucket(project_root), create=False) as present:
        return present


@contextmanager
def _storage_errors():
    try:
        yield
    except TaskInputError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise TaskInputError("task_input_store_unavailable") from exc


class TaskSnapshotStore:
    def __init__(self, *, workspace: Path, project_key: str, worker_root: Path):
        from goodteam_os.service import service_state_dir

        workspace, worker_root = Path(workspace).resolve(), Path(worker_root).resolve()
        operator = Path(service_state_dir()).expanduser().resolve()
        if any(operator.is_relative_to(root) for root in (workspace, worker_root)):
            raise TaskInputError("task_input_unsafe_store_root")
        self.scope = digest(canonical_bytes({"workspace": str(workspace), "project_key": project_key,
                                             "worker_root": str(worker_root)}))
        self.root = _project_bucket(worker_root)
        self._cache: dict[str, TaskExecutionSnapshot] = {}

    def _read(self, root: Path, *, allow_pending: bool = False) -> TaskExecutionSnapshot | None:
        raw = read_bytes(root / "snapshot.json", maximum=MAX_SNAPSHOT_BYTES)
        required = read_bytes(root / "required.json", maximum=4096)
        if raw is None:
            if required is not None:
                raise TaskInputError("task_input_snapshot_missing")
            return None
        value = TaskExecutionSnapshot(raw)
        if value.to_dict()["scope"] != self.scope or execution_key(value.execution_id) != root.name:
            raise TaskInputError("task_input_scope_mismatch")
        if required is None:
            if not allow_pending:
                raise TaskInputError("task_input_commit_missing")
        elif required != canonical_bytes({"snapshot": value.reference, "job_id": value.job_id}):
            raise TaskInputError("task_input_digest_mismatch")
        cached = self._cache.get(value.payload_digest)
        if cached is None:
            self._cache[value.payload_digest] = value
            cached = value
        return cached

    def load(self, execution_id: str, *, allow_pending: bool = False) -> TaskExecutionSnapshot | None:
        root = self.root / execution_key(execution_id)
        with _storage_errors(), role_lock(root, create=False) as present:
            return self._read(root, allow_pending=allow_pending) if present else None

    def commit(self, proposed: TaskExecutionSnapshot) -> TaskExecutionSnapshot:
        if proposed.to_dict()["scope"] != self.scope:
            raise TaskInputError("task_input_scope_mismatch")
        render_task_contract(proposed)  # Reject oversized mandatory content before any publication.
        root = self.root / execution_key(proposed.execution_id)
        with _storage_errors(), role_lock(root):
            original = self._read(root, allow_pending=True)
            if original is not None and not original.same_request(proposed):
                raise TaskInputError("task_input_operation_conflict")
            value = original or proposed
            if original is None:
                atomic_write(root / "snapshot.json", value.data, maximum=MAX_SNAPSHOT_BYTES)
            marker = canonical_bytes({"snapshot": value.reference, "job_id": value.job_id})
            if read_bytes(root / "required.json", maximum=4096) is None:
                atomic_write(root / "required.json", marker, maximum=4096)
            self._cache[value.payload_digest] = value
            return value

    def accept(self, snapshot: TaskExecutionSnapshot) -> TaskExecutionSnapshot:
        """Keep the handed-off object after checking its durable admission proof."""
        current = self.load(snapshot.execution_id)
        if current is None or current.data != snapshot.data:
            raise TaskInputError("task_input_snapshot_missing")
        self._cache[snapshot.payload_digest] = snapshot
        return snapshot

    def admit(self, job_id: str, job: dict[str, Any], *, require_ready: bool = True) -> TaskExecutionSnapshot:
        if not re.fullmatch(r"job-input-[a-f0-9]{64}", job_id):
            raise TaskInputError("task_input_job_mismatch")
        root = self.root / job_id.removeprefix(INPUT_JOB_PREFIX)
        with _storage_errors(), role_lock(root, create=False) as present:
            snapshot = self._read(root) if present else None
            if snapshot is None:
                raise TaskInputError("task_input_snapshot_missing")
            payload = snapshot.to_dict()
            if (job.get("id") != job_id or snapshot.job_id != job_id
                    or job.get("run_id") != snapshot.execution_id
                    or job.get("execution_id") != snapshot.execution_id
                    or job.get("role") != payload["role"] or job.get("adapter") != payload["adapter"]
                    or job.get("idempotency_key") != payload["operation_id"]
                    or job.get("task_input_snapshot") != snapshot.reference):
                raise TaskInputError("task_input_job_mismatch")
            if require_ready:
                ready = read_bytes(root / "job-ready.json", maximum=16384)
                if ready is None:
                    raise TaskInputError("task_input_job_not_ready")
                if ready != _job_binding(snapshot, job):
                    raise TaskInputError("task_input_job_mismatch")
            return snapshot

    def bind_job(self, snapshot: TaskExecutionSnapshot, job: dict[str, Any]) -> None:
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False) as present:
            if not present or self.admit(snapshot.job_id, job, require_ready=False).data != snapshot.data:
                raise TaskInputError("task_input_job_mismatch")
            binding = _job_binding(snapshot, job)
            previous = read_bytes(root / "job-ready.json", maximum=16384)
            if previous is not None and previous != binding:
                raise TaskInputError("task_input_operation_conflict")
            if previous is None:
                self.assert_dispatchable(snapshot)
                if read_bytes(root / "receipts.json", maximum=1024 * 1024) is None:
                    atomic_write(root / "receipts.json", canonical_bytes([]), maximum=1024 * 1024)
                atomic_write(root / "job-ready.json", binding, maximum=16384)

    def record(self, snapshot: TaskExecutionSnapshot, *, stage: str, input_digest: str = "") -> dict[str, Any]:
        if stage not in {"worker_input_prepared", "transport_attempt"}:
            raise TaskInputError("task_input_receipt_invalid")
        if not re.fullmatch(r"[a-f0-9]{64}", input_digest):
            raise TaskInputError("task_input_receipt_invalid")
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False) as present:
            current = self._read(root) if present else None
            if current is None or current.data != snapshot.data:
                raise TaskInputError("task_input_snapshot_missing")
            if stage == "transport_attempt" and _correction_present(root):
                raise TaskInputError("task_input_correction_active")
            if stage == "transport_attempt" and read_bytes(root / "dispatch.json", maximum=4096) is not None:
                raise TaskInputError("task_input_transport_indeterminate")
            receipts = self._receipts(root, snapshot)
            if stage == "transport_attempt" and any(item["stage"] == stage for item in receipts):
                # A missing result/ack is not proof that no side effect occurred.
                # This initial-capture slice has no authority to retry an attempt.
                raise TaskInputError("task_input_transport_indeterminate")
            receipt = {"stage": stage, "snapshot_digest": snapshot.payload_digest,
                       "job_id": snapshot.job_id, "context_policy": snapshot.to_dict()["context_policy"],
                       "input_digest": input_digest, "observation": "host_dispatch_boundary" if stage == "transport_attempt" else "host_prepared_input",
                       "provider_receipt": "unknown"}
            # Preparation is deterministic/idempotent. An attempt records entry
            # to the call boundary, not proof that a process/provider received it.
            receipt["sequence"] = len(receipts) + 1
            receipt["receipt_id"] = "handoff:" + digest(canonical_bytes(receipt))
            if stage == "worker_input_prepared":
                previous = next((item for item in receipts if item["stage"] == stage and item["input_digest"] == input_digest), None)
                if previous:
                    return previous
            if len(receipts) >= 1024:
                raise TaskInputError("task_input_receipt_limit")
            if stage == "transport_attempt":
                # This independent, commit-once marker precedes both the audit
                # row and the external call. A lost audit file cannot reset an
                # entered transport boundary to a fresh job.
                atomic_write(root / "dispatch.json", canonical_bytes(receipt), maximum=4096)
            atomic_write(root / "receipts.json", canonical_bytes([*receipts, receipt]), maximum=1024 * 1024)
            return receipt

    def assert_dispatchable(self, snapshot: TaskExecutionSnapshot) -> None:
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False) as present:
            current = self._read(root) if present else None
            if current is None or current.data != snapshot.data:
                raise TaskInputError("task_input_snapshot_missing")
            if _correction_present(root):
                raise TaskInputError("task_input_correction_active")
            if read_bytes(root / "dispatch.json", maximum=4096) is not None:
                raise TaskInputError("task_input_transport_indeterminate")
            if any(item["stage"] == "transport_attempt" for item in self._receipts(root, snapshot)):
                raise TaskInputError("task_input_transport_indeterminate")

    def _receipts(self, root: Path, snapshot: TaskExecutionSnapshot) -> list[dict[str, Any]]:
        raw = read_bytes(root / "receipts.json", maximum=1024 * 1024)
        if raw is None:
            if (read_bytes(root / "job-ready.json", maximum=16384) is not None
                    or read_bytes(root / "dispatch.json", maximum=4096) is not None):
                raise TaskInputError("task_input_receipt_missing")
            return []
        receipts = json.loads(raw)
        if not isinstance(receipts, list) or len(receipts) > 1024:
            raise TaskInputError("task_input_receipt_invalid")
        for sequence, item in enumerate(receipts, 1):
            if not isinstance(item, dict):
                raise TaskInputError("task_input_receipt_invalid")
            body = {key: value for key, value in item.items() if key != "receipt_id"}
            if (item.get("sequence") != sequence or item.get("snapshot_digest") != snapshot.payload_digest
                    or item.get("job_id") != snapshot.job_id
                    or item.get("receipt_id") != "handoff:" + digest(canonical_bytes(body))):
                raise TaskInputError("task_input_receipt_invalid")
        marker = read_bytes(root / "dispatch.json", maximum=4096)
        if marker is not None:
            entered = json.loads(marker)
            if (not isinstance(entered, dict) or entered.get("stage") != "transport_attempt"
                    or not any(canonical_bytes(item) == marker for item in receipts)):
                raise TaskInputError("task_input_receipt_incomplete")
        return receipts

    def receipts(self, snapshot: TaskExecutionSnapshot) -> list[dict[str, Any]]:
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False) as present:
            current = self._read(root) if present else None
            if current is None or current.data != snapshot.data:
                raise TaskInputError("task_input_snapshot_missing")
            return self._receipts(root, snapshot)

    def record_host_return(self, snapshot, *, status, output_digest):
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False):
            self.accept(snapshot)
            self._receipts(root, snapshot)
            dispatch_raw = read_bytes(root / "dispatch.json", maximum=4096)
            if dispatch_raw is None:
                return None  # Synthetic/no-transport legacy completion stays unchanged.
            record = host_return_record(snapshot, json.loads(dispatch_raw), status, output_digest)
            previous = read_bytes(root / "host-return.json", maximum=16384)
            if previous is not None and previous != canonical_bytes(record):
                raise TaskInputError("task_input_host_return_conflict")
            if previous is None:
                atomic_write(root / "host-return.json", canonical_bytes(record), maximum=16384)
            return record

    def host_return(self, snapshot):
        root = self.root / execution_key(snapshot.execution_id)
        with _storage_errors(), role_lock(root, create=False):
            self.accept(snapshot)
            self._receipts(root, snapshot)
            dispatch_raw = read_bytes(root / "dispatch.json", maximum=4096)
            raw = read_bytes(root / "host-return.json", maximum=16384)
            if raw is None:
                return None
            value = json.loads(raw)
            if dispatch_raw is None or value != host_return_record(snapshot, json.loads(dispatch_raw),
                    value.get("status"), value.get("output_digest", "")):
                raise TaskInputError("task_input_host_return_invalid")
            return value


def store_for_project(project_root: Path, *, existing_only: bool = False) -> TaskSnapshotStore | None:
    from goodteam_os.skills.task_start import registered_project_scope

    enrolled = _enrolled_project(project_root)
    if existing_only and not enrolled:
        return None
    scope = registered_project_scope(project_root)
    if scope is None:
        if enrolled:
            raise TaskInputError("task_input_scope_missing")
        return None
    workspace, key = scope
    return TaskSnapshotStore(workspace=workspace, project_key=key, worker_root=project_root)
