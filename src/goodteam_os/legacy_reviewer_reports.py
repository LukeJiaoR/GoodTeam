"""Read-only legacy Reviewer prose, separate from structured Skill receipts."""
from hashlib import sha256
import json
import re
from urllib.parse import urlencode

from goodteam_os.delivery_outcomes import DeliveryArtifactError
from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.review_provenance import reviewer_phase_for_approval
from goodteam_os.roles import approval_artifact_for_gate
from goodteam_os.workers import WorkerJobStore

MAX_REPORT_BYTES = 1_048_576
JOB_ID = re.compile(r"job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _matches(job, state):
    job_id = str(job.get("id", ""))
    approval = str(job.get("approval") or job.get("gate") or "")
    phase = reviewer_phase_for_approval(approval)
    if (not JOB_ID.fullmatch(job_id) or job.get("run_id") != state.run_id
            or job.get("project_key") not in (None, "", state.project_key)
            or phase not in {"implementation", "release"} or job.get("phase") != phase
            or job.get("role") != {"implementation": "code_review", "release": "release"}[phase]
            or not job.get("profile_id") or not job.get("dispatch_group_id") or not job.get("idempotency_key")):
        return False
    key = approval_artifact_for_gate(approval)
    path = state.artifacts.get(key, "")
    version = artifact_version_for_path(path)
    return bool(path and job.get("artifact_key") == key and job.get("artifact_path") == path
                and version and job.get("artifact_version") == version)


def _bound_job(store, job_id, state):
    if not JOB_ID.fullmatch(job_id):
        return None
    try:
        with AnchoredRepository(store.root) as repository:
            raw = repository.read_bytes(job_id + "/job.json", max_bytes=MAX_REPORT_BYTES)
            if raw.truncated:
                return None
            job = json.loads(raw.data)
            if not isinstance(job, dict) or job.get("id") != job_id or not _matches(job, state):
                return None
            if (store.root / job_id / "status.json").exists():
                raw = repository.read_bytes(job_id + "/status.json", max_bytes=MAX_REPORT_BYTES)
                if raw.truncated:
                    return None
                status = json.loads(raw.data)
                if not isinstance(status, dict) or any(
                    name in status and status[name] != job.get(name)
                    for name in ("profile_id", "approval", "gate", "phase", "artifact_key", "artifact_path",
                                 "artifact_version", "dispatch_group_id", "idempotency_key", "run_id", "project_key")
                ):
                    return None
                job["status"] = status.get("status", job.get("status", "unknown"))
            return job
    except (OSError, ValueError, RepositoryAccessError):
        return None


def _report_bytes(store, job_id):
    # Never use the adapter's result_path or a client-supplied disk path.
    with AnchoredRepository(store.root) as repository:
        result = repository.read_bytes(job_id + "/result.md", max_bytes=MAX_REPORT_BYTES)
    if result.truncated or not result.data.strip():
        raise DeliveryArtifactError("Reviewer report is empty or exceeds the preview limit.")
    result.data.decode("utf-8")
    return result.data


def _item(state, job, store):
    item = {"project_key": state.project_key, "run_id": state.run_id, "job_id": job["id"],
            "profile_id": job["profile_id"], "approval": job.get("approval") or job["gate"],
            "artifact_key": job["artifact_key"], "artifact_version": job["artifact_version"],
            "status": job.get("status", "unknown"), "report_version": "", "href": "",
            "advisory_only": True}
    if item["status"] == "completed":
        try:
            raw = _report_bytes(store, item["job_id"])
            item["report_version"] = "sha256:" + sha256(raw).hexdigest()
            query = urlencode({"project": state.project_key, "run": state.run_id,
                "job": item["job_id"], "profile": item["profile_id"], "approval": item["approval"],
                "artifact": item["artifact_key"], "artifact_version": item["artifact_version"],
                "version": item["report_version"]})
            item["href"] = "/api/reviewer-report?" + query
        except (OSError, ValueError, RepositoryAccessError):
            pass
    return item


def build_legacy_reviewer_reports(state, worker_roots):
    items = []
    seen = set()
    for root in worker_roots:
        store = WorkerJobStore(root)
        try:
            jobs = store.list_jobs()
        except (OSError, ValueError):
            continue
        for candidate in jobs:
            job = _bound_job(store, str(candidate.get("id", "")), state)
            if job is not None and job["id"] not in seen:
                seen.add(job["id"])
                items.append(_item(state, job, store))
    return items


def read_legacy_reviewer_report(state, worker_roots, *, job_id, profile_id, approval,
                                artifact_key, artifact_version, version):
    if not JOB_ID.fullmatch(job_id):
        raise DeliveryArtifactError("Reviewer report was not found.")
    for root in worker_roots:
        store = WorkerJobStore(root)
        job = _bound_job(store, job_id, state)
        if (job is None or not _matches(job, state) or job.get("status") != "completed"
                or job.get("profile_id") != profile_id or (job.get("approval") or job.get("gate")) != approval
                or job.get("artifact_key") != artifact_key or job.get("artifact_version") != artifact_version):
            continue
        try:
            raw = _report_bytes(store, job_id)
        except (OSError, ValueError, RepositoryAccessError) as exc:
            raise DeliveryArtifactError("Reviewer report is unavailable.") from exc
        actual = "sha256:" + sha256(raw).hexdigest()
        if actual != version:
            raise DeliveryArtifactError("Reviewer report changed. Reload the review.", 409)
        return {**_item(state, job, store), "report_version": actual, "content": raw.decode("utf-8")}
    raise DeliveryArtifactError("Reviewer report was not found for this artifact version.")
