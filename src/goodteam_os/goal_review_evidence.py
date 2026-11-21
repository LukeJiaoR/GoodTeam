"""Original advisory Reviewer reports for an exactly bound completed Goal run.

Report validation establishes source/provenance, not correctness or approval.
Legacy prose and protected Skill receipts retain their separate trust paths.
"""
from pathlib import Path

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
        # Verify current ancestry/content above, but keep retry evidence bound
        # to the original delivery rather than unrelated subsequent commits.
        yield "delivery", canonical_json({**outcome,
            "project_head": outcome["delivery"]["landed_commit"]})
