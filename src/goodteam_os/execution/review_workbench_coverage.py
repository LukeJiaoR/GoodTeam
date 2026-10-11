"""Read-only presentation of captured criteria and existing check receipts."""
from __future__ import annotations

from datetime import datetime
import re


def _bound_check(validation: dict, digest: str) -> bool:
    receipt = validation.get("receipt") or {}
    candidate = validation.get("candidate") or {}
    return (validation.get("available") is True
            and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
            and bool(receipt.get("receipt_id")) and receipt.get("snapshot_digest") == digest
            and receipt.get("candidate") == candidate
            and isinstance(candidate.get("head_sha"), str)
            and re.fullmatch(r"[a-f0-9]{40}", candidate["head_sha"]) is not None)


def current_result_details(evidence: dict) -> list[tuple[str, str]]:
    """Present the existing protected observations; never advance workflow state.

    The ledger's legacy decision/status may describe an older candidate. Keep
    those in the historical metadata rather than using them as a handoff.
    """
    captured = evidence.get("task_input") or {}
    validation = evidence.get("candidate_validation") or {}
    loop = evidence.get("correction_loop") or {}
    digest = (captured.get("snapshot") or {}).get("payload_digest", "")
    bound = (captured.get("available") is True
             and captured.get("status") in {"snapshot_committed", "partial"}
             and _bound_check(validation, digest))
    receipt = validation.get("receipt") or {}
    candidate = validation.get("candidate") or {}
    check = validation.get("check_status") if bound else "unknown"
    check = check if check in {"passed", "failed"} else "unknown"
    human = validation.get("review") or {}
    recorded = (human.get("decision") if bound
                and human.get("validation_receipt_id") == receipt.get("receipt_id")
                and human.get("candidate") == candidate else None)
    recorded = recorded if recorded in {"approve", "request_changes", "reject"} else "missing"
    decision = recorded if check != "unknown" else "missing"
    human_label = (f"{recorded} (historical; current applicability unknown)"
                   if recorded != "missing" and check == "unknown" else decision)
    rows = [("Candidate checks", check),
            ("Latest checked head", candidate["head_sha"] if bound else "unavailable"),
            ("Check receipt", receipt["receipt_id"] if bound else "unavailable"),
            ("Human record for this check", human_label),
            ("Acceptance", "unknown (semantic acceptance is not verified)")]
    if bound:
        rows.append(("Task execution mode", validation.get("task_execution_mode", "unknown")))
    reason = validation.get("reason") or ""
    if not bound:
        reason = reason or "no_matching_current_receipt"
    if reason:
        rows.append(("Check limitation", reason))
    phase = loop.get("status", "not_started")
    rows.append(("Correction state", phase))
    if loop.get("available") is True:
        loop_candidate = loop.get("candidate") or {}
        if loop_candidate != candidate:
            rows.append(("Correction candidate", loop_candidate.get("head_sha", "unavailable")))
        # Evidence reads are separate observations. Do not combine a newer loop
        # review state with an older receipt if a concurrent transition occurred.
        mismatch = phase in {"awaiting_review", "awaiting_human_acceptance", "awaiting_fix"} and (
            not bound or loop_candidate != candidate)
        mismatch = mismatch or (phase in {"awaiting_review", "awaiting_human_acceptance"} and check == "unknown")
        mismatch = mismatch or (phase == "awaiting_human_acceptance" and decision != "approve")
        # During an explicit fix, the old check is expected to stop applying.
        # Its still-bound request_changes record remains the reason for recheck.
        mismatch = mismatch or (phase == "awaiting_fix" and recorded != "request_changes")
        mismatch = mismatch or (phase == "awaiting_review" and decision != "missing")
        next_action = ("Refresh and inspect the preserved evidence; the correction and check observations do not match."
                       if mismatch else loop.get("next_action"))
        if not mismatch and phase == "awaiting_human_acceptance" and check == "failed" and decision == "approve":
            next_action = "Inspect the failed check and recorded review; acceptance remains unresolved."
    elif phase != "not_started":
        next_action = "Inspect the preserved correction evidence; do not repeat an uncertain check or Worker dispatch."
    elif not bound or check == "unknown":
        next_action = ("Select a clean candidate and explicitly run its local check; no check is recorded yet."
                       if validation.get("reason") == "validation_not_run" and captured.get("available") is True
                       else "Inspect the original input, candidate and check evidence before continuing; no current result is established.")
    else:
        next_action = {
            "missing": "Review this exact candidate and its check receipt.",
            "request_changes": "Address the recorded feedback, then explicitly recheck a new candidate against the original task.",
            "reject": "Inspect the rejected candidate and feedback with the reviewer before continuing.",
            "approve": "A person must decide acceptance against the original task; the recorded approval is not semantic verification.",
        }[decision]
        if check == "failed" and decision == "approve":
            next_action = "Inspect the failed check and recorded review; acceptance remains unresolved."
    if loop.get("reason"):
        rows.append(("Correction limitation", loop["reason"]))
    rows.append(("Next action", next_action or "Inspect the preserved evidence before continuing."))
    return rows


