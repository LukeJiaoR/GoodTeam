"""Read-only, exact-candidate report details for the execution Workbench."""

from goodteam_os.memory_storage import role_lock
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.workers import WorkerJobStore

from .candidate_probe import probe_candidate
from .candidate_validation import CandidateValidationService


def retained_report(project_root, execution_id, displayed):
    unavailable = {"available": False, "reason": "report_unavailable"}
    if displayed.get("review_status") != "machine_recorded":
        return {**unavailable, "reason": "machine_review_missing"}
    if not displayed.get("available") or displayed.get("check_status") not in {"passed", "failed"}:
        return {**unavailable, "reason": "candidate_not_current"}
    try:
        service = CandidateValidationService(project_root)
        snapshot = service._snapshot(execution_id)
        with role_lock(service._root(execution_id), create=False):
            current = service._current(snapshot)
            if (current is None or current[1] != displayed.get("receipt")
                    or current[2] != displayed.get("review")
                    or current[1]["candidate"] != displayed.get("candidate")):
                return {**unavailable, "reason": "displayed_review_changed"}
            plan, result, review = current
            if review is None or review.get("schema_version") != "candidate_machine_review.v1":
                return {**unavailable, "reason": "machine_review_missing"}
            root = service.source_root(snapshot, plan["request"])
            probe_candidate(root, result["candidate"]["head_sha"])
            provenance = review["provenance"]
            detail = ReviewResultStore(WorkerJobStore(project_root), review["reviewer"]).detail(
                expected_task_context=provenance["task_skill_context"], include_report=True)
            if (detail.get("status") != "received" or not detail.get("report_available")
                    or any(detail.get(key) != item for key, item in provenance.items())):
                return unavailable
            # Do not join a report read to a newer check or source observation.
            if service._current(snapshot) != current:
                return {**unavailable, "reason": "displayed_review_changed"}
            probe_candidate(root, result["candidate"]["head_sha"])
            return {"available": True, "reason": "", "report": detail["report"],
                    "validation_receipt_id": result["receipt_id"], "review_receipt_id": review["receipt_id"],
                    "reviewer_job_id": review["reviewer"], "base_sha": review["base_sha"],
                    "head_sha": result["candidate"]["head_sha"], "decision": review["decision"],
                    "report_digest": provenance["report_digest"],
                    "runtime_verification": provenance["runtime_verification"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Never expose exception text, private prompts, paths, or raw logs.
        return unavailable


_LIMITATION = ("This is the retained Reviewer's advisory judgment for the exact candidate below. "
               "A checked item records Reviewer inspection, not an independently observed test run. "
               "Matched source citations prove the quoted input, not that the judgment is correct. "
               "This view grants no approval or permission to run, merge or deploy.")
_UNAVAILABLE = {
    "machine_review_missing": "No verified machine report is available for this check. Human records are shown separately.",
    "candidate_not_current": "The recorded check no longer establishes the current candidate; report details are unavailable.",
    "displayed_review_changed": "The check or review changed while this page was read. Refresh to inspect one matching version.",
    "report_unavailable": "The retained report could not be verified. Inspect the preserved evidence before continuing.",
}


def report_metadata(value):
    return [("Reviewed head", value["head_sha"]), ("Review base", value["base_sha"]),
            ("Machine decision", value["decision"]), ("Validation receipt", value["validation_receipt_id"]),
            ("Review receipt", value["review_receipt_id"]), ("Reviewer job", value["reviewer_job_id"]),
            ("Report digest", value["report_digest"]), ("Runtime verification", value["runtime_verification"])]


def report_lines(value):
    if not value.get("available"):
        return [_UNAVAILABLE.get(value.get("reason"), _UNAVAILABLE["report_unavailable"])]
    report = value["report"]
    lines = [_LIMITATION, *(f"{label}: {text}" for label, text in report_metadata(value)),
             "Summary:", report["summary"], "Findings:"]
    if not report["findings"]:
        lines.append("No findings recorded. This alone does not establish acceptance.")
    for finding in report["findings"]:
        lines.extend([f"{finding['id']} [{finding['severity']}]: {finding['title']}", finding["detail"],
                      "Recommended change: " + finding["recommendation"],
                      "Evidence: " + ", ".join(finding["evidence_ids"])])
    lines.append("Checks and limitations:")
    for check in report["checks"]:
        lines.extend([f"{check['name']} [{check['status']}]", check["detail"],
                      "Evidence: " + (", ".join(check["evidence_ids"]) or "none")])
    lines.append("Captured source evidence (logical review-input paths):")
    for item in report["evidence"]:
        lines.extend([f"{item['id']}: {item['path']}:{item['line_start']}-{item['line_end']}",
                      item["observation"], item["quote"], "Source digest: " + item["source_digest"],
                      "Reference status: " + item["reference_status"]])
    return lines

