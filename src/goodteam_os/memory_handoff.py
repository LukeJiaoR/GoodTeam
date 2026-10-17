"""Internal ordinary-worker memory handoff observations, never dispatch authority.

Only metadata is retained outside the project. Host preparation/call-entry
observations do not prove that a provider received or read an input. Missing
historical enrollment is unknown; retained partial enrollment fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .memory_storage import atomic_write, read_bytes, role_lock

MAX_CAPTURE_BYTES = 16 * 1024
MAX_RECEIPTS_BYTES = 1024 * 1024
MAX_RECEIPTS = 1024


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryHandoffError(ValueError):
    """Stable internal failure. Never feed this into provider retry selection."""


@dataclass(frozen=True)
class PreparedMemoryRecord:
    """Immutable receipt passed along with the exact prepared input."""

    data: bytes

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(self.data)

    @property
    def reference(self) -> str:
        return digest(self.data)


class MemoryHandoffStore:
    def __init__(self, project_root: Path):
        from .service import service_state_dir

        project = Path(project_root).resolve()
        operator = Path(service_state_dir()).expanduser().absolute()
        if operator.resolve().is_relative_to(project):
            raise MemoryHandoffError("memory_handoff_unsafe_store_root")
        self.project_scope = digest(str(project).encode("utf-8"))
        # Do not resolve operator symlinks: the anchored helpers reject them.
        self.root = operator / "worker-memory" / self.project_scope

    def job_root(self, job: dict[str, Any]) -> Path:
        return self.root / digest(self._identity(job)["job_id"].encode("utf-8"))

    def _identity(self, job: dict[str, Any]) -> dict[str, str]:
        values = {"project_scope": self.project_scope, "job_id": job.get("id"),
                  "run_id": job.get("run_id"), "role": job.get("role"), "adapter": job.get("adapter")}
        for key in ("project_key", "execution_id"):
            if job.get(key):
                values[key] = job[key]
        if any(not isinstance(value, str) or not value or len(value) > 256
               or any(character in value for character in ("\x00", "\n", "\r")) for value in values.values()):
            raise MemoryHandoffError("memory_handoff_identity_invalid")
        if not values["job_id"].startswith("job-") or values["job_id"].startswith(("job-input-", "job-correction-", "job-skill-")):
            raise MemoryHandoffError("memory_handoff_job_not_ordinary")
        return values

    def reserve(self, job: dict[str, Any]) -> None:
        """Publish required enrollment before publishing any ordinary job files."""
        root = self.job_root(job)
        expected = canonical_bytes({"schema_version": "worker_memory_capture.v1", **self._identity(job)})
        with role_lock(root):
            previous = read_bytes(root / "required.json", maximum=MAX_CAPTURE_BYTES)
            if previous is not None and previous != expected:
                raise MemoryHandoffError("memory_handoff_identity_mismatch")
            if previous is None:
                atomic_write(root / "required.json", expected, maximum=MAX_CAPTURE_BYTES)

    def capture(self, job: dict[str, Any], packet_bytes: bytes) -> dict[str, Any]:
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                raise MemoryHandoffError("memory_handoff_capture_missing")
            required = self._required(root, job)
            previous = read_bytes(root / "capture.json", maximum=MAX_CAPTURE_BYTES)
            if previous is not None:
                value = json.loads(previous)
                if value.get("packet_digest") != digest(packet_bytes):
                    raise MemoryHandoffError("memory_handoff_capture_conflict")
                return self._load(root, job)
            if any(read_bytes(root / name, maximum=MAX_RECEIPTS_BYTES) is not None
                   for name in ("capture-required.json", "receipts.json", "receipts-required.json")):
                raise MemoryHandoffError("memory_handoff_capture_missing")
            value = {**required, "packet_digest": digest(packet_bytes), "captured_at": _now()}
            # Both retained files are needed for every read. Interrupted initial
            # capture and a lost/rewound receipt history remain unavailable.
            empty = canonical_bytes([])
            atomic_write(root / "receipts.json", empty, maximum=MAX_RECEIPTS_BYTES)
            atomic_write(root / "receipts-required.json", canonical_bytes({"count": 0, "digest": digest(empty)}), maximum=MAX_CAPTURE_BYTES)
            captured = canonical_bytes(value)
            atomic_write(root / "capture.json", captured, maximum=MAX_CAPTURE_BYTES)
            atomic_write(root / "capture-required.json", canonical_bytes({"capture_ref": digest(captured)}), maximum=MAX_CAPTURE_BYTES)
            return value

    def _required(self, root: Path, job: dict[str, Any]) -> dict[str, Any]:
        raw = read_bytes(root / "required.json", maximum=MAX_CAPTURE_BYTES)
        expected = {"schema_version": "worker_memory_capture.v1", **self._identity(job)}
        if raw != canonical_bytes(expected):
            raise MemoryHandoffError("memory_handoff_enrollment_missing_or_mismatched")
        return expected

    def _load(self, root: Path, job: dict[str, Any]) -> dict[str, Any]:
        required = self._required(root, job)
        raw = read_bytes(root / "capture.json", maximum=MAX_CAPTURE_BYTES)
        if raw is None:
            raise MemoryHandoffError("memory_handoff_capture_missing")
        marker = read_bytes(root / "capture-required.json", maximum=MAX_CAPTURE_BYTES)
        if marker != canonical_bytes({"capture_ref": digest(raw)}):
            raise MemoryHandoffError("memory_handoff_capture_mismatched")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != set(required) | {"packet_digest", "captured_at"}
                or any(value.get(key) != item for key, item in required.items())
                or not _is_digest(value.get("packet_digest")) or not isinstance(value.get("captured_at"), str)):
            raise MemoryHandoffError("memory_handoff_capture_invalid")
        return value

    def load(self, job: dict[str, Any]) -> dict[str, Any] | None:
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                return None
            capture = self._load(root, job)
            self._receipts(root)
            return capture

    def _receipts(self, root: Path) -> list[dict[str, Any]]:
        raw = read_bytes(root / "receipts.json", maximum=MAX_RECEIPTS_BYTES)
        marker = read_bytes(root / "receipts-required.json", maximum=MAX_CAPTURE_BYTES)
        if raw is None or marker is None:
            raise MemoryHandoffError("memory_handoff_receipts_missing")
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > MAX_RECEIPTS or any(not isinstance(item, dict) for item in value):
            raise MemoryHandoffError("memory_handoff_receipts_invalid")
        if marker != canonical_bytes({"count": len(value), "digest": digest(raw)}):
            raise MemoryHandoffError("memory_handoff_receipts_mismatched")
        return value

    def _append(self, root: Path, value: dict[str, Any]) -> None:
        receipts = self._receipts(root)
        if len(receipts) >= MAX_RECEIPTS:
            raise MemoryHandoffError("memory_handoff_receipts_limit")
        updated = canonical_bytes([*receipts, value])
        if len(updated) > MAX_RECEIPTS_BYTES:
            raise MemoryHandoffError("memory_handoff_receipts_limit")
        # Write the expectation first. A failed second write is unknown and
        # cannot be repaired implicitly into another external attempt.
        atomic_write(root / "receipts-required.json",
                     canonical_bytes({"count": len(receipts) + 1, "digest": digest(updated)}), maximum=MAX_CAPTURE_BYTES)
        atomic_write(root / "receipts.json", updated, maximum=MAX_RECEIPTS_BYTES)

    def prepare(self, job: dict[str, Any], *, prompt: str, fragment: str, citations: tuple[str, ...],
                packet_digest: str | None, validation: dict[str, Any]) -> PreparedMemoryRecord | None:
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                return None
            capture = self._load(root, job)
            if (packet_digest is not None and not _is_digest(packet_digest)) or len(citations) > 1024:
                raise MemoryHandoffError("memory_handoff_preparation_invalid")
            # Citation IDs are the only content-bearing identifiers retained.
            if any(not isinstance(item, str) or len(item) > 256 for item in citations):
                raise MemoryHandoffError("memory_handoff_preparation_invalid")
            value = {"schema_version": "worker_memory_receipt.v1", "stage": "worker_input_prepared",
                     **self._identity(job), "capture_ref": digest(canonical_bytes(capture)),
                     "prepared_id": str(uuid4()), "prepared_at": _now(),
                     "packet_digest": packet_digest, "fragment_digest": digest(fragment.encode("utf-8")),
                     "input_digest": digest(prompt.encode("utf-8")), "citations": list(citations),
                     "capture_packet_match": packet_digest == capture["packet_digest"], "validation": validation,
                     "observation": "host_input_prepared", "provider_receipt": "unknown", "provider_read": "unknown"}
            self._append(root, value)
            return PreparedMemoryRecord(canonical_bytes(value))

    def transport(self, job: dict[str, Any], prepared: PreparedMemoryRecord | None) -> str | None:
        if prepared is None:
            # A historical job must not silently gain a capture at dispatch.
            if self.load(job) is not None:
                raise MemoryHandoffError("memory_handoff_preparation_missing")
            return None
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                raise MemoryHandoffError("memory_handoff_capture_missing")
            capture = self._load(root, job)
            value = prepared.value
            if (value.get("capture_ref") != digest(canonical_bytes(capture))
                    or any(value.get(key) != item for key, item in self._identity(job).items())
                    or value not in self._receipts(root)):
                raise MemoryHandoffError("memory_handoff_preparation_missing")
            receipt = {"schema_version": "worker_memory_receipt.v1", "stage": "transport_attempt",
                       **self._identity(job), "transport_id": str(uuid4()), "prepared_ref": prepared.reference,
                       "prepared_id": value["prepared_id"], "attempted_at": _now(),
                       "observation": "host_call_entry", "provider_receipt": "unknown", "provider_read": "unknown"}
            self._append(root, receipt)
            return digest(canonical_bytes(receipt))

    def writeback(self, job: dict[str, Any], *, kind: str, receipt: dict[str, Any],
                  prepared: PreparedMemoryRecord | None, transport_ref: str | None) -> None:
        if kind not in {"result", "provenance"}:
            raise MemoryHandoffError("memory_handoff_writeback_invalid")
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                return
            capture = self._load(root, job)
            receipts = self._receipts(root)
            if prepared is not None:
                if (prepared.value not in receipts or prepared.value.get("capture_ref") != digest(canonical_bytes(capture))
                        or any(prepared.value.get(key) != item for key, item in self._identity(job).items())):
                    raise MemoryHandoffError("memory_handoff_preparation_missing")
            if transport_ref is not None:
                if prepared is None or not any(
                    item.get("stage") == "transport_attempt" and digest(canonical_bytes(item)) == transport_ref
                    and item.get("prepared_ref") == prepared.reference
                    and all(item.get(key) == expected for key, expected in self._identity(job).items())
                    for item in receipts
                ):
                    raise MemoryHandoffError("memory_handoff_transport_mismatch")
            value = {"schema_version": "worker_memory_receipt.v1", "stage": "memory_writeback",
                     **self._identity(job), "capture_ref": digest(canonical_bytes(capture)),
                     "writeback_id": str(uuid4()), "kind": kind,
                     "prepared_ref": prepared.reference if prepared is not None else None,
                     "transport_ref": transport_ref, "receipt": receipt}
            self._append(root, value)

    def receipts(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        root = self.job_root(job)
        with role_lock(root, create=False) as present:
            if not present:
                return []
            self._load(root, job)
            return self._receipts(root)

    def inspection_snapshot(self, job: dict[str, Any]) -> tuple[dict, list[dict]] | None:
        """Read one verified observation snapshot without creating even a lock.

        The ordinary write/read helpers can initialize a missing lock inode.
        Public inspection must instead preserve missing or damaged enrollment.
        A shared lock on the existing inode excludes concurrent receipt writes.
        """
        from .memory_storage import fcntl
        from .skills.filesystem import directory, open_lock_file
        from .skills.models import SkillError

        if fcntl is None or os.name != "posix":
            raise MemoryHandoffError("memory_handoff_inspection_unavailable")
        root = self.job_root(job)
        entered = False
        try:
            with directory(root) as descriptor:
                entered = True
                lock = open_lock_file(descriptor, ".store.lock", create=False)
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH)
                    return self._load(root, job), self._receipts(root)
                finally:
                    os.close(lock)
        except SkillError as exc:
            if not entered and isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise MemoryHandoffError("memory_handoff_inspection_unavailable") from exc


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
