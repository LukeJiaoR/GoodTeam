"""Read-only, run-bound reports. A report or successful process is not acceptance."""

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlencode

from goodteam_os.assistant_workspace import is_assistant_activity
from goodteam_os.attempt_memory import attempt_memory_observations
from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.resources import ResourceCapability, ResourceKind, ResourceScope
from goodteam_os.state import StateStore, normalize_project_key

MAX_ARTIFACT_BYTES = 1_048_576
DELIVERY_KEYS = ("implementation_handoff", "qa_report", "code_review", "release_notes")
SIMULATED_ADAPTERS = {"fake", "noop", "builtin"}
FAILED_STATUSES = {"failed", "error", "blocked", "interrupted", "timeout", "timed_out"}
ACTIVE_STATUSES = {"running", "queued", "in_progress", "pending"}
STOPPED_STATUSES = {"cancelled", "canceled", "aborted", "rejected", "stopped", "terminated"}
COMPLETE_STATUSES = {"completed", "succeeded", "success"}
ATTEMPT_FIELDS = (
    "id", "role", "adapter", "status", "updated_at", "worker_id", "execution_id",
    "profile_id", "approval", "gate", "phase", "artifact_key", "artifact_version",
    "dispatch_group_id", "idempotency_key", "original_failed_job_id",
    "retry_of_idempotency_key", "retry_attempt",
)
RETRY_CONTRACT_FIELDS = (
    "role", "worker_id", "execution_id", "profile_id", "approval", "gate", "phase",
    "artifact_key", "artifact_version", "dispatch_group_id",
)


class DeliveryArtifactError(ValueError):
    def __init__(self, message: str, status: int = 404):
        super().__init__(message)
        self.status = status


def _valid_run_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None


def _artifact_bytes(workspace, state, key):
    if not _valid_run_id(state.run_id) or key not in DELIVERY_KEYS or not state.artifacts.get(key):
        raise DeliveryArtifactError("Delivery artifact is not registered.")
    path = Path(state.artifacts[key])
    resource = state.resources.get(key)
    # A legacy artifacts-only state is supported. A present typed registry must
    # not be bypassed by a stray legacy map entry or a mismatched descriptor.
    if (resource is None and state.resources) or (resource is not None and (
        resource.key != key
        or resource.kind != ResourceKind.MARKDOWN_ARTIFACT
        or resource.scope != ResourceScope.RUN
        or ResourceCapability.READ not in resource.capabilities
        or resource.path != str(path)
    )):
        raise DeliveryArtifactError("Delivery artifact is not readable.")
    root = Path(workspace).absolute() / "artifacts" / "runs" / state.run_id
    if not path.is_absolute():
        path = Path(workspace).absolute() / path
    try:
        relative = path.relative_to(root).as_posix()
        with AnchoredRepository(root) as repository:
            result = repository.read_bytes(relative, max_bytes=MAX_ARTIFACT_BYTES)
        if result.truncated or not result.data.strip():
            raise DeliveryArtifactError("Delivery artifact is empty or exceeds the preview limit.")
        result.data.decode("utf-8")
        return result.data
    except (ValueError, OSError, RepositoryAccessError) as exc:
        # Do not expose local paths, directory contents or partial file bytes.
        raise DeliveryArtifactError("Delivery artifact is unavailable.") from exc


def _artifact_item(workspace, state, key):
    item = {"key": key, "label": Path(state.artifacts[key]).name, "status": "unavailable", "version": "", "href": ""}
    try:
        raw = _artifact_bytes(workspace, state, key)
    except DeliveryArtifactError:
        return item
    version = "sha256:" + sha256(raw).hexdigest()
    query = urlencode({"project": state.project_key, "run": state.run_id, "key": key, "version": version})
    return {**item, "status": "available", "version": version, "href": "/api/delivery-artifact?" + query}


def _retry_number(attempt):
    value = str(attempt.get("retry_attempt", ""))
    return int(value) if value.isdecimal() and len(value) < 10 else 0


def _effective_attempts(attempts):
    """Collapse only explicitly linked retries, never independent same-role jobs."""
    by_id = {item["id"]: item for item in attempts if item["id"]}

    def root_id(item):
        # Iterative traversal also tolerates unusually long imported retry chains.
        while True:
            parent = by_id.get(item["original_failed_job_id"])
            if not parent or parent["status"] not in FAILED_STATUSES:
                return item["id"]
            if _retry_number(item) <= _retry_number(parent):
                return item["id"]
            if any(item[field] != parent[field] for field in RETRY_CONTRACT_FIELDS):
                return item["id"]
            if item["retry_of_idempotency_key"] and item["retry_of_idempotency_key"] != parent["idempotency_key"]:
                return item["id"]
            # Strictly decreasing ordinals also reject malformed cycles.
            item = parent

    groups = {}
    for index, item in enumerate(attempts):
        key = root_id(item) if item["id"] else f"unknown:{index}"
        groups.setdefault(key, []).append(item)
    effective = []
    for group in groups.values():
        latest = max(_retry_number(item) for item in group)
        # Ambiguous equal-ordinal records remain visible rather than hiding a failure.
        effective.extend(item for item in group if _retry_number(item) == latest)
    return effective


