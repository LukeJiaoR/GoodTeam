from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goodteam_os.execution.models import LedgerEvent
from goodteam_os.execution.review_workbench_contract import json_ready
from goodteam_os.execution.sandbox_policy import REDACTION_MARKER


SANDBOX_SCHEMA_VERSION = "sandbox.v1"


def sandbox_surface(project_root: Path, execution_id: str, events: list[LedgerEvent]) -> dict[str, Any]:
    sandbox_events = [event for event in events if event.payload.get("source") == "sandbox"]
    if not sandbox_events:
        return {"available": False, "reason": "sandbox_evidence_missing"}
    metadata = read_json(Path(project_root) / ".goodteam" / "executions" / execution_id / "sandbox.json") or {}
    return json_ready(
        {
            "available": True,
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "status": metadata.get("status", "unknown"),
            "mode": metadata.get("mode", ""),
            "workspace_path": metadata.get("workspace_path", ""),
            "worktree_path": metadata.get("worktree_path", ""),
            "branch_name": metadata.get("branch_name", ""),
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
    return event.payload.get("transition") in {"sandbox_command_executed", "sandbox_command_rejected"}


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
        "redacted_env_keys": payload.get("redacted_env_keys", []),
        "redaction_marker": REDACTION_MARKER
        if REDACTION_MARKER in str(payload.get("stdout_excerpt", ""))
        or REDACTION_MARKER in str(payload.get("stderr_excerpt", ""))
        else "",
    }


def _event_conflicts(event: LedgerEvent) -> list[dict[str, str]]:
    risks = event.payload.get("conflict_risks")
    if not isinstance(risks, list):
        return []
    return [dict(risk) for risk in risks if isinstance(risk, dict)]
