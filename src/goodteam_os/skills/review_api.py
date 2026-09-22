"""Task-scoped, read-only projection of protected Reviewer results.

The original task registry selects jobs, not Worker directory listings, status
JSON or a URL supplied by a model. No public report-write endpoint is provided.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from . import worker_requests as requests
from .models import SkillError
from .review_results import DETAIL_SCHEMA, ReviewResultStore
from .task_context import workflow_skill_context
from .task_locks import SNAPSHOT_SCHEMA, TaskSkillIdentity


def _jobs(workers, context):
    root, _operator, locks, _availability = requests._services(workers)
    lock = locks.read(context["task_lock_ref"], task=TaskSkillIdentity(**context["task"]))
    binding = lock.get("start_input", {}).get("request", {}).get("confirmed_input", {}).get("producer_binding")
    if binding is None or binding.get("worker_root") != str(root):
        raise SkillError("snapshot_scope_mismatch", "The current project root does not match this task's original Worker root.")
    records = []
    with locks._locked() as fd:
        for operation, entry in locks._registry(fd)["jobs"].items():
            snapshot = locks._resource(fd, entry["ref"], SNAPSHOT_SCHEMA)
            if snapshot["task"] != context["task"] or snapshot["subject"] != "role:code_review":
                continue
            payload = snapshot["job_input"]
            if snapshot["task_lock_ref"] != context["task_lock_ref"] or payload.get("worker_root") != str(root):
                raise SkillError("snapshot_scope_mismatch", "A registered Reviewer belongs to another task lock or Worker root.")
            job_id = requests._job_id(root, locks.scope, payload["request"].get("idempotency_key"))
            if requests._digest(job_id)[7:] != operation:
                raise SkillError("review_identity_invalid", "A registered Reviewer has an inconsistent command identity.")
            records.append((job_id, deepcopy(snapshot), deepcopy(entry["ref"])))
    return sorted(records, key=lambda row: (row[1]["created_at"], row[0]))


def _unavailable(job_id, snapshot, ref, context, code):
    request = snapshot["job_input"]["request"]
    return {"schema_version": DETAIL_SCHEMA, "job_id": job_id, "task_skill_context": context,
            "snapshot_ref": ref, "status": "unavailable", "error_code": code, "report_available": False,
            "advisory": True, "judgment_verification": "not_independently_verified",
            "target": {"artifact_key": request.get("artifact_key", ""),
                       "artifact_version": request.get("artifact_version", ""),
                       "display_name": Path(request.get("artifact_path") or "").name},
            "methods": [{key: item[key] for key in ("skill_id", "version_id", "package_digest", "activation")}
                        for item in snapshot["methods"]["items"]]}


def task_review_details(workers, context, *, job_id=None, include_report=False):
    records = _jobs(workers, context)
    selected = [row for row in records if job_id is None or row[0] == job_id]
    if job_id is not None and not selected:
        return 404, {"error": "review_not_found", "message": "This Reviewer does not belong to the selected task."}
    items = []
    for identity, snapshot, ref in selected:
        try:
            detail = ReviewResultStore(workers, identity).detail(expected_task_context=context, include_report=include_report)
        except (SkillError, OSError) as exc:
            code = exc.code if isinstance(exc, SkillError) else "review_result_unavailable"
            detail = _unavailable(identity, snapshot, ref, context, code)
        items.append(detail)
    if job_id is not None:
        detail = items[0]
        if include_report and not detail["report_available"]:
            return 409, {"error": detail.get("error_code", "review_report_unavailable"),
                         "message": "No valid received report is available for this task and Reviewer.", "execution": detail}
        return 200, detail
    return 200, {"schema_version": "goodteam.task-reviews.v1", "task_skill_context": context, "reviews": items}


def handle_review_get(workspace: Path, path: str):
    match = re.fullmatch(r"/api/projects/([^/]+)/runs/([^/]+)/skill-reviews(?:/(job-skill-[0-9a-f]{64})(/report)?)?", path)
    if not match:
        return None
    # Lazy imports avoid a second web/runtime implementation or circular startup.
    from goodteam_os import web
    from goodteam_os.state import StateStore
    from goodteam_os.workers import WorkerJobStore

    key, run_id = web._decode_project_api_key(match[1]), web._decode_project_run_id(match[2])
    _key, _target, root = web._project_target_and_root(workspace, key)
    states = StateStore(workspace)
    state = states.read_project_current(key)
    if state is None or state.run_id != run_id:
        state = next((item for _path, item in states.list_project_archived(key) if item.run_id == run_id), None)
    if state is None:
        return 404, {"error": "run_not_found", "message": "The selected task does not belong to this project."}
    try:
        context = workflow_skill_context(state.task_skill_context, run_id=run_id, project_key=key)
        if not context:
            if match[3]:
                return 404, {"error": "review_not_found", "message": "This task has no locked Reviewer report."}
            return 200, {"schema_version": "goodteam.task-reviews.v1", "project_key": key, "run_id": run_id, "reviews": []}
        status, payload = task_review_details(WorkerJobStore(root), context, job_id=match[3], include_report=bool(match[4]))
    except (SkillError, OSError) as exc:
        status, payload = 409, {"error": exc.code if isinstance(exc, SkillError) else "review_result_unavailable",
                                "message": "Task-bound review evidence is unavailable; no report was accepted."}
    return status, {**payload, "project_key": key, "run_id": run_id}
