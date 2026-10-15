"""Read-only, identity-bound attention across the existing project work surfaces.

This is a presentation of durable state, not an inbox or an approval authority.
Only current workflow attention, preparation steps and clarification drafts are
covered. Counts describe items, not blocker strings, unread events or jobs.
"""

from collections.abc import Mapping

SCHEMA_VERSION = "project_attention.v1"
PRIORITY = {"blocked": 0, "approval": 1, "needs_input": 2}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def build_project_attention(*, project_key, readiness, workflow=None, session=None):
    items = []
    mission = (workflow or {}).get("mission") or {}
    attention = (workflow or {}).get("attention_request") or {}
    run_id = _text(mission.get("run_id"))
    if (run_id and mission.get("source") == "workflow"
            and mission.get("project_key") == project_key and not mission.get("read_only")):
        kind = {"blocker": "blocked", "approval": "approval"}.get(attention.get("kind"))
        intent = attention.get("intent_commit") or {}
        if intent.get("status") in {"discussing", "ready_to_confirm", "stale", "ambiguous", "committing"}:
            kind = "needs_input"
        # A running attempt has superseded the old failure, just as in Work.
        if kind == "blocked" and any(actor.get("state") == "working" and actor.get("role") != "orchestrator"
                                     for actor in (workflow or {}).get("execution_actors", [])):
            kind = None
        if kind and _text(attention.get("id")):
            items.append({
                "id": f"workflow:{run_id}:{attention['id']}",
                "kind": kind,
                "source": "workflow",
                "run_id": run_id,
                "target_id": _text(attention.get("target_id")) or run_id,
                "title": _text(mission.get("title")) or run_id,
                "reason": _text(attention.get("reason")),
            })
    execution = readiness.get("execution")
    if execution is None:
        execution = {}
    if not isinstance(execution, Mapping):
        raise ValueError("Preparation execution is invalid")
    execution_id = _text(execution.get("id")) or "unstarted"
    steps = readiness.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("Preparation steps are invalid")
    for step in steps:
        if not isinstance(step, Mapping):
            raise ValueError("Preparation step is invalid")
        step_id = _text(step.get("id"))
        if not step_id or not isinstance(step.get("status"), str):
            raise ValueError("Preparation step identity or status is invalid")
        kind = {"failed": "blocked", "needs_input": "needs_input"}.get(step["status"])
        if not kind:
            continue
        error = step.get("error") or {}
        reason = _text(error.get("message")) if isinstance(error, Mapping) else _text(error)
        items.append({
            "id": f"readiness:{execution_id}:{step_id}",
            "kind": kind,
            "source": "readiness",
            "run_id": "",
            "target_id": step_id,
            "title": step_id,
            "reason": reason or _text(step.get("status")),
        })
    proposal = (session or {}).get("proposed_task")
    if proposal is None:
        proposal = {}
    if not isinstance(proposal, Mapping):
        raise ValueError("Task proposal is invalid")
    proposal_id = _text(proposal.get("id"))
    if (proposal_id and proposal.get("needs_clarification") is True
            and not proposal.get("run_id") and proposal.get("status") != "cancelled"):
        items.append({
            "id": f"assistant:proposal:{proposal_id}",
            "kind": "needs_input",
            "source": "assistant",
            "run_id": "",
            "target_id": proposal_id,
            "title": _text(proposal.get("exact_request")) or _text(proposal.get("summary")) or proposal_id,
            "reason": "clarification_required",
        })
    # Duplicate records must not inflate counts or generate duplicate UI keys.
    items = list({item["id"]: item for item in items}.values())
    items.sort(key=lambda item: (PRIORITY[item["kind"]], item["id"]))
    counts = {kind: sum(item["kind"] == kind for item in items) for kind in PRIORITY}
    return {"schema_version": SCHEMA_VERSION, "items": items, "counts": {**counts, "total": len(items)}}
