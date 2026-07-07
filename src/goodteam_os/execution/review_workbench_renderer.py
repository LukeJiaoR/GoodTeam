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
    contract = dict(review.get("task_contract", {}))
    lines = [
        f"Execution: {review.get('execution_id', '')}",
        f"Status: {review.get('status', '')}",
        f"Issue: {review.get('source_issue', '') or 'n/a'}",
        f"PR: {pr.get('ref', '') or 'n/a'}",
        f"Branch: {pr.get('branch_name', '') or 'n/a'}",
        f"Commit: {pr.get('commit_sha', '') or 'n/a'}",
        f"Task contract: {_contract_label(contract)}",
        f"Contract summary: {contract.get('summary', '') or 'n/a'}",
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
        "Expected artifacts:",
        *_render_contract_artifacts(contract),
        "",
        "Path scope:",
        *_render_path_scope(contract),
        "",
        "Acceptance criteria:",
        *_render_contract_list(contract.get("acceptance_criteria")),
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


def _contract_label(contract: dict[str, Any]) -> str:
    if contract.get("available"):
        return str(contract.get("schema_version", ""))
    warning = contract.get("fallback_warning")
    if isinstance(warning, dict):
        return f"missing ({warning.get('code', 'task_contract_missing')})"
    return "missing"


def _render_contract_artifacts(contract: dict[str, Any]) -> list[str]:
    artifacts = contract.get("expected_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ["- none"]
    lines = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name", "") or artifact.get("path", "")
        kind = artifact.get("kind", "")
        path = artifact.get("path", "")
        lines.append(f"- {name} ({kind}) {path}".rstrip())
    return lines or ["- none"]


def _render_path_scope(contract: dict[str, Any]) -> list[str]:
    path_scope = contract.get("path_scope")
    if not isinstance(path_scope, dict) or not path_scope:
        return ["- none"]
    lines = []
    for key in ("focus_paths", "allowed_paths", "excluded_paths"):
        values = path_scope.get(key)
        if isinstance(values, list) and values:
            lines.append(f"- {key}: {', '.join(str(value) for value in values)}")
    return lines or ["- none"]


def _render_contract_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["- none"]
    return [f"- {item}" for item in value]


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
