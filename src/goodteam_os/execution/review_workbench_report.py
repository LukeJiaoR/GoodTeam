"""Read-only, exact-candidate report details for the execution Workbench."""
from html import escape

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


def render_report_html(value):
    heading = '<section class="panel review-report" id="candidate-review-report"><h2>Reviewer report</h2>'
    if not value.get("available"):
        text = _UNAVAILABLE.get(value.get("reason"), _UNAVAILABLE["report_unavailable"])
        return heading + '<p class="empty-state">' + escape(text) + '</p></section>'
    report = value["report"]
    body = '<p>' + escape(_LIMITATION) + '</p><p class="review-report-copy">' + escape(report["summary"]) + '</p>'
    body += '<details><summary>Exact candidate and report identity</summary><dl class="details">'
    body += ''.join('<dt>' + escape(label) + '</dt><dd>' + escape(text) + '</dd>'
                    for label, text in report_metadata(value)) + '</dl></details>'
    body += '<h3>Findings (' + str(len(report["findings"])) + ')</h3>'
    if not report["findings"]:
        body += '<p>No findings recorded. This alone does not establish acceptance.</p>'
    for finding in report["findings"]:
        body += ('<article class="review-report-item"><h4>' + escape(finding["id"] + ' · ' + finding["severity"]
                 + ' · ' + finding["title"]) + '</h4><p class="review-report-copy">' + escape(finding["detail"])
                 + '</p><p class="review-report-copy"><strong>Recommended change:</strong> '
                 + escape(finding["recommendation"]) + '</p>' + _citations(finding["evidence_ids"]) + '</article>')
    body += '<details><summary>Checks and limitations (' + str(len(report["checks"])) + ')</summary>'
    for check in report["checks"]:
        body += ('<article class="review-report-item"><h4>' + escape(check["status"] + ' · ' + check["name"])
                 + '</h4><p class="review-report-copy">' + escape(check["detail"]) + '</p>'
                 + _citations(check["evidence_ids"]) + '</article>')
    body += ('</details><h3>Captured source evidence</h3><p>Follow a citation and expand its source entry to inspect the quote. '
             'Paths name the fixed review input; they are not file-opening links.</p>')
    for item in report["evidence"]:
        body += ('<details class="review-report-item" id="review-evidence-' + escape(item["id"], quote=True)
                 + '"><summary>' + escape(f"{item['id']} · {item['path']}:{item['line_start']}-{item['line_end']}")
                 + '</summary><p class="review-report-copy">' + escape(item["observation"])
                 + '</p><pre><code>' + escape(item["quote"]) + '</code></pre><p>Source digest: '
                 + escape(item["source_digest"]) + '</p><p>Reference status: ' + escape(item["reference_status"]) + '</p></details>')
    return heading + body + '</section>'


def _citations(ids):
    return '<p>Evidence: ' + (', '.join('<a href="#review-evidence-' + escape(key, quote=True) + '">'
                                      + escape(key) + '</a>' for key in ids) or 'none') + '</p>'
