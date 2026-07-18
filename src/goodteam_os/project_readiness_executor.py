from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import threading
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any
from uuid import uuid4

from goodteam_os.project_import import (
    mutate_project_readiness,
    mutate_project_readiness_root_failure,
    read_bound_project_readiness,
)
from goodteam_os.project_readiness import MODE_STEPS
from goodteam_os.projects import load_project_target
from goodteam_os.state import normalize_project_key
from goodteam_os.workers import worker_backend_local_preflight


STEP_DEFAULTS: dict[str, dict[str, object]] = {
    "project_record": {"unlocks": ["inspect_project_facts"]},
    "safe_preflight": {"unlocks": ["run_readiness_steps"]},
    "project_mode": {"unlocks": ["run_readiness_steps"]},
    "new_project_init": {"unlocks": ["project_brief"]},
    "project_brief": {"unlocks": ["worker_preflight"]},
    "repository_understanding": {"unlocks": ["command_verification"]},
    "command_verification": {"unlocks": ["constraints_confirmation"]},
    "constraints_confirmation": {"unlocks": ["worker_preflight"]},
    "worker_preflight": {"unlocks": ["start_workflow"]},
}
TERMINAL_EXECUTION_STATUSES = {"complete", "failed", "cancelled"}
ACTIVE_EXECUTION_STATUSES = {"running", "needs_input"}
MAX_ENTRIES = 1_500
MAX_DEPTH = 4
MAX_READ_BYTES = 256 * 1024
SKIPPED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}
DOCUMENT_NAMES = {
    "README",
    "README.md",
    "README.rst",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
}
ENTRY_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "manage.py",
    "index.js",
    "index.ts",
    "main.js",
    "main.ts",
    "main.tsx",
    "main.jsx",
}
MANIFEST_TECH = {
    "package.json": "Node.js / JavaScript",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java / Maven",
    "build.gradle": "Java / Gradle",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
}


class ReadinessExecutionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        recovery: list[str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.recovery = recovery or []


_RUNNERS: dict[tuple[str, str, str, int, str], threading.Thread] = {}
_RESERVATIONS: dict[tuple[str, str], int] = {}
_RUNNERS_GUARD = threading.Lock()
_PROCESS_TOKEN = uuid4().hex
RUNNER_LEASE = timedelta(seconds=30)
RUNNER_HEARTBEAT_SECONDS = 10.0


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reserve_runner(workspace: Path, key: str) -> tuple[str, str]:
    runner_key = (str(workspace), key)
    with _RUNNERS_GUARD:
        _RESERVATIONS[runner_key] = _RESERVATIONS.get(runner_key, 0) + 1
    return runner_key


def _release_runner(runner_key: tuple[str, str]) -> None:
    with _RUNNERS_GUARD:
        count = _RESERVATIONS.get(runner_key, 0)
        if count <= 1:
            _RESERVATIONS.pop(runner_key, None)
        else:
            _RESERVATIONS[runner_key] = count - 1


def _has_live_runner(
    project_runner_key: tuple[str, str],
    execution_id: str,
    attempt: int,
    current_step_id: str,
) -> bool:
    expected_stages = {
        "repository_understanding": {"_run_existing"},
        "new_project_init": {"_run_new"},
        "worker_preflight": {"_run_preflight", "_run_new"},
    }.get(current_step_id, set())
    with _RUNNERS_GUARD:
        if _RESERVATIONS.get(project_runner_key, 0):
            return True
        return any(
            runner.is_alive()
            for (*prefix, stage), runner in _RUNNERS.items()
            if tuple(prefix) == (*project_runner_key, execution_id, attempt)
            and stage in expected_stages
        )


def _root_identity(root: Path) -> dict[str, int]:
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise
        raise ReadinessExecutionError(
            "project_root_changed",
            "The imported project root path is no longer the same directory entry.",
            recovery=["reload_project", "choose_another_directory"],
        ) from exc
    try:
        value = os.fstat(descriptor)
        return {"device": value.st_dev, "inode": value.st_ino}
    finally:
        os.close(descriptor)


def _step(step: dict[str, object]) -> dict[str, object]:
    step_id = str(step.get("id", ""))
    return {
        "id": step_id,
        "project_key": step.get("project_key", ""),
        "canonical_root": step.get("canonical_root", ""),
        "execution_id": step.get("execution_id", ""),
        "status": str(step.get("status", "pending")),
        "required": bool(step.get("required", True)),
        "attempt": int(step.get("attempt", 0) or 0),
        "started_at": step.get("started_at"),
        "completed_at": step.get("completed_at"),
        "input_requirements": step.get("input_requirements"),
        "artifact_refs": list(step.get("artifact_refs", []) or []),
        "error": step.get("error"),
        "recovery": list(step.get("recovery", []) or []),
        "modifies_target_root": False,
        "invokes_model": False,
        "unlocks": list(
            step.get("unlocks", STEP_DEFAULTS.get(step_id, {}).get("unlocks", []))
            or []
        ),
    }


def upgrade_readiness(
    readiness: dict[str, object],
    root: Path,
    *,
    verify_root: bool = True,
) -> dict[str, object]:
    upgraded = dict(readiness)
    source_schema_version = int(upgraded.get("schema_version", 1) or 1)
    current_identity = _root_identity(root) if verify_root else None
    stored_identity = upgraded.get("root_identity")
    if current_identity is not None:
        if not isinstance(stored_identity, dict) or not all(
            isinstance(stored_identity.get(field), int) for field in ("device", "inode")
        ):
            raise ReadinessExecutionError(
                "project_root_identity_missing",
                "The imported project root identity is missing.",
                recovery=["reload_project", "choose_another_directory"],
            )
        if (
            stored_identity.get("device") != current_identity["device"]
            or stored_identity.get("inode") != current_identity["inode"]
        ):
            raise ReadinessExecutionError(
                "project_root_changed",
                "The project root no longer matches the directory that was imported.",
                recovery=["reload_project", "choose_another_directory"],
            )
    raw_steps = [value for value in upgraded.get("steps", []) if isinstance(value, dict)]
    if source_schema_version >= 2 and any(
        step.get("project_key") != upgraded.get("project_key")
        or step.get("canonical_root") != upgraded.get("canonical_root")
        for step in raw_steps
    ):
        raise ReadinessExecutionError(
            "readiness_step_binding_mismatch",
            "A readiness step is not bound to the requested project root.",
            recovery=["reload_project", "return_to_projects"],
        )
    upgraded["schema_version"] = 2
    upgraded["steps"] = [_step(value) for value in raw_steps]
    for step in upgraded["steps"]:
        if source_schema_version < 2:
            step["project_key"] = upgraded.get("project_key", "")
            step["canonical_root"] = upgraded.get("canonical_root", "")
        if step.get("id") in {"project_record", "safe_preflight", "project_mode"}:
            identity = stored_identity if isinstance(stored_identity, dict) else {}
            foundation_id = (
                f"foundation:{upgraded.get('project_key', '')}:"
                f"{identity.get('device', '')}:{identity.get('inode', '')}"
            )
            timestamp = str(upgraded.get("updated_at", "")) or None
            step["execution_id"] = step.get("execution_id") or foundation_id
            step["attempt"] = int(step.get("attempt", 0) or 1)
            step["started_at"] = step.get("started_at") or timestamp
            if step.get("status") == "complete":
                step["completed_at"] = step.get("completed_at") or timestamp
    upgraded.setdefault("execution", None)
    upgraded.setdefault("artifacts", {})
    upgraded.setdefault("inputs", {})
    upgraded.setdefault("idempotency", {})
    upgraded.setdefault("state_revision", 0)
    return upgraded


def _find_step(readiness: dict[str, object], step_id: str) -> dict[str, object]:
    for value in readiness.get("steps", []):
        if isinstance(value, dict) and value.get("id") == step_id:
            return value
    raise ReadinessExecutionError(
        "readiness_step_not_found",
        f"Unknown readiness step: {step_id}",
        details={"step_id": step_id},
        recovery=["reload_project"],
    )


def _bound_step_artifacts(
    readiness: dict[str, object],
    step_id: str,
) -> list[dict[str, object]]:
    step = _find_step(readiness, step_id)
    artifacts = dict(readiness.get("artifacts", {}) or {})
    bound: list[dict[str, object]] = []
    for artifact_ref in list(step.get("artifact_refs", []) or []):
        artifact = artifacts.get(artifact_ref)
        if (
            not isinstance(artifact, dict)
            or artifact.get("project_key") != readiness.get("project_key")
            or artifact.get("canonical_root") != readiness.get("canonical_root")
            or artifact.get("step_id") != step_id
            or artifact.get("execution_id") != step.get("execution_id")
            or artifact.get("attempt") != step.get("attempt")
        ):
            raise ReadinessExecutionError(
                "readiness_artifact_mismatch",
                "A readiness artifact is not bound to its owning step.",
                details={"step_id": step_id, "artifact_ref": str(artifact_ref)},
                recovery=["reload_project", "retry_readiness"],
            )
        bound.append(artifact)
    return bound


def _request_digest(inputs: dict[str, object]) -> str:
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProjectReadinessExecutor:
    def __init__(
        self,
        workspace: Path,
        *,
        preflight: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self._preflight = preflight or self._default_preflight

    def start(
        self,
        project_key: str,
        root: Path,
        inputs: dict[str, object] | None = None,
        *,
        idempotency_key: str = "",
        background: bool = True,
    ) -> dict[str, object]:
        key = normalize_project_key(project_key)
        root = Path(root).expanduser()
        submitted = dict(inputs or {})
        created = False
        should_run = False
        mode = ""
        execution_id = ""
        attempt = 0

        def transform(current: dict[str, object]) -> dict[str, object]:
            nonlocal created, should_run, mode, execution_id, attempt
            state = upgrade_readiness(current, root)
            mode = str(state.get("project_mode", ""))
            if mode not in {"new_project", "existing_project"}:
                raise ReadinessExecutionError(
                    "project_mode_unconfirmed",
                    "Confirm the project mode before starting readiness.",
                    recovery=["choose_project_mode"],
                )
            execution = state.get("execution")
            if isinstance(execution, dict) and str(execution.get("status")) == "failed":
                raise ReadinessExecutionError(
                    "readiness_retry_required",
                    "The failed readiness step must be retried before starting a new execution.",
                    details={"step_id": str(execution.get("current_step_id", ""))},
                    recovery=["retry_readiness", "reload_project"],
                )
            digest = _request_digest(submitted)
            idempotency = dict(state.get("idempotency", {}) or {})
            if idempotency_key and idempotency_key in idempotency:
                previous = idempotency[idempotency_key]
                if not isinstance(previous, dict) or previous.get("digest") != digest:
                    raise ReadinessExecutionError(
                        "readiness_idempotency_conflict",
                        "This idempotency key was already used with different inputs.",
                        recovery=["use_new_idempotency_key", "reload_project"],
                    )
                return state
            if isinstance(execution, dict) and str(execution.get("status")) in ACTIVE_EXECUTION_STATUSES:
                return state
            if isinstance(execution, dict) and str(execution.get("status")) == "complete":
                return state
            attempt = int(execution.get("attempt", 0) if isinstance(execution, dict) else 0) + 1
            execution_id = uuid4().hex
            timestamp = _now()
            state["execution"] = {
                "id": execution_id,
                "status": "running",
                "project_key": key,
                "canonical_root": str(root),
                "attempt": attempt,
                "started_at": timestamp,
                "completed_at": None,
                "updated_at": timestamp,
                "current_step_id": (
                    "repository_understanding"
                    if mode == "existing_project"
                    else "new_project_init"
                ),
                "owner_pid": os.getpid(),
                "owner_token": _PROCESS_TOKEN,
                "cancel_requested": False,
            }
            for mode_step_id in MODE_STEPS[mode]:
                mode_step = _find_step(state, mode_step_id)
                if mode_step.get("status") != "complete":
                    mode_step["execution_id"] = execution_id
                    mode_step["attempt"] = attempt
            state["lifecycle"] = "preparing"
            state["inputs"] = {**dict(state.get("inputs", {}) or {}), **submitted}
            if idempotency_key:
                idempotency[idempotency_key] = {"digest": digest, "execution_id": execution_id}
                state["idempotency"] = idempotency
            current_step = _find_step(
                state,
                "repository_understanding" if mode == "existing_project" else "new_project_init",
            )
            current_step.update(
                {
                    "status": "running",
                    "attempt": attempt,
                    "execution_id": execution_id,
                    "started_at": timestamp,
                    "completed_at": None,
                    "error": None,
                    "recovery": [],
                    "input_requirements": None,
                }
            )
            if mode == "new_project" and not self._valid_new_inputs(submitted):
                current_step["status"] = "needs_input"
                current_step["input_requirements"] = self._new_input_request()
                state["execution"]["status"] = "needs_input"
                state["lifecycle"] = "attention_required"
            else:
                should_run = True
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            created = True
            return state

        runner_key = _reserve_runner(self.workspace, key)
        try:
            readiness = mutate_project_readiness(self.workspace, key, root, transform)
            if created and should_run:
                runner = self._run_existing if mode == "existing_project" else self._run_new
                self._dispatch(key, root, execution_id, attempt, runner, background)
                return self.status(key, root, recover=False)
            return readiness
        finally:
            _release_runner(runner_key)

    def status(
        self,
        project_key: str,
        root: Path,
        *,
        recover: bool = True,
    ) -> dict[str, object]:
        key = normalize_project_key(project_key)
        root = Path(root).expanduser()
        readiness = read_bound_project_readiness(self.workspace, key, root)
        readiness = upgrade_readiness(readiness, root)
        if not recover:
            return readiness
        execution = readiness.get("execution")
        if not isinstance(execution, dict) or execution.get("status") != "running":
            return readiness
        runner_key = (str(self.workspace), key)
        execution_id = str(execution.get("id", ""))
        attempt = int(execution.get("attempt", 0) or 0)
        current_step_id = str(execution.get("current_step_id", ""))
        if _has_live_runner(runner_key, execution_id, attempt, current_step_id):
            return readiness
        owner_pid = int(execution.get("owner_pid", 0) or 0)
        owner_token = str(execution.get("owner_token", ""))
        if owner_token and owner_token != _PROCESS_TOKEN and owner_pid and _pid_alive(owner_pid):
            updated_at = _parse_time(str(execution.get("updated_at", "")))
            if updated_at is not None and datetime.now(UTC) - updated_at < RUNNER_LEASE:
                return readiness
        return self._mark_interrupted(key, root, execution_id, attempt)

    def submit_input(
        self,
        project_key: str,
        root: Path,
        step_id: str,
        values: dict[str, object],
        *,
        background: bool = True,
    ) -> dict[str, object]:
        key = normalize_project_key(project_key)
        root = Path(root).expanduser()
        spawn_preflight = False
        execution_id = ""
        attempt = 0

        def transform(current: dict[str, object]) -> dict[str, object]:
            nonlocal spawn_preflight, execution_id, attempt
            state = upgrade_readiness(current, root)
            execution = state.get("execution")
            if not isinstance(execution, dict):
                raise ReadinessExecutionError(
                    "readiness_not_started",
                    "Start readiness before submitting input.",
                    recovery=["start_readiness"],
                )
            execution_id = str(execution.get("id", ""))
            attempt = int(execution.get("attempt", 0) or 0)
            step = _find_step(state, step_id)
            submission_digest = _request_digest(values)
            if step.get("status") == "complete":
                previous = dict(state.get("inputs", {}) or {}).get(step_id)
                if isinstance(previous, dict) and previous.get("digest") == submission_digest:
                    return state
                raise ReadinessExecutionError(
                    "readiness_input_conflict",
                    "This readiness step was already confirmed with different input.",
                    details={"step_id": step_id},
                    recovery=["reload_project"],
                )
            if step.get("status") != "needs_input":
                raise ReadinessExecutionError(
                    "readiness_input_not_expected",
                    "This readiness step is not waiting for input.",
                    details={"step_id": step_id},
                    recovery=["reload_project"],
                )
            self._validate_input(state, step_id, values)
            timestamp = _now()
            state_inputs = dict(state.get("inputs", {}) or {})
            state_inputs[step_id] = {
                "values": dict(values),
                "submitted_at": timestamp,
                "digest": submission_digest,
                "project_key": state.get("project_key", ""),
                "canonical_root": state.get("canonical_root", ""),
                "execution_id": step.get("execution_id", ""),
                "attempt": step.get("attempt", 0),
                "source_artifact_refs": self._input_source_artifact_refs(state, step_id),
            }
            state["inputs"] = state_inputs
            step.update(
                {
                    "status": "complete",
                    "completed_at": timestamp,
                    "input_requirements": None,
                    "error": None,
                    "recovery": [],
                }
            )
            if step_id == "new_project_init":
                if not self._valid_new_inputs(values):
                    raise ReadinessExecutionError(
                        "readiness_input_invalid",
                        "Project goal and brief are required.",
                        details={"step_id": step_id},
                        recovery=["complete_required_fields"],
                    )
                state["inputs"].update(values)
                self._complete_new_plan_steps(state, execution_id, attempt, timestamp)
                spawn_preflight = True
            elif step_id == "command_verification":
                next_step = _find_step(state, "constraints_confirmation")
                next_step.update(
                    {
                        "status": "needs_input",
                        "attempt": attempt,
                        "execution_id": execution_id,
                        "started_at": timestamp,
                        "input_requirements": self._constraints_input_request(state),
                    }
                )
                execution["status"] = "needs_input"
                execution["current_step_id"] = "constraints_confirmation"
                state["lifecycle"] = "attention_required"
            elif step_id == "constraints_confirmation":
                if values.get("confirmed") is not True:
                    raise ReadinessExecutionError(
                        "readiness_input_invalid",
                        "Constraint confirmation is required.",
                        details={"step_id": step_id},
                        recovery=["confirm_constraints"],
                    )
                spawn_preflight = True
            elif step_id == "worker_preflight":
                if values.get("confirmed") is not True:
                    raise ReadinessExecutionError(
                        "readiness_input_invalid",
                        "Worker preflight uncertainty must be explicitly acknowledged.",
                        details={"step_id": step_id},
                        recovery=["confirm_worker_preflight"],
                    )
                execution.update(
                    {
                        "status": "complete",
                        "completed_at": timestamp,
                        "current_step_id": "",
                    }
                )
                state["lifecycle"] = "ready"
            else:
                raise ReadinessExecutionError(
                    "readiness_input_not_supported",
                    "This readiness input cannot be submitted here.",
                    details={"step_id": step_id},
                    recovery=["reload_project"],
                )
            if spawn_preflight:
                worker = _find_step(state, "worker_preflight")
                worker.update(
                    {
                        "status": "running",
                        "attempt": attempt,
                        "execution_id": execution_id,
                        "started_at": timestamp,
                        "completed_at": None,
                        "error": None,
                        "recovery": [],
                    }
                )
                execution["status"] = "running"
                execution["current_step_id"] = "worker_preflight"
                execution["owner_pid"] = os.getpid()
                execution["owner_token"] = _PROCESS_TOKEN
                state["lifecycle"] = "preparing"
            execution["updated_at"] = timestamp
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            return state

        runner_key = _reserve_runner(self.workspace, key)
        try:
            readiness = mutate_project_readiness(self.workspace, key, root, transform)
            if spawn_preflight:
                self._dispatch(key, root, execution_id, attempt, self._run_preflight, background)
                return self.status(key, root, recover=False)
            return readiness
        finally:
            _release_runner(runner_key)

    def retry(
        self,
        project_key: str,
        root: Path,
        step_id: str = "",
        *,
        background: bool = True,
    ) -> dict[str, object]:
        key = normalize_project_key(project_key)
        root = Path(root).expanduser()
        readiness = self.status(key, root)
        execution = readiness.get("execution")
        if not isinstance(execution, dict) or execution.get("status") != "failed":
            return readiness
        retry_step = step_id or str(execution.get("current_step_id", ""))
        runners = {
            "repository_understanding": self._run_existing,
            "new_project_init": self._run_new,
            "worker_preflight": self._run_preflight,
        }
        runner = runners.get(retry_step)
        if runner is None:
            raise ReadinessExecutionError(
                "readiness_step_not_retryable",
                "This readiness step cannot be retried automatically.",
                details={"step_id": retry_step},
                recovery=["reload_project"],
            )
        failed_step = _find_step(readiness, retry_step)
        if failed_step.get("status") != "failed":
            raise ReadinessExecutionError(
                "readiness_step_not_retryable",
                "Only the failed readiness step can be retried.",
                details={"step_id": retry_step},
                recovery=["reload_project"],
            )
        attempt = int(execution.get("attempt", 0) or 0) + 1
        execution_id = uuid4().hex
        transitioned = False

        def transform(current: dict[str, object]) -> dict[str, object]:
            nonlocal transitioned
            state = upgrade_readiness(current, root)
            current_execution = state.get("execution")
            if not isinstance(current_execution, dict) or current_execution.get("status") != "failed":
                return state
            step = _find_step(state, retry_step)
            if step.get("status") != "failed":
                return state
            timestamp = _now()
            current_execution.update(
                {
                    "id": execution_id,
                    "status": "running",
                    "attempt": attempt,
                    "started_at": timestamp,
                    "completed_at": None,
                    "updated_at": timestamp,
                    "current_step_id": retry_step,
                    "owner_pid": os.getpid(),
                    "owner_token": _PROCESS_TOKEN,
                    "cancel_requested": False,
                }
            )
            step.update(
                {
                    "status": "running",
                    "attempt": attempt,
                    "execution_id": execution_id,
                    "started_at": timestamp,
                    "completed_at": None,
                    "error": None,
                    "recovery": [],
                }
            )
            state["lifecycle"] = "preparing"
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            transitioned = True
            return state

        runner_key = _reserve_runner(self.workspace, key)
        try:
            readiness = mutate_project_readiness(self.workspace, key, root, transform)
            if not transitioned:
                return readiness
            self._dispatch(key, root, execution_id, attempt, runner, background)
            return self.status(key, root, recover=False)
        finally:
            _release_runner(runner_key)

    def cancel(self, project_key: str, root: Path) -> dict[str, object]:
        key = normalize_project_key(project_key)
        root = Path(root).expanduser()

        def transform(current: dict[str, object]) -> dict[str, object]:
            state = upgrade_readiness(current, root)
            execution = state.get("execution")
            if not isinstance(execution, dict) or execution.get("status") not in ACTIVE_EXECUTION_STATUSES:
                return state
            timestamp = _now()
            execution.update(
                {
                    "status": "cancelled",
                    "cancel_requested": True,
                    "completed_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            current_step_id = str(execution.get("current_step_id", ""))
            if current_step_id:
                step = _find_step(state, current_step_id)
                if step.get("status") in {"running", "needs_input"}:
                    step.update({"status": "cancelled", "completed_at": timestamp})
            state["lifecycle"] = "attention_required"
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            return state

        return mutate_project_readiness(self.workspace, key, root, transform)

    def artifact(
        self,
        project_key: str,
        root: Path,
        artifact_ref: str,
    ) -> dict[str, object]:
        readiness = self.status(project_key, root)
        artifact = dict(readiness.get("artifacts", {}) or {}).get(artifact_ref)
        if not isinstance(artifact, dict):
            raise ReadinessExecutionError(
                "readiness_artifact_not_found",
                "The requested readiness artifact was not found.",
                details={"artifact_ref": artifact_ref},
                recovery=["reload_project"],
            )
        owning_step = next(
            (
                step
                for step in readiness.get("steps", [])
                if isinstance(step, dict) and artifact_ref in list(step.get("artifact_refs", []) or [])
            ),
            None,
        )
        if (
            artifact.get("project_key") != normalize_project_key(project_key)
            or artifact.get("canonical_root") != str(Path(root).expanduser())
            or not isinstance(owning_step, dict)
            or artifact.get("step_id") != owning_step.get("id")
            or artifact.get("execution_id") != owning_step.get("execution_id")
            or artifact.get("attempt") != owning_step.get("attempt")
        ):
            raise ReadinessExecutionError(
                "readiness_artifact_mismatch",
                "The readiness artifact is not bound to this execution.",
                recovery=["reload_project", "retry_readiness"],
            )
        return artifact

    def _dispatch(
        self,
        key: str,
        root: Path,
        execution_id: str,
        attempt: int,
        runner: Callable[[str, Path, str, int], None],
        background: bool,
    ) -> None:
        if not background:
            runner(key, root, execution_id, attempt)
            return
        runner_key = (str(self.workspace), key, execution_id, attempt, runner.__name__)

        def target() -> None:
            heartbeat_stop = threading.Event()

            def heartbeat() -> None:
                while not heartbeat_stop.wait(RUNNER_HEARTBEAT_SECONDS):
                    try:
                        self._heartbeat_execution(key, root, execution_id, attempt)
                    except (OSError, ValueError):
                        continue

            heartbeat_thread = threading.Thread(
                target=heartbeat,
                name=f"readiness-heartbeat-{key}-{execution_id}-{attempt}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                runner(key, root, execution_id, attempt)
            finally:
                heartbeat_stop.set()
                with _RUNNERS_GUARD:
                    if _RUNNERS.get(runner_key) is threading.current_thread():
                        _RUNNERS.pop(runner_key, None)

        thread = threading.Thread(
            target=target,
            name=f"readiness-{key}-{execution_id}-{attempt}",
            daemon=True,
        )
        with _RUNNERS_GUARD:
            existing = _RUNNERS.get(runner_key)
            if existing is not None and existing.is_alive():
                return
            _RUNNERS[runner_key] = thread
        thread.start()

    def _heartbeat_execution(
        self,
        key: str,
        root: Path,
        execution_id: str,
        attempt: int,
    ) -> None:
        def transform(current: dict[str, object]) -> dict[str, object]:
            state = upgrade_readiness(current, root)
            execution = self._current_execution(state, execution_id, attempt)
            if execution is None:
                return state
            timestamp = _now()
            execution["updated_at"] = timestamp
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            return state

        mutate_project_readiness(self.workspace, key, root, transform)

    def _run_existing(self, key: str, root: Path, execution_id: str, attempt: int) -> None:
        try:
            bound = read_bound_project_readiness(self.workspace, key, root)
            expected_identity = bound.get("root_identity")
            if not isinstance(expected_identity, dict):
                raise ReadinessExecutionError(
                    "project_root_identity_missing",
                    "The imported project root identity is missing.",
                    recovery=["reload_project"],
                )
            overview = scan_existing_project(root, expected_identity=expected_identity)
            timestamp = _now()

            def transform(current: dict[str, object]) -> dict[str, object]:
                state = upgrade_readiness(current, root)
                execution = self._current_execution(state, execution_id, attempt)
                if execution is None:
                    return state
                artifact_ref = self._put_artifact(
                    state,
                    key,
                    root,
                    execution_id,
                    attempt,
                    "repository_understanding",
                    overview,
                    timestamp,
                )
                repository = _find_step(state, "repository_understanding")
                repository.update(
                    {
                        "status": "complete",
                        "execution_id": execution_id,
                        "attempt": attempt,
                        "completed_at": timestamp,
                        "artifact_refs": [artifact_ref],
                    }
                )
                commands = list(overview.get("commands", []))
                command_step = _find_step(state, "command_verification")
                command_step.update(
                    {
                        "status": "needs_input",
                        "attempt": attempt,
                        "execution_id": execution_id,
                        "started_at": timestamp,
                        "input_requirements": {
                            "step_id": "command_verification",
                            "kind": "command_confirmation",
                            "commands": commands,
                        },
                    }
                )
                execution.update(
                    {
                        "status": "needs_input",
                        "current_step_id": "command_verification",
                        "updated_at": timestamp,
                    }
                )
                state["lifecycle"] = "attention_required"
                state["updated_at"] = timestamp
                state["state_revision"] = int(state.get("state_revision", 0)) + 1
                return state

            mutate_project_readiness(self.workspace, key, root, transform)
        except Exception as exc:  # executor failures become safe persisted state
            self._mark_failed(key, root, execution_id, attempt, "repository_understanding", exc)

    def _run_new(self, key: str, root: Path, execution_id: str, attempt: int) -> None:
        try:
            timestamp = _now()

            def transform(current: dict[str, object]) -> dict[str, object]:
                state = upgrade_readiness(current, root)
                execution = self._current_execution(state, execution_id, attempt)
                if execution is None:
                    return state
                self._complete_new_plan_steps(state, execution_id, attempt, timestamp)
                worker = _find_step(state, "worker_preflight")
                worker.update(
                    {
                        "status": "running",
                        "attempt": attempt,
                        "execution_id": execution_id,
                        "started_at": timestamp,
                    }
                )
                execution.update(
                    {
                        "status": "running",
                        "current_step_id": "worker_preflight",
                        "updated_at": timestamp,
                    }
                )
                state["updated_at"] = timestamp
                state["state_revision"] = int(state.get("state_revision", 0)) + 1
                return state

            mutate_project_readiness(self.workspace, key, root, transform)
            self._run_preflight(key, root, execution_id, attempt)
        except Exception as exc:
            self._mark_failed(key, root, execution_id, attempt, "new_project_init", exc)

    def _run_preflight(self, key: str, root: Path, execution_id: str, attempt: int) -> None:
        try:
            report = self._preflight(key)
            timestamp = _now()

            def transform(current: dict[str, object]) -> dict[str, object]:
                state = upgrade_readiness(current, root)
                execution = self._current_execution(state, execution_id, attempt)
                if execution is None:
                    return state
                artifact_ref = self._put_artifact(
                    state,
                    key,
                    root,
                    execution_id,
                    attempt,
                    "worker_preflight",
                    report,
                    timestamp,
                )
                worker = _find_step(state, "worker_preflight")
                worker["execution_id"] = execution_id
                worker["attempt"] = attempt
                report_status = str(report.get("status", "fail"))
                if report_status == "warn":
                    worker.update(
                        {
                            "status": "needs_input",
                            "completed_at": None,
                            "artifact_refs": [artifact_ref],
                            "input_requirements": {
                                "step_id": "worker_preflight",
                                "kind": "worker_preflight_confirmation",
                                "message": str(
                                    report.get(
                                        "message",
                                        "Worker readiness could not be determined safely.",
                                    )
                                ),
                            },
                            "error": {
                                "code": "worker_preflight_needs_input",
                                "message": str(
                                    report.get(
                                        "message",
                                        "Worker readiness could not be determined safely.",
                                    )
                                ),
                            },
                            "recovery": ["confirm_worker_preflight", "configure_worker"],
                        }
                    )
                    execution.update(
                        {
                            "status": "needs_input",
                            "completed_at": None,
                            "updated_at": timestamp,
                        }
                    )
                    state["lifecycle"] = "attention_required"
                elif report_status != "ready":
                    worker.update(
                        {
                            "status": "failed",
                            "completed_at": timestamp,
                            "artifact_refs": [artifact_ref],
                            "error": {
                                "code": "worker_preflight_failed",
                                "message": str(report.get("message", "Worker preflight did not pass.")),
                            },
                            "recovery": ["configure_worker", "retry_readiness"],
                        }
                    )
                    execution.update(
                        {
                            "status": "failed",
                            "completed_at": timestamp,
                            "updated_at": timestamp,
                        }
                    )
                    state["lifecycle"] = "attention_required"
                else:
                    worker.update(
                        {
                            "status": "complete",
                            "completed_at": timestamp,
                            "artifact_refs": [artifact_ref],
                            "error": None,
                            "recovery": [],
                        }
                    )
                    execution.update(
                        {
                            "status": "complete",
                            "completed_at": timestamp,
                            "updated_at": timestamp,
                            "current_step_id": "",
                        }
                    )
                    state["lifecycle"] = "ready"
                state["updated_at"] = timestamp
                state["state_revision"] = int(state.get("state_revision", 0)) + 1
                return state

            mutate_project_readiness(self.workspace, key, root, transform)
        except Exception as exc:
            self._mark_failed(key, root, execution_id, attempt, "worker_preflight", exc)

    def _complete_new_plan_steps(
        self,
        state: dict[str, object],
        execution_id: str,
        attempt: int,
        timestamp: str,
    ) -> None:
        inputs = dict(state.get("inputs", {}) or {})
        content = {
            "goal": str(inputs.get("goal", "")),
            "brief": str(inputs.get("brief", "")),
            "tech_preferences": str(inputs.get("tech_preferences", "")),
            "constraints": str(inputs.get("constraints", "")),
            "prohibited_operations": str(inputs.get("prohibited_operations", "")),
            "target_root_writes_authorized": False,
            "next_steps": ["Review the project brief", "Start the existing GoodTeam workflow"],
        }
        key = str(state.get("project_key", ""))
        root = Path(str(state.get("canonical_root", "")))
        init_ref = self._put_artifact(
            state, key, root, execution_id, attempt, "new_project_init", content, timestamp
        )
        brief_ref = self._put_artifact(
            state, key, root, execution_id, attempt, "project_brief", content, timestamp
        )
        for step_id, artifact_ref in (
            ("new_project_init", init_ref),
            ("project_brief", brief_ref),
        ):
            step = _find_step(state, step_id)
            step.update(
                {
                    "status": "complete",
                    "attempt": attempt,
                    "execution_id": execution_id,
                    "started_at": step.get("started_at") or timestamp,
                    "completed_at": timestamp,
                    "artifact_refs": [artifact_ref],
                    "input_requirements": None,
                    "error": None,
                    "recovery": [],
                }
            )

    def _put_artifact(
        self,
        state: dict[str, object],
        key: str,
        root: Path,
        execution_id: str,
        attempt: int,
        step_id: str,
        content: dict[str, object],
        timestamp: str,
    ) -> str:
        artifact_ref = f"readiness://{execution_id}/{attempt}/{step_id}"
        artifacts = dict(state.get("artifacts", {}) or {})
        artifacts[artifact_ref] = {
            "id": artifact_ref,
            "project_key": key,
            "canonical_root": str(root),
            "execution_id": execution_id,
            "attempt": attempt,
            "step_id": step_id,
            "created_at": timestamp,
            "content": content,
        }
        state["artifacts"] = artifacts
        return artifact_ref

    def _mark_failed(
        self,
        key: str,
        root: Path,
        execution_id: str,
        attempt: int,
        step_id: str,
        error: Exception,
    ) -> dict[str, object]:
        code = error.code if isinstance(error, ReadinessExecutionError) else "readiness_step_failed"
        message = str(error) if isinstance(error, ReadinessExecutionError) else "GoodTeam could not complete this readiness step."
        recovery = (
            list(error.recovery)
            if isinstance(error, ReadinessExecutionError) and error.recovery
            else ["retry_readiness", "return_to_projects"]
        )
        if code in {"project_root_changed", "project_root_identity_missing"}:
            recovery = ["reload_project", "choose_another_directory"]
        root_changed = code == "project_root_changed"

        def transform(current: dict[str, object]) -> dict[str, object]:
            state = upgrade_readiness(current, root, verify_root=not root_changed)
            execution = self._current_execution(state, execution_id, attempt)
            if execution is None:
                return state
            timestamp = _now()
            step = _find_step(state, step_id)
            step.update(
                {
                    "status": "failed",
                    "completed_at": timestamp,
                    "error": {"code": code, "message": message},
                    "recovery": recovery,
                }
            )
            execution.update(
                {
                    "status": "failed",
                    "completed_at": timestamp,
                    "updated_at": timestamp,
                    "current_step_id": step_id,
                }
            )
            state["lifecycle"] = "attention_required"
            state["updated_at"] = timestamp
            state["state_revision"] = int(state.get("state_revision", 0)) + 1
            return state

        if root_changed:
            return mutate_project_readiness_root_failure(
                self.workspace,
                key,
                root,
                transform,
            )
        return mutate_project_readiness(self.workspace, key, root, transform)

    def _mark_interrupted(
        self,
        key: str,
        root: Path,
        execution_id: str,
        attempt: int,
    ) -> dict[str, object]:
        return self._mark_failed(
            key,
            root,
            execution_id,
            attempt,
            str(read_bound_project_readiness(self.workspace, key, root).get("execution", {}).get("current_step_id", "worker_preflight")),
            ReadinessExecutionError(
                "readiness_interrupted",
                "The readiness executor stopped before this step completed.",
                recovery=["retry_readiness"],
            ),
        )

    @staticmethod
    def _current_execution(
        state: dict[str, object],
        execution_id: str,
        attempt: int,
    ) -> dict[str, object] | None:
        execution = state.get("execution")
        if (
            not isinstance(execution, dict)
            or execution.get("id") != execution_id
            or int(execution.get("attempt", 0) or 0) != attempt
            or execution.get("status") != "running"
            or execution.get("cancel_requested") is True
        ):
            return None
        return execution

    @staticmethod
    def _valid_new_inputs(values: dict[str, object]) -> bool:
        return bool(str(values.get("goal", "")).strip() and str(values.get("brief", "")).strip())

    def _validate_input(
        self,
        state: dict[str, object],
        step_id: str,
        values: dict[str, object],
    ) -> None:
        if step_id == "new_project_init":
            if self._valid_new_inputs(values):
                return
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Project goal and brief are required.",
                details={"step_id": step_id},
                recovery=["complete_required_fields"],
            )
        if step_id == "constraints_confirmation":
            if values.get("confirmed") is True:
                return
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Constraint confirmation is required.",
                details={"step_id": step_id},
                recovery=["confirm_constraints"],
            )
        if step_id == "worker_preflight":
            if values.get("confirmed") is True:
                return
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Worker preflight uncertainty must be explicitly acknowledged.",
                details={"step_id": step_id},
                recovery=["confirm_worker_preflight"],
            )
        if step_id != "command_verification":
            return
        submitted = values.get("confirmed_command_ids")
        if not isinstance(submitted, list) or any(not isinstance(value, str) for value in submitted):
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Confirmed command IDs must be a list of discovered command IDs.",
                details={"step_id": step_id},
                recovery=["confirm_commands"],
            )
        candidates: set[str] = set()
        for artifact in _bound_step_artifacts(state, "repository_understanding"):
            content = artifact.get("content")
            if not isinstance(content, dict):
                continue
            candidates.update(
                str(command.get("id"))
                for command in content.get("commands", [])
                if isinstance(command, dict) and command.get("id")
            )
        selected = set(submitted)
        if len(selected) != len(submitted) or not selected.issubset(candidates):
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Confirmed commands must match the current repository scan.",
                details={"step_id": step_id},
                recovery=["reload_project", "confirm_commands"],
            )
        if candidates and not selected:
            raise ReadinessExecutionError(
                "readiness_input_invalid",
                "Confirm at least one discovered command before continuing.",
                details={"step_id": step_id},
                recovery=["confirm_commands"],
            )

    @staticmethod
    def _input_source_artifact_refs(
        state: dict[str, object],
        step_id: str,
    ) -> list[str]:
        source_step_id = {
            "command_verification": "repository_understanding",
            "constraints_confirmation": "repository_understanding",
            "worker_preflight": "worker_preflight",
        }.get(step_id)
        if source_step_id is None:
            return []
        return [
            str(artifact.get("id", ""))
            for artifact in _bound_step_artifacts(state, source_step_id)
        ]

    @staticmethod
    def _new_input_request() -> dict[str, object]:
        return {
            "step_id": "new_project_init",
            "kind": "project_initialization",
            "required_fields": ["goal", "brief"],
            "optional_fields": [
                "tech_preferences",
                "constraints",
                "prohibited_operations",
            ],
        }

    @staticmethod
    def _constraints_input_request(state: dict[str, object]) -> dict[str, object]:
        constraints: list[object] = []
        for artifact in _bound_step_artifacts(state, "repository_understanding"):
            content = artifact.get("content", {})
            if isinstance(content, dict):
                constraints.extend(list(content.get("constraints", []) or []))
        return {
            "step_id": "constraints_confirmation",
            "kind": "constraint_confirmation",
            "constraints": constraints,
        }

    def _default_preflight(self, key: str) -> dict[str, Any]:
        mode = "codex"
        for config in sorted((self.workspace / "projects").glob("*.local.yaml")):
            target = load_project_target(config)
            if normalize_project_key(str(target.get("key", config.stem))) != key:
                continue
            mode = str(dict(target.get("worker_backend", {})).get("mode", "codex") or "codex")
            break
        return worker_backend_local_preflight(mode, dry_run=True, allow_real_worker=False)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def scan_existing_project(
    root: Path,
    *,
    expected_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    root = Path(root).expanduser()
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise
        raise ReadinessExecutionError(
            "project_root_changed",
            "The project root changed before GoodTeam could read it.",
            recovery=["reload_project", "retry_readiness"],
        ) from exc
    try:
        root_stat = os.fstat(root_fd)
        if isinstance(expected_identity, dict) and (
            expected_identity.get("device") != root_stat.st_dev
            or expected_identity.get("inode") != root_stat.st_ino
        ):
            raise ReadinessExecutionError(
                "project_root_changed",
                "The project root no longer matches the imported directory.",
                recovery=["reload_project", "retry_readiness"],
            )
        files: list[str] = []
        directories: list[str] = []
        symlinks: list[str] = []
        manifests: dict[str, str] = {}
        document_contents: dict[str, str] = {}
        documents: list[str] = []
        entries: list[str] = []
        extension_counts: dict[str, int] = {}
        entry_count = 0
        truncated = False

        def visit(directory_fd: int, prefix: str, depth: int) -> None:
            nonlocal entry_count, truncated
            if entry_count >= MAX_ENTRIES or depth > MAX_DEPTH:
                truncated = True
                return
            remaining = MAX_ENTRIES - entry_count
            with os.scandir(directory_fd) as iterator:
                listed = list(islice(iterator, remaining + 1))
            if len(listed) > remaining:
                truncated = True
                listed = listed[:remaining]
            listed.sort(key=lambda item: item.name.lower())
            for entry in listed:
                if entry_count >= MAX_ENTRIES:
                    truncated = True
                    return
                entry_count += 1
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(entry_stat.st_mode):
                    symlinks.append(relative)
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    directories.append(relative)
                    if entry.name in SKIPPED_DIRECTORY_NAMES:
                        continue
                    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                    try:
                        child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                    except OSError:
                        continue
                    try:
                        child_stat = os.fstat(child_fd)
                        if (
                            child_stat.st_dev != entry_stat.st_dev
                            or child_stat.st_ino != entry_stat.st_ino
                            or not stat.S_ISDIR(child_stat.st_mode)
                        ):
                            raise ReadinessExecutionError(
                                "project_entry_changed",
                                "A project directory changed while GoodTeam was reading it.",
                                details={"path": relative},
                                recovery=["retry_readiness", "reload_project"],
                            )
                        visit(child_fd, relative, depth + 1)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    continue
                files.append(relative)
                suffix = Path(entry.name).suffix.lower()
                if suffix:
                    extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
                if entry.name in ENTRY_NAMES:
                    entries.append(relative)
                if entry.name in DOCUMENT_NAMES or (prefix == "docs" and suffix in {".md", ".rst", ".txt"}):
                    documents.append(relative)
                    content = _read_regular_file(
                        directory_fd,
                        entry.name,
                        expected_stat=entry_stat,
                        relative_path=relative,
                    )
                    if content is not None:
                        document_contents[relative] = content
                if entry.name in MANIFEST_TECH or entry.name in {"Makefile", "justfile"}:
                    content = _read_regular_file(
                        directory_fd,
                        entry.name,
                        expected_stat=entry_stat,
                        relative_path=relative,
                    )
                    if content is not None:
                        manifests[relative] = content

        visit(root_fd, "", 0)
        current = os.stat(root, follow_symlinks=False)
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
            root_stat.st_dev,
            root_stat.st_ino,
        ):
            raise ReadinessExecutionError(
                "project_root_changed",
                "The project root changed while GoodTeam was reading it.",
                recovery=["reload_project", "retry_readiness"],
            )
        stack = sorted(
            {
                MANIFEST_TECH[Path(path).name]
                for path in manifests
                if Path(path).name in MANIFEST_TECH
            }
        )
        commands = _discover_commands(manifests)
        constraints = _extract_constraints(document_contents)
        risks = []
        if symlinks:
            risks.append(f"Skipped {len(symlinks)} symbolic link(s) during the read-only scan.")
        if truncated:
            risks.append("Repository scan reached the bounded entry limit; deeper indexing is recommended.")
        if not commands:
            risks.append("No run, test, lint, build, format, or check commands were discovered safely.")
        return {
            "technology_stack": stack or _stack_from_extensions(extension_counts),
            "directory_structure": directories[:120],
            "entry_points": entries[:80],
            "commands": commands,
            "key_documents": documents[:80],
            "constraints": constraints,
            "risks": risks,
            "next_steps": [
                "Confirm the discovered commands and project constraints.",
                "Run Worker preflight after confirmation.",
            ],
            "scan": {
                "files_seen": len(files),
                "directories_seen": len(directories),
                "entries_seen": entry_count,
                "symlinks_skipped": symlinks[:80],
                "max_depth": MAX_DEPTH,
                "max_entries": MAX_ENTRIES,
                "read_only": True,
            },
        }
    finally:
        os.close(root_fd)


