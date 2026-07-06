from __future__ import annotations

from pathlib import Path
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.review_workbench_contract import (
    REVIEW_DECISION_TRANSITION,
    json_ready,
)
from goodteam_os.workers import WorkerJobStore


def task_surface(task: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    if task is not None:
        payload = json_ready(task)
    else:
        source = dict(evidence.get("source", {}))
        payload = {
            "task_id": str(evidence.get("task_id", "")),
            "source_type": str(source.get("type", "")),
            "repo": str(source.get("repo", "")),
            "issue_number": source.get("issue_number"),
            "title": "",
            "body": "",
            "goal": "",
            "status": "",
            "created_at": "",
            "updated_at": "",
        }
    payload["source_issue"] = str(evidence.get("source_issue", ""))
    return payload


def rationale_surface(
    *,
    task: Any,
    run: Any,
    events: list[LedgerEvent],
    worker_job: dict[str, Any] | None,
    worker_root: Path,
) -> dict[str, Any]:
    worker_result = _worker_result_surface(worker_job, worker_root)
    snapshots = _prompt_snapshots(worker_job, worker_root)
    task_payload = json_ready(task) if task is not None else {}
    run_payload = json_ready(run) if run is not None else {}
    lifecycle = [
        str(event.payload.get("transition", event.event_type.value))
        for event in events
    ]
    available = bool(task_payload or run_payload or worker_result["available"] or snapshots)
    return {
        "available": available,
        "reason": "" if available else "rationale_artifact_missing",
        "task": task_payload,
        "run": run_payload,
        "lifecycle": lifecycle,
        "worker_result": worker_result,
        "prompt_snapshots": snapshots,
    }


def warnings_surface(
    evidence: dict[str, Any],
    diff: dict[str, Any],
    rationale: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    incomplete_reason = str(evidence.get("incomplete_reason", "")).strip()
    if incomplete_reason:
        code = _incomplete_warning_code(incomplete_reason)
        warnings.append({"code": code, "message": incomplete_reason})
    if not diff.get("available"):
        warnings.append(
            {
                "code": str(diff.get("reason", "diff_artifact_missing")),
                "message": "No persisted PR diff artifact is available.",
            }
        )
    elif diff.get("patch_state") == "missing":
        warnings.append(
            {
                "code": "patch_missing",
                "message": "Persisted PR diff metadata is available, but pr-diff.patch is missing.",
            }
        )
    elif diff.get("patch_state") == "parse_failed":
        warnings.append(
            {
                "code": "patch_parse_failed",
                "message": "Persisted pr-diff.patch could not be parsed as unified diff.",
            }
        )
    if not rationale.get("available"):
        warnings.append(
            {
                "code": str(rationale.get("reason", "rationale_artifact_missing")),
                "message": "No rationale artifact is available.",
            }
        )
    integrity = evidence.get("memory_provenance_integrity")
    if _has_broken_memory_integrity(integrity):
        warnings.append(
            {
                "code": "memory_provenance_incomplete",
                "message": "Memory provenance integrity is incomplete.",
            }
        )
    return _dedupe_warnings(warnings)


def latest_review_decision(events: list[LedgerEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.payload.get("transition") != REVIEW_DECISION_TRANSITION:
            continue
        payload = event.payload
        return {
            "available": True,
            "event_id": event.event_id,
            "decision": str(payload.get("decision", "")),
            "reviewer": str(payload.get("reviewer", "")),
            "reason": str(payload.get("reason", "")),
            "reviewed_at": str(payload.get("reviewed_at", event.created_at)),
            "evidence_hash": str(payload.get("evidence_hash", "")),
        }
    return {
        "available": False,
        "event_id": "",
        "decision": "",
        "reviewer": "",
        "reason": "",
        "reviewed_at": "",
        "evidence_hash": "",
    }


def matching_decision_event(
    events: list[LedgerEvent],
    *,
    decision: str,
    reviewer: str,
    reason: str,
) -> LedgerEvent | None:
    for event in events:
        payload = event.payload
        if payload.get("transition") != REVIEW_DECISION_TRANSITION:
            continue
        if (
            payload.get("decision") == decision
            and payload.get("reviewer") == reviewer
            and payload.get("reason", "") == reason
            and payload.get("source") == "review_workbench"
        ):
            return event
    return None


def worker_job(worker_store: WorkerJobStore, worker_job_id: str) -> dict[str, Any] | None:
    if not worker_job_id:
        return None
    for job in worker_store.list_jobs():
        if job.get("id") == worker_job_id:
            return job
    return None


def worker_job_id(run: Any, events: list[LedgerEvent]) -> str:
    if run is not None and str(run.worker_job_id).strip():
        return str(run.worker_job_id).strip()
    for event in reversed(events):
        value = event.payload.get("worker_job_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _worker_result_surface(
    worker_job_value: dict[str, Any] | None,
    worker_root: Path,
) -> dict[str, Any]:
    result_path = _worker_file(worker_job_value, worker_root, "result.md")
    summary = str(worker_job_value.get("summary", "")) if worker_job_value else ""
    if result_path is None:
        return {"available": False, "path": "", "summary": summary, "excerpt": ""}
    text = result_path.read_text(encoding="utf-8").strip()
    return {
        "available": bool(text or summary),
        "path": str(result_path),
        "summary": summary,
        "excerpt": " ".join(text.split())[:1000],
    }


def _prompt_snapshots(
    worker_job_value: dict[str, Any] | None,
    worker_root: Path,
) -> list[dict[str, str]]:
    snapshots = []
    for name, filename in (
        ("role_prompt", "role_prompt.md"),
        ("task", "task.md"),
        ("context", "context.md"),
        ("constraints", "constraints.md"),
    ):
        path = _worker_file(worker_job_value, worker_root, filename)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8").strip()
        snapshots.append(
            {
                "name": name,
                "path": str(path),
                "excerpt": " ".join(text.split())[:1000],
            }
        )
    return snapshots


def _worker_file(
    worker_job_value: dict[str, Any] | None,
    worker_root: Path,
    filename: str,
) -> Path | None:
    if worker_job_value is None:
        return None
    job_id = str(worker_job_value.get("id", "")).strip()
    if not job_id:
        return None
    path = worker_root / job_id / filename
    return path if path.is_file() else None


def _incomplete_warning_code(incomplete_reason: str) -> str:
    if incomplete_reason.startswith("execution_failed"):
        return "execution_failed"
    return incomplete_reason


def _has_broken_memory_integrity(integrity: Any) -> bool:
    if not isinstance(integrity, dict):
        return False
    return not bool(integrity.get("complete", False)) and not bool(integrity.get("pending", False))


def _dedupe_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for warning in warnings:
        code = warning["code"]
        if code in seen:
            continue
        seen.add(code)
        deduped.append(warning)
    return deduped
