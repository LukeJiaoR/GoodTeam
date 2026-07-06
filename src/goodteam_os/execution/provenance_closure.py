from __future__ import annotations

import hashlib
import json
from typing import Any

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.memory_provenance import (
    PROVENANCE_HORIZON,
    PROVENANCE_ROLE,
    PROVENANCE_TRANSITION,
    memory_source,
)
from goodteam_os.execution.models import ExecutionActor, LedgerEvent, LedgerEventType


def memory_text(evidence: dict[str, Any]) -> str:
    transitions = [
        str(event["payload"].get("transition", event["event_type"]))
        for event in evidence["events"]
        if isinstance(event.get("payload"), dict)
    ]
    lifecycle = " -> ".join(transitions)
    return (
        f"Execution {evidence['execution_id']} finalized with status "
        f"{evidence['final_status']}. Issue: {evidence['issue'] or 'n/a'}. "
        f"PR: {evidence['pr'] or 'n/a'}. Ledger replay: "
        f".goodteam/executions/{evidence['execution_id']}/events.jsonl. "
        f"Lifecycle: {lifecycle}."
    )


def ledger_event(
    ledger: ExecutionLedger,
    *,
    evidence: dict[str, Any],
    memory_event_id: str,
    created_at: str,
) -> LedgerEvent:
    execution_id = str(evidence["execution_id"])
    event_number = len(ledger.read_events(execution_id)) + 1
    merge_summary = dict(evidence["merge_summary"])
    payload = {
        "transition": PROVENANCE_TRANSITION,
        "execution_id": execution_id,
        "memory_event_id": memory_event_id,
        "source": memory_source(execution_id),
        "role": PROVENANCE_ROLE,
        "horizon": PROVENANCE_HORIZON,
        "evidence_ref": f"execution:{execution_id}:evidence_manifest",
        "pr_number": _pr_number_from_ref(str(evidence.get("pr", ""))),
        "merge_commit_sha": str(merge_summary.get("merge_commit_sha", "")),
        "evidence_hash": _evidence_hash(evidence),
    }
    return LedgerEvent(
        event_id=f"{execution_id}:{event_number:04d}:status_changed:{PROVENANCE_TRANSITION}",
        execution_id=execution_id,
        task_id=str(evidence["task_id"]),
        event_type=LedgerEventType.STATUS_CHANGED,
        actor=ExecutionActor.ORCHESTRATOR,
        payload=payload,
        created_at=created_at,
    )


def _pr_number_from_ref(pr_ref: str) -> int | None:
    if "#" not in pr_ref:
        return None
    _, number = pr_ref.rsplit("#", 1)
    try:
        return int(number)
    except ValueError:
        return None


def _evidence_hash(evidence: dict[str, Any]) -> str:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