def _read_regular_file(
    directory_fd: int,
    name: str,
    *,
    expected_stat: os.stat_result,
    relative_path: str,
) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ReadinessExecutionError(
                "project_entry_changed",
                "A project file changed while GoodTeam was reading it.",
                details={"path": relative_path},
                recovery=["retry_readiness", "reload_project"],
            ) from exc
        return None
    try:
        value = os.fstat(descriptor)
        if (
            value.st_dev != expected_stat.st_dev
            or value.st_ino != expected_stat.st_ino
            or not stat.S_ISREG(value.st_mode)
            or value.st_size != expected_stat.st_size
            or value.st_mtime_ns != expected_stat.st_mtime_ns
        ):
            raise ReadinessExecutionError(
                "project_entry_changed",
                "A project file was replaced while GoodTeam was reading it.",
                details={"path": relative_path},
                recovery=["retry_readiness", "reload_project"],
            )
        if not stat.S_ISREG(value.st_mode) or value.st_size > MAX_READ_BYTES:
            return None
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_READ_BYTES)
        after = os.fstat(descriptor)
        if (
            after.st_dev != value.st_dev
            or after.st_ino != value.st_ino
            or after.st_size != value.st_size
            or after.st_mtime_ns != value.st_mtime_ns
        ):
            raise ReadinessExecutionError(
                "project_entry_changed",
                "A project file changed while GoodTeam was reading it.",
                details={"path": relative_path},
                recovery=["retry_readiness", "reload_project"],
            )
        return content
    finally:
        os.close(descriptor)


