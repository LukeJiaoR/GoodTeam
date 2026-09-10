"""Read-only, run-bound delivery reports; never infer acceptance from exit status."""

from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.resources import ResourceCapability, ResourceKind, ResourceScope
from goodteam_os.state import StateStore, normalize_project_key

MAX_ARTIFACT_BYTES = 1_048_576
DELIVERY_KEYS = ("implementation_handoff", "qa_report", "code_review", "release_notes")
SIMULATED_ADAPTERS = {"fake", "noop"}


class DeliveryArtifactError(ValueError):
    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status


def _artifact_bytes(workspace, state, key):
    path = Path(state.artifacts.get(key, ""))
    resource = state.resources.get(key)
    if resource is not None and (
        resource.kind != ResourceKind.MARKDOWN_ARTIFACT
        or resource.scope != ResourceScope.RUN
        or ResourceCapability.READ not in resource.capabilities
        or resource.path != str(path)
    ):
        raise DeliveryArtifactError("Delivery artifact is not readable.")
    root = Path(workspace).absolute() / "artifacts" / "runs" / state.run_id
    if key not in DELIVERY_KEYS or not state.artifacts.get(key):
        raise DeliveryArtifactError("Delivery artifact is not registered.")
    if not path.is_absolute():
        path = Path(workspace).absolute() / path
    try:
        relative = path.relative_to(root).as_posix()
        with AnchoredRepository(root) as repository:
            result = repository.read_bytes(relative, max_bytes=MAX_ARTIFACT_BYTES)
        if result.truncated:
            raise DeliveryArtifactError("Delivery artifact exceeds the preview limit.")
        result.data.decode("utf-8")
        return result.data
    except (ValueError, OSError, RepositoryAccessError) as exc:
        raise DeliveryArtifactError("Delivery artifact is unavailable.") from exc


def _artifact_item(workspace, state, key):
    item = {"key": key, "label": Path(state.artifacts[key]).name, "status": "unavailable", "version": "", "href": ""}
    try:
        raw = _artifact_bytes(workspace, state, key)
    except DeliveryArtifactError:
        return item
    if not raw.strip():
        return item
    version = "sha256:" + sha256(raw).hexdigest()
    query = urlencode({"project": state.project_key, "run": state.run_id, "key": key, "version": version})
    return {**item, "status": "available", "version": version, "href": "/api/delivery-artifact?" + query}


def _delivery_status(state, attempts):
    statuses = {item["status"] for item in attempts}
    if state.blockers or statuses & {"failed", "error", "blocked"}:
        return "blocked"
    if statuses & {"running", "queued", "in_progress"}:
        return "working"
    if statuses & {"cancelled", "canceled", "aborted"}:
        return "stopped"
    if state.next_node.startswith("await_"):
        return "awaiting_review"
    return "working" if state.next_node else "needs_verification"


def build_delivery_summary(workspace, *, state, jobs=(), historical_reason=""):
    if state is None:
        return None
    attempts = [
        {name: str(job.get(name, "") or "") for name in ("id", "role", "adapter", "status", "updated_at")}
        for job in jobs
        if job.get("run_id") == state.run_id
    ]
    latest = {}
    for attempt in sorted(attempts, key=lambda item: (item["updated_at"], item["id"])):
        latest[attempt["role"]] = attempt
    artifacts = [_artifact_item(workspace, state, key) for key in DELIVERY_KEYS if key in state.artifacts]
    return {
        "run_id": state.run_id,
        "project_key": state.project_key,
        "status": "stopped" if historical_reason else _delivery_status(state, list(latest.values())),
        "verification": "unverified",
        "artifacts": artifacts,
        "attempts": attempts,
        "simulated": any(item["adapter"] in SIMULATED_ADAPTERS for item in attempts),
        "next_node": state.next_node,
    }


def read_delivery_artifact(workspace, *, project_key, run_id, artifact_key, version):
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
    return {"content": raw.decode("utf-8"), "version": actual, "artifact": artifact_key, "run_id": run_id}
