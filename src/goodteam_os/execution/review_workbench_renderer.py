from __future__ import annotations

from typing import Any


def render_review_workbench(review: dict[str, Any]) -> str:
    evidence = dict(review.get("evidence", {}))
    pr = dict(review.get("pr", {}))
    diff = dict(review.get("diff", {}))
    ci = dict(evidence.get("ci_summary", {}))
    review_summary = dict(evidence.get("review_summary", {}))
    integrity = dict(review.get("memory_provenance_integrity", {}))
    decision = dict(review.get("reviewer_decision", {}))
    lines = [
        f"Execution: {review.get('execution_id', '')}",
        f"Status: {review.get('status', '')}",
        f"Issue: {review.get('source_issue', '') or 'n/a'}",
        f"PR: {pr.get('ref', '') or 'n/a'}",
        f"Branch: {pr.get('branch_name', '') or 'n/a'}",
        f"Commit: {pr.get('commit_sha', '') or 'n/a'}",
        f"Diff: {_diff_label(diff)}",
        f"CI: {ci.get('state', 'unavailable')}",
        (
            "Review observations: "
            f"{review_summary.get('reviews', 0)} reviews, "
            f"{review_summary.get('comments', 0)} comments"
        ),
        f"Memory provenance: {_memory_integrity_label(integrity)}",
        f"Decision: {_decision_label(decision)}",
        "",
        "Changed files:",
        *_render_changed_files(diff),
        "",
        "Warnings:",
        *_render_warnings(list(review.get("warnings", []))),
        "",
        "Timeline:",
        *_render_timeline(list(evidence.get("ledger_events", []))),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_warnings(warnings: list[Any]) -> list[str]:
    if not warnings:
        return ["- none"]
    return [f"- {item.get('code', '')}: {item.get('message', '')}" for item in warnings]


def _render_timeline(events: list[Any]) -> list[str]:
    if not events:
        return ["- none"]
    lines = []
    for event in events:
        payload = event.get("payload", {}) if isinstance(event, dict) else {}
        transition = payload.get("transition", event.get("event_type", ""))
        lines.append(f"- {event.get('created_at', '')}: {transition}")
    return lines


def _diff_label(diff: dict[str, Any]) -> str:
    if not diff.get("available"):
        return f"unavailable ({diff.get('reason', 'diff_artifact_missing')})"
    return str(diff.get("summary", ""))


def _render_changed_files(diff: dict[str, Any]) -> list[str]:
    if not diff.get("available"):
        return ["- none"]
    files = diff.get("changed_files")
    if not isinstance(files, list) or not files:
        return ["- none"]
    return [_changed_file_line(file) for file in files if isinstance(file, dict)]


def _changed_file_line(file: dict[str, Any]) -> str:
    return (
        f"- {file.get('path', '')} {file.get('status', '')} "
        f"+{file.get('additions', 0)} -{file.get('deletions', 0)}"
    )


def _memory_integrity_label(integrity: dict[str, Any]) -> str:
    if integrity.get("complete") is True:
        return "complete"
    if integrity.get("pending") is True:
        return f"pending ({integrity.get('pending_reason', '')})"
    return "incomplete"


def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("available"):
        return "none"
    return f"{decision.get('decision', '')} by {decision.get('reviewer', '')}"
