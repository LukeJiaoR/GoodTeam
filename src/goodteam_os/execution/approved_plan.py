"""Bind a captured issue to an explicitly selected derived task and approved intent.

Only trusted local composition registers this association. Issue text and current
project selection never select a workflow run. The mapping is derived, not a new
human approval. Every use rechecks the original durable decisions and bytes.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re

from goodteam_os.memory_storage import MemoryStorageError, atomic_write, read_bytes, role_lock
from goodteam_os.skills.task_start import registered_project_scope
from goodteam_os.state import StateStore, normalize_project_key
from goodteam_os.workflow_mutations import workflow_guard

from .task_snapshot import TaskInputError, canonical_bytes, digest, execution_key
from .task_snapshot_store import store_for_project

SCHEMA = "execution_approved_plan.v1"
MAX_BYTES = 192 * 1024


@contextmanager
def _errors():
    try:
        yield
    except TaskInputError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, MemoryStorageError) as exc:
        raise TaskInputError("approved_plan_unavailable") from exc


def _artifact(workspace, state, key):
    value = state.artifacts.get(key)
    if not isinstance(value, str) or not value:
        raise TaskInputError("approved_plan_artifact_missing")
    path = Path(value).absolute()
    base = Path(workspace).resolve() / "artifacts" / "runs" / state.run_id
    if not path.is_relative_to(base) or path.resolve() != path:
        raise TaskInputError("approved_plan_artifact_scope_mismatch")
    raw = read_bytes(path, maximum=MAX_BYTES)
    if not raw:
        raise TaskInputError("approved_plan_artifact_missing")
    return {"artifact_key": key, "artifact_path": str(path),
            "artifact_version": "sha256:" + digest(raw), "content": raw.decode("utf-8")}


def _task_section(content, anchor):
    # Exact heading text is a local selection, never a fuzzy semantic match.
    if not isinstance(anchor, str) or not anchor.strip() or "\n" in anchor:
        raise TaskInputError("approved_plan_task_anchor_invalid")
    headings = list(re.finditer(r"(?m)^(#{2,6}) ([^\n]+)\s*$", content))
    selected = [item for item in headings if item.group(2).strip() == anchor]
    if len(selected) != 1:
        raise TaskInputError("approved_plan_task_membership_missing")
    item = selected[0]
    end = next((other.start() for other in headings
                if other.start() > item.start() and len(other.group(1)) <= len(item.group(1))), len(content))
    section = content[item.start():end].strip()
    if not content[item.end():end].strip():
        raise TaskInputError("approved_plan_task_membership_missing")
    return section


def _decision_scope(states, action, project_key, run_id, gate):
    if "project_key" in action:
        return action["project_key"] == project_key
    # Older logs omitted project_key, while their durable transition/checkpoint
    # already retained it. Reuse only that exact operation, never log location
    # alone or another run's most recent approval.
    root = states.project_state_dir(project_key) / "checkpoints" / run_id
    paths = sorted(root.glob(gate + "_review_gate-*.json"))
    if len(paths) > 256:
        raise TaskInputError("approved_plan_history_limit")
    fields = ("run_id", "actor", "action", "gate", "decision", "artifact", "artifact_path",
              "artifact_version", "workflow_operation_id")
    for path in paths:
        raw = read_bytes(path, maximum=1024 * 1024)
        value = json.loads(raw) if raw is not None else {}
        transition = value.get("pending_transition", {})
        recorded = transition.get("action", {})
        if (value.get("project_key") == project_key and value.get("run_id") == run_id
                and transition.get("id") == action.get("workflow_operation_id")
                and recorded.get("project_key") == project_key
                and all(recorded.get(field) == action.get(field) for field in fields)):
            return True
    return False


def _capture(workspace, project_key, workflow_run_id, snapshot, task_anchor):
    if not isinstance(workflow_run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workflow_run_id):
        raise TaskInputError("approved_plan_run_invalid")
    states = StateStore(workspace)
    state = states.read_project_current(project_key)
    if (state is None or state.run_id != workflow_run_id
            or normalize_project_key(state.project_key) != project_key):
        raise TaskInputError("approved_plan_run_not_current")
    profile = state.load_profile or "greenfield"
    gates = {"rfc": "rfc", "security": "rfc"} if profile == "greenfield" else {"fix_plan": "fix_plan"} if profile == "fix" else None
    if gates is None:
        raise TaskInputError("approved_plan_profile_unsupported")
    raw = read_bytes(states.project_state_dir(project_key) / "action-log.jsonl", maximum=8 * 1024 * 1024)
    actions = [json.loads(line) for line in raw.splitlines() if line.strip()] if raw else []
    approvals, artifacts = [], {}
    for gate, key in gates.items():
        if str(state.approvals.get(gate, "")) != "approved":
            raise TaskInputError("approved_plan_reapproval_required")
        material = artifacts.setdefault(key, _artifact(workspace, state, key))
        decisions = [item for item in actions if isinstance(item, dict)
                     and item.get("run_id") == workflow_run_id and item.get("action") == "approval_decided"
                     and item.get("gate") == gate]
        action = decisions[-1] if decisions else {}
        operation = action.get("workflow_operation_id")
        if (action.get("decision") != "approved" or action.get("actor") != "human"
                or not _decision_scope(states, action, project_key, workflow_run_id, gate)
                or not isinstance(operation, str) or not operation.strip()
                or action.get("artifact") != key or action.get("artifact_path") != material["artifact_path"]
                or action.get("artifact_version") != material["artifact_version"]):
            raise TaskInputError("approved_plan_reapproval_required")
        if sum(1 for event in actions if isinstance(event, dict)
               and event.get("workflow_operation_id") == operation) != 1:
            raise TaskInputError("approved_plan_operation_reused")
        approvals.append({"gate": gate, "workflow_operation_id": operation,
                          "artifact_key": key, "artifact_version": material["artifact_version"]})
    # Fix plans are themselves the approved task decomposition. Greenfield's
    # task breakdown is derived material, never described as human-approved.
    breakdown = _artifact(workspace, state, "task_breakdown" if profile == "greenfield" else "fix_plan")
    section = _task_section(breakdown["content"], task_anchor)
    return {"schema_version": SCHEMA, "scope": snapshot.to_dict()["scope"],
            "project_key": project_key, "workflow_run_id": workflow_run_id, "profile": profile,
            "input_snapshot": snapshot.reference, "approvals": approvals,
            "approved_artifacts": list(artifacts.values()),
            "derived_task": {"trust": "locally_registered_derivation", "human_approved": False,
                "task_id": snapshot.task.task_id, "task_anchor": task_anchor,
                "artifact_key": breakdown["artifact_key"], "artifact_path": breakdown["artifact_path"],
                "artifact_version": breakdown["artifact_version"], "content": section,
                "content_digest": digest(section.encode())}}


def _bound(value):
    body = {key: item for key, item in value.items() if key != "binding_id"}
    return {**body, "binding_id": "approved-plan:" + digest(canonical_bytes(body))}


class ApprovedPlanBinding:
    def __init__(self, project_root):
        self.project_root = Path(project_root).resolve()
        self.store = store_for_project(self.project_root)
        self.scope = registered_project_scope(self.project_root)
        if self.store is None or self.scope is None:
            raise TaskInputError("approved_plan_project_unregistered")

    def _root(self, snapshot):
        return self.store.root / execution_key(snapshot.execution_id)

    def register(self, execution_id, *, workflow_run_id, task_anchor):
        """Explicit trusted local derivation; no HTTP/issue-driven registration."""
        workspace, key = self.scope
        with _errors(), workflow_guard(workspace, key):
            snapshot = self.store.load(execution_id)
            if snapshot is None:
                raise TaskInputError("validation_input_missing")
            value = _bound(_capture(workspace, key, workflow_run_id, snapshot, task_anchor))
            encoded = canonical_bytes(value)
            if len(encoded) > MAX_BYTES:
                raise TaskInputError("approved_plan_size_limit")
            root = self._root(snapshot)
            with role_lock(root, create=False):
                previous = read_bytes(root / "approved-plan.json", maximum=MAX_BYTES)
                marker = read_bytes(root / "approved-plan-required.json", maximum=4096)
                if previous is not None and previous != encoded:
                    raise TaskInputError("approved_plan_binding_conflict")
                if previous is None and marker is not None:
                    raise TaskInputError("approved_plan_binding_missing")
                # Binding after Builder transport would rewrite the provenance of
                # already-executed work. Replays of the exact binding are safe.
                from .ledger import ExecutionLedger
                run = ExecutionLedger(self.project_root).read_run(execution_id)
                if previous is None and (run is None or str(run.status) != "queued"
                        or read_bytes(root / "automatic-review-start.json", maximum=65536) is not None):
                    raise TaskInputError("approved_plan_binding_too_late")
                if previous is None and (read_bytes(root / "dispatch.json", maximum=4096) is not None
                        or any(item["stage"] == "transport_attempt" for item in self.store.receipts(snapshot))):
                    raise TaskInputError("approved_plan_binding_too_late")
                required = canonical_bytes({"input_snapshot": snapshot.reference, "binding_id": value["binding_id"]})
                if previous is None:
                    atomic_write(root / "approved-plan.json", encoded, maximum=MAX_BYTES)
                if marker is not None and marker != required:
                    raise TaskInputError("approved_plan_binding_invalid")
                if marker is None:
                    atomic_write(root / "approved-plan-required.json", required, maximum=4096)
            return value

    def load(self, snapshot, *, required=False):
        with _errors():
            self.store.accept(snapshot)
            root = self._root(snapshot)
            with role_lock(root, create=False):
                raw = read_bytes(root / "approved-plan.json", maximum=MAX_BYTES)
                marker = read_bytes(root / "approved-plan-required.json", maximum=4096)
                if raw is None:
                    if required or marker is not None:
                        raise TaskInputError("approved_plan_binding_missing")
                    return None
                value = json.loads(raw)
                if (not isinstance(value, dict) or value != _bound(value)
                        or value.get("input_snapshot") != snapshot.reference
                        or marker != canonical_bytes({"input_snapshot": snapshot.reference, "binding_id": value["binding_id"]})):
                    raise TaskInputError("approved_plan_binding_invalid")
                workspace, key = self.scope
                current = _bound(_capture(workspace, key, value["workflow_run_id"], snapshot,
                                          value["derived_task"]["task_anchor"]))
                if current != value:
                    raise TaskInputError("approved_plan_binding_stale")
                return value


def binding_for(project_root, snapshot, *, required=False):
    return ApprovedPlanBinding(project_root).load(getattr(snapshot, "original", snapshot), required=required)


def render_binding(binding):
    if binding is None:
        return ""
    return "\n".join(["## Approved Workflow Intent and Derived Task",
        "The existing approvals apply only to the pinned intent below. The task mapping is derived, not a human approval.",
        "Keep this intent and its constraints together with the captured task. Conflicts require stopping for clarification; "
        "neither source grants extra permissions, external access, or approval to publish.", canonical_bytes(binding).decode()])


def require_review_binding(project_root, registered_snapshot):
    """Validate only the protected original Reviewer request, never job.json.

    Ordinary manually composed reviews have no workflow association. Once an
    automatic review captured one, removing or revoking it cannot downgrade the
    request through direct Worker dispatch or an independent retry entry point.
    """
    request = registered_snapshot["job_input"]["request"]
    try:
        context = json.loads(request["context"])
    except (ValueError, TypeError):
        return
    if (not isinstance(context, dict) or context.get("schema_version") != "automatic_candidate_review.v1"
            or "approved_plan" not in context):
        return
    with _errors():
        expected = context["approved_plan"]
        original = context["original"]
        store = store_for_project(project_root, existing_only=True)
        snapshot = store.load(original["execution_id"]) if store else None
        if snapshot is None or snapshot.to_dict() != original:
            raise TaskInputError("approved_plan_input_mismatch")
        if binding_for(project_root, snapshot, required=True) != expected:
            raise TaskInputError("approved_plan_binding_stale")