def _extract_constraints(document_contents: dict[str, str]) -> list[str]:
    constraint_names = {"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "SECURITY.md"}
    constraints: list[str] = []
    for path, content in sorted(document_contents.items()):
        if Path(path).name not in constraint_names:
            continue
        excerpts: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip().lstrip("#*- ").strip()
            if not line or line.startswith(("![", "[![")):
                continue
            excerpts.append(line[:240])
            if len(excerpts) >= 12:
                break
        if not excerpts:
            constraints.append(f"{path}: review this project constraint document")
        else:
            constraints.extend(f"{path}: {excerpt}" for excerpt in excerpts)
    return constraints[:80]


def _discover_commands(manifests: dict[str, str]) -> list[dict[str, object]]:
    commands: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        path: str,
        kind: str,
        command: str,
        *,
        declared_command: str | None = None,
    ) -> None:
        identity = (path, command)
        if identity in seen:
            return
        seen.add(identity)
        candidate: dict[str, object] = {
            "id": hashlib.sha256(
                f"readiness-command:v1\0{path}\0{command}".encode()
            ).hexdigest()[:16],
            "kind": kind,
            "command": command,
            "source": path,
            "requires_confirmation": True,
        }
        if declared_command is not None:
            candidate["declared_command"] = declared_command
        commands.append(candidate)

    def safe_name(value: str) -> bool:
        return bool(value) and value.replace("-", "").replace("_", "").replace(
            ".", ""
        ).isalnum()

    for path, content in sorted(manifests.items()):
        name = Path(path).name
        if name == "package.json":
            try:
                scripts = dict(json.loads(content).get("scripts", {}) or {})
            except (json.JSONDecodeError, AttributeError, TypeError):
                scripts = {}
            for script_name, value in sorted(scripts.items()):
                kind = _command_kind(str(script_name))
                if kind is None:
                    continue
                add(
                    path,
                    kind,
                    f"npm run {script_name}",
                    declared_command=str(value),
                )
        elif name == "pyproject.toml":
            try:
                project = tomllib.loads(content)
            except (tomllib.TOMLDecodeError, TypeError):
                project = {}
            project_metadata = project.get("project", {})
            scripts = (
                project_metadata.get("scripts", {})
                if isinstance(project_metadata, dict)
                else {}
            )
            if isinstance(scripts, dict):
                for script_name, entry_point in sorted(scripts.items()):
                    if (
                        not isinstance(script_name, str)
                        or not safe_name(script_name)
                        or not isinstance(entry_point, str)
                    ):
                        continue
                    add(
                        path,
                        "run",
                        script_name,
                        declared_command=entry_point,
                    )
            tools = project.get("tool", {})
            if isinstance(tools, dict):
                if isinstance(tools.get("pytest"), dict):
                    add(path, "test", "python -m pytest")
                if isinstance(tools.get("ruff"), dict):
                    add(path, "lint", "python -m ruff check .")
                    if isinstance(tools["ruff"].get("format"), dict):
                        add(path, "format", "python -m ruff format --check .")
                if isinstance(tools.get("mypy"), dict):
                    add(path, "check", "python -m mypy .")
            if isinstance(project.get("build-system"), dict):
                add(path, "build", "python -m build")
        elif name == "Cargo.toml":
            try:
                cargo = tomllib.loads(content)
            except (tomllib.TOMLDecodeError, TypeError):
                cargo = {}
            if isinstance(cargo.get("package"), dict) or isinstance(
                cargo.get("workspace"), dict
            ):
                add(path, "check", "cargo check")
                add(path, "test", "cargo test")
                add(path, "build", "cargo build")
                add(path, "format", "cargo fmt --check")
                add(path, "lint", "cargo clippy --all-targets --all-features")
                binaries = cargo.get("bin", [])
                if isinstance(binaries, list):
                    for binary in binaries:
                        if not isinstance(binary, dict):
                            continue
                        binary_name = binary.get("name")
                        if isinstance(binary_name, str) and safe_name(binary_name):
                            add(path, "run", f"cargo run --bin {binary_name}")
        elif name == "go.mod":
            module_lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            if (
                module_lines
                and module_lines[0].startswith("module ")
                and module_lines[0].removeprefix("module ").strip()
            ):
                add(path, "test", "go test ./...")
                add(path, "check", "go vet ./...")
                add(path, "build", "go build ./...")
                add(path, "format", "gofmt -l .")
        elif name == "Makefile":
            for line in content.splitlines():
                if not line or line.startswith(("\t", " ", "#", ".")) or ":" not in line:
                    continue
                target = line.split(":", 1)[0].strip()
                kind = _command_kind(target)
                if kind is None or not target.replace("-", "").replace("_", "").isalnum():
                    continue
                add(path, kind, f"make {target}")
        elif name == "justfile":
            for line in content.splitlines():
                if not line or line.startswith((" ", "\t", "#")) or ":" not in line:
                    continue
                target = line.split(":", 1)[0].strip()
                kind = _command_kind(target)
                if kind:
                    add(path, kind, f"just {target}")
    return commands[:120]


def _command_kind(name: str) -> str | None:
    lowered = name.lower()
    for kind in ("test", "lint", "format", "build", "check"):
        if kind in lowered:
            return kind
    if any(token in lowered for token in ("start", "serve", "dev", "run")):
        return "run"
    return None


def _stack_from_extensions(counts: dict[str, int]) -> list[str]:
    labels = {
        ".py": "Python",
        ".js": "JavaScript",
        ".jsx": "React / JavaScript",
        ".ts": "TypeScript",
        ".tsx": "React / TypeScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
    }
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [labels[suffix] for suffix, _count in ranked if suffix in labels][:8]
