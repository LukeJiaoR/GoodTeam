from __future__ import annotations

from typing import Any


EXECUTION_REPLAY_GRAPH_SCHEMA_VERSION = "execution_replay_graph.v1"
KNOWN_NODE_TYPES = [
    "intake",
    "task_contract",
    "runtime_policy",
    "worker_job",
    "sandbox_created",
    "sandbox_destroyed",
    "artifact",
    "pr",
    "ci",
    "review",
    "broker_status",
    "merge",
    "finalize",
    "memory",
    "reviewer_decision",
    "warning",
]
WARNING_MESSAGES = {
    "task_contract_missing": "Legacy execution has no persisted task_contract.v1 data.",
    "runtime_policy_missing": "Legacy execution has no persisted runtime_policy.v1 preflight data.",
    "broker_status_missing": "Legacy execution has no GitHub/CI broker status evidence.",
    "pr_missing": "Execution has not opened or bound a pull request.",
    "execution_failed": "Execution failed before a complete replay graph could be produced.",
}
BROKER_TRANSITIONS = {
    "pr_opened",
    "ci_observed",
    "review_recorded",
    "pr_comment_recorded",
    "merge_completed",
}


def add_node(
    nodes: list[dict[str, Any]],
    node_id: str,
    node_type: str,
    label: str,
    *,
    status: str,
    event: dict[str, Any] | None,
    data: dict[str, Any],
    warnings: list[dict[str, str]] | None = None,
) -> None:
    if any(node["id"] == node_id for node in nodes):
        return
    event_id = str(event.get("event_id", "")) if event else ""
    nodes.append(
        {
            "id": node_id,
            "type": node_type,
            "label": label,
            "status": status,
            "occurred_at": str(event.get("created_at", "")) if event else "",
            "refs": {
                "ledger_event_ids": [event_id] if event_id else [],
                "transitions": [str(event_payload(event).get("transition", ""))] if event else [],
            },
            "data": data,
            "warnings": list(warnings or []),
        }
    )


def add_warning(
    nodes: list[dict[str, Any]],
    warnings: list[dict[str, str]],
    code: str,
    *,
    message: str | None = None,
    after_node_id: str = "",
) -> None:
    warning = {"code": code, "message": message or WARNING_MESSAGES.get(code, code)}
    if code not in {item["code"] for item in warnings}:
        warnings.append(warning)
    node_id = f"warning:{code}"
    add_node(
        nodes,
        node_id,
        "warning",
        f"Warning: {code}",
        status="warning",
        event=None,
        data={"code": code, "message": warning["message"], "after": after_node_id},
    )


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def event_payload(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def events_for(events: list[dict[str, Any]], transition: str) -> list[dict[str, Any]]:
    return [event for event in events if event_payload(event).get("transition") == transition]


def latest_event(events: list[dict[str, Any]], transition: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event_payload(event).get("transition") == transition:
            return event
    return None


def first_event_payload_value(events: list[dict[str, Any]], key: str) -> str:
    for event in events:
        value = event_payload(event).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def surface_source_event(surface: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    sources = surface.get("sources")
    if isinstance(sources, list):
        source_event_ids = {
            str(item.get("event_id", ""))
            for item in sources
            if isinstance(item, dict) and item.get("event_id")
        }
        for event in events:
            if str(event.get("event_id", "")) in source_event_ids:
                return event
    schema_version = str(surface.get("schema_version", ""))
    if schema_version == "runtime_policy.v1":
        return first_event_with_payload_key(events, "runtime_policy")
    if schema_version == "github_broker_status.v1":
        return latest_broker_event(events)
    return None


def latest_broker_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event_payload(event).get("transition") in BROKER_TRANSITIONS:
            return event
    return None


def first_event_with_payload_key(events: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for event in events:
        if key in event_payload(event):
            return event
    return None


def warning_code(reason: str) -> str:
    if reason.startswith("execution_failed"):
        return "execution_failed"
    return reason
