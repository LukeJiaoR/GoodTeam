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
            and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", candidate["head_sha"]) is not None)


def candidate_review_projection(validation: dict, *, bound: bool = True, snapshot: dict | None = None) -> dict:
    """Separate an observed machine judgment from an optional human record.

    The evidence service validates protected receipts. This presentation layer
    additionally joins only the current check identities; it never grants
    acceptance from an unbound decision or from a successful command alone.
    """
    receipt = validation.get("receipt") or {}
    candidate = validation.get("candidate") or {}
    bound = bound and _bound_check(validation, receipt.get("snapshot_digest", ""))
    check = validation.get("check_status") if bound else "unknown"
    check = check if check in {"passed", "failed"} else "unknown"
    review = validation.get("review") or {}
    matched = (bound and review.get("validation_receipt_id") == receipt.get("receipt_id")
               and review.get("candidate") == candidate)
    target = (review.get("provenance") or {}).get("target") or {}
    machine = (matched and review.get("schema_version") == "candidate_machine_review.v1"
               and review.get("producer") == "automatic_candidate_review_controller"
               and validation.get("review_status") == "machine_recorded"
               and (review.get("snapshot") or {}).get("payload_digest") == receipt.get("snapshot_digest")
               and (snapshot is None or review.get("snapshot") == snapshot)
               and isinstance(review.get("base_sha"), str)
               and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", review["base_sha"]) is not None
               and target.get("base_sha") == review["base_sha"]
               and target.get("head_sha") == candidate.get("head_sha"))
    human = (matched and review.get("schema_version") == "candidate_review_attestation.v1"
             and review.get("producer") in {"explicit_operator_cli", "explicit_operator_workbench"}
             and validation.get("review_status") == "operator_recorded")
    allowed = {"approve", "request_changes", "inconclusive"} if machine else {"approve", "request_changes", "reject"}
    recorded = review.get("decision") if machine or human else None
    recorded = recorded if recorded in allowed else "missing"
    decision = recorded if check != "unknown" else "missing"
    label = (f"{recorded} (historical; current applicability unknown)"
             if recorded != "missing" and check == "unknown" else decision)
    human_record = (validation.get("human_review") or {}) if machine else review if human else {}
    human_decision = human_record.get("decision")
    human_matches = (bound and human_record.get("schema_version") == "candidate_review_attestation.v1"
                     and human_record.get("producer") in {"explicit_operator_cli", "explicit_operator_workbench"}
                     and human_record.get("validation_receipt_id") == receipt.get("receipt_id")
                     and human_record.get("candidate") == candidate
                     and human_decision in {"approve", "request_changes", "reject"})
    human_label = ((f"{human_decision} (historical; current applicability unknown)"
                    if check == "unknown" else human_decision) if human_matches else "missing")
    expected = "accepted_by_review_policy" if machine else "human_recorded" if human else "unknown"
    acceptance = (expected if check == "passed" and decision == "approve"
                  and validation.get("acceptance_status") == expected else "unknown")
    return {"check_status": check, "recorded": recorded, "decision": decision,
            "kind": "machine" if machine else "human" if human else "missing",
            "machine_label": label if machine else "missing", "human_label": human_label,
            "acceptance_status": acceptance,
            "semantic_verification": ("judgment_not_independently_verified"
                                      if recorded != "missing" else "unverified")}


def current_result_details(evidence: dict) -> list[tuple[str, str]]:
    """Present protected observations without advancing workflow or acceptance.

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
    review = candidate_review_projection(validation, bound=bound, snapshot=captured.get("snapshot"))
    check, decision, recorded = review["check_status"], review["decision"], review["recorded"]
    acceptance = review["acceptance_status"]
    rows = [("Candidate checks", check),
            ("Latest checked head", candidate["head_sha"] if bound else "unavailable"),
            ("Check receipt", receipt["receipt_id"] if bound else "unavailable"),
            ("Machine review for this check", review["machine_label"]),
            ("Human record for this check", review["human_label"])]
    if bound:
        rows.append(("Task execution mode", validation.get("task_execution_mode", "unknown")))
    record = validation.get("review") or {}
    if review["kind"] != "missing":
        rows.append(("Review producer", record["producer"]))
        rows.append(("Review receipt", record.get("receipt_id", "unavailable")))
        if review["kind"] == "machine":
            rows.append(("Review base", record.get("base_sha", "unavailable")))
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
        # Evidence reads are separate observations. A newer loop result cannot
        # promote an older check, including a different receipt for one head.
        current_phases = {"awaiting_review", "reviewed", "awaiting_fix"}
        mismatch = phase in current_phases and (not bound or loop_candidate != candidate)
        mismatch = mismatch or (phase in current_phases and loop.get("validation_receipt_id") is not None
                                and loop["validation_receipt_id"] != receipt.get("receipt_id"))
        mismatch = mismatch or (phase in {"awaiting_review", "reviewed"} and check == "unknown")
        mismatch = mismatch or (phase == "reviewed" and (check != "passed" or decision != "approve"
                                  or acceptance == "unknown" or loop.get("acceptance_status") != acceptance))
        # During an explicit fix the old check is expected to stop applying.
        # Its still-bound request_changes record remains the reason for recheck.
        mismatch = mismatch or (phase == "awaiting_fix" and recorded != "request_changes")
        mismatch = mismatch or (phase == "awaiting_review" and decision not in {"missing", "inconclusive"})
        if mismatch:
            acceptance = "unknown"
        next_action = ("Refresh and inspect the preserved evidence; the correction and check observations do not match."
                       if mismatch else "Inspect the retained machine review; it did not establish a review-policy decision."
                       if phase == "awaiting_review" and decision == "inconclusive" else loop.get("next_action"))
    elif phase != "not_started":
        acceptance = "unknown"
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
            "inconclusive": "Inspect the retained machine review; it did not establish a review-policy decision.",
            "approve": ("Candidate review policy is satisfied; inspect the retained evidence before any separately authorized delivery action."
                        if acceptance == "accepted_by_review_policy" else
                        "The optional human approval is recorded for the passed check; inspect the retained evidence before delivery."
                        if acceptance == "human_recorded" else
                        "Inspect the recorded review and its check binding; acceptance remains unresolved."),
        }[decision]
    if check == "failed" and decision == "approve":
        acceptance = "unknown"
        next_action = "Inspect the failed check and recorded review; acceptance remains unresolved."
    rows.append(("Acceptance", acceptance if acceptance != "unknown" else "unknown (no applicable acceptance record)"))
    rows.append(("Semantic verification", review["semantic_verification"]))
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
