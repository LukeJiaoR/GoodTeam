from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.review_workbench_contract import json_ready
from goodteam_os.execution.sandbox_policy import REDACTION_MARKER


SANDBOX_SCHEMA_VERSION = "sandbox.v1"
RUNTIME_SURFACE_KEYS = (
    "runtime_adapter",
    "runtime_binary",
    "runtime_version",
    "runtime_version_warning",
    "runtime_version_warning_reason",
    "runtime_version_failure_reason",
    "runtime_model",
    "runtime_cwd",
    "runtime_workspace_mode",
    "runtime_workspace_status",
    "runtime_sandbox_mode",
    "runtime_approval_policy",
    "runtime_unsafe_mode",
    "runtime_policy_warning",
    "runtime_policy_warning_reason",
    "runtime_transcript_path",
    "stdout_path",
    "stderr_path",
    "result_path",
)


def sandbox_surface(
    project_root: Path,
    execution_id: str,
    events: list[LedgerEvent],
    *,
    worker_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sandbox_events = [event for event in events if event.payload.get("source") == "sandbox"]
    runtime = _runtime_surface(worker_job)
    if not sandbox_events:
        if runtime:
            return json_ready(
                {
                    "available": True,
                    "sandbox_available": False,
                    "reason": "sandbox_evidence_missing",
                    "schema_version": SANDBOX_SCHEMA_VERSION,
                    "status": "sandbox_evidence_missing",
                    "mode": "",
                    "workspace_path": "",
                    "worktree_path": "",
                    "branch_name": "",
                    "cleanup_required": False,
                    "cleanup_warning_reason": "",
                    "cleanup_recovery_required": False,
                    "recovery_state": "",
                    "cleanup_failure_reason": "",
                    "cleanup_failure_error": "",
                    "runtime": runtime,
                    "claimed_paths": [],
                    "path_claims": [],
                    "path_claim_warnings": [],
                    "commands": [],
                    "conflict_risks": [],
                    "events": [],
                }
            )
        return {"available": False, "reason": "sandbox_evidence_missing"}
    metadata = read_json(Path(project_root) / ".goodteam" / "executions" / execution_id / "sandbox.json") or {}
    status = str(metadata.get("status", "unknown"))
    cleanup_required, cleanup_reason = _cleanup_warning(status)
    cleanup_recovery_required = status == "cleanup_failed"
    return json_ready(
        {
            "available": True,
            "sandbox_available": True,
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "status": status,
            "mode": metadata.get("mode", ""),
            "workspace_path": metadata.get("workspace_path", ""),
            "worktree_path": metadata.get("worktree_path", ""),
            "branch_name": metadata.get("branch_name", ""),
            "cleanup_required": cleanup_required,
            "cleanup_warning_reason": cleanup_reason,
            "cleanup_recovery_required": cleanup_recovery_required,
            "recovery_state": "sandbox_cleanup_recovery_required" if cleanup_recovery_required else "",
            "cleanup_failure_reason": metadata.get("cleanup_failure_reason", ""),
            "cleanup_failure_error": metadata.get("cleanup_failure_error", ""),
            "runtime": runtime,
            "claimed_paths": list(metadata.get("claimed_paths", [])),
            "path_claims": [
                dict(claim) for claim in metadata.get("path_claims", []) if isinstance(claim, dict)
            ],
            "path_claim_warnings": list(metadata.get("path_claim_warnings", [])),
            "commands": [
                _command_surface(event)
                for event in sandbox_events
                if _is_command_event(event)
            ],
            "conflict_risks": [
                risk for event in sandbox_events for risk in _event_conflicts(event)
            ],
            "events": [event.to_dict() for event in sandbox_events],
        }
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, dict) else None


def _is_command_event(event: LedgerEvent) -> bool:
    return event.payload.get("transition") in {
        "sandbox_command_executed",
        "sandbox_command_failed",
        "sandbox_command_rejected",
    }


def _command_surface(event: LedgerEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "transition": payload.get("transition", ""),
        "status": payload.get("status", ""),
        "reason": payload.get("reason", ""),
        "argv": payload.get("argv", []),
        "cwd": payload.get("cwd", ""),
        "exit_code": payload.get("exit_code"),
        "stdout_excerpt": payload.get("stdout_excerpt", ""),
        "stderr_excerpt": payload.get("stderr_excerpt", ""),
        "error_excerpt": payload.get("error_excerpt", ""),
        "redacted_env_keys": payload.get("redacted_env_keys", []),
        "redaction_marker": REDACTION_MARKER
        if REDACTION_MARKER in str(payload.get("stdout_excerpt", ""))
        or REDACTION_MARKER in str(payload.get("stderr_excerpt", ""))
        or REDACTION_MARKER in str(payload.get("error_excerpt", ""))
        else "",
    }


def _event_conflicts(event: LedgerEvent) -> list[dict[str, str]]:
    risks = event.payload.get("conflict_risks")
    if not isinstance(risks, list):
        return []
    return [dict(risk) for risk in risks if isinstance(risk, dict)]


def _cleanup_warning(status: str) -> tuple[bool, str]:
    if status == "retained_for_pr":
        return True, "sandbox_retained_for_pr"
    if status == "cleanup_failed":
        return True, "sandbox_cleanup_recovery_required"
    return False, ""


def _runtime_surface(worker_job: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(worker_job, dict):
        return {}
    evidence = worker_job.get("invocation_evidence")
    source = evidence if isinstance(evidence, dict) else worker_job
    runtime: dict[str, Any] = {}
    for key in RUNTIME_SURFACE_KEYS:
        value = source.get(key)
        if value not in (None, ""):
            runtime[key] = value
    return runtime
