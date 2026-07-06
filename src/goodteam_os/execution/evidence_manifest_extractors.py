from __future__ import annotations

import json
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.memory import RoleMemoryStore


PROVENANCE_TRANSITION = "provenance_written"
PROVENANCE_ROLE = "orchestrator"
MEMORY_SOURCE_SUFFIX = "final_provenance"


def ci_observations(events: list[LedgerEvent]) -> list[dict[str, Any]]:
    observations = []
    for event in events:
        if event.payload.get("transition") != "ci_observed":
            continue
        observation = {
            "event_id": event.event_id,
            "repo": str(event.payload.get("repo", "")),
            "ref": str(event.payload.get("ref", "")),
            "state": str(event.payload.get("state", "")),
            "summary": json_ready(event.payload.get("summary", {})),
        }
        if "reason" in event.payload:
            observation["reason"] = str(event.payload.get("reason", ""))
        observations.append(observation)
    return observations


def ci_summary(events: list[LedgerEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.payload.get("transition") == "ci_observed":
            summary = event.payload.get("summary")
            return dict(summary) if isinstance(summary, dict) else {}
    return {}


def review_observations(events: list[LedgerEvent]) -> list[dict[str, Any]]:
    observations = []
    for event in events:
        transition = event.payload.get("transition")
        if transition not in {"review_recorded", "pr_comment_recorded"}:
            continue
        observations.append(
            {
                "event_id": event.event_id,
                "kind": "comment" if transition == "pr_comment_recorded" else "review",
                "github_id": str(event.payload.get("github_id", "")),
                "state": str(event.payload.get("review_state", "")),
                "actor_login": str(event.payload.get("actor_login", "")),
                "submitted_at": str(event.payload.get("submitted_at", "")),
                "body_excerpt": str(event.payload.get("body_excerpt", "")),
            }
        )
    return observations


def review_summary(events: list[LedgerEvent]) -> dict[str, Any]:
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


def merge_summary(events: list[LedgerEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.payload.get("transition") == "merge_completed":
            return {
                "merged": True,
                "merged_at": event.payload.get("merged_at", ""),
                "merge_commit_sha": event.payload.get("merge_commit_sha", ""),
                "merged_by": event.payload.get("merged_by", ""),
            }
    return {"merged": False, "merged_at": "", "merge_commit_sha": "", "merged_by": ""}


def memory_provenance_refs(
    memory_store: RoleMemoryStore,
    events: list[LedgerEvent],
    execution_id: str,
) -> list[dict[str, Any]]:
    source = memory_source(execution_id)
    refs = _ledger_closure_refs(events, source)
    for event in memory_store._read_events(PROVENANCE_ROLE):
        if event.get("source") != source:
            continue
        refs.append(
            {
                "kind": "memory_event",
                "memory_event_id": memory_store._event_id(event),
                "source": source,
                "role": str(event.get("role_key", "")),
                "horizon": str(event.get("horizon", "")),
                "timestamp": str(event.get("timestamp", "")),
            }
        )
    return refs


def memory_source(execution_id: str) -> str:
    return f"execution:{execution_id}:{MEMORY_SOURCE_SUFFIX}"


def json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.loads(json.dumps(value))


def _ledger_closure_refs(events: list[LedgerEvent], source: str) -> list[dict[str, Any]]:
    refs = []
    for event in events:
        if event.payload.get("transition") != PROVENANCE_TRANSITION:
            continue
        refs.append(
            {
                "kind": "ledger_closure",
                "ledger_event_id": event.event_id,
                "memory_event_id": str(event.payload.get("memory_event_id", "")),
                "source": source,
                "evidence_hash": str(event.payload.get("evidence_hash", "")),
                "created_at": event.created_at,
            }
        )
    return refs
