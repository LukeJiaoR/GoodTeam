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
    runtime_policy = dict(review.get("runtime_policy", {}))
    broker_status = dict(review.get("github_broker_status", {}))
    lines = [
        f"Execution: {review.get('execution_id', '')}",
        f"Status: {review.get('status', '')}",
        f"Issue: {review.get('source_issue', '') or 'n/a'}",
        f"PR: {pr.get('ref', '') or 'n/a'}",
        f"Branch: {pr.get('branch_name', '') or 'n/a'}",
        f"Commit: {pr.get('commit_sha', '') or 'n/a'}",
        f"Task contract: {_contract_label(contract)}",
        f"Contract summary: {contract.get('summary', '') or 'n/a'}",
        f"Runtime policy: {_runtime_policy_label(runtime_policy)}",
        f"Worker backend: {_runtime_worker_backend_label(runtime_policy)}",
        f"External invocation: {_runtime_external_label(runtime_policy)}",
        f"Runtime sandbox: {_runtime_sandbox_label(runtime_policy)}",
        f"Broker status: {_broker_status_label(broker_status)}",
        f"Broker PR: {_broker_pr_label(broker_status)}",
        f"Broker CI: {_broker_ci_label(broker_status)}",
        f"Broker review: {_broker_review_label(broker_status)}",
        f"Merge readiness: {_broker_merge_readiness_label(broker_status)}",
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


def _runtime_policy_label(policy: dict[str, Any]) -> str:
    if policy.get("available"):
        return str(policy.get("schema_version", ""))
    warning = policy.get("fallback_warning")
    if isinstance(warning, dict):
        return f"missing ({warning.get('code', 'runtime_policy_missing')})"
    return "missing"


def _runtime_worker_backend_label(policy: dict[str, Any]) -> str:
    backend = policy.get("worker_backend")
    if not isinstance(backend, dict) or not backend:
        return "n/a"
    mode = str(backend.get("mode", "") or "n/a")
    backend_class = str(backend.get("class", "") or "n/a")
    return f"{mode} ({backend_class})"


def _runtime_external_label(policy: dict[str, Any]) -> str:
    external = policy.get("external_invocation")
    if not isinstance(external, dict) or not external:
        return "n/a"
    if external.get("blocked"):
        return f"blocked ({external.get('reason', '')})"
    if external.get("requested"):
        return "allowed" if external.get("allowed") else "requested"
    return "not requested"


def _runtime_sandbox_label(policy: dict[str, Any]) -> str:
    sandbox = policy.get("sandbox")
    if not isinstance(sandbox, dict) or not sandbox:
        return "n/a"
    enabled = "enabled" if sandbox.get("enabled") else "disabled"
    return f"{sandbox.get('mode', 'n/a')} ({enabled})"


def _broker_status_label(status: dict[str, Any]) -> str:
    if status.get("available"):
        return str(status.get("schema_version", ""))
    warning = status.get("fallback_warning")
    if isinstance(warning, dict):
        return f"missing ({warning.get('code', 'broker_status_missing')})"
    return "missing"


def _broker_pr_label(status: dict[str, Any]) -> str:
    pr = status.get("pr")
    if not isinstance(pr, dict) or not pr:
        return "n/a"
    repo = str(pr.get("repo", "") or "n/a")
    number = pr.get("number")
    state = str(pr.get("state", "") or "unknown")
    branch = str(pr.get("branch", "") or "n/a")
    mergeable = str(pr.get("mergeable", "") or "unknown")
    ref = f"{repo}#{number}" if number is not None else repo
    return f"{ref} {state}, {branch}, mergeable={mergeable}"


def _broker_ci_label(status: dict[str, Any]) -> str:
    ci = status.get("ci")
    if not isinstance(ci, dict) or not ci:
        return "n/a"
    observed = str(ci.get("observed_state", "") or "missing")
    required = str(ci.get("required_state", "") or "missing")
    observations = ci.get("observations")
    count = len(observations) if isinstance(observations, list) else 0
    return f"{observed} ({required}, {count} observations)"


def _broker_review_label(status: dict[str, Any]) -> str:
    review = status.get("review")
    if not isinstance(review, dict) or not review:
        return "n/a"
    state = str(review.get("state", "") or "missing")
    blockers = int(review.get("blocker_count", 0) or 0)
    approvals = int(review.get("approval_count", 0) or 0)
    return f"{state}, approvals={approvals}, blockers={blockers}"


def _broker_merge_readiness_label(status: dict[str, Any]) -> str:
    readiness = status.get("merge_readiness")
    if not isinstance(readiness, dict) or not readiness:
        return "n/a"
    state = str(readiness.get("state", "") or "unknown")
    reason = str(readiness.get("reason", "") or "")
    return f"{state} ({reason})" if reason else state


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
