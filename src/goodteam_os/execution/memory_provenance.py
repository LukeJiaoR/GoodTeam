from __future__ import annotations

from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.memory import RoleMemoryStore


PROVENANCE_TRANSITION = "provenance_written"
PROVENANCE_ROLE = "orchestrator"
PROVENANCE_HORIZON = "long-term"
MEMORY_SOURCE_SUFFIX = "final_provenance"


def build_memory_provenance_projection(
    memory_store: RoleMemoryStore,
    events: list[LedgerEvent],
    execution_id: str,
    *,
    final_status: str,
    incomplete_reason: str,
) -> dict[str, Any]:
    source = memory_source(execution_id)
    memory_events = _memory_events_by_id(memory_store, source)
    closure_events = _closure_events(events)
    refs = _refs_from_closures(memory_store, closure_events, memory_events, execution_id, source)
    refs.extend(_orphan_refs(memory_store, memory_events, refs, execution_id, source))
    return {
        "refs": refs,
        "integrity": _integrity(refs, closure_events, memory_events, final_status, incomplete_reason),
    }


def memory_source(execution_id: str) -> str:
    return f"execution:{execution_id}:{MEMORY_SOURCE_SUFFIX}"


def _memory_events_by_id(
    memory_store: RoleMemoryStore,
    source: str,
) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for event in memory_store._read_events(PROVENANCE_ROLE):
        if event.get("source") != source:
            continue
        events[memory_store._event_id(event)] = event
    return events


def _closure_events(events: list[LedgerEvent]) -> list[LedgerEvent]:
    return [event for event in events if event.payload.get("transition") == PROVENANCE_TRANSITION]


def _refs_from_closures(
    memory_store: RoleMemoryStore,
    closure_events: list[LedgerEvent],
    memory_events: dict[str, dict[str, Any]],
    execution_id: str,
    source: str,
) -> list[dict[str, Any]]:
    refs = []
    for index, event in enumerate(closure_events):
        memory_event_id = str(event.payload.get("memory_event_id", "")).strip()
        memory_event = memory_events.get(memory_event_id)
        issues = _closure_issues(index, memory_event)
        refs.append(
            _contract_ref(
                execution_id=execution_id,
                memory_event_id=memory_event_id,
                ledger_event_id=event.event_id,
                source=str(event.payload.get("source", source) or source),
                role=_ref_role(event, memory_event),
                horizon=_ref_horizon(event, memory_event),
                evidence_hash=str(event.payload.get("evidence_hash", "")),
                created_at=event.created_at,
                timestamp=_memory_value(memory_event, "timestamp"),
                supersedes=_memory_value(memory_event, "supersedes"),
                is_superseded=_is_superseded(memory_store, memory_event_id, memory_event),
                status=_status_for_issues(issues),
                issues=issues,
            )
        )
    return refs


def _orphan_refs(
    memory_store: RoleMemoryStore,
    memory_events: dict[str, dict[str, Any]],
    refs: list[dict[str, Any]],
    execution_id: str,
    source: str,
) -> list[dict[str, Any]]:
    linked_ids = {str(ref["memory_event_id"]) for ref in refs if ref.get("ledger_event_id")}
    orphaned = []
    for memory_event_id, event in memory_events.items():
        if memory_event_id in linked_ids:
            continue
        orphaned.append(
            _contract_ref(
                execution_id=execution_id,
                memory_event_id=memory_event_id,
                ledger_event_id="",
                source=source,
                role=str(event.get("role_key", "")),
                horizon=str(event.get("horizon", "")),
                evidence_hash="",
                created_at="",
                timestamp=str(event.get("timestamp", "")),
                supersedes=str(event.get("supersedes", "")),
                is_superseded=_is_superseded(memory_store, memory_event_id, event),
                status="orphan_memory_event",
                issues=["missing_ledger_link"],
            )
        )
    return orphaned


def _contract_ref(
    *,
    execution_id: str,
    memory_event_id: str,
    ledger_event_id: str,
    source: str,
    role: str,
    horizon: str,
    evidence_hash: str,
    created_at: str,
    timestamp: str,
    supersedes: str,
    is_superseded: bool,
    status: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "memory_event_id": memory_event_id,
        "ledger_event_id": ledger_event_id,
        "source": source,
        "role": role,
        "horizon": horizon,
        "evidence_hash": evidence_hash,
        "created_at": created_at,
        "timestamp": timestamp,
        "supersedes": supersedes,
        "is_superseded": is_superseded,
        "status": status,
        "issues": issues,
    }


def _closure_issues(index: int, memory_event: dict[str, Any] | None) -> list[str]:
    issues = []
    if index > 0:
        issues.append("duplicate_ledger_closure")
    if memory_event is None:
        issues.append("missing_memory_event")
    return issues


def _status_for_issues(issues: list[str]) -> str:
    if "missing_memory_event" in issues:
        return "missing_memory_event"
    if "duplicate_ledger_closure" in issues:
        return "duplicate_ledger_closure"
    return "complete"


def _integrity(
    refs: list[dict[str, Any]],
    closure_events: list[LedgerEvent],
    memory_events: dict[str, dict[str, Any]],
    final_status: str,
    incomplete_reason: str,
) -> dict[str, Any]:
    missing_ids = [
        str(ref["memory_event_id"])
        for ref in refs
        if "missing_memory_event" in ref.get("issues", [])
    ]
    orphan_ids = [
        str(ref["memory_event_id"])
        for ref in refs
        if "missing_ledger_link" in ref.get("issues", [])
    ]
    duplicate_ids = [
        str(ref["ledger_event_id"])
        for ref in refs
        if "duplicate_ledger_closure" in ref.get("issues", [])
    ]
    pending_reason = _pending_reason(final_status, incomplete_reason, closure_events, memory_events)
    complete = (
        not pending_reason
        and len(closure_events) == 1
        and len(memory_events) == 1
        and not missing_ids
        and not orphan_ids
        and not duplicate_ids
    )
    return {
        "complete": complete,
        "pending": bool(pending_reason),
        "pending_reason": pending_reason,
        "ledger_closure_count": len(closure_events),
        "memory_event_count": len(memory_events),
        "missing_memory_event_ids": missing_ids,
        "orphan_memory_event_ids": orphan_ids,
        "duplicate_ledger_event_ids": duplicate_ids,
    }


def _pending_reason(
    final_status: str,
    incomplete_reason: str,
    closure_events: list[LedgerEvent],
    memory_events: dict[str, dict[str, Any]],
) -> str:
    if closure_events or memory_events:
        return ""
    if final_status == "completed":
        return "provenance_not_finalized"
    if final_status in {"pending", "failed", "incomplete"}:
        return incomplete_reason or f"final_status:{final_status}"
    return ""


def _ref_role(event: LedgerEvent, memory_event: dict[str, Any] | None) -> str:
    return str(event.payload.get("role") or _memory_value(memory_event, "role_key") or PROVENANCE_ROLE)


def _ref_horizon(event: LedgerEvent, memory_event: dict[str, Any] | None) -> str:
    return str(event.payload.get("horizon") or _memory_value(memory_event, "horizon") or PROVENANCE_HORIZON)


def _memory_value(memory_event: dict[str, Any] | None, key: str) -> str:
    if memory_event is None:
        return ""
    return str(memory_event.get(key, ""))


def _is_superseded(
    memory_store: RoleMemoryStore,
    memory_event_id: str,
    memory_event: dict[str, Any] | None,
) -> bool:
    if memory_event is None:
        return False
    info = memory_store.event_supersede_info(memory_event_id)
    if info is None:
        return False
    return bool(info.get("is_superseded", False))
