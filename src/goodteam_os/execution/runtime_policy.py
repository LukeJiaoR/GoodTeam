from __future__ import annotations

from pathlib import Path
from typing import Any

from goodteam_os.execution.models import ExecutionRun, ExecutionTask, RuntimePolicy


RUNTIME_POLICY_TRANSITION = "runtime_policy_preflight"
REAL_EXTERNAL_BACKENDS = {"codex", "anthropic", "claude", "gemini", "agy", "antigravity", "opencode"}
LOCAL_BACKENDS = {"human", "manual", "local"}


def build_runtime_policy(
    *,
    project_root: Path,
    task: ExecutionTask | None,
    run: ExecutionRun,
    sandbox_enabled: bool,
    sandbox_mode: str,
    operator_flags: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> RuntimePolicy:
    flags = dict(operator_flags or {})
    backend_mode = str(run.adapter or "").strip() or "unknown"
    backend_class = classify_worker_backend(backend_mode, dry_run=dry_run)
    external_requested = backend_class == "real-external"
    external_allowed = external_requested
    external_blocked = False
    if dry_run and external_requested:
        external_allowed = False
        external_blocked = True
    elif bool(flags.get("block_external_invocation")):
        external_allowed = False
        external_blocked = external_requested

    contract = task.task_contract if task is not None else None
    contract_payload = contract.to_dict() if contract is not None else {}
    path_scope = _contract_path_scope(contract_payload)
    warnings = []
    if contract is None:
        warnings.append(
            {
                "code": "task_contract_missing",
                "message": "Runtime policy could not derive task-contract allowances.",
            }
        )

    return RuntimePolicy(
        task_contract={
            "available": contract is not None,
            "schema_version": str(contract_payload.get("schema_version", "")),
            "allowed_summary": str(contract_payload.get("summary", "")),
            "constraints": list(contract_payload.get("constraints", [])),
        },
        worker_backend={
            "mode": backend_mode,
            "class": backend_class,
            "source": "execution_run.adapter",
        },
        external_invocation={
            "requested": external_requested,
            "allowed": external_allowed,
            "blocked": external_blocked,
            "reason": _external_invocation_reason(
                backend_class,
                backend_mode=backend_mode,
                blocked=external_blocked,
                dry_run=dry_run,
            ),
        },
        sandbox={
            "enabled": bool(sandbox_enabled),
            "mode": _effective_sandbox_mode(project_root, enabled=sandbox_enabled, mode=sandbox_mode),
            "workspace_mode": _effective_sandbox_mode(project_root, enabled=sandbox_enabled, mode=sandbox_mode),
            "source": "execution_orchestrator",
        },
        path_scope=path_scope,
        verification={
            "expectations": list(contract_payload.get("verification_expectations", [])),
            "full_smoke_compatible": _full_smoke_compatible(
                backend_class,
                external_allowed=external_allowed,
                dry_run=dry_run,
            ),
            "full_smoke_reason": _full_smoke_reason(
                backend_class,
                external_allowed=external_allowed,
                dry_run=dry_run,
            ),
        },
        operator_flags=flags,
        sources=["task_contract", "execution_run.adapter", "sandbox_settings", "operator_flags"],
        warnings=warnings,
    )


def classify_worker_backend(mode: str, *, dry_run: bool = False) -> str:
    if dry_run:
        return "dry-run"
    normalized = (mode or "").strip().lower()
    if normalized == "fake":
        return "fake"
    if normalized == "noop":
        return "noop"
    if normalized in LOCAL_BACKENDS:
        return "local"
    if normalized in REAL_EXTERNAL_BACKENDS:
        return "real-external"
    return "local"


def runtime_policy_from_events(events: list[Any]) -> dict[str, Any] | None:
    for event in events:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            continue
        policy = payload.get("runtime_policy")
        if not isinstance(policy, dict):
            continue
        if policy.get("schema_version") == "runtime_policy.v1":
            return dict(policy)
    return None


def runtime_policy_fallback_manifest() -> dict[str, Any]:
    return {
        "available": False,
        "schema_version": "",
        "task_contract": {},
        "worker_backend": {},
        "external_invocation": {},
        "sandbox": {},
        "path_scope": {},
        "verification": {},
        "operator_flags": {},
        "sources": [],
        "warnings": [],
        "fallback_warning": {
            "code": "runtime_policy_missing",
            "message": "Legacy execution has no persisted runtime_policy.v1 preflight data.",
        },
    }


def runtime_policy_manifest(events: list[Any]) -> dict[str, Any]:
    policy = runtime_policy_from_events(events)
    if policy is None:
        return runtime_policy_fallback_manifest()
    return {
        "available": True,
        "schema_version": str(policy.get("schema_version", "")),
        "task_contract": dict(policy.get("task_contract", {})),
        "worker_backend": dict(policy.get("worker_backend", {})),
        "external_invocation": dict(policy.get("external_invocation", {})),
        "sandbox": dict(policy.get("sandbox", {})),
        "path_scope": dict(policy.get("path_scope", {})),
        "verification": dict(policy.get("verification", {})),
        "operator_flags": dict(policy.get("operator_flags", {})),
        "sources": list(policy.get("sources", [])),
        "warnings": list(policy.get("warnings", [])),
        "fallback_warning": {},
    }


def _contract_path_scope(contract_payload: dict[str, Any]) -> dict[str, Any]:
    scope = contract_payload.get("path_scope")
    scope = scope if isinstance(scope, dict) else {}
    return {
        "policy": "task_contract.path_scope" if scope else "none",
        "focus_paths": _string_list(scope.get("focus_paths")),
        "allowed_paths": _string_list(scope.get("allowed_paths")),
        "excluded_paths": _string_list(scope.get("excluded_paths")),
    }


def _effective_sandbox_mode(project_root: Path, *, enabled: bool, mode: str) -> str:
    if not enabled:
        return "disabled"
    normalized = (mode or "auto").strip().lower()
    if normalized != "auto":
        return normalized
    return "worktree" if (Path(project_root) / ".git").exists() else "workspace"


def _external_invocation_reason(
    backend_class: str,
    *,
    backend_mode: str,
    blocked: bool,
    dry_run: bool,
) -> str:
    if blocked and dry_run:
        return "dry-run blocks real external worker invocation"
    if blocked:
        return "operator flag blocks real external worker invocation"
    if backend_class == "real-external":
        return "real external worker backend selected by operator"
    if backend_class == "fake":
        return "fake worker backend does not invoke an external agent"
    if backend_class == "noop":
        return "noop worker backend does not invoke an external agent"
    if backend_class == "dry-run":
        return "dry-run preflight does not invoke an external agent"
    return f"{backend_mode} worker backend runs without external agent invocation"


def _full_smoke_compatible(backend_class: str, *, external_allowed: bool, dry_run: bool) -> bool:
    if dry_run:
        return True
    if backend_class in {"fake", "noop", "local", "dry-run"}:
        return True
    return external_allowed


def _full_smoke_reason(backend_class: str, *, external_allowed: bool, dry_run: bool) -> str:
    if dry_run:
        return "dry-run full smoke never invokes a real worker"
    if backend_class in {"fake", "noop", "local", "dry-run"} or external_allowed:
        return "fake/noop/local/dry-run compatible or real worker explicitly allowed"
    return "real external worker requires explicit full-smoke opt-in"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value]
