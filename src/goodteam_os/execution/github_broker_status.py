from __future__ import annotations

import json
from typing import Any


GITHUB_BROKER_STATUS_SCHEMA_VERSION = "github_broker_status.v1"
BROKER_TRANSITIONS = {
    "pr_opened",
    "ci_observed",
    "review_recorded",
    "pr_comment_recorded",
    "merge_completed",
}
APPROVAL_STATES = {"APPROVED", "APPROVE", "APPROVAL"}
REQUEST_CHANGES_STATES = {"REQUEST_CHANGES", "CHANGES_REQUESTED", "REQUESTED_CHANGES"}
COMMENT_STATES = {"COMMENTED", "COMMENT", ""}
PASSING_CI_STATES = {"success"}
BLOCKING_CI_STATES = {"failure"}


def github_broker_status_manifest(run: Any, events: list[Any]) -> dict[str, Any]:
    broker_events = [_event for _event in events if _transition(_event) in BROKER_TRANSITIONS]
    if not broker_events:
        return github_broker_status_fallback_manifest()

    pr = _pr_status(run, broker_events)
    ci = _ci_status(broker_events)
    review = _review_status(broker_events)
    merge_readiness = _merge_readiness(pr=pr, ci=ci, review=review)
    return _json_ready(
        {
            "available": True,
            "schema_version": GITHUB_BROKER_STATUS_SCHEMA_VERSION,
            "pr": pr,
            "ci": ci,
            "review": review,
            "merge_readiness": merge_readiness,
            "sources": _sources(broker_events),
            "warnings": _status_warnings(pr=pr, ci=ci, review=review, merge_readiness=merge_readiness),
            "fallback_warning": {},
        }
    )


def github_broker_status_fallback_manifest() -> dict[str, Any]:
    return {
        "available": False,
        "schema_version": "",
        "pr": {},
        "ci": {},
        "review": {},
        "merge_readiness": {},
        "sources": [],
        "warnings": [],
        "fallback_warning": {
            "code": "broker_status_missing",
            "message": "Legacy execution has no GitHub/CI broker status evidence.",
        },
    }


def _pr_status(run: Any, events: list[Any]) -> dict[str, Any]:
    pr_opened = _latest_event(events, "pr_opened")
    merge_completed = _latest_event(events, "merge_completed")
    pr_payload = _payload(pr_opened)
    merge_payload = _payload(merge_completed)
    repo = _first_non_empty(
        merge_payload.get("repo"),
        pr_payload.get("repo"),
    )
    branch = _first_non_empty(
        merge_payload.get("branch_name"),
        pr_payload.get("branch_name"),
        getattr(run, "branch_name", ""),
    )
    number = _int_or_none(
        _first_non_empty(
            merge_payload.get("pr_number"),
            pr_payload.get("pr_number"),
            getattr(run, "pr_number", None),
        )
    )
    merged = bool(merge_payload)
    state = "merged" if merged else _pr_state(pr_payload)
    return {
        "repo": repo,
        "branch": branch,
        "number": number,
        "commit_sha": _first_non_empty(
            pr_payload.get("commit_sha"),
            merge_payload.get("head_sha"),
        ),
        "state": state,
        "draft": _bool_or_none(pr_payload.get("draft")),
        "open": state == "open",
        "closed": state in {"closed", "merged"},
        "merged": merged,
        "mergeable": _mergeability(pr_payload),
        "merge_commit_sha": _first_non_empty(merge_payload.get("merge_commit_sha")),
        "merged_at": _first_non_empty(merge_payload.get("merged_at")),
        "merged_by": _first_non_empty(merge_payload.get("merged_by")),
    }


def _ci_status(events: list[Any]) -> dict[str, Any]:
    observations = [_ci_observation(event) for event in events if _transition(event) == "ci_observed"]
    latest = observations[-1] if observations else {}
    observed_state = str(latest.get("status", "missing") or "missing")
    return {
        "provider": "github",
        "required_state": "observed" if observations else "missing",
        "observed_state": observed_state,
        "conclusion": _ci_conclusion(observed_state),
        "observations": observations,
    }


def _ci_observation(event: Any) -> dict[str, Any]:
    payload = _payload(event)
    summary = payload.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    raw = summary.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "event_id": str(getattr(event, "event_id", "")),
        "provider": "github",
        "repo": _first_non_empty(payload.get("repo")),
        "ref": _first_non_empty(payload.get("ref")),
        "status": _first_non_empty(payload.get("state"), summary.get("state")),
        "required": True,
        "observed": True,
        "run_ids": _run_ids(raw),
        "check_runs": _check_runs(raw),
        "observed_at": str(getattr(event, "created_at", "")),
        "reason": _first_non_empty(payload.get("reason")),
    }


def _review_status(events: list[Any]) -> dict[str, Any]:
    observations = [
        _review_observation(event)
        for event in events
        if _transition(event) in {"review_recorded", "pr_comment_recorded"}
    ]
    review_states = [item["state"] for item in observations if item["kind"] == "review"]
    approval_count = sum(1 for state in review_states if state in APPROVAL_STATES)
    request_changes_count = sum(1 for state in review_states if state in REQUEST_CHANGES_STATES)
    comment_count = sum(1 for item in observations if item["kind"] == "comment")
    unknown_count = sum(1 for state in review_states if _review_state_category(state) == "unknown")
    return {
        "state": _aggregate_review_state(
            approval_count=approval_count,
            request_changes_count=request_changes_count,
            comment_count=comment_count,
            unknown_count=unknown_count,
        ),
        "approval_count": approval_count,
        "request_changes_count": request_changes_count,
        "comment_count": comment_count,
        "blocker_count": request_changes_count,
        "stale": None,
        "unknown_state_count": unknown_count,
        "observations": observations,
    }


