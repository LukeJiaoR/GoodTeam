"""Conservative read projection of protected input, never acceptance authority."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_snapshot import INPUT_JOB_PREFIX, TaskInputError
from .task_snapshot_store import store_for_project


def task_input_evidence(project_root: Path, execution_id: str, *, worker_job_id: str = "") -> dict[str, Any]:
    legacy = {"status": "legacy_unverified", "available": False, "reason": "task_input_not_captured",
              "snapshot": {}, "receipts": [], "acceptance_coverage": [], "verification": "unverified"}
    try:
        store = store_for_project(project_root, existing_only=True)
        snapshot = store.load(execution_id) if store else None
        if snapshot is None:
            if worker_job_id.startswith(INPUT_JOB_PREFIX):
                raise TaskInputError("task_input_snapshot_missing")
            return legacy
        reason = ""
        try:
            receipts = store.receipts(snapshot)
        except TaskInputError as exc:
            receipts, reason = [], exc.code
        return {"status": "partial" if reason else "snapshot_committed", "available": True, "reason": reason,
                "snapshot": snapshot.reference, "source_trust": "captured_external_source",
                "source_revision": snapshot.to_dict()["source_revision"],
                "contract": snapshot.task.task_contract.to_dict(),
                "receipts": receipts, "handoff_status": "unavailable" if reason else "available", "verification": "unverified",
                "acceptance_coverage": [
                    {"criterion_id": f"criterion:{snapshot.payload_digest}:{index}", "text": text,
                     "status": "unknown", "evidence": [], "reason": "no_bound_validation_receipt"}
                    for index, text in enumerate(snapshot.task.task_contract.acceptance_criteria)
                ]}
    except (OSError, ValueError) as exc:
        return {**legacy, "status": "unavailable",
                "reason": exc.code if isinstance(exc, TaskInputError) else "task_input_store_unavailable"}
