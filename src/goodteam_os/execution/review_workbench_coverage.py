"""Read-only presentation of captured criteria and existing check receipts."""
from __future__ import annotations

from datetime import datetime
import re


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
    bound = (validation.get("available") is True and bool(receipt_id)
             and receipt.get("snapshot_digest") == digest
             and receipt.get("candidate") == validation.get("candidate"))
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
