"""One correction attempt's input and host receipts, under its execution lock."""
from __future__ import annotations

import json
from pathlib import Path
import re
import os
import stat

from goodteam_os.memory_storage import atomic_write, read_bytes, role_lock
from goodteam_os.skills.filesystem import directory

from .candidate_validation import _bound, _read, _write
from .correction_workspace import worktree_identity
from .correction_input import CORRECTION_JOB, MAX_CORRECTION_INPUT, CorrectionAttemptInput
from .task_snapshot import TaskInputError, canonical_bytes, execution_key
from .task_snapshot_store import _job_binding, host_return_record, store_for_project


class CorrectionAttemptStore:
    def __init__(self, project_root, job_id, *, original_store=None):
        match = CORRECTION_JOB.fullmatch(str(job_id))
        if match is None:
            raise TaskInputError("correction_job_mismatch")
        self.project_root = Path(project_root).resolve()
        self.original_store = original_store or store_for_project(self.project_root, existing_only=True)
        if self.original_store is None:
            raise TaskInputError("correction_input_missing")
        self.job_id = job_id
        self.execution_root = self.original_store.root / match[1]
        self.root = self.execution_root / ("correction-worker-" + match[2])
        self._cache = {}
        self._start_lease = None

    def lock(self):
        return role_lock(self.execution_root, create=False)

    def load(self):
        with self.lock():
            raw = read_bytes(self.root / "input.json", maximum=MAX_CORRECTION_INPUT)
            required = _read(self.root / "required.json")
            if raw is None or required is None:
                raise TaskInputError("correction_input_missing")
            try:
                reference = json.loads(raw)["original"]
                original = self.original_store.load(reference["execution_id"])
                if original is None or execution_key(original.execution_id) != self.execution_root.name:
                    raise TaskInputError("correction_job_mismatch")
                value = CorrectionAttemptInput(original, raw)
                if value.job_id != self.job_id or required != value.reference:
                    raise TaskInputError("correction_input_mismatch")
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, TaskInputError):
                    raise
                raise TaskInputError("correction_input_invalid") from exc
            return self._cache.setdefault(value.payload_digest, value)

    def commit(self, value):
        with self.lock():
            if value.job_id != self.job_id:
                raise TaskInputError("correction_job_mismatch")
            value.render()
            self.original_store.accept(value.original)
            with directory(self.root, create=True):
                pass
            previous = read_bytes(self.root / "input.json", maximum=MAX_CORRECTION_INPUT)
            required = _read(self.root / "required.json")
            if (previous is not None and previous != value.data) or (required is not None and required != value.reference):
                raise TaskInputError("correction_operation_conflict")
            if required is not None and previous is None:
                raise TaskInputError("correction_input_missing")
            if previous is None:
                atomic_write(self.root / "input.json", value.data, maximum=MAX_CORRECTION_INPUT)
            if required is None:
                _write(self.root / "required.json", value.reference)
            self._cache[value.payload_digest] = value
            return value

    def accept(self, value):
        current = self.load()
        if current.data != value.data or current.original.data != value.original.data:
            raise TaskInputError("correction_input_mismatch")
        self._cache[value.payload_digest] = value
        return value

    def admit(self, job_id, job, *, require_ready=True):
        with self.lock():
            value = self.load()
            self.check_job_projection(value)
            payload = value.original.to_dict()
            if (job_id != value.job_id or job.get("id") != job_id
                    or job.get("execution_id") != value.execution_id or job.get("run_id") != value.execution_id
                    or job.get("role") != payload["role"] or job.get("adapter") != payload["adapter"]
                    or job.get("idempotency_key") != value.to_dict()["operation_id"]
                    or job.get("task_input_snapshot") != value.reference):
                raise TaskInputError("correction_job_mismatch")
            if require_ready:
                ready = read_bytes(self.root / "job-ready.json", maximum=16384)
                if ready is None:
                    raise TaskInputError("task_input_job_not_ready")
                if ready != _job_binding(value, job):
                    raise TaskInputError("correction_job_mismatch")
            return value

    def bind_job(self, value, job):
        with self.lock():
            self.admit(value.job_id, job, require_ready=False)
            binding = _job_binding(value, job)
            previous = read_bytes(self.root / "job-ready.json", maximum=16384)
            if previous is not None and previous != binding:
                raise TaskInputError("correction_operation_conflict")
            if previous is None:
                if _read(self.root / "dispatch.json") is not None:
                    raise TaskInputError("correction_transport_indeterminate")
                if read_bytes(self.root / "receipts.json", maximum=1024 * 1024) is None:
                    atomic_write(self.root / "receipts.json", canonical_bytes([]))
                atomic_write(self.root / "job-ready.json", binding, maximum=16384)

    def receipts(self, value):
        with self.lock():
            self.accept(value)
            raw = read_bytes(self.root / "receipts.json", maximum=1024 * 1024)
            if raw is None:
                if (_read(self.root / "dispatch.json") is not None
                        or read_bytes(self.root / "job-ready.json", maximum=16384) is not None):
                    raise TaskInputError("correction_receipt_missing")
                return []
            rows = json.loads(raw)
            if not isinstance(rows, list) or len(rows) > 128 or canonical_bytes(rows) != raw:
                raise TaskInputError("correction_receipt_invalid")
            for number, row in enumerate(rows, 1):
                if (not isinstance(row, dict) or row != _bound(row) or row.get("sequence") != number
                        or row.get("input") != value.reference
                        or row.get("stage") not in {"worker_input_prepared", "transport_attempt"}):
                    raise TaskInputError("correction_receipt_invalid")
            marker = _read(self.root / "dispatch.json")
            if marker is not None and (marker not in rows or marker.get("stage") != "transport_attempt"):
                raise TaskInputError("correction_receipt_incomplete")
            return rows

    def check_job_projection(self, value, *, create=False):
        """Reject redirected parents and leaf files before legacy projection IO.

        This is a stable-path observation, not a claim against unrestricted
        same-UID directory replacement while another process writes it.
        """
        root = self.project_root / ".goodteam" / "workers" / "jobs" / value.job_id
        with directory(root, create=create) as fd:
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)):
                    raise TaskInputError("correction_projection_unsafe")
        return root

    def workspace(self, value, *, require_ready=True):
        self.accept(value)
        expected = self.project_root / value.workspace_relative
        try:
            ownership = worktree_identity(self.project_root, expected, value.job_id)
            if require_ready and _read(self.root / "workspace-ready.json") != _bound({
                    "input": value.reference, "candidate": value.to_dict()["candidate"], "ownership": ownership}):
                raise TaskInputError("correction_workspace_unavailable")
        except (OSError, ValueError) as exc:
            raise TaskInputError("correction_workspace_unavailable") from exc
        return expected

    def start(self, value):
        """One in-process handoff lease; a durable start alone cannot restart it."""
        with self.lock():
            self.assert_dispatchable(value)
            if _read(self.root / "run-started.json") is not None:
                raise TaskInputError("correction_worker_outcome_unknown")
            _write(self.root / "run-started.json", {"input": value.reference})
            self._start_lease = (os.getpid(), value.payload_digest)

    def release_start(self):
        self._start_lease = None

    def check_runtime_context(self, value, job):
        root = self.workspace(value)
        context = job.get("sandbox_context", {})
        if (not isinstance(context, dict) or context.get("mode") != "worktree"
                or context.get("workspace_path") != str(root)
                or context.get("worktree_path") != str(root)):
            raise TaskInputError("correction_workspace_mismatch")
        return root

    def assert_dispatchable(self, value):
        with self.lock():
            self.accept(value)
            from .correction_input import correction_runtime_capability
            capability = correction_runtime_capability(value.original.to_dict()["adapter"])
            if not capability["available"]:
                raise TaskInputError(capability["reason"])
            rows = self.receipts(value)
            if _read(self.root / "dispatch.json") is not None or any(row["stage"] == "transport_attempt" for row in rows):
                raise TaskInputError("correction_transport_indeterminate")
            if (_read(self.root / "run-started.json") is not None
                    and self._start_lease != (os.getpid(), value.payload_digest)):
                raise TaskInputError("correction_worker_outcome_unknown")
            from .correction_loop import CorrectionLoopService
            remaining = CorrectionLoopService(self.project_root).admit_worker_dispatch(value)
            authorization = _read(self.root / "authorization.json")
            if authorization != {"input": value.reference, "producer": "explicit_operator_cli", "max_invocations": 1,
                    "max_seconds": value.to_dict()["max_seconds"], "external_cost": "operator_acknowledged_unknown"}:
                raise TaskInputError("correction_runtime_authorization_required")
            return remaining

    def assert_preparable(self, value):
        with self.lock():
            self.accept(value)
            from .correction_loop import CorrectionLoopService
            CorrectionLoopService(self.project_root).admit_worker_dispatch(value)
            if _read(self.root / "dispatch.json") is not None:
                raise TaskInputError("correction_transport_indeterminate")

    def record_host_return(self, value, *, status, output_digest):
        with self.lock():
            self.receipts(value)
            dispatch = _read(self.root / "dispatch.json")
            record = host_return_record(value, dispatch, status, output_digest)
            previous = _read(self.root / "host-return.json")
            if previous is not None and previous != record:
                raise TaskInputError("correction_result_conflict")
            if previous is None:
                _write(self.root / "host-return.json", record)
            return record

    def host_return(self, value):
        with self.lock():
            self.receipts(value)
            record = _read(self.root / "host-return.json")
            if record is not None and record != host_return_record(value, _read(self.root / "dispatch.json"),
                    record.get("status"), record.get("output_digest", "")):
                raise TaskInputError("correction_result_invalid")
            return record

    def record(self, value, *, stage, input_digest=""):
        with self.lock():
            if stage not in {"worker_input_prepared", "transport_attempt"} or re.fullmatch(r"[a-f0-9]{64}", input_digest) is None:
                raise TaskInputError("correction_receipt_invalid")
            rows = self.receipts(value)
            if stage == "transport_attempt":
                if (self._start_lease != (os.getpid(), value.payload_digest)
                        or _read(self.root / "run-started.json") != {"input": value.reference}):
                    raise TaskInputError("correction_worker_outcome_unknown")
                self.assert_dispatchable(value)
                if any(row["stage"] == stage for row in rows):
                    raise TaskInputError("correction_transport_indeterminate")
            elif any(row["stage"] == stage and row["input_digest"] == input_digest for row in rows):
                return next(row for row in rows if row["stage"] == stage and row["input_digest"] == input_digest)
            if len(rows) >= 128:
                raise TaskInputError("correction_receipt_limit")
            row = _bound({"input": value.reference, "sequence": len(rows) + 1, "stage": stage,
                          "input_digest": input_digest, "observation": "host_boundary", "provider_receipt": "unknown"})
            if stage == "transport_attempt":
                _write(self.root / "dispatch.json", row)
            atomic_write(self.root / "receipts.json", canonical_bytes([*rows, row]))
            return row

    def outcome(self, value):
        self.accept(value)
        self.receipts(value)
        started, result = _read(self.root / "run-started.json"), _read(self.root / "run-result.json")
        if result is not None and (started != {"input": value.reference} or result != _bound(result)
                or result.get("input") != value.reference or result.get("status") not in {"completed", "failed"}
                or result.get("host_return") != self.host_return(value) or result.get("host_return") is None):
            raise TaskInputError("correction_result_invalid")
        return started, result
