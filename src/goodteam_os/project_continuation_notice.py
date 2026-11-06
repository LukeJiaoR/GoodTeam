"""Presentation of existing continuation state; never an execution authority."""
from hashlib import sha256
import json


_COPY = {
    "review_required": "The batch produced a candidate and needs independent review and delivery confirmation.",
    "quality_required": "The batch needs its configured checks and independent review before delivery.",
    "delivery_required": "Candidate checks and independent review passed. Delivery still needs your confirmation.",
    "blocked": "Automatic project iteration is blocked and needs your attention.",
    "exhausted": "Automatic project iteration reached an approved limit and has stopped.",
    "no_followup": "Manager proposed no follow-up within the approved scope. This does not mean the project is complete.",
    "completed": "This authorized project iteration has ended. Review its recorded delivery evidence.",
    "stopped": "Automatic project iteration was stopped. Its existing evidence and results are preserved.",
}
_NEXT = {
    "review_required": "Inspect the candidate, independently review it, then use the existing delivery confirmation after landing it.",
    "quality_required": "Open the batch checks and review card and follow its next step for this same input.",
    "delivery_required": "Inspect the retained evidence, land this exact candidate through the authorized Git workflow, then ask to confirm delivery in this conversation.",
    "blocked": "Inspect the retained result and resolve the blocker through the existing batch recovery or project configuration controls.",
    "exhausted": "Review the results and authorize a new bounded continuation if more work is needed.",
    "no_followup": "Discuss the rationale and provide missing goals or context in the project conversation. Select initial tasks and authorize again to execute more work.",
    "completed": "Review the results; select tasks and authorize a new continuation if more work is needed.",
    "stopped": "Review any pending candidate or ask about the results here. Further execution needs a new bounded authorization.",
}


def project_continuation_notice(session):
    """Derive the current handoff from the original grant and its own batch."""
    value = (session or {}).get("project_continuation") or {}
    identifier, status = value.get("id"), value.get("status")
    if not identifier or status not in {"active", "blocked", "exhausted", "completed", "stopped"}:
        return None
    pending = value.get("pending_batch") or {}
    run_id = pending.get("run_id", "")
    batches = session.get("project_task_batches", {})
    batch = batches.get(run_id) or {}
    own_batch = batch.get("expected_continuation_id") == identifier
    quality = batch.get("quality") or {}
    phase, reason = status, value.get("reason", "")
    if status == "active" and own_batch and batch.get("status") == "review":
        phase, reason = "review_required", "independent_review_and_delivery_required"
        if quality.get("required"):
            accepted = (quality.get("status") == "reviewed" and quality.get("check_status") == "passed"
                        and quality.get("review_status") == "machine_recorded"
                        and quality.get("acceptance_status") == "accepted_by_review_policy")
            phase = "delivery_required" if accepted else "quality_required"
            reason = quality.get("reason") or phase
    elif status == "active":
        return None
    if reason == "discovery_no_followup":
        phase = "no_followup"
    receipts, delivered = [], []
    for row in sorted(batches.values(), key=lambda row: row.get("created_at", "")):
        receipt = row.get("delivery_receipt") or {}
        if row.get("expected_continuation_id") == identifier and row.get("status") == "delivered" and receipt.get("receipt_id"):
            receipts.append(receipt["receipt_id"])
            delivered.extend(row.get("task_ids", []))
    identity = json.dumps([identifier, phase, run_id, reason, quality.get("input_digest", "")])
    return {
        "id": f"continuation:{identifier}:" + sha256(identity.encode()).hexdigest()[:24],
        "continuation_id": identifier, "phase": phase, "reason": reason,
        "kind": "blocked" if phase == "blocked" else "" if phase == "completed" else "needs_input",
        "run_id": run_id, "task_ids": list(pending.get("task_ids") or value.get("task_ids", [])),
        "delivered_task_ids": list(dict.fromkeys(delivered)), "delivery_receipt_ids": receipts,
        "batches_started": value.get("batches_started", 0), "max_batches": value.get("max_batches", 0),
        "manager_calls": value.get("manager_calls", 0), "max_manager_calls": value.get("max_manager_calls", 0),
        "rationale": (value.get("discovery_last") or {}).get("rationale", "") if phase == "no_followup" else "",
        "next_action": quality.get("next_action") if phase == "quality_required" else "",
    }


def notice_content(notice):
    phase = notice["phase"]
    lines = [_COPY[phase], "Reason: " + notice["reason"],
             f"Batch reservations: {notice['batches_started']}/{notice['max_batches']}; "
             f"Manager reservations: {notice['manager_calls']}/{notice['max_manager_calls']}."]
    if notice["delivered_task_ids"]:
        lines.append("Recorded deliveries: " + ", ".join(notice["delivered_task_ids"]))
        lines.append("Delivery receipts: " + ", ".join(notice["delivery_receipt_ids"]))
    if notice["rationale"]:
        lines.append("Manager rationale: " + notice["rationale"])
    lines.append("Next step: " + (notice["next_action"] or _NEXT[phase]))
    return "\n\n".join(lines)


def append_continuation_notice(session, previous, *, created_at):
    """Persist new handoffs with the state change, once, under the session lock.

    Comparing the prior projection avoids rewriting historical stopped sessions
    on unrelated writes. The stable timeline identity also deduplicates replay.
    """
    notice = project_continuation_notice(session)
    if not notice or previous and previous["id"] == notice["id"]:
        return
    if any(item.get("message_id") == notice["id"] for item in session["timeline"]):
        return
    session["timeline"].append({
        "message_id": notice["id"], "actor": "task_assistant",
        "event": "project_continuation_handoff", "content": notice_content(notice),
        "run_id": "", "details": notice, "created_at": created_at,
    })
