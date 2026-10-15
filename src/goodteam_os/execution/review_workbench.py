from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionActor, LedgerEvent, LedgerEventType
from goodteam_os.execution.review_workbench_contract import (
    ALLOWED_REVIEW_DECISIONS,
    Clock,
    REVIEW_DECISION_TRANSITION,
    REVIEW_WORKBENCH_SCHEMA_VERSION,
    json_ready,
    stable_hash,
    utc_now,
)
from goodteam_os.execution.review_workbench_diff import diff_surface
from goodteam_os.execution.review_workbench_rationale_mapping import (
    map_changed_files_to_rationale,
)
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from goodteam_os.execution.review_workbench_report import retained_report
from goodteam_os.execution.review_workbench_surfaces import (
    latest_review_decision,
    matching_decision_event,
    rationale_surface,
    warnings_surface,
    worker_job,
    worker_job_id,
    task_surface,
)
from goodteam_os.execution.sandbox import sandbox_surface
from goodteam_os.workers import WorkerJobStore


class ReviewWorkbenchService:
    def __init__(self, project_root: Path, *, clock: Clock | None = None):
        self.project_root = Path(project_root)
        self._clock = clock or utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.worker_store = WorkerJobStore(self.project_root)
        self.evidence_service = ExecutionEvidenceService(self.project_root, clock=clock)

    def get_review(self, execution_id: str) -> dict[str, Any]:
        evidence = self.evidence_service.get_evidence(execution_id)
        replay = self.ledger.replay(execution_id)
        events = replay["events"]
        run = replay["run"]
        job = worker_job(self.worker_store, worker_job_id(run, events))
        diff = diff_surface(self.project_root, execution_id, events)
        rationale = rationale_surface(
            task=replay["task"],
            run=run,
            events=events,
            worker_job=job,
            worker_root=self.worker_store.root,
        )
        diff = map_changed_files_to_rationale(self.project_root, diff, rationale)
        workbench = {
            "schema_version": REVIEW_WORKBENCH_SCHEMA_VERSION,
            "execution_id": execution_id,
            "task": task_surface(replay["task"], evidence),
            "task_contract": dict(evidence.get("task_contract", {})),
            "runtime_policy": dict(evidence.get("runtime_policy", {})),
            "github_broker_status": dict(evidence.get("github_broker_status", {})),
            "execution_replay_graph": dict(evidence.get("execution_replay_graph", {})),
            "source_issue": str(evidence.get("source_issue", "")),
            "pr": dict(evidence.get("pr", {})),
            "diff": diff,
            "rationale": rationale,
            "evidence": evidence,
            "candidate_review_report": retained_report(self.project_root, execution_id,
                                                        evidence.get("candidate_validation", {})),
            "memory_refs": list(evidence.get("memory_provenance_refs", [])),
            "memory_provenance_integrity": dict(
                evidence.get("memory_provenance_integrity", {})
            ),
            "reviewer_decision": latest_review_decision(events),
            "status": str(evidence.get("final_status", "")),
            "warnings": warnings_surface(evidence, diff, rationale),
            "sandbox": sandbox_surface(self.project_root, execution_id, events, worker_job=job),
        }
        return json_ready(workbench)

    def record_decision(
        self,
        execution_id: str,
        decision: str,
        reviewer: str,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized_decision = _normalize_decision(decision)
        normalized_reviewer = reviewer.strip()
        if not normalized_reviewer:
            raise ValueError("Review decision reviewer is required")
        normalized_reason = reason.strip()
        if normalized_decision in ("request_changes", "reject") and not normalized_reason:
            raise ValueError(f"Review decision reason is required for {normalized_decision}")
        replay = self.ledger.replay(execution_id)
        if replay["run"] is None and not replay["events"]:
            raise ValueError(f"Execution not found: {execution_id}")

        evidence = self.evidence_service.get_evidence(execution_id)
        decision_evidence_hash = _decision_evidence_hash(evidence)
        existing = matching_decision_event(
            replay["events"],
            decision=normalized_decision,
            reviewer=normalized_reviewer,
            reason=normalized_reason,
            decision_evidence_hash=decision_evidence_hash,
        )
        if existing is not None:
            return {"status": "deduped", "event": json_ready(existing)}

        event = self._decision_event(
            execution_id,
            task_id=str(evidence.get("task_id") or execution_id),
            decision=normalized_decision,
            reviewer=normalized_reviewer,
            reason=normalized_reason,
            evidence=evidence,
        )
        self.ledger.append_event(event)
        return {"status": "recorded", "event": json_ready(event)}

    def _decision_event(
        self,
        execution_id: str,
        *,
        task_id: str,
        decision: str,
        reviewer: str,
        reason: str,
        evidence: dict[str, Any],
    ) -> LedgerEvent:
        event_number = len(self.ledger.read_events(execution_id)) + 1
        reviewed_at = self._clock()
        payload = _decision_payload(
            execution_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            reviewed_at=reviewed_at,
            evidence=evidence,
        )
        return LedgerEvent(
            event_id=_decision_event_id(execution_id, event_number),
            execution_id=execution_id,
            task_id=task_id,
            event_type=LedgerEventType.REVIEW_RECORDED,
            actor=ExecutionActor.SYSTEM,
            payload=payload,
            created_at=reviewed_at,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m goodteam_os.execution.review_workbench")
    parser.add_argument("execution_id")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    review = ReviewWorkbenchService(args.project_root).get_review(args.execution_id)
    if args.json:
        print(json.dumps(review, indent=2))
    else:
        print(render_review_workbench(review), end="")
    return 0


def _decision_payload(
    execution_id: str,
    *,
    decision: str,
    reviewer: str,
    reason: str,
    reviewed_at: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transition": REVIEW_DECISION_TRANSITION,
        "execution_id": execution_id,
        "decision": decision,
        "reviewer": reviewer,
        "actor": reviewer,
        "reason": reason,
        "reviewer_note": reason,
        "source": "review_workbench",
        "decision_origin": "review_workbench",
        "review_mode": "dashboard",
        "evidence_ref": f"evidence/{execution_id}.json",
        "evidence_schema_version": str(evidence.get("schema_version", "")),
        "evidence_hash": stable_hash(evidence),
        "decision_evidence_hash": _decision_evidence_hash(evidence),
        "reviewed_at": reviewed_at,
    }


def _decision_evidence_hash(evidence: dict[str, Any]) -> str:
    stable_evidence = dict(evidence)
    stable_evidence.pop("execution_replay_graph", None)
    events = stable_evidence.get("ledger_events")
    if isinstance(events, list):
        stable_evidence["ledger_events"] = [
            event
            for event in events
            if not _is_workbench_decision_event(event)
        ]
    return stable_hash(stable_evidence)


def _is_workbench_decision_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    payload = event.get("payload")
    return (
        isinstance(payload, dict)
        and payload.get("transition") == REVIEW_DECISION_TRANSITION
        and payload.get("source") == "review_workbench"
    )


def _decision_event_id(execution_id: str, event_number: int) -> str:
    return (
        f"{execution_id}:{event_number:04d}:"
        f"{LedgerEventType.REVIEW_RECORDED.value}:{REVIEW_DECISION_TRANSITION}"
    )


def _normalize_decision(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized not in ALLOWED_REVIEW_DECISIONS:
        allowed = ", ".join(ALLOWED_REVIEW_DECISIONS)
        raise ValueError(f"Review decision must be one of: {allowed}")
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