def check_metadata_lines(validation: dict) -> list[str]:
    receipt = validation.get("receipt") or {}
    if not validation.get("available") or not receipt:
        return []
    observed = receipt.get("observation") or {}
    # Never render argv, environment, raw output, or an executable's parent path.
    name = str(validation.get("command_name", "")).replace("\\", "/").rsplit("/", 1)[-1]
    name = name if re.fullmatch(r"[A-Za-z0-9_.+-]{1,96}", name) else "unavailable"
    code = observed.get("exit_code")
    code = str(code) if type(code) is int else "unknown"
    duration = "unknown"
    try:
        start = datetime.fromisoformat(observed["started_at"])
        end = datetime.fromisoformat(observed["finished_at"])
        seconds = (end - start).total_seconds()
        if start.tzinfo is not None and end.tzinfo is not None and seconds >= 0:
            duration = f"{seconds:.3f} seconds (host wall clock)"
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return [f"- Validation receipt: {receipt.get('receipt_id', 'unavailable')}",
            f"- Check command: {name}", f"- Process exit code: {code}", f"- Check duration: {duration}"]


def acceptance_coverage_lines(evidence: dict) -> list[str]:
    captured = evidence.get("task_input") or {}
    if not captured.get("available") or captured.get("status") not in {"snapshot_committed", "partial"}:
        return ["- Original captured criteria unavailable; no criteria are inferred from the project ledger.",
                f"- Reason: {captured.get('reason') or 'task_input_not_captured'}"]
    snapshot = captured.get("snapshot") or {}
    digest = snapshot.get("payload_digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return ["- Original captured criteria unavailable: invalid snapshot reference."]
    lines = [f"- Original input digest: {digest}",
             "- Source: captured external task input; capture does not establish user approval.",
             "- Command-to-criterion mapping: operator supplied; checks do not verify semantic acceptance."]
    if captured.get("reason"):
        lines.append(f"- Input history incomplete: {captured['reason']}")
    validation = evidence.get("candidate_validation") or {}
    receipt = validation.get("receipt") or {}
    receipt_id = receipt.get("receipt_id")
    bound = _bound_check(validation, digest)
    checks = {row.get("index"): row for row in validation.get("criteria", []) if isinstance(row, dict)}
    coverage = captured.get("acceptance_coverage") or []
    if not coverage:
        lines.append("- No acceptance criteria were captured.")
    for index, row in enumerate(coverage):
        criterion_id = f"criterion:{digest}:{index}"
        if row.get("criterion_id") != criterion_id:
            lines.append(f"- Criterion {index + 1}: unavailable (captured identity mismatch); acceptance unknown")
            continue
        selected = checks.get(index)
        linked = bound and selected is not None and receipt_id in row.get("evidence", [])
        if linked:
            state = {"passed": "Check passed", "failed": "Check failed"}.get(
                selected.get("check_status"), "Check result unknown")
        elif not selected and not row.get("evidence") and (bound or validation.get("reason") == "validation_not_run"):
            state = "Not checked (no current check mapped)"
        else:
            state = "Check result unknown (no matching current receipt)"
        lines.extend([f"- Criterion {index + 1} (CLI index {index}): {row.get('text', '')}",
                      f"  Identity: {criterion_id}", f"  {state}; acceptance unknown"])
        if linked:
            lines.extend([f"  Receipt: {receipt_id}", f"  Head: {validation['candidate']['head_sha']}"])
    return lines
