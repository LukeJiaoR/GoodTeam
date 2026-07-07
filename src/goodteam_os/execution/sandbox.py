from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from goodteam_os.execution.models import (
    ExecutionActor,
    LedgerEventType,
    LedgerEvent,
)
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.path_claims import path_claims_to_dicts
from goodteam_os.execution.review_workbench_contract import json_ready, utc_now
from goodteam_os.execution.sandbox_policy import (
    ALLOWED_COMMANDS,
    COMMAND_TIMEOUT_SECONDS,
    SandboxGitAdapter,
    SubprocessSandboxGitAdapter,
    command_cwd,
    command_env,
    command_name,
    excerpt,
    is_shell_command,
    mode as normalize_mode,
    normalize_paths,
    paths_overlap,
    scrub_argv,
    within,
)
from goodteam_os.execution.sandbox_surface import (
    SANDBOX_SCHEMA_VERSION,
    read_json,
    sandbox_surface,
)


Clock = Callable[[], str]
__all__ = ["SandboxGitAdapter", "SandboxService", "sandbox_surface"]


class SandboxService:
    def __init__(
        self,
        project_root: Path,
        *,
        clock: Clock | None = None,
        git_adapter: SandboxGitAdapter | None = None,
    ):
        self.project_root = Path(project_root)
        self._clock = clock or utc_now
        self.git_adapter = git_adapter or SubprocessSandboxGitAdapter()
        self.ledger = ExecutionLedger(self.project_root)

    def create_sandbox(
        self,
        execution_id: str,
        *,
        claimed_paths: Sequence[str] | None = None,
        path_claims: Sequence[Any] | None = None,
        path_claim_warnings: Sequence[str] | None = None,
        mode: str = "workspace",
        base_ref: str = "HEAD",
    ) -> dict[str, Any]:
        self._require_run(execution_id)
        try:
            normalized_mode = normalize_mode(mode)
            paths = normalize_paths(claimed_paths or [])
            claims = path_claims_to_dicts(list(path_claims or []))
            warnings = [str(warning) for warning in path_claim_warnings or [] if str(warning).strip()]
            metadata = self._metadata(execution_id, normalized_mode, paths, claims, warnings)
            conflicts = self._conflict_risks(execution_id, paths)
        except Exception as exc:
            metadata = self._metadata(execution_id, "workspace", [], [], [])
            return self._create_failed(execution_id, metadata, exc, reason="sandbox_create_failed")
        metadata["conflict_risk"] = bool(conflicts)
        metadata["conflict_risks"] = conflicts
        try:
            self._prepare_workspace(metadata, normalized_mode, base_ref)
        except Exception as exc:
            reason = "worktree_create_failed" if normalized_mode == "worktree" else "workspace_create_failed"
            return self._create_failed(execution_id, metadata, exc, reason=reason)
        if conflicts:
            self._append_event(
                execution_id,
                "sandbox_conflict_risk_detected",
                {
                    "conflict_risks": conflicts,
                    "claimed_paths": metadata["claimed_paths"],
                    "path_claims": metadata["path_claims"],
                    "path_claim_warnings": metadata["path_claim_warnings"],
                },
            )
        self._write_metadata(execution_id, metadata)
        self._append_event(execution_id, "sandbox_created", _created_payload(metadata))
        return json_ready(metadata)

    def get_sandbox(self, execution_id: str) -> dict[str, Any]:
        metadata = self._read_metadata(execution_id)
        if metadata is None:
            return {"available": False, "reason": "sandbox_evidence_missing"}
        return json_ready(metadata)

    def retain_sandbox(self, execution_id: str, *, reason: str = "pr_pending") -> dict[str, Any]:
        self._require_run(execution_id)
        metadata = self._read_metadata(execution_id)
        if metadata is None:
            return {"available": False, "reason": "sandbox_evidence_missing"}
        if metadata.get("status") == "destroyed":
            return json_ready({**metadata, "status": "already_destroyed"})
        metadata["status"] = "retained_for_pr"
        metadata["retained_at"] = self._clock()
        metadata["retention_reason"] = reason
        self._write_metadata(execution_id, metadata)
        self._append_event(
            execution_id,
            "sandbox_retained_for_pr",
            {**_created_payload(metadata), "reason": reason},
        )
        return json_ready(metadata)

    def destroy_sandbox(self, execution_id: str) -> dict[str, Any]:
        self._require_run(execution_id)
        metadata = self._read_metadata(execution_id)
        if metadata is None:
            return {"available": False, "reason": "sandbox_evidence_missing"}
        if metadata.get("status") == "destroyed":
            return json_ready({**metadata, "status": "already_destroyed"})
        requested_at = self._clock()
        previous_status = str(metadata.get("status", "unknown"))
        metadata["cleanup_requested_at"] = requested_at
        metadata["cleanup_requested_from_status"] = previous_status
        self._write_metadata(execution_id, metadata)
        self._append_event(
            execution_id,
            "sandbox_cleanup_requested",
            {
                **_created_payload(metadata),
                "status": previous_status,
                "requested_at": requested_at,
            },
        )
        try:
            if metadata.get("mode") == "worktree":
                self.git_adapter.remove_worktree(
                    repo_root=self.project_root,
                    worktree_path=Path(str(metadata["worktree_path"])),
                )
            metadata["status"] = "destroyed"
            metadata["destroyed_at"] = self._clock()
            self._write_metadata(execution_id, metadata)
        except Exception as exc:
            return self._destroy_failed(execution_id, metadata, exc)
        self._append_event(execution_id, "sandbox_destroyed", _created_payload(metadata))
        return json_ready(metadata)

    def run_command(
        self,
        execution_id: str,
        argv: Sequence[str] | str,
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        metadata = self._active_metadata(execution_id)
        run_env, redacted_keys, secret_values = command_env(env or {})
        rejected = self._rejection(metadata, argv, cwd)
        if rejected is not None:
            return self._record_rejection(
                execution_id,
                argv,
                rejected,
                redacted_keys=redacted_keys,
                secret_values=secret_values,
            )
        assert not isinstance(argv, str)
        run_cwd = command_cwd(metadata, cwd)
        try:
            completed = subprocess.run(
                list(argv),
                cwd=run_cwd,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            return self._record_command_failure(
                execution_id,
                argv,
                run_cwd,
                "timeout",
                redacted_keys=redacted_keys,
                secret_values=secret_values,
                stdout=_failure_output(exc.output),
                stderr=_failure_output(exc.stderr),
                error=f"Command timed out after {exc.timeout} seconds",
            )
        except OSError as exc:
            return self._record_command_failure(
                execution_id,
                argv,
                run_cwd,
                "spawn_failed",
                redacted_keys=redacted_keys,
                secret_values=secret_values,
                error=str(exc),
            )
        except Exception as exc:
            return self._record_command_failure(
                execution_id,
                argv,
                run_cwd,
                "execution_failed",
                redacted_keys=redacted_keys,
                secret_values=secret_values,
                error=str(exc),
            )
        result = {
            "status": "completed",
            "argv": scrub_argv(list(argv), secret_values),
            "cwd": str(run_cwd),
            "exit_code": completed.returncode,
            "stdout_excerpt": excerpt(completed.stdout, secret_values),
            "stderr_excerpt": excerpt(completed.stderr, secret_values),
            "redacted_env_keys": redacted_keys,
        }
        self._append_event(execution_id, "sandbox_command_executed", result)
        return json_ready(result)

    def _prepare_workspace(self, metadata: dict[str, Any], mode: str, base_ref: str) -> None:
        workspace_path = Path(metadata["workspace_path"])
        if mode == "workspace":
            workspace_path.mkdir(parents=True, exist_ok=True)
            return
        self.git_adapter.create_worktree(
            repo_root=self.project_root,
            worktree_path=Path(metadata["worktree_path"]),
            branch_name=str(metadata["branch_name"]),
            base_ref=base_ref,
        )

    def _create_failed(
        self,
        execution_id: str,
        metadata: dict[str, Any],
        exc: Exception,
        *,
        reason: str,
    ) -> dict[str, Any]:
        result = {
            "status": "failed",
            "reason": reason,
            "error": excerpt(str(exc), []),
            **_created_payload(metadata),
        }
        self._append_event(execution_id, "sandbox_create_failed", result)
        return json_ready(result)

    def _destroy_failed(
        self,
        execution_id: str,
        metadata: dict[str, Any],
        exc: Exception,
    ) -> dict[str, Any]:
        metadata["status"] = "cleanup_failed"
        metadata["cleanup_failed_at"] = self._clock()
        metadata["cleanup_failure_reason"] = "sandbox_destroy_failed"
        metadata["cleanup_failure_error"] = excerpt(str(exc), [])
        self._write_metadata(execution_id, metadata)
        result = {
            "status": "cleanup_failed",
            "reason": "sandbox_destroy_failed",
            "error": metadata["cleanup_failure_error"],
            "cleanup_recovery_required": True,
            "recovery_state": "sandbox_cleanup_recovery_required",
            **_created_payload(metadata),
        }
        self._append_event(execution_id, "sandbox_destroy_failed", result)
        return json_ready(result)

    def _metadata(
        self,
        execution_id: str,
        mode: str,
        claimed_paths: list[str],
        path_claims: list[dict[str, str]],
        path_claim_warnings: list[str],
    ) -> dict[str, Any]:
        root = self.project_root / ".goodteam" / "sandboxes" / execution_id
        worktree_path = root / "worktree" if mode == "worktree" else None
        workspace_path = worktree_path or root / "workspace"
        return {
            "available": True,
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "execution_id": execution_id,
            "sandbox_id": f"sandbox_{execution_id}",
            "status": "active",
            "mode": mode,
            "workspace_path": str(workspace_path),
            "worktree_path": str(worktree_path) if worktree_path else "",
            "branch_name": f"sandbox/{execution_id}",
            "claimed_paths": claimed_paths,
            "path_claims": path_claims,
            "path_claim_warnings": path_claim_warnings,
            "conflict_risk": False,
            "conflict_risks": [],
            "created_at": self._clock(),
        }

    def _conflict_risks(self, execution_id: str, claimed_paths: list[str]) -> list[dict[str, str]]:
        risks = []
        for metadata in self._active_sandboxes():
            other_id = str(metadata.get("execution_id", ""))
            if other_id == execution_id:
                continue
            for path in claimed_paths:
                for other in metadata.get("claimed_paths", []):
                    if paths_overlap(path, str(other)):
                        exact = path == str(other)
                        risks.append(
                            {
                                "execution_id": other_id,
                                "path": path,
                                "conflicting_path": str(other),
                                "confidence": "high" if exact else "medium",
                                "reason": "exact_path_overlap" if exact else "path_prefix_overlap",
                            }
                        )
        return risks

    def _active_sandboxes(self) -> list[dict[str, Any]]:
        root = self.project_root / ".goodteam" / "executions"
        if not root.is_dir():
            return []
        return [
            payload
            for path in sorted(root.glob("*/sandbox.json"))
            if (payload := read_json(path)) is not None and payload.get("status") == "active"
        ]

    def _rejection(self, metadata: dict[str, Any], argv: Sequence[str] | str, cwd: Any) -> str | None:
        if isinstance(argv, str):
            return "shell_string_rejected"
        values = [str(item) for item in argv]
        if not values:
            return "empty_command"
        if is_shell_command(values):
            return "shell_command_rejected"
        if any(token in value for value in values for token in ("&&", ";", "|")):
            return "chained_command_rejected"
        if command_name(values[0]) not in ALLOWED_COMMANDS:
            return "unknown_command"
        if not within(Path(command_cwd(metadata, cwd)), Path(metadata["workspace_path"])):
            return "cwd_outside_sandbox"
        return None

    def _record_rejection(
        self,
        execution_id: str,
        argv: Sequence[str] | str,
        reason: str,
        *,
        redacted_keys: list[str],
        secret_values: list[str],
    ) -> dict[str, Any]:
        result = {
            "status": "rejected",
            "reason": reason,
            "argv": scrub_argv(argv, secret_values),
            "redacted_env_keys": redacted_keys,
        }
        self._append_event(execution_id, "sandbox_command_rejected", result)
        return json_ready(result)

    def _record_command_failure(
        self,
        execution_id: str,
        argv: Sequence[str],
        cwd: Path,
        reason: str,
        *,
        redacted_keys: list[str],
        secret_values: list[str],
        stdout: str = "",
        stderr: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        result = {
            "status": "failed",
            "reason": reason,
            "argv": scrub_argv(list(argv), secret_values),
            "cwd": str(cwd),
            "exit_code": None,
            "stdout_excerpt": excerpt(stdout, secret_values),
            "stderr_excerpt": excerpt(stderr, secret_values),
            "error_excerpt": excerpt(error, secret_values),
            "redacted_env_keys": redacted_keys,
        }
        self._append_event(execution_id, "sandbox_command_failed", result)
        return json_ready(result)

    def _append_event(self, execution_id: str, transition: str, payload: dict[str, Any]) -> None:
        run = self._require_run(execution_id)
        event_number = len(self.ledger.read_events(execution_id)) + 1
        event = LedgerEvent(
            event_id=f"{execution_id}:{event_number:04d}:status_changed:{transition}",
            execution_id=execution_id,
            task_id=run.task_id,
            event_type=LedgerEventType.STATUS_CHANGED,
            actor=ExecutionActor.SYSTEM,
            payload={
                "transition": transition,
                "source": "sandbox",
                "schema_version": SANDBOX_SCHEMA_VERSION,
                "execution_id": execution_id,
                **payload,
            },
            created_at=self._clock(),
        )
        self.ledger.append_event(event)

    def _require_run(self, execution_id: str) -> Any:
        run = self.ledger.read_run(execution_id)
        if run is None:
            raise ValueError(f"Execution run not found: {execution_id}")
        return run

    def _active_metadata(self, execution_id: str) -> dict[str, Any]:
        metadata = self._read_metadata(execution_id)
        if metadata is None or metadata.get("status") != "active":
            raise ValueError(f"Active sandbox not found: {execution_id}")
        return metadata

    def _read_metadata(self, execution_id: str) -> dict[str, Any] | None:
        return read_json(self._metadata_path(execution_id))

    def _write_metadata(self, execution_id: str, metadata: dict[str, Any]) -> None:
        path = self._metadata_path(execution_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _metadata_path(self, execution_id: str) -> Path:
        return self.project_root / ".goodteam" / "executions" / execution_id / "sandbox.json"


def _created_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "sandbox_id": metadata["sandbox_id"],
        "mode": metadata["mode"],
        "workspace_path": metadata["workspace_path"],
        "worktree_path": metadata["worktree_path"],
        "branch_name": metadata["branch_name"],
        "claimed_paths": metadata["claimed_paths"],
        "path_claims": metadata.get("path_claims", []),
        "path_claim_warnings": metadata.get("path_claim_warnings", []),
        "conflict_risk": metadata["conflict_risk"],
    }


def _failure_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