def _delivery_status(state, attempts, historical_reason="", *, received_review_ids=frozenset()):
    reason = str(historical_reason or "").strip().lower()
    if reason in STOPPED_STATUSES or state.next_node in STOPPED_STATUSES or state.current_node in STOPPED_STATUSES:
        return "stopped"
    if reason and reason not in {"complete", "completed"}:
        return "stopped"
    statuses = {item["status"] for item in attempts}
    known = FAILED_STATUSES | ACTIVE_STATUSES | STOPPED_STATUSES | COMPLETE_STATUSES
    unknown = any(item["status"] not in known and not (
        item["status"] == "review_received" and item["id"] in received_review_ids
    ) for item in attempts)
    if state.blockers or statuses & FAILED_STATUSES or unknown:
        return "blocked"
    if statuses & ACTIVE_STATUSES:
        return "working"
    if statuses & STOPPED_STATUSES:
        return "stopped"
    if state.next_node.startswith("await_"):
        return "awaiting_review"
    return "working" if state.next_node else "needs_verification"


def _received_review_ids(workspace, state, worker_root):
    """Read protected receipts, never turn a mutable Worker flag into evidence."""
    if worker_root is None:
        return frozenset()
    from goodteam_os.skills.models import SkillError
    from goodteam_os.skills.review_api import _review_context, task_review_details
    from goodteam_os.workers import WorkerJobStore

    try:
        workers = WorkerJobStore(worker_root)
        context = _review_context(workers, workspace, state)
        if not context:
            return frozenset()
        code, payload = task_review_details(workers, context)
        if code != 200:
            return frozenset()
        return frozenset(item["job_id"] for item in payload["reviews"]
                         if item["status"] == "received" and item["report_available"] is True
                         and item.get("advisory") is True
                         and item.get("judgment_verification") == "not_independently_verified"
                         and not item.get("cancel_requested") and not item.get("error_code"))
    except (SkillError, OSError, ValueError, KeyError, TypeError):
        # Unknown, changed, revoked or unreadable receipts remain blocking.
        return frozenset()


def build_delivery_summary(workspace, *, state, jobs=(), historical_reason="", worker_root=None):
    if state is None:
        return None
    attempts = [
        {**{name: str(job.get(name, "") or "") for name in ATTEMPT_FIELDS},
         "memory_observations": attempt_memory_observations(
             worker_root, project_key=state.project_key, run_id=state.run_id, job=job)}
        for job in jobs
        if isinstance(job, Mapping) and not is_assistant_activity(job) and job.get("run_id") == state.run_id
        and (not job.get("project_key") or normalize_project_key(str(job["project_key"])) == state.project_key)
    ]
    artifacts = [_artifact_item(workspace, state, key) for key in DELIVERY_KEYS if key in state.artifacts]
    received_review_ids = (_received_review_ids(workspace, state, worker_root)
                           if any(item["status"] == "review_received" for item in attempts) else frozenset())
    return {
        "run_id": state.run_id,
        "project_key": state.project_key,
        "status": _delivery_status(state, _effective_attempts(attempts), historical_reason,
                                   received_review_ids=received_review_ids),
        "verification": "unverified",
        "artifacts": artifacts,
        "attempts": attempts,
        "simulated": any(item["adapter"] in SIMULATED_ADAPTERS for item in attempts),
        "next_node": state.next_node,
    }


def read_delivery_artifact(workspace, *, project_key, run_id, artifact_key, version):
    if not _valid_run_id(run_id) or not isinstance(project_key, str) or not project_key.strip():
        raise DeliveryArtifactError("Delivery run was not found.")
    project_key = normalize_project_key(project_key)
    store = StateStore(workspace)
    current = store.read_project_current(project_key)
    state = (
        current
        if current is not None and current.run_id == run_id
        else next((state for _, state in store.list_project_archived(project_key) if state.run_id == run_id), None)
    )
    if state is None or state.project_key != project_key:
        raise DeliveryArtifactError("Delivery run was not found.")
    raw = _artifact_bytes(workspace, state, artifact_key)
    actual = "sha256:" + sha256(raw).hexdigest()
    if version != actual:
        raise DeliveryArtifactError("Delivery artifact changed. Reload the task.", 409)
    return {"content": raw.decode("utf-8"), "version": actual, "artifact": artifact_key,
            "run_id": run_id, "project_key": project_key}
