from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goodteam_os.execution.github import GitHubClient, GitHubExecutionBindingStore
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionActor, LedgerEvent, LedgerEventType


Clock = Callable[[], str]
PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}
FAILING_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}


class CIStatusService:
    def __init__(
        self,
        project_root: Path,
        github_client: GitHubClient,
        *,
        clock: Clock | None = None,
    ):
        self.project_root = Path(project_root)
        self.github_client = github_client
        self._clock = clock or _utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.binding_store = GitHubExecutionBindingStore(self.project_root)

    def fetch_checks(self, repo: str, ref: str) -> dict[str, Any]:
        return self.github_client.fetch_checks(repo, ref)

    def summarize_checks(self, checks: dict[str, Any]) -> dict[str, Any]:
        runs = _extract_check_runs(checks)
        passed = failed = pending = 0
        for check in runs:
            state = _check_state(check)
            if state == "passed":
                passed += 1
            elif state == "failed":
                failed += 1
            else:
                pending += 1

        total = len(runs)
        if checks.get("unavailable") or total == 0:
            state = "unavailable"
        elif failed:
            state = "failure"
        elif pending:
            state = "pending"
        else:
            state = "success"
        return {
            "state": state,
            "total": total,
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "raw": checks,
        }

    def observe_execution_checks(self, execution_id: str) -> dict[str, Any]:
        run = self.ledger.read_run(execution_id)
        binding = self.binding_store.read_binding(execution_id)
        if binding is None or binding.pr_number is None or not binding.branch_name:
            summary = self.summarize_checks({"unavailable": True})
            reason = "missing_pr_binding"
            self._append_ci_event(execution_id, summary, repo="", ref="", reason=reason)
            return summary

        checks = self.fetch_checks(binding.repo, binding.branch_name)
        summary = self.summarize_checks(checks)
        task_id = run.task_id if run is not None else execution_id
        self._append_ci_event(
            execution_id,
            summary,
            repo=binding.repo,
            ref=binding.branch_name,
            task_id=task_id,
        )
        return summary

    def _append_ci_event(
        self,
        execution_id: str,
        summary: dict[str, Any],
        *,
        repo: str,
        ref: str,
        task_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        run = self.ledger.read_run(execution_id)
        resolved_task_id = task_id or (run.task_id if run is not None else execution_id)
        payload = {
            "transition": "ci_observed",
            "repo": repo,
            "ref": ref,
            "state": summary["state"],
            "summary": summary,
            "observation_key": _observation_key(repo=repo, ref=ref, summary=summary, reason=reason),
        }
        if reason is not None:
            payload["reason"] = reason
        if _has_observation(self.ledger, execution_id, payload["observation_key"]):
            return
        self.ledger.append_event(
            _ledger_event(
                self.ledger,
                execution_id=execution_id,
                task_id=resolved_task_id,
                payload=payload,
                created_at=self._clock(),
            )
        )


def _extract_check_runs(checks: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = checks.get("check_runs", checks.get("runs", checks.get("statuses", [])))
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _check_state(check: dict[str, Any]) -> str:
    status = str(check.get("status", "")).lower()
    conclusion = check.get("conclusion")
    normalized_conclusion = str(conclusion).lower() if conclusion is not None else ""
    if status == "completed" and normalized_conclusion in PASSING_CONCLUSIONS:
        return "passed"
    if status == "completed" and normalized_conclusion in FAILING_CONCLUSIONS:
        return "failed"
    return "pending"


def _ledger_event(
    ledger: ExecutionLedger,
    *,
    execution_id: str,
    task_id: str,
    payload: dict[str, Any],
    created_at: str,
) -> LedgerEvent:
    event_number = len(ledger.read_events(execution_id)) + 1
    return LedgerEvent(
        event_id=f"{execution_id}:{event_number:04d}:status_changed:ci_observed",
        execution_id=execution_id,
        task_id=task_id,
        event_type=LedgerEventType.STATUS_CHANGED,
        actor=ExecutionActor.GITHUB,
        payload=payload,
        created_at=created_at,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _observation_key(
    *,
    repo: str,
    ref: str,
    summary: dict[str, Any],
    reason: str | None,
) -> str:
    payload = {
        "repo": repo,
        "ref": ref,
        "state": summary.get("state"),
        "total": summary.get("total"),
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "pending": summary.get("pending"),
        "raw": summary.get("raw"),
        "reason": reason,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _has_observation(ledger: ExecutionLedger, execution_id: str, observation_key: str) -> bool:
    return any(
        event.payload.get("transition") == "ci_observed"
        and event.payload.get("observation_key") == observation_key
        for event in ledger.read_events(execution_id)
    )
