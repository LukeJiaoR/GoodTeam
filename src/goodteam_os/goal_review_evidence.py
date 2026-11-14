"""Original advisory Reviewer reports for an exactly bound completed Goal run.

Report validation establishes source/provenance, not correctness or approval.
Legacy prose and protected Skill receipts retain their separate trust paths.
"""
from hashlib import sha256
from pathlib import Path

from goodteam_os.legacy_reviewer_reports import (
    _bound_job, build_legacy_reviewer_reports, read_legacy_reviewer_report,
)
from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.workers import WorkerJobStore


def _artifact_matches(state, run_root, request):
    key, path = request.get("artifact_key"), request.get("artifact_path")
    if not path or state.artifacts.get(key) != path:
        return False
    try:
        relative = Path(path).resolve().relative_to(run_root).as_posix()
        with AnchoredRepository(run_root) as repository:
            raw = repository.read_bytes(relative, max_bytes=1_000_000)
        return not raw.truncated and "sha256:" + sha256(raw.data).hexdigest() == request.get("artifact_version")
    except (OSError, ValueError, RepositoryAccessError):
        return False


def _separate_reviewer(request, state, role):
    # A release artifact author is not a Reviewer. Require the original
    # separately dispatched, read-only/new-session operation, not a role label
    # or a completed status copied from the mutable Worker projection.
    return (request.get("project_key") == state.project_key and request.get("run_id") == state.run_id
            and request.get("role") == role and request.get("execution_policy") == "read_only"
            and request.get("session_policy") == "new" and not request.get("worker_session_ref"))


def _reference(state, job_id, target, report_version):
    return (f"run:{state.run_id}:reviewer:{job_id}:artifact:{target['artifact_key']}:"
            f"{target['artifact_version']}:report:{report_version}")


def reviewer_evidence(workspace, state, project_root):
    """Yield complete report text only through existing bound report readers."""
    run_root = (Path(workspace) / "artifacts" / "runs" / state.run_id).resolve()
    workers = WorkerJobStore(project_root)
    for item in build_legacy_reviewer_reports(state, [project_root]):
        if item["approval"] != "release" or not item["report_version"]:
            continue
        job = _bound_job(workers, item["job_id"], state)
        if (job is None or not _separate_reviewer(job, state, "release")
                or job.get("phase") != "release" or job.get("approval") != "release"
                or job.get("gate") != "release" or not _artifact_matches(state, run_root, job)):
            continue
        try:
            report = read_legacy_reviewer_report(state, [project_root], job_id=item["job_id"],
                profile_id=item["profile_id"], approval=item["approval"], artifact_key=item["artifact_key"],
                artifact_version=item["artifact_version"], version=item["report_version"])
        except (OSError, ValueError, RepositoryAccessError):
            continue
        if _bound_job(workers, item["job_id"], state) != job or not _artifact_matches(state, run_root, job):
            continue
        yield _reference(state, item["job_id"], report, report["report_version"]), report["content"]

    # The existing protected protocol supports code_review, not ordinary Skill
    # release jobs. Never reinterpret a Skill result.md as legacy evidence.
    from goodteam_os.skills.models import SkillError, canonical_json, content_digest
    from goodteam_os.skills.review_api import _jobs, _review_context
    from goodteam_os.skills.review_results import ReviewResultStore

    try:
        context = _review_context(workers, workspace, state)
        records = _jobs(workers, context) if context else []
    except (SkillError, OSError, ValueError):
        return
    for job_id, snapshot, _ref in records:
        request = snapshot["job_input"]["request"]
        if not _separate_reviewer(request, state, "code_review"):
            continue
        try:
            detail = ReviewResultStore(workers, job_id).detail(expected_task_context=context, include_report=True)
            if (detail["status"] != "received" or not detail["report_available"]
                    or detail.get("cancel_requested") or detail.get("error_code")):
                continue
            target = detail["target"]
            if target["kind"] == "artifact":
                matches = _artifact_matches(state, run_root, request)
            else:
                candidate = state.release_candidate.get("repository_result", {})
                matches = (target["kind"] == "git_change" and candidate.get("project_key") == state.project_key
                           and candidate.get("run_id") == state.run_id
                           and candidate.get("baseline_commit") == target.get("base_sha")
                           and candidate.get("result_commit") == target.get("head_sha"))
            if not matches:
                continue
            # This is the complete validated report, canonically serialized with
            # the receipt's existing digest; no summary substitutes for findings.
            text = canonical_json(detail["report"])
            if content_digest(text.encode("utf-8")) != detail["report_digest"]:
                continue
            yield _reference(state, job_id, target, detail["report_digest"]), text
        except (SkillError, OSError, ValueError):
            continue


