"""[INPUT]: 执行账本、角色记忆与共享证据规则。
[OUTPUT]: ExecutionEvidenceService 查询证据并写入归档链接。
[POS]: 按执行状态归档记忆，读取证据清单供评审使用。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goodteam_os.execution.evidence_manifest import build_evidence_manifest
from goodteam_os.execution.evidence_manifest_extractors import (
    ci_summary,
    final_status,
    first_payload_value,
    json_ready,
    merge_summary,
    pr_identity,
    pr_ref,
    review_summary,
    source_events,
    source_manifest,
)
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.memory_provenance import (
    PROVENANCE_HORIZON,
    PROVENANCE_ROLE,
    PROVENANCE_TRANSITION,
    memory_source,
)
from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.provenance_closure import ledger_event, memory_text
from goodteam_os.memory import RoleMemoryStore


Clock = Callable[[], str]


class ExecutionEvidenceService:
    def __init__(self, project_root: Path, *, clock: Clock | None = None):
        self.project_root = Path(project_root)
        self._clock = clock or _utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.memory_store = RoleMemoryStore(self.project_root)

    def get_evidence(self, execution_id: str) -> dict[str, Any]:
        return build_evidence_manifest(self.ledger, self.memory_store, execution_id)

    def audit_memory_provenance(self, execution_id: str) -> dict[str, Any]:
        return self.get_evidence(execution_id)["memory_provenance_integrity"]

    def finalize_provenance(self, execution_id: str) -> dict[str, Any]:
        replay = self.ledger.replay(execution_id)
        if replay["run"] is None:
            raise ValueError(f"Execution run not found: {execution_id}")

        evidence = self.build_provenance(execution_id)
        if evidence["final_status"] != "completed":
            return _finalize_result(execution_id, evidence=evidence)

        existing_closure = self._closure_event(execution_id)
        if existing_closure is not None:
            memory_event_id = str(existing_closure.payload.get("memory_event_id", "")).strip()
            return _finalize_result(
                execution_id,
                memory_event_id=memory_event_id or None,
                ledger_event_id=existing_closure.event_id,
                evidence=evidence,
            )

        source = _memory_source(execution_id)
        memory_event, memory_written = self._write_memory(source, evidence)
        memory_event_id = self.memory_store._event_id(memory_event)
        ledger_event = self._write_ledger_closure(evidence, memory_event_id)
        return _finalize_result(
            execution_id,
            memory_written=memory_written,
            memory_event_id=memory_event_id,
            ledger_event_id=ledger_event.event_id,
            evidence=evidence,
        )

    def build_provenance(self, execution_id: str) -> dict[str, Any]:
        replay = self.ledger.replay(execution_id)
        run = replay["run"]
        task = replay["task"]
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")

        events = source_events(replay["events"])
        source = source_manifest(task, events)
        pr_repo, pr_number = pr_identity(run, events, fallback_repo=source["repo"])
        ci = ci_summary(events)
        merge = merge_summary(events)
        method_refs = {
            key: value
            for key in ("task_skill_context", "skill_execution_snapshot_ref")
            if (value := first_payload_value(events, key)) is not None
        }
        return {
            **({"skill_execution": {"schema_version": "skill_execution_evidence.v1", "state": "captured", **method_refs}}
               if method_refs else {}),
            "execution_id": execution_id,
            "task_id": run.task_id,
            "issue": source["issue_ref"],
            "pr": pr_ref(pr_repo, pr_number),
            "events": [json_ready(event) for event in events],
            "ci_summary": ci,
            "review_summary": review_summary(events),
            "merge_summary": merge,
            "final_status": final_status(run, pr_number, ci_state=ci.get("state"), merged=merge["merged"]),
            "timestamp": _evidence_timestamp(run, events),
        }

    def _write_memory(self, source: str, evidence: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._memory_event_for_source(source)
        if existing is not None:
            return existing, False
        event = self.memory_store.append_memory_event(
            PROVENANCE_ROLE,
            horizon=PROVENANCE_HORIZON,
            text=memory_text(evidence),
            source=source,
        )
        return event, True

    def _write_ledger_closure(self, evidence: dict[str, Any], memory_event_id: str) -> LedgerEvent:
        existing = self._closure_event(str(evidence["execution_id"]))
        if existing is not None:
            return existing

        event = ledger_event(
            self.ledger,
            evidence=evidence,
            memory_event_id=memory_event_id,
            created_at=self._clock(),
        )
        self.ledger.append_event(event)
        return event

    def _memory_event_for_source(self, source: str) -> dict[str, Any] | None:
        for event in self.memory_store._read_events(PROVENANCE_ROLE):
            if event.get("source") == source:
                return event
        return None

    def _closure_event(self, execution_id: str) -> LedgerEvent | None:
        for event in self.ledger.read_events(execution_id):
            if event.payload.get("transition") == PROVENANCE_TRANSITION:
                return event
        return None


def _finalize_result(
    execution_id: str,
    *,
    memory_written: bool = False,
    memory_event_id: str | None = None,
    ledger_event_id: str | None = None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "memory_written": memory_written,
        "memory_event_id": memory_event_id,
        "ledger_event_id": ledger_event_id,
        "evidence": evidence,
    }


def _evidence_timestamp(run: Any, events: list[LedgerEvent]) -> str:
    if events:
        return events[-1].created_at
    return str(run.updated_at)


def _memory_source(execution_id: str) -> str:
    return memory_source(execution_id)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
