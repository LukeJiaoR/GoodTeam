"""A workflow retry acts on the exact observed run, stage and attempt set."""

from dataclasses import asdict
from hashlib import sha256
import json

from goodteam_os.assistant_workspace import is_assistant_activity
from goodteam_os.state import normalize_project_key


SCHEMA = "workflow-retry-target.v1"
ACTIVE = {"queued", "ready", "pending", "starting", "running", "in_progress"}
TERMINAL = {
    "failed", "error", "blocked", "interrupted", "timeout", "timed_out",
    "completed", "succeeded", "success", "ok", "review_received", "skipped",
    "cancelled", "canceled", "aborted", "rejected", "stopped", "terminated",
}
ATTEMPT_FIELDS = (
    "id", "role", "status", "attempt", "retry_attempt", "updated_at",
    "execution_id", "idempotency_key", "original_failed_job_id",
    "retry_of_idempotency_key", "dispatch_group_id", "phase", "gate",
    "artifact_key", "artifact_version", "disposition", "failure_class",
)


def retry_mode(state):
    if state.pending_transition.get("status") in {"recording", "executing", "interrupted"}:
        return "resume_transition"
    if state.next_node.startswith("revise_"):
        return "retry_revision"
    if state.load_profile == "fix" and state.next_node == "blocked" and state.current_node == "fix_implement":
        return "retry_fix"
    if state.current_node == "implementation" and (
        state.next_node == "blocked" or (state.next_node == "failure_triage" and state.blockers)
    ):
        return "retry_staging" if any(item.startswith("staging ") for item in state.blockers) else "retry_implementation"
    return "unavailable"


def _same_project(job, state):
    if not job.get("project_key"):
        return True
    try:
        return normalize_project_key(str(job["project_key"])) == state.project_key
    except ValueError:
        return False


def build_retry_target(state, *, jobs=(), actions=(), execution_context=None):
    if state is None:
        return None
    attempts = sorted((
        {name: "" if job.get(name) is None else str(job[name]) for name in ATTEMPT_FIELDS}
        for job in jobs if job.get("run_id") == state.run_id
        and _same_project(job, state)
        and not is_assistant_activity(job)
    ), key=lambda job: job["id"])
    active = [job["id"] for job in attempts if job["status"] in ACTIVE]
    unknown = any(job["status"] not in ACTIVE | TERMINAL for job in attempts)
    mode = retry_mode(state)
    in_transition = state.pending_transition.get("status") in {"recording", "executing"}
    reason = "active_attempt" if active or in_transition else "attempt_unknown" if unknown else "not_retryable" if mode == "unavailable" else ""
    # A retry-start event consumes even a failed attempt that restores identical
    # workflow fields. A second click must inspect that new attempt first.
    starts = [item.get("workflow_operation_id", "") for item in actions
              if item.get("run_id") == state.run_id and item.get("action") == "blocked_workflow_retry_started"]
    versioned = {"state": asdict(state), "attempts": attempts,
                 "retry_starts": starts, "execution_context": execution_context}
    version = "sha256:" + sha256(json.dumps(versioned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": SCHEMA, "project_key": state.project_key, "run_id": state.run_id,
        "version": version, "available": not reason, "mode": mode, "reason": reason,
        "attempts": [{key: job[key] for key in ("id", "role", "status", "attempt", "retry_attempt")} for job in attempts],
        "active_attempt_ids": active,
    }
