from __future__ import annotations

import json
from typing import Any

from goodteam_os.execution.models import LedgerEvent


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


def json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.loads(json.dumps(value))
