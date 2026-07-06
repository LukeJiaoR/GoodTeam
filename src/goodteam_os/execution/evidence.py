from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import (
    ExecutionActor,
    ExecutionRunStatus,
    LedgerEvent,
    LedgerEventType,
)
from goodteam_os.memory import RoleMemoryStore


Clock = Callable[[], str]
PROVENANCE_TRANSITION, PROVENANCE_ROLE, PROVENANCE_HORIZON = "provenance_written", "orchestrator", "long-term"


class ExecutionEvidenceService:
    def __init__(self, project_root: Path, *, clock: Clock | None = None):
        self.project_root = Path(project_root)
        self._clock = clock or _utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.memory_store = RoleMemoryStore(self.project_root)

    def finalize_provenance(self, execution_id: str) -> dict[str, Any]:
        replay = self.ledger.replay(execution_id)
        if replay["run"] is None:
            raise ValueError(f"Execution run not found: {execution_id}")

        evidence = self.build_provenance(execution_id)
        if evidence["final_status"] == "pending":
            return _finalize_result(execution_id, evidence=evidence)

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

        events = _source_events(replay["events"])
        issue = _issue_ref(task, events)
        pr_number = _pr_number(run, events)
        pr_repo = _repo_from_events(events) or (task.repo if task is not None else "")
        return {
            "execution_id": execution_id,
            "task_id": run.task_id,
            "issue": issue,
            "pr": _pr_ref(pr_repo, pr_number),
            "events": [_json_ready(event) for event in events],
            "ci_summary": _ci_summary(events),
            "review_summary": _review_summary(events),
            "merge_summary": _merge_summary(events),
            "final_status": _final_status(run, pr_number, events),
            "timestamp": _evidence_timestamp(run, events),
        }

    def _write_memory(self, source: str, evidence: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._memory_event_for_source(source)
        if existing is not None:
            return existing, False
        event = self.memory_store.append_memory_event(
            PROVENANCE_ROLE,
            horizon=PROVENANCE_HORIZON,
            text=_memory_text(evidence),
            source=source,
        )
        return event, True

    def _write_ledger_closure(self, evidence: dict[str, Any], memory_event_id: str) -> LedgerEvent:
        existing = self._closure_event(str(evidence["execution_id"]))
        if existing is not None:
            return existing

        event = _ledger_event(
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


def _source_events(events: list[LedgerEvent]) -> list[LedgerEvent]:
    return [event for event in events if event.payload.get("transition") != PROVENANCE_TRANSITION]


def _issue_ref(task: Any, events: list[LedgerEvent]) -> str:
    if task is not None:
        return f"{task.repo}#{task.issue_number}"
    repo = _repo_from_events(events)
    issue_number = _first_payload_value(events, "issue_number")
    if repo and issue_number is not None:
        return f"{repo}#{issue_number}"
    return ""


def _pr_ref(repo: str, pr_number: int | None) -> str:
    return f"{repo}#{pr_number}" if repo and pr_number is not None else ""


def _repo_from_events(events: list[LedgerEvent]) -> str:
    for event in events:
        repo = event.payload.get("repo")
        if isinstance(repo, str) and repo.strip():
            return repo.strip()
    return ""


def _pr_number(run: Any, events: list[LedgerEvent]) -> int | None:
    if run.pr_number is not None:
        return int(run.pr_number)
    for event in reversed(events):
        value = event.payload.get("pr_number")
        if isinstance(value, int):
            return value
    return None


def _first_payload_value(events: list[LedgerEvent], key: str) -> object | None:
    for event in events:
        if key in event.payload:
            return event.payload[key]
    return None


def _ci_summary(events: list[LedgerEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.payload.get("transition") == "ci_observed":
            summary = event.payload.get("summary")
            return dict(summary) if isinstance(summary, dict) else {}
    return {}


def _review_summary(events: list[LedgerEvent]) -> dict[str, Any]:
    states: list[str] = []
    reviews = comments = 0
    for event in events:
        transition = event.payload.get("transition")
        if transition == "review_recorded":
            reviews += 1
            state = str(event.payload.get("review_state", "")).strip()
            if state and state not in states:
                states.append(state)
        elif transition == "pr_comment_recorded":
            comments += 1
    return {"reviews": reviews, "comments": comments, "states": states}


def _merge_summary(events: list[LedgerEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.payload.get("transition") == "merge_completed":
            return {
                "merged": True,
                "merged_at": event.payload.get("merged_at", ""),
                "merge_commit_sha": event.payload.get("merge_commit_sha", ""),
                "merged_by": event.payload.get("merged_by", ""),
            }
    return {"merged": False, "merged_at": "", "merge_commit_sha": "", "merged_by": ""}


def _final_status(run: Any, pr_number: int | None, events: list[LedgerEvent]) -> str:
    if pr_number is None:
        return "pending"
    if run.status is ExecutionRunStatus.FAILED:
        return "failed"
    ci = _ci_summary(events)
    if ci.get("state") == "failure":
        return "failed"
    if run.status is ExecutionRunStatus.SUCCEEDED and _merge_summary(events)["merged"]:
        return "completed"
    return "pending"


def _evidence_timestamp(run: Any, events: list[LedgerEvent]) -> str:
    if events:
        return events[-1].created_at
    return str(run.updated_at)


def _memory_source(execution_id: str) -> str:
    return f"execution:{execution_id}:final_provenance"


def _memory_text(evidence: dict[str, Any]) -> str:
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


def _ledger_event(
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


def _json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.loads(json.dumps(value))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
