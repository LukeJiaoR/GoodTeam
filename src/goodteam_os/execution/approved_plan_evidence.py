"""Read-only, bounded approved-intent metadata for the existing Workbench.

A valid binding proves a pinned local association, not semantic plan compliance.
Never project full intent, task bodies, filesystem paths or Worker context.
"""
import json
from pathlib import Path

from goodteam_os.memory_storage import read_bytes
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.task_start import registered_project_scope

from .approved_plan import MAX_BYTES, _bound, _capture
from .task_snapshot import TaskInputError, canonical_bytes, execution_key
from .task_snapshot_store import TaskSnapshotStore, _project_bucket


def approved_plan_evidence(project_root: Path, execution_id: str) -> dict:
    result = {"status": "legacy_unverified", "reason": "task_input_not_captured",
              "semantic_compliance": "not_independently_verified"}
    try:
        scope = registered_project_scope(project_root)
        if scope is None:
            retained = _project_bucket(project_root) / execution_key(execution_id)
            if retained.exists() or retained.is_symlink():
                raise TaskInputError("approved_plan_unavailable")
            return result
        workspace, key = scope
        store = TaskSnapshotStore(workspace=workspace, project_key=key, worker_root=project_root)
        root = store.root / execution_key(execution_id)
        if root.is_symlink():
            raise TaskInputError("approved_plan_unavailable")
        if not root.exists():
            return result
        # Inspection must not repair missing lock files. Use the existing safe
        # snapshot parser without mutation-capable locking wrappers, and reject
        # movement after validating the retained bytes. This grants no authority.
        snapshot = store._read(root)
        raw = read_bytes(root / "approved-plan.json", maximum=MAX_BYTES)
        marker = read_bytes(root / "approved-plan-required.json", maximum=4096)
        if snapshot is None:
            if raw is not None or marker is not None:
                raise TaskInputError("approved_plan_input_missing")
            return result
        if raw is None:
            if marker is not None:
                raise TaskInputError("approved_plan_binding_missing")
            return {**result, "status": "unbound", "reason": "approved_plan_not_bound"}
        binding = json.loads(raw)
        if (not isinstance(binding, dict) or binding != _bound(binding)
                or binding.get("input_snapshot") != snapshot.reference
                or marker != canonical_bytes({"input_snapshot": snapshot.reference, "binding_id": binding["binding_id"]})):
            raise TaskInputError("approved_plan_binding_invalid")
        current = _bound(_capture(workspace, key, binding["workflow_run_id"], snapshot,
                                  binding["derived_task"]["task_anchor"]))
        if current != binding:
            raise TaskInputError("approved_plan_binding_stale")
        after = store._read(root)
        if (after is None or after.data != snapshot.data
                or read_bytes(root / "approved-plan.json", maximum=MAX_BYTES) != raw
                or read_bytes(root / "approved-plan-required.json", maximum=4096) != marker):
            raise TaskInputError("approved_plan_binding_stale")
        derived = binding["derived_task"]
        return {**result, "status": "current", "reason": "approved_plan_binding_current",
                "binding_id": binding["binding_id"], "workflow_run_id": binding["workflow_run_id"],
                "profile": binding["profile"], "input_snapshot": snapshot.reference,
                "approvals": [{"gate": item["gate"], "artifact_key": item["artifact_key"],
                               "artifact_version": item["artifact_version"]} for item in binding["approvals"]],
                "derived_task": {"heading": " ".join(derived["task_anchor"].split())[:160],
                                 "artifact_version": derived["artifact_version"],
                                 "human_approved": False}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, SkillError) as exc:
        # Error messages may contain paths or external content. Only fixed codes
        # from the trusted binding validator are allowed in this projection.
        reason = exc.code if isinstance(exc, TaskInputError) else "approved_plan_unavailable"
        if reason not in _REASONS:
            reason = "approved_plan_unavailable"
        return {**result, "status": "unavailable", "reason": reason}


_REASONS = {
    "approved_plan_unavailable": "Plan evidence could not be verified.",
    "approved_plan_input_missing": "The original task input for the recorded binding is missing.",
    "approved_plan_binding_missing": "The recorded plan binding is missing.",
    "approved_plan_binding_invalid": "Plan binding evidence is incomplete or invalid.",
    "approved_plan_binding_stale": "The plan or selected task changed after binding.",
    "approved_plan_reapproval_required": "The current intent no longer has matching approval evidence.",
    "approved_plan_run_not_current": "The bound workflow is no longer current.",
    "approved_plan_artifact_missing": "A required plan artifact is missing.",
    "approved_plan_artifact_scope_mismatch": "Plan artifact scope could not be verified.",
    "approved_plan_task_membership_missing": "The selected task no longer matches the plan.",
    "approved_plan_operation_reused": "Approval decision identity could not be verified.",
    "approved_plan_history_limit": "Approval history exceeds the verification limit.",
}


def approved_plan_summary(value: dict) -> str:
    status = value.get("status")
    if status == "current":
        return "Approved intent: current binding; task compliance remains unverified."
    if status == "unbound":
        return "Approved intent: no plan binding recorded."
    if status == "unavailable":
        return "Approved intent: unavailable. " + _REASONS.get(value.get("reason"), _REASONS["approved_plan_unavailable"])
    return "Approved intent: no captured evidence for this execution."


def approved_plan_details(value: dict) -> list[tuple[str, str]]:
    if value.get("status") != "current":
        return [("Next step", "Inspect the existing workflow approvals and preserved binding; this view cannot approve or rebind a task.")]
    rows = [("Workflow", value["workflow_run_id"]), ("Profile", value["profile"]),
            ("Binding", value["binding_id"])]
    rows.extend((item["gate"] + " approved artifact", item["artifact_key"] + " · " + item["artifact_version"])
                for item in value["approvals"])
    rows.extend([("Derived task", value["derived_task"]["heading"]),
                 ("Derived task artifact version", value["derived_task"]["artifact_version"]),
                 ("Meaning", "The task association is locally derived, not a separate human approval or proof that the result follows the plan.")])
    return rows