def batch_reviewer_evidence(project_root, quality):
    """Read the full protected report identified by a validated batch receipt."""
    from goodteam_os.skills.models import canonical_json, content_digest
    from goodteam_os.skills.review_results import ReviewResultStore

    checked = quality.get("validation") or {}
    review = checked.get("review")
    if not review or review.get("schema_version") != "candidate_machine_review.v1":
        return
    provenance = review["provenance"]
    detail = ReviewResultStore(WorkerJobStore(Path(project_root)), provenance["job_id"]).detail(
        expected_task_context=provenance["task_skill_context"], include_report=True)
    if (detail["status"] != "received" or not detail["report_available"]
            or detail.get("cancel_requested") or detail.get("error_code")
            or any(detail.get(key) != value for key, value in provenance.items())
            or detail["target"].get("head_sha") != checked["receipt"]["candidate"]["head_sha"]):
        raise ValueError("The batch Reviewer report differs from its original quality receipt.")
    text = canonical_json(detail["report"])
    if content_digest(text.encode("utf-8")) != provenance["report_digest"]:
        raise ValueError("The batch Reviewer report digest changed.")
    yield f"review:{review['receipt_id']}:report:{provenance['report_digest']}", text


def batch_execution_evidence(workspace, project_key, project_root, batch):
    """Read original execution artifacts whether or not quality was configured.

    Worker reports are observations, not independent validation. Delivery is
    read through the existing original-receipt verifier, not inferred from them.
    """
    import json
    import re
    from goodteam_os.dispatch_workspace import verify_dispatch_result
    from goodteam_os.memory_storage import read_bytes
    from goodteam_os.project_iteration_execution import iteration_batch_report
    from goodteam_os.skills.models import canonical_json

    root = Path(project_root).resolve()
    run_id = batch["run_id"]
    report = iteration_batch_report(workspace=workspace, project_key=project_key,
                                    root=root, run_id=run_id)
    jobs = report.get("jobs", [])
    if (len(jobs) != len(batch["task_ids"])
            or {job.get("project_task_id") for job in jobs} != set(batch["task_ids"])):
        raise ValueError("The Goal dispatch report differs from its original batch tasks.")
    yield "dispatch", canonical_json(report)
    for row in jobs:
        job_id = row.get("job_id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job_id):
            raise ValueError("The Goal dispatch has an invalid original Worker identity.")
        dispatch_id = row.get("dispatch_run_id", report["run_id"])
        job_root = root / ".goodteam" / "workers" / "jobs" / job_id
        raw_job = read_bytes(job_root / "job.json")
        job = json.loads(raw_job) if raw_job is not None else {}
        if (job.get("id") != job_id or job.get("run_id") != dispatch_id
                or job.get("project_key") != project_key
                or job.get("project_task_id") not in {None, "", row["project_task_id"]}):
            raise ValueError("The Goal Worker output belongs to another execution or task.")
        if row.get("attribution_status") == "captured":
            captured = verify_dispatch_result(root, {**row, "dispatch_run_id": dispatch_id})
            yield f"worker:{job_id}:candidate", canonical_json(captured)
        raw = read_bytes(job_root / "result.md", maximum=2 * 1024 * 1024)
        if raw is not None:
            yield f"worker:{job_id}:report", raw.decode("utf-8")
        else:
            yield f"worker:{job_id}:report-status", canonical_json({
                "job_id": job_id, "report_status": "missing", "task_acceptance": "not_assessed"})
    if batch.get("status") == "delivered":
        from goodteam_os.project_outcome_discovery import verified_outcome

        outcome = verified_outcome(workspace=workspace, project_key=project_key,
                                   root=root, run_id=run_id)
        yield "delivery", canonical_json(outcome)
