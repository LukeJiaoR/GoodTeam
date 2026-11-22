"""[INPUT]: 执行账本中的任务、运行记录和事件。
[OUTPUT]: 提取来源、正整数 PR 编号、CI、评审、合并和最终状态。
[POS]: 证据清单和记忆归档共用的回放规则。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from itertools import chain
from typing import Any

from goodteam_os.execution.memory_provenance import PROVENANCE_TRANSITION
from goodteam_os.execution.models import ExecutionRun, ExecutionRunStatus, ExecutionTask, LedgerEvent


def source_events(events: list[LedgerEvent]) -> list[LedgerEvent]:
    return [event for event in events if event.payload.get("transition") != PROVENANCE_TRANSITION]


def source_manifest(task: ExecutionTask | None, events: list[LedgerEvent]) -> dict[str, Any]:
    repo = task.repo if task is not None else _repo_from_events(events)
    issue_number = task.issue_number if task is not None else _int_or_none(first_payload_value(events, "issue_number"))
    issue_ref = f"{repo}#{issue_number}" if repo and issue_number is not None else ""
    source_type = task.source_type if task is not None else ("github_issue" if issue_ref else "")
    return {
        "type": source_type,
        "repo": repo,
        "issue_number": issue_number,
        "issue_ref": issue_ref,
        "github_ref": f"github:{issue_ref}" if issue_ref else "",
    }


def pr_identity(
    run: ExecutionRun | None,
    events: list[LedgerEvent],
    *,
    fallback_repo: str,
) -> tuple[str, int | None]:
    repo = _repo_from_events(events) or fallback_repo
    run_number = run.pr_number if run is not None else None
    event_numbers = (event.payload.get("pr_number") for event in reversed(events))
    return repo, first_pr_number(chain((run_number,), event_numbers))


def first_pr_number(values: Iterable[object]) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def pr_ref(repo: str, pr_number: int | None) -> str:
    return f"{repo}#{pr_number}" if repo and pr_number is not None else ""


def first_payload_value(events: list[LedgerEvent], key: str) -> object | None:
    for event in events:
        if key in event.payload:
            return event.payload[key]
    return None


def final_status(
    run: ExecutionRun | None,
    pr_number: int | None,
    *,
    ci_state: object,
    merged: bool,
) -> str:
    if run is None:
        return "incomplete"
    if run.status is ExecutionRunStatus.FAILED:
        return "failed"
    if pr_number is None:
        return "pending"
    if ci_state == "failure":
        return "failed"
    if run.status is ExecutionRunStatus.SUCCEEDED and merged:
        return "completed"
    return "pending"


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


def _repo_from_events(events: list[LedgerEvent]) -> str:
    for event in events:
        repo = event.payload.get("repo")
        if isinstance(repo, str) and repo.strip():
            return repo.strip()
    return ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