def _review_observation(event: Any) -> dict[str, Any]:
    payload = _payload(event)
    transition = _transition(event)
    state = _first_non_empty(payload.get("review_state"))
    return {
        "event_id": str(getattr(event, "event_id", "")),
        "kind": "comment" if transition == "pr_comment_recorded" else "review",
        "github_id": _first_non_empty(payload.get("github_id")),
        "state": state,
        "state_category": _review_state_category(state),
        "actor_login": _first_non_empty(payload.get("actor_login")),
        "submitted_at": _first_non_empty(payload.get("submitted_at")),
        "body_excerpt": _first_non_empty(payload.get("body_excerpt")),
    }


def _merge_readiness(
    *,
    pr: dict[str, Any],
    ci: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    if pr.get("merged"):
        state = "ready"
        reason = "merged"
    elif not pr.get("number"):
        state = "blocked"
        reason = "pr_missing"
    elif ci.get("observed_state") in BLOCKING_CI_STATES:
        state = "blocked"
        reason = "ci_failed"
    elif review.get("blocker_count", 0) > 0:
        state = "blocked"
        reason = "review_blockers"
    elif ci.get("observed_state") not in PASSING_CI_STATES:
        state = "unknown"
        reason = "ci_not_success"
    elif review.get("state") != "approved":
        state = "unknown"
        reason = "review_not_approved"
    elif pr.get("mergeable") not in {"clean", "mergeable", True}:
        state = "unknown"
        reason = "mergeability_unknown"
    else:
        state = "ready"
        reason = "ci_and_review_ready"
    return {
        "state": state,
        "reason": reason,
        "ci_state": str(ci.get("observed_state", "missing")),
        "review_state": str(review.get("state", "missing")),
        "pr_state": str(pr.get("state", "unknown")),
        "blocker_count": int(review.get("blocker_count", 0)),
    }


def _status_warnings(
    *,
    pr: dict[str, Any],
    ci: dict[str, Any],
    review: dict[str, Any],
    merge_readiness: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not pr.get("number"):
        warnings.append({"code": "broker_pr_missing", "message": "No typed PR number was observed."})
    if ci.get("required_state") == "missing":
        warnings.append({"code": "broker_ci_missing", "message": "No typed CI observation was observed."})
    if review.get("state") in {"missing", "unknown"}:
        warnings.append(
            {
                "code": "broker_review_unknown",
                "message": "Review state is missing or contains unknown states.",
            }
        )
    if merge_readiness.get("state") != "ready":
        warnings.append(
            {
                "code": "broker_merge_not_ready",
                "message": str(merge_readiness.get("reason", "")),
            }
        )
    return warnings


def _run_ids(raw: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in _raw_check_items(raw):
        value = item.get("run_id") or item.get("workflow_run_id") or item.get("id")
        normalized = _first_non_empty(value)
        if normalized and normalized not in ids:
            ids.append(normalized)
    return ids


def _check_runs(raw: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": _first_non_empty(item.get("id")),
            "name": _first_non_empty(item.get("name"), item.get("context")),
            "status": _first_non_empty(item.get("status"), item.get("state")),
            "conclusion": _first_non_empty(item.get("conclusion")),
            "started_at": _first_non_empty(item.get("started_at")),
            "completed_at": _first_non_empty(item.get("completed_at")),
        }
        for item in _raw_check_items(raw)
    ]


def _raw_check_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = raw.get("check_runs", raw.get("runs", raw.get("statuses", [])))
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _sources(events: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "event_id": str(getattr(event, "event_id", "")),
            "transition": _transition(event),
            "created_at": str(getattr(event, "created_at", "")),
        }
        for event in events
    ]


def _latest_event(events: list[Any], transition: str) -> Any | None:
    for event in reversed(events):
        if _transition(event) == transition:
            return event
    return None


def _transition(event: Any) -> str:
    return str(_payload(event).get("transition", ""))


def _payload(event: Any | None) -> dict[str, Any]:
    if event is None:
        return {}
    payload = getattr(event, "payload", {})
    return payload if isinstance(payload, dict) else {}


def _pr_state(payload: dict[str, Any]) -> str:
    state = _first_non_empty(payload.get("state"))
    if state:
        return state.lower()
    if payload:
        return "open"
    return "unknown"


def _mergeability(payload: dict[str, Any]) -> str:
    for key in ("mergeable_state", "mergeability", "mergeable"):
        value = payload.get(key)
        if isinstance(value, bool):
            return "mergeable" if value else "conflicting"
        normalized = _first_non_empty(value)
        if normalized:
            return normalized.lower()
    return "unknown"


def _ci_conclusion(state: str) -> str:
    if state == "success":
        return "success"
    if state == "failure":
        return "failure"
    if state in {"pending", "unavailable", "missing"}:
        return state
    return "unknown"


def _aggregate_review_state(
    *,
    approval_count: int,
    request_changes_count: int,
    comment_count: int,
    unknown_count: int,
) -> str:
    if request_changes_count:
        return "request_changes"
    if approval_count:
        return "approved"
    if unknown_count:
        return "unknown"
    if comment_count:
        return "commented"
    return "missing"


def _review_state_category(state: str) -> str:
    normalized = state.upper()
    if normalized in APPROVAL_STATES:
        return "approval"
    if normalized in REQUEST_CHANGES_STATES:
        return "request_changes"
    if normalized in COMMENT_STATES:
        return "comment"
    return "unknown"


def _first_non_empty(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value))
