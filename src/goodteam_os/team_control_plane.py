"""Team Control Plane: Role capabilities vs Worker instances with an audit ledger.

Domain boundaries enforced here:

- A **Role Definition** is a catalog-level capability contract. It never owns
  runtime configuration or worker counts.
- A **TeamRole** is the project-level membership state (``enabled``/``disabled``)
  of a Role Definition.
- A **Worker** is a first-class persistent team member carrying one Role, its
  own runtime configuration, and an independent lifecycle
  (``active``/``inactive``/``released``/``deleted``).

Every Team mutation from any surface (Team UI, Manager conversation, API
adapter, migration) must flow through :func:`execute_team_commands`. Each
execution appends immutable events to the project decision/event ledger and
shares one ``decision_id`` across the events caused by one intent.

Current state, decision history, and execution snapshots are three different
concepts: ``team-state.json`` holds current truth, ``team-events.jsonl`` holds
the append-only decision ledger, and workflow runs bind their own immutable
execution snapshot at start.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from goodteam_os.skills.bindings import (
    default_binding,
    manager_subject,
    normalize_binding,
    normalize_binding_map,
    normalize_items,
    project_binding,
    role_subject,
    workflow_role_subject,
)
from goodteam_os.skills.models import SkillError
from goodteam_os.worker_capabilities import REASONING_DEPTHS
from goodteam_os.state import normalize_project_key
from goodteam_os.workers import BUILTIN_WORKER_MODES, CLI_BACKENDS, SESSION_BACKENDS, worker_backend_doctor


TEAM_CONTROL_PLANE_SCHEMA_VERSION = "team_control_plane.v1"
TEAM_CAPABILITY_VIEW_SCHEMA_VERSION = "team_capability_view.v1"
TEAM_EXECUTION_SNAPSHOT_SCHEMA_VERSION = "team_execution_snapshot.v1"

MANAGER_ROLE = "orchestrator"
# project_intake is a workflow Role Definition, not a TeamRole. Its Worker
# identity/runtime is provided by the Research Team capability today; its
# method configuration remains an independent workflow-role subject.
WORKFLOW_ROLE_PROVIDERS: dict[str, str] = {"project_intake": "research"}

# Worker runtime providers this product accepts, mirroring the web-layer
# accepted backend set without importing the web module.
WORKER_PROVIDER_OPTIONS = frozenset({*BUILTIN_WORKER_MODES, *CLI_BACKENDS, *SESSION_BACKENDS, "manual"})

ROLE_CATALOG: tuple[dict[str, str], ...] = (
    {"key": "research", "label": "Research", "worker_prefix": "Researcher"},
    {"key": "product_requirement", "label": "Product / Requirement", "worker_prefix": "PM"},
    {"key": "architect", "label": "Architect", "worker_prefix": "Architect"},
    {"key": "review", "label": "Review", "worker_prefix": "Reviewer"},
    {"key": "planner", "label": "Planner", "worker_prefix": "Planner"},
    {"key": "ui", "label": "UI", "worker_prefix": "Designer"},
    {"key": "implementation", "label": "Implementation", "worker_prefix": "Builder"},
    {"key": "frontend", "label": "Frontend", "worker_prefix": "Builder"},
    {"key": "backend", "label": "Backend", "worker_prefix": "Builder"},
    {"key": "qa", "label": "QA", "worker_prefix": "QA"},
    {"key": "code_review", "label": "Code review", "worker_prefix": "Reviewer"},
    {"key": "release", "label": "Release", "worker_prefix": "Releaser"},
    {"key": "retro_memory", "label": "Retro / Memory", "worker_prefix": "Historian"},
)
ROLE_KEYS = tuple(item["key"] for item in ROLE_CATALOG)

# Roles that were dispatchable under the legacy worker-config model when the
# control plane shipped (#402 §11). This baseline is an explicit frozen
# literal **on purpose**: it must never be derived from ROLE_CATALOG, or the
# next release that adds a Role Definition would silently auto-enable it for
# every migrating team. Add a key here only as a deliberate, reviewed product
# decision that all teams should gain that capability — migration
# compatibility never requires it (#407).
MIGRATION_BASELINE_ROLES: frozenset[str] = frozenset({
    "research",
    "product_requirement",
    "architect",
    "review",
    "planner",
    "ui",
    "implementation",
    "frontend",
    "backend",
    "qa",
    "code_review",
    "release",
    "retro_memory",
})

TEAM_ROLE_STATES = ("enabled", "disabled")
WORKER_LIFECYCLES = ("active", "inactive", "released", "deleted")
DISPATCHABLE_LIFECYCLES = ("active",)
ROSTERED_LIFECYCLES = ("active", "inactive")
CAPABILITY_STATES = ("disabled", "unstaffed", "ready", "degraded")

ADDITIONAL_PROMPT_MAX_CHARS = 4000
WORKER_NAME_MAX_CHARS = 80

ACTORS = ("user", "manager", "system", "migration")
SURFACES = ("manager_conversation", "team_ui", "api", "migration")

TEAM_ROLE_EVENT_PREFIX = "team_role"
WORKER_EVENT_PREFIX = "worker"
MANAGER_EVENT_PREFIX = "manager"

# Reserved key attached to a parsed project target by `attach_team_control_plane`.
TEAM_STATE_TARGET_KEY = "team_control_plane"


class TeamControlPlaneError(ValueError):
    """Raised when a team control-plane request is invalid."""


class TeamDispatchBlocked(TeamControlPlaneError):
    """Raised when dispatch is gated by the Team Control Plane.

    Fail closed: a disabled TeamRole or a TeamRole without an eligible active
    Worker can never dispatch, regardless of the calling surface.
    """

    def __init__(self, code: str, message: str, *, role: str = ""):
        super().__init__(message)
        self.code = code
        self.role = role


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _command_fingerprint(
    commands: list[dict[str, object]],
    *,
    reason: str,
    source_ref: dict[str, object] | None,
) -> str:
    try:
        data = json.dumps(
            {
                "commands": commands,
                "reason": reason,
                "source_ref": dict(source_ref or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TeamControlPlaneError("Team commands must be JSON-compatible values") from exc
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def role_label(role: str) -> str:
    for item in ROLE_CATALOG:
        if item["key"] == role:
            return item["label"]
    return role


def worker_prefix(role: str) -> str:
    for item in ROLE_CATALOG:
        if item["key"] == role:
            return item["worker_prefix"]
    return role_label(role)


def normalize_provider(provider: object) -> str:
    value = str(provider or "").strip()
    if value == "manual":
        return "human"
    return value


def validate_provider(provider: object) -> str:
    value = normalize_provider(provider)
    if value not in WORKER_PROVIDER_OPTIONS:
        options = ", ".join(WORKER_PROVIDER_OPTIONS)
        raise TeamControlPlaneError(f"Unknown worker provider: {value or '(blank)'}. Use one of: {options}")
    return value


def validate_reasoning_depth(value: object) -> str:
    depth = str(value or "default").strip().lower()
    if depth not in REASONING_DEPTHS:
        raise TeamControlPlaneError(f"reasoning_depth must be one of: {', '.join(REASONING_DEPTHS)}")
    return depth


def validate_worker_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or len(name) > WORKER_NAME_MAX_CHARS:
        raise TeamControlPlaneError(f"Worker name must be 1-{WORKER_NAME_MAX_CHARS} characters")
    return name


def _sanitize_worker_runtime(raw: object) -> dict[str, str]:
    runtime = dict(raw) if isinstance(raw, dict) else {}
    return {
        "provider": normalize_provider(runtime.get("provider", "")),
        "model": str(runtime.get("model", "") or "").strip(),
        "reasoning_depth": validate_reasoning_depth(runtime.get("reasoning_depth", "default")),
        "additional_prompt": str(runtime.get("additional_prompt", "") or "").strip()[:ADDITIONAL_PROMPT_MAX_CHARS],
    }


def _sanitize_worker_record(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise TeamControlPlaneError("Worker records must be objects")
    worker_id = str(raw.get("id", "") or "").strip()
    if not worker_id:
        raise TeamControlPlaneError("Worker records require an id")
    lifecycle = str(raw.get("lifecycle", "") or "").strip()
    if lifecycle not in WORKER_LIFECYCLES:
        raise TeamControlPlaneError(f"Worker {worker_id} has invalid lifecycle: {lifecycle or '(blank)'}")
    return {
        "id": worker_id,
        "name": str(raw.get("name", "") or "").strip() or worker_id,
        "role": str(raw.get("role", "") or "").strip(),
        "lifecycle": lifecycle,
        "runtime": _sanitize_worker_runtime(raw.get("runtime", {})),
        "created_at": str(raw.get("created_at", "") or ""),
        "updated_at": str(raw.get("updated_at", "") or ""),
        "released_at": str(raw.get("released_at", "") or ""),
        "deleted_at": str(raw.get("deleted_at", "") or ""),
    }


def _sanitize_team_state(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise TeamControlPlaneError("Team state must be a JSON object")
    if payload.get("schema_version") != TEAM_CONTROL_PLANE_SCHEMA_VERSION:
        raise TeamControlPlaneError("Team control-plane state is invalid")
    roles_raw = payload.get("roles", {})
    if not isinstance(roles_raw, dict):
        raise TeamControlPlaneError("Team roles must be an object")
    roles: dict[str, dict[str, str]] = {}
    for role, raw in roles_raw.items():
        role_key = str(role).strip()
        state = str(dict(raw).get("state", "enabled") if isinstance(raw, dict) else "enabled")
        if state not in TEAM_ROLE_STATES:
            raise TeamControlPlaneError(f"TeamRole {role_key} has invalid state: {state}")
        roles[role_key] = {"state": state, "updated_at": str(dict(raw).get("updated_at", "") or "") if isinstance(raw, dict) else ""}
    workers_raw = payload.get("workers", [])
    if not isinstance(workers_raw, list):
        raise TeamControlPlaneError("Team workers must be a list")
    manager = payload.get("manager", {})
    manager_runtime = _sanitize_worker_runtime(dict(manager).get("runtime", {}) if isinstance(manager, dict) else {})
    project_key = normalize_project_key(str(payload.get("project_key", "") or ""))
    try:
        skill_bindings = normalize_binding_map(
            payload.get("skill_bindings", {}),
            role_keys=ROLE_KEYS,
            manager_role=MANAGER_ROLE,
            project_key=project_key,
            workflow_role_keys=tuple(WORKFLOW_ROLE_PROVIDERS),
        )
    except SkillError as exc:
        raise TeamControlPlaneError(str(exc)) from exc
    pending_raw = payload.get("pending_events", [])
    if not isinstance(pending_raw, list) or any(not isinstance(item, dict) or not item.get("id") for item in pending_raw):
        raise TeamControlPlaneError("Pending Team audit events are invalid")
    return {
        "schema_version": TEAM_CONTROL_PLANE_SCHEMA_VERSION,
        "project_key": project_key,
        "revision": int(payload.get("revision", 0) or 0),
        "roles": roles,
        "workers": [_sanitize_worker_record(item) for item in workers_raw],
        "manager": {"runtime": manager_runtime, "updated_at": str(dict(manager).get("updated_at", "") or "") if isinstance(manager, dict) else ""},
        "skill_bindings": skill_bindings,
        "migrated": dict(payload.get("migrated", {}) or {}) if isinstance(payload.get("migrated", {}), dict) else {},
        "executed_commands": dict(payload.get("executed_commands", {}) or {}) if isinstance(payload.get("executed_commands", {}), dict) else {},
        "pending_events": [deepcopy(item) for item in pending_raw],
    }


class TeamControlPlaneStore:
    """Durable current-state store plus append-only decision/event ledger."""

    def __init__(self, workspace: Path, project_key: str):
        self.workspace = Path(workspace)
        self.project_key = normalize_project_key(project_key)
        self.state_dir = self.workspace / "state" / "projects" / self.project_key
        self.state_path = self.state_dir / "team-state.json"
        self.events_path = self.state_dir / "team-events.jsonl"
        self.lock_path = self.state_dir / "team-state.lock"

    # ------------------------------------------------------------------ locks

    def _locked(self):
        return _file_lock(self.lock_path)

    # ------------------------------------------------------------------ state

    def state_file_exists(self) -> bool:
        return self.state_path.is_file()

    def read(self) -> dict[str, object] | None:
        if not self.state_path.is_file():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TeamControlPlaneError("Team control-plane state could not be read") from exc
        return _sanitize_team_state(payload)

    def load_or_migrate(
        self,
        *,
        legacy_target: dict[str, object] | None = None,
        config_path: Path | None = None,
    ) -> dict[str, object] | None:
        """Load current state, lazily migrating legacy worker config once.

        Migration is idempotent: the persisted schema version is the migration
        marker, so reruns never duplicate workers or events.
        """
        existing = self.read()
        if existing is not None:
            return existing
        if legacy_target is None:
            return None
        with self._locked():
            existing = self.read()
            if existing is not None:
                return existing
            state = build_team_state_from_legacy_config(self.project_key, legacy_target)
            self._write_state(state)
            events = migration_events(self.project_key, state, legacy_target)
            self._append_events(events)
            if config_path is not None:
                _sync_legacy_worker_config_view(config_path, state)
            return state

    def _write_state(self, state: dict[str, object], *, durable: bool = False) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.state_dir, delete=False) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, self.state_path)
            if durable:
                directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    # ----------------------------------------------------------------- events

    def read_events(self, *, limit: int | None = None, decision_id: str = "") -> list[dict[str, object]]:
        if not self.events_path.is_file():
            return []
        events: list[dict[str, object]] = []
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise TeamControlPlaneError("Team decision ledger could not be read") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TeamControlPlaneError("Team decision ledger is corrupt") from exc
            if decision_id and str(event.get("decision_id", "")) != decision_id:
                continue
            events.append(event)
        if limit is not None and limit >= 0:
            events = events[-limit:]
        return events

    def _append_events(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _append_events_idempotent(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        existing_ids = {str(item.get("id", "")) for item in self.read_events()}
        pending = [event for event in events if str(event.get("id", "")) not in existing_ids]
        if not pending:
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.events_path.exists()
        with self.events_path.open("a", encoding="utf-8") as handle:
            for event in pending:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            directory = os.open(self.events_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def _recover_pending_events(self, state: dict[str, object]) -> None:
        pending = [deepcopy(item) for item in list(state.get("pending_events", []) or [])]
        if not pending:
            return
        self._append_events_idempotent(pending)
        state["pending_events"] = []
        self._write_state(state, durable=True)

    def reconcile_pending_audit(self) -> dict[str, object] | None:
        """Recover a committed state mutation whose audit append was interrupted."""
        with self._locked():
            state = self.read()
            if state is None:
                return None
            self._recover_pending_events(state)
            return state

    # --------------------------------------------------------------- commands

    def execute(
        self,
        commands: list[dict[str, object]],
        *,
        actor: str,
        surface: str,
        reason: str = "",
        source_ref: dict[str, object] | None = None,
        decision_id: str | None = None,
        command_key: str = "",
        config_path: Path | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        """Execute one authorized decision through one serialized Team truth."""
        if actor not in ACTORS:
            raise TeamControlPlaneError(f"Unknown actor: {actor}")
        if surface not in SURFACES:
            raise TeamControlPlaneError(f"Unknown surface: {surface}")
        if not commands:
            raise TeamControlPlaneError("At least one team command is required")
        if any(not isinstance(item, dict) for item in commands):
            raise TeamControlPlaneError("Each team command must be a JSON object")
        fingerprint = _command_fingerprint(commands, reason=reason, source_ref=source_ref)

        with self._locked():
            state = self.read()
            if state is None:
                raise TeamControlPlaneError("Team state is unavailable for this project")
            self._recover_pending_events(state)
            normalized_key = str(command_key or "").strip()
            if normalized_key:
                previous = dict(state["executed_commands"]).get(normalized_key)
                if isinstance(previous, dict) and previous.get("decision_id"):
                    previous_fingerprint = str(previous.get("fingerprint", "") or "")
                    if previous_fingerprint and previous_fingerprint != fingerprint:
                        raise TeamControlPlaneError("Team command key already belongs to different inputs.")
                    receipt = previous.get("result")
                    if isinstance(receipt, dict):
                        return {
                            "ok": True,
                            "changed": bool(receipt.get("changed", False)),
                            "replayed": True,
                            "decision_id": str(previous["decision_id"]),
                            "team_revision": int(receipt.get("team_revision", state["revision"])),
                            "events": deepcopy(list(receipt.get("events", []) or [])),
                            "team": build_team_capability_view(state),
                        }
                    # Legacy receipts predate payload/result binding. Preserve their
                    # historical replay shape rather than fabricating old events.
                    return {
                        "ok": True,
                        "changed": False,
                        "replayed": True,
                        "decision_id": str(previous["decision_id"]),
                        "team_revision": int(state["revision"]),
                        "events": [],
                        "team": build_team_capability_view(state),
                    }
            recoverable_binding_decision = any(
                str(item.get("kind", "") or "") == "set_role_skills"
                for item in commands
            )
            if recoverable_binding_decision and expected_revision is None:
                raise TeamControlPlaneError("Role-Skill binding changes require the current Team revision.")
            if expected_revision is not None and expected_revision != state["revision"]:
                raise TeamControlPlaneError("Team changed since this proposal. Request a new proposal.")

            binding_resolver = None
            needs_binding_lookup = any(
                str(item.get("kind", "") or "") == "set_role_skills"
                and bool(list(item.get("bindings", []) or []))
                for item in commands
            )
            if needs_binding_lookup:
                from goodteam_os.service import service_state_dir
                from goodteam_os.skills.library import SkillLibraryService

                library = SkillLibraryService(service_state_dir(), worker_roots=(self.workspace,))
                binding_resolver = library.binding_reference

            decision = decision_id or _new_id("dec")
            events: list[dict[str, object]] = []
            for raw_command in commands:
                if not str(raw_command.get("kind", "")).strip():
                    raise TeamControlPlaneError("Each team command requires a kind")
                events.extend(
                    _apply_command(
                        state,
                        raw_command,
                        actor=actor,
                        surface=surface,
                        decision_id=decision,
                        source_ref=source_ref,
                        reason=reason,
                        binding_resolver=binding_resolver,
                    )
                )
            if events:
                state["revision"] = int(state["revision"]) + 1
                for event in events:
                    event["team_revision"] = int(state["revision"])
            if normalized_key:
                executed = dict(state["executed_commands"])
                receipt = {
                    "decision_id": decision,
                    "executed_at": _now(),
                    "event_count": len(events),
                    "fingerprint": fingerprint,
                }
                if recoverable_binding_decision:
                    receipt["result"] = {
                        "changed": bool(events),
                        "team_revision": int(state["revision"]),
                        "events": deepcopy(events),
                    }
                executed[normalized_key] = receipt
                state["executed_commands"] = executed

            if recoverable_binding_decision:
                state["pending_events"] = deepcopy(events)
                self._write_state(state, durable=True)
                if events:
                    self._append_events_idempotent(events)
                    state["pending_events"] = []
                    self._write_state(state, durable=True)
            else:
                self._write_state(state)
                self._append_events(events)
            if events and config_path is not None:
                _sync_legacy_worker_config_view(config_path, state)
            return {
                "ok": True,
                "changed": bool(events),
                "replayed": False,
                "decision_id": decision,
                "team_revision": int(state["revision"]),
                "events": events,
                "team": build_team_capability_view(state),
            }


class _file_lock:
    """Cross-process advisory lock, mirroring the Task Assistant session store."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._descriptor: int | None = None

    def __enter__(self) -> "_file_lock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(self._descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None


# ----------------------------------------------------------------- migration


def build_team_state_from_legacy_config(project_key: str, target: dict[str, object]) -> dict[str, object]:
    """Materialize the canonical team state from legacy worker-role config.

    Migration preserves behavior exactly:

    - every legacy-dispatchable Role (the frozen
      :data:`MIGRATION_BASELINE_ROLES` baseline, plus any Role the project's
      legacy config explicitly used) becomes an **enabled** TeamRole; catalog
      roles added after the baseline migrate as **disabled** so future
      capability growth never silently expands an existing team;
    - ``desired_instances = N`` materializes N distinct, deterministically
      named Worker records;
    - per-role backend overrides, ``role_model_overrides``, and
      ``worker_type_settings`` (reasoning depth, additional prompt) are copied
      onto each materialized Worker's runtime;
    - the orchestrator stays a single logical Task Assistant owner, not a
      TeamRole, and keeps its own runtime configuration.
    """
    key = normalize_project_key(project_key)
    workers_config = dict(target.get("workers", {}) or {}) if isinstance(target.get("workers", {}), dict) else {}
    overrides = dict(target.get("role_model_overrides", {}) or {}) if isinstance(target.get("role_model_overrides", {}), dict) else {}
    settings = dict(target.get("worker_type_settings", {}) or {}) if isinstance(target.get("worker_type_settings", {}), dict) else {}
    default_provider = normalize_provider(dict(target.get("worker_backend", {}) or {}).get("mode", "codex") or "codex")

    roles: dict[str, dict[str, str]] = {}
    workers: list[dict[str, object]] = []
    for catalog_item in ROLE_CATALOG:
        role = catalog_item["key"]
        role_settings = dict(settings.get(role, {}) or {}) if isinstance(settings.get(role, {}), dict) else {}
        # Explicit legacy config is evidence the capability was already in use.
        has_legacy_config = (
            role in settings
            or role in workers_config
            or bool(str(overrides.get(role, "") or "").strip())
        )
        enabled = role in MIGRATION_BASELINE_ROLES or has_legacy_config
        roles[role] = {"state": "enabled" if enabled else "disabled", "updated_at": _now()}
        if not enabled:
            continue
        desired_raw = role_settings.get("desired_instances", 1)
        try:
            desired = int(desired_raw)
        except (TypeError, ValueError):
            desired = 1
        desired = min(max(desired, 1), 8)
        runtime = {
            "provider": normalize_provider(workers_config.get(role, "") or default_provider),
            "model": _model_part(str(overrides.get(role, "") or "")),
            "reasoning_depth": validate_reasoning_depth(role_settings.get("reasoning_depth", "default")),
            "additional_prompt": str(role_settings.get("additional_prompt", "") or "").strip()[:ADDITIONAL_PROMPT_MAX_CHARS],
        }
        for index in range(desired):
            letter = chr(ord("a") + index)
            workers.append(
                {
                    "id": f"{role}-{letter}",
                    "name": f"{worker_prefix(role)} {letter.upper()}",
                    "role": role,
                    "lifecycle": "active",
                    "runtime": dict(runtime),
                    "created_at": _now(),
                    "updated_at": _now(),
                    "released_at": "",
                    "deleted_at": "",
                }
            )

    manager_settings = dict(settings.get(MANAGER_ROLE, {}) or {}) if isinstance(settings.get(MANAGER_ROLE, {}), dict) else {}
    manager_runtime = {
        "provider": normalize_provider(workers_config.get(MANAGER_ROLE, "") or default_provider),
        "model": _model_part(str(overrides.get(MANAGER_ROLE, "") or "")),
        "reasoning_depth": validate_reasoning_depth(manager_settings.get("reasoning_depth", "default")),
        "additional_prompt": str(manager_settings.get("additional_prompt", "") or "").strip()[:ADDITIONAL_PROMPT_MAX_CHARS],
    }
    return {
        "schema_version": TEAM_CONTROL_PLANE_SCHEMA_VERSION,
        "project_key": key,
        "revision": 1,
        "roles": roles,
        "workers": workers,
        "manager": {"runtime": manager_runtime, "updated_at": _now()},
        "skill_bindings": {
            **{role_subject(role): default_binding(role_subject(role), project_key=key) for role in ROLE_KEYS},
            **{
                workflow_role_subject(role): default_binding(workflow_role_subject(role), project_key=key)
                for role in WORKFLOW_ROLE_PROVIDERS
            },
            manager_subject(MANAGER_ROLE): default_binding(manager_subject(MANAGER_ROLE), project_key=key),
        },
        "migrated": {
            "source": "legacy_worker_config",
            "at": _now(),
            "legacy_digest": _legacy_config_digest(target),
        },
        "executed_commands": {},
        "pending_events": [],
    }


def _model_part(role_model: str) -> str:
    value = str(role_model or "").strip()
    if ":" in value:
        return value.split(":", 1)[1].strip()
    return value


def _legacy_config_digest(target: dict[str, object]) -> str:
    import hashlib

    relevant = {
        "worker_backend": target.get("worker_backend", {}),
        "workers": target.get("workers", {}),
        "role_model_overrides": target.get("role_model_overrides", {}),
        "worker_type_settings": target.get("worker_type_settings", {}),
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def migration_events(
    project_key: str,
    state: dict[str, object],
    legacy_target: dict[str, object],
) -> list[dict[str, object]]:
    decision_id = _new_id("dec")
    timestamp = _now()
    events: list[dict[str, object]] = []
    for role, role_state in dict(state["roles"]).items():
        enabled = str(role_state.get("state", "enabled")) == "enabled"
        events.append(
            {
                "id": _new_id("evt"),
                "decision_id": decision_id,
                "project_key": project_key,
                "timestamp": timestamp,
                "actor": "migration",
                "surface": "migration",
                "source_ref": {"legacy_digest": dict(state["migrated"]).get("legacy_digest", "")},
                "event": f"{TEAM_ROLE_EVENT_PREFIX}.{'enabled' if enabled else 'disabled'}",
                "target": {"kind": "team_role", "id": role},
                "reason": (
                    "Migrated from legacy worker-role config: role was dispatchable."
                    if enabled
                    else "Role added to the catalog after this project's migration baseline; not enabled by migration."
                ),
                "before": {"state": "absent"},
                "after": {"state": role_state["state"]},
                "cause": "migration_init",
            }
        )
    for worker in list(state["workers"]):
        events.append(
            {
                "id": _new_id("evt"),
                "decision_id": decision_id,
                "project_key": project_key,
                "timestamp": timestamp,
                "actor": "migration",
                "surface": "migration",
                "source_ref": {"legacy_digest": dict(state["migrated"]).get("legacy_digest", "")},
                "event": f"{WORKER_EVENT_PREFIX}.initialized",
                "target": {"kind": "worker", "id": worker["id"]},
                "reason": "Materialized worker from legacy desired_instances config.",
                "before": None,
                "after": _worker_payload(worker),
                "cause": "migration_init",
            }
        )
    manager = dict(state["manager"])
    events.append(
        {
            "id": _new_id("evt"),
            "decision_id": decision_id,
            "project_key": project_key,
            "timestamp": timestamp,
            "actor": "migration",
            "surface": "migration",
            "source_ref": {"legacy_digest": dict(state["migrated"]).get("legacy_digest", "")},
            "event": f"{MANAGER_EVENT_PREFIX}.initialized",
            "target": {"kind": "manager", "id": MANAGER_ROLE},
            "reason": "Task Assistant remains the single logical primary owner.",
            "before": None,
            "after": {"runtime": dict(manager["runtime"])},
            "cause": "migration_init",
        }
    )
    return events


def _worker_payload(worker: dict[str, object]) -> dict[str, object]:
    return {
        "id": worker["id"],
        "name": worker["name"],
        "role": worker["role"],
        "lifecycle": worker["lifecycle"],
        "runtime": dict(worker["runtime"]),
    }


# ------------------------------------------------------------------ commands


def preview_team_commands(state: dict[str, object], commands: list[dict[str, object]]) -> dict[str, object]:
    """Project the real command effects on a copy; never write state or audit events."""
    simulated = deepcopy(state)
    changes = []
    for index, command in enumerate(commands):
        before_workers = {worker["id"]: dict(worker) for worker in _workers(simulated)}
        events = _apply_command(
            simulated, command, actor="manager", surface="manager_conversation",
            decision_id="preview", source_ref=None, reason="",
        )
        for event in events:
            target = dict(event["target"])
            if target["kind"] == "worker":
                worker = before_workers.get(target["id"]) or _find_worker(simulated, target["id"])
                target.update(name=worker["name"], role=worker["role"], label=role_label(worker["role"]))
            elif target["kind"] == "team_role":
                target.update(role=target["id"], label=role_label(target["id"]))
            elif target["kind"] == "role_skill_binding":
                subject = str(target["id"])
                if subject.startswith("role:"):
                    role = subject.removeprefix("role:")
                    target.update(role=role, label=role_label(role))
                elif subject.startswith("workflow_role:"):
                    role = subject.removeprefix("workflow_role:")
                    target.update(
                        role=role,
                        label="Project Intake" if role == "project_intake" else role_label(role),
                    )
                else:
                    target.update(role=MANAGER_ROLE, label="Task Assistant")
            else:
                target.update(role=MANAGER_ROLE, label="Task Assistant")
            changes.append({
                "command_index": index, "target": target,
                "before": deepcopy(event["before"]), "after": deepcopy(event["after"]),
            })
    return {"schema_version": "team_decision_preview.v1", "team_revision": state["revision"], "changes": changes}


def _apply_command(
    state: dict[str, object],
    command: dict[str, object],
    *,
    actor: str,
    surface: str,
    decision_id: str,
    source_ref: dict[str, object] | None,
    reason: str,
    binding_resolver=None,
) -> list[dict[str, object]]:
    kind = str(command.get("kind", "")).strip()
    if kind == "set_role_skills":
        return _cmd_set_role_skills(
            state,
            command,
            actor=actor,
            surface=surface,
            decision_id=decision_id,
            source_ref=source_ref,
            reason=reason,
            binding_resolver=binding_resolver,
        )
    handlers = {
        "enable_role": _cmd_enable_role,
        "disable_role": _cmd_disable_role,
        "initialize_worker": _cmd_initialize_worker,
        "activate_worker": _cmd_activate_worker,
        "inactivate_worker": _cmd_inactivate_worker,
        "release_worker": _cmd_release_worker,
        "delete_worker": _cmd_delete_worker,
        "restore_worker": _cmd_restore_worker,
        "change_worker_runtime": _cmd_change_worker_runtime,
        "change_worker_role": _cmd_change_worker_role,
        "rename_worker": _cmd_rename_worker,
        "change_manager_runtime": _cmd_change_manager_runtime,
    }
    handler = handlers.get(kind)
    if handler is None:
        raise TeamControlPlaneError(f"Unknown team command: {kind}")
    return handler(
        state,
        command,
        actor=actor,
        surface=surface,
        decision_id=decision_id,
        source_ref=source_ref,
        reason=reason,
    )


def _event(
    *,
    project_key: str,
    decision_id: str,
    actor: str,
    surface: str,
    source_ref: dict[str, object] | None,
    event: str,
    target: dict[str, str],
    reason: str,
    before: object,
    after: object,
    cause: str,
) -> dict[str, object]:
    return {
        "id": _new_id("evt"),
        "decision_id": decision_id,
        "project_key": project_key,
        "timestamp": _now(),
        "actor": actor,
        "surface": surface,
        "source_ref": dict(source_ref or {}),
        "event": event,
        "target": target,
        "reason": reason,
        "before": before,
        "after": after,
        "cause": cause,
    }


def _roles(state: dict[str, object]) -> dict[str, dict[str, str]]:
    return dict(state["roles"])  # type: ignore[arg-type]


def _workers(state: dict[str, object]) -> list[dict[str, object]]:
    return list(state["workers"])  # type: ignore[arg-type]


def _skill_bindings(state: dict[str, object]) -> dict[str, dict[str, object]]:
    return deepcopy(dict(state.get("skill_bindings", {}) or {}))


def _binding_subject(command: dict[str, object]) -> str:
    subject = str(command.get("subject", "") or "").strip()
    if not subject and command.get("role"):
        subject = role_subject(_command_role(command))
    allowed = {
        *(role_subject(role) for role in ROLE_KEYS),
        *(workflow_role_subject(role) for role in WORKFLOW_ROLE_PROVIDERS),
        manager_subject(MANAGER_ROLE),
    }
    if subject not in allowed:
        raise TeamControlPlaneError(f"Unknown Role-Skill binding subject: {subject or '(blank)'}")
    return subject


def _cmd_set_role_skills(
    state,
    command,
    *,
    actor,
    surface,
    decision_id,
    source_ref,
    reason,
    binding_resolver,
):
    subject = _binding_subject(command)
    bindings = _skill_bindings(state)
    current = normalize_binding(
        bindings.get(subject, {}),
        subject=subject,
        project_key=str(state["project_key"]),
    )
    expected = command.get("expected_binding_revision")
    if type(expected) is not int:
        raise TeamControlPlaneError("Role-Skill binding changes require the current binding revision.")
    if expected != current["binding_revision"]:
        raise TeamControlPlaneError("Skill binding changed since this edit. Reload the Role before saving.")
    mode = str(command.get("mode", "custom") or "").strip()
    try:
        items = normalize_items(command.get("bindings", []), resolver=binding_resolver)
        candidate = normalize_binding(
            {
                "schema_version": current["schema_version"],
                "project_key": current["project_key"],
                "subject": subject,
                "role_contract_ref": current["role_contract_ref"],
                "binding_revision": current["binding_revision"],
                "mode": mode,
                "items": items,
                "updated_at": current.get("updated_at", ""),
            },
            subject=subject,
            project_key=str(state["project_key"]),
        )
    except SkillError as exc:
        raise TeamControlPlaneError(str(exc)) from exc

    if candidate["mode"] == current["mode"] and candidate["items"] == current["items"]:
        return []
    before = project_binding(current)
    candidate["binding_revision"] = int(current["binding_revision"]) + 1
    candidate["updated_at"] = _now()
    bindings[subject] = candidate
    state["skill_bindings"] = bindings
    after = project_binding(candidate)
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event="role_skills.changed",
            target={"kind": "role_skill_binding", "id": subject},
            reason=reason or f"Update methods for {subject}.",
            before=before,
            after=after,
            cause="user_command",
        )
    ]


def _find_worker(state: dict[str, object], worker_id: object) -> dict[str, object]:
    identifier = str(worker_id or "").strip()
    for worker in _workers(state):
        if str(worker["id"]) == identifier:
            return worker
    raise TeamControlPlaneError(f"Unknown worker: {identifier or '(blank)'}")


def _cmd_enable_role(state, command, *, actor, surface, decision_id, source_ref, reason):
    role = _command_role(command)
    roles = _roles(state)
    role_state = dict(roles.get(role) or {"state": "enabled", "updated_at": ""})
    before = {"state": role_state.get("state", "enabled")}
    if before["state"] == "enabled":
        return []
    role_state["state"] = "enabled"
    role_state["updated_at"] = _now()
    roles[role] = role_state
    state["roles"] = roles
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{TEAM_ROLE_EVENT_PREFIX}.enabled",
            target={"kind": "team_role", "id": role},
            reason=reason or f"Enable {role_label(role)} capability.",
            before=before,
            after={"state": "enabled"},
            cause="user_command",
        )
    ]


def _cmd_disable_role(state, command, *, actor, surface, decision_id, source_ref, reason):
    role = _command_role(command)
    roles = _roles(state)
    role_state = dict(roles.get(role) or {"state": "enabled", "updated_at": ""})
    before = {"state": role_state.get("state", "enabled")}
    events: list[dict[str, object]] = []
    if before["state"] != "disabled":
        role_state["state"] = "disabled"
        role_state["updated_at"] = _now()
        roles[role] = role_state
        state["roles"] = roles
        events.append(
            _event(
                project_key=str(state["project_key"]),
                decision_id=decision_id,
                actor=actor,
                surface=surface,
                source_ref=source_ref,
                event=f"{TEAM_ROLE_EVENT_PREFIX}.disabled",
                target={"kind": "team_role", "id": role},
                reason=reason or f"Disable {role_label(role)} capability.",
                before=before,
                after={"state": "disabled"},
                cause="user_command",
            )
        )
    # Cascade: active workers of the role become inactive. They are never
    # released or deleted by a role disable.
    for worker in _workers(state):
        if str(worker["role"]) != role or worker["lifecycle"] != "active":
            continue
        worker["lifecycle"] = "inactive"
        worker["updated_at"] = _now()
        events.append(
            _event(
                project_key=str(state["project_key"]),
                decision_id=decision_id,
                actor="system",
                surface=surface,
                source_ref=source_ref,
                event=f"{WORKER_EVENT_PREFIX}.inactivated",
                target={"kind": "worker", "id": worker["id"]},
                reason=f"{role_label(role)} capability was disabled by this decision.",
                before={"lifecycle": "active"},
                after={"lifecycle": "inactive"},
                cause="cascade",
            )
        )
    return events


def _next_worker_identity(state: dict[str, object], role: str) -> tuple[str, str]:
    existing_ids = {str(worker["id"]) for worker in _workers(state)}
    index = 0
    while True:
        letter = chr(ord("a") + index)
        candidate = f"{role}-{letter}"
        if candidate not in existing_ids:
            return candidate, f"{worker_prefix(role)} {letter.upper()}"
        index += 1


def _cmd_initialize_worker(state, command, *, actor, surface, decision_id, source_ref, reason):
    role = _command_role(command)
    if role not in ROLE_KEYS:
        raise TeamControlPlaneError(f"Unknown role: {role}")
    roles = _roles(state)
    events: list[dict[str, object]] = []
    role_state = dict(roles.get(role) or {"state": "enabled", "updated_at": ""})
    if role_state.get("state") != "enabled":
        if not bool(command.get("ensure_role_enabled", False)):
            raise TeamControlPlaneError(
                f"{role_label(role)} is disabled for this team. Enable the capability before initializing a Worker."
            )
        events.extend(
            _cmd_enable_role(
                state,
                {"kind": "enable_role", "role": role},
                actor=actor,
                surface=surface,
                decision_id=decision_id,
                source_ref=source_ref,
                reason=reason,
            )
        )
    runtime = _sanitize_worker_runtime(command.get("runtime", {}))
    # Workers own their runtime; a new Worker without an explicit provider uses
    # the product default backend convention.
    runtime["provider"] = validate_provider(runtime["provider"] or "codex")
    worker_id = str(command.get("worker_id", "") or "").strip()
    name = str(command.get("name", "") or "").strip()
    if worker_id:
        if any(str(worker["id"]) == worker_id for worker in _workers(state)):
            raise TeamControlPlaneError(f"Worker id already exists: {worker_id}")
    else:
        worker_id, generated_name = _next_worker_identity(state, role)
        name = name or generated_name
    if not name:
        name = worker_id
    name = validate_worker_name(name)
    lifecycle = "active" if dict(_roles(state).get(role) or {}).get("state") == "enabled" else "inactive"
    worker = {
        "id": worker_id,
        "name": name,
        "role": role,
        "lifecycle": lifecycle,
        "runtime": runtime,
        "created_at": _now(),
        "updated_at": _now(),
        "released_at": "",
        "deleted_at": "",
    }
    workers = _workers(state)
    workers.append(worker)
    state["workers"] = workers
    events.append(
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{WORKER_EVENT_PREFIX}.initialized",
            target={"kind": "worker", "id": worker_id},
            reason=reason or f"Initialize {name} for {role_label(role)}.",
            before=None,
            after=_worker_payload(worker),
            cause="user_command",
        )
    )
    return events


def _lifecycle_transition(
    state,
    command,
    *,
    actor,
    surface,
    decision_id,
    source_ref,
    reason,
    allowed_from: tuple[str, ...],
    to_lifecycle: str,
    event_name: str,
    timestamp_field: str = "",
):
    worker = _find_worker(state, command.get("worker_id"))
    before = {"lifecycle": worker["lifecycle"]}
    if worker["lifecycle"] == to_lifecycle:
        return []
    if worker["lifecycle"] not in allowed_from:
        raise TeamControlPlaneError(
            f"Worker {worker['id']} cannot move from {worker['lifecycle']} to {to_lifecycle}"
        )
    worker["lifecycle"] = to_lifecycle
    worker["updated_at"] = _now()
    if timestamp_field:
        worker[timestamp_field] = _now()
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=event_name,
            target={"kind": "worker", "id": worker["id"]},
            reason=reason or f"{event_name} {worker['name']}.",
            before=before,
            after={"lifecycle": to_lifecycle},
            cause="user_command",
        )
    ]


def _cmd_activate_worker(state, command, **context):
    return _lifecycle_transition(
        state,
        command,
        **context,
        allowed_from=("inactive",),
        to_lifecycle="active",
        event_name=f"{WORKER_EVENT_PREFIX}.activated",
    )


def _cmd_inactivate_worker(state, command, **context):
    return _lifecycle_transition(
        state,
        command,
        **context,
        allowed_from=("active",),
        to_lifecycle="inactive",
        event_name=f"{WORKER_EVENT_PREFIX}.inactivated",
    )


def _cmd_release_worker(state, command, **context):
    return _lifecycle_transition(
        state,
        command,
        **context,
        allowed_from=("active", "inactive"),
        to_lifecycle="released",
        event_name=f"{WORKER_EVENT_PREFIX}.released",
        timestamp_field="released_at",
    )


def _cmd_delete_worker(state, command, **context):
    # Delete is a tombstone: visibility/lifecycle change only, never a
    # physical erase. Historical identity remains resolvable.
    return _lifecycle_transition(
        state,
        command,
        **context,
        allowed_from=("active", "inactive", "released"),
        to_lifecycle="deleted",
        event_name=f"{WORKER_EVENT_PREFIX}.deleted",
        timestamp_field="deleted_at",
    )


def _cmd_restore_worker(state, command, **context):
    return _lifecycle_transition(
        state,
        command,
        **context,
        allowed_from=("released", "deleted"),
        to_lifecycle="inactive",
        event_name=f"{WORKER_EVENT_PREFIX}.restored",
    )


def _cmd_change_worker_runtime(state, command, *, actor, surface, decision_id, source_ref, reason):
    worker = _find_worker(state, command.get("worker_id"))
    runtime = dict(worker["runtime"])
    before = dict(runtime)
    if "provider" in command:
        runtime["provider"] = validate_provider(command.get("provider"))
    if "model" in command:
        runtime["model"] = str(command.get("model", "") or "").strip()
    if "reasoning_depth" in command:
        runtime["reasoning_depth"] = validate_reasoning_depth(command.get("reasoning_depth"))
    if "additional_prompt" in command:
        runtime["additional_prompt"] = str(command.get("additional_prompt", "") or "").strip()[:ADDITIONAL_PROMPT_MAX_CHARS]
    if runtime == before:
        return []
    worker["runtime"] = runtime
    worker["updated_at"] = _now()
    changed = {key: {"before": before.get(key, ""), "after": runtime[key]} for key in runtime if runtime[key] != before.get(key)}
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{WORKER_EVENT_PREFIX}.runtime_changed",
            target={"kind": "worker", "id": worker["id"]},
            reason=reason or f"Change runtime for {worker['name']}.",
            before=before,
            after=dict(runtime),
            cause="user_command",
        )
        | {"changed_fields": changed}
    ]


def _cmd_change_worker_role(state, command, *, actor, surface, decision_id, source_ref, reason):
    worker = _find_worker(state, command.get("worker_id"))
    target_role = _command_role(command)
    if target_role not in ROLE_KEYS:
        raise TeamControlPlaneError(f"Unknown role: {target_role}")
    if str(worker["role"]) == target_role:
        return []
    roles = _roles(state)
    role_state = dict(roles.get(target_role) or {"state": "enabled"})
    if role_state.get("state") != "enabled":
        raise TeamControlPlaneError(
            f"{role_label(target_role)} is disabled for this team. Enable the capability before moving a Worker onto it."
        )
    before = {"role": worker["role"]}
    worker["role"] = target_role
    worker["updated_at"] = _now()
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{WORKER_EVENT_PREFIX}.role_changed",
            target={"kind": "worker", "id": worker["id"]},
            reason=reason or f"Move {worker['name']} to {role_label(target_role)}.",
            before=before,
            after={"role": target_role},
            cause="user_command",
        )
    ]


def _cmd_rename_worker(state, command, *, actor, surface, decision_id, source_ref, reason):
    worker = _find_worker(state, command.get("worker_id"))
    name = validate_worker_name(command.get("name"))
    if name == worker["name"]:
        return []
    before = {"name": worker["name"]}
    worker["name"] = name
    worker["updated_at"] = _now()
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{WORKER_EVENT_PREFIX}.renamed",
            target={"kind": "worker", "id": worker["id"]},
            reason=reason or f"Rename worker to {name}.",
            before=before,
            after={"name": name},
            cause="user_command",
        )
    ]


def _cmd_change_manager_runtime(state, command, *, actor, surface, decision_id, source_ref, reason):
    manager = dict(state["manager"])  # type: ignore[arg-type]
    runtime = dict(manager.get("runtime", {}) or {})
    before = dict(runtime)
    if "provider" in command:
        runtime["provider"] = validate_provider(command.get("provider"))
    if "model" in command:
        runtime["model"] = str(command.get("model", "") or "").strip()
    if "reasoning_depth" in command:
        runtime["reasoning_depth"] = validate_reasoning_depth(command.get("reasoning_depth"))
    if "additional_prompt" in command:
        runtime["additional_prompt"] = str(command.get("additional_prompt", "") or "").strip()[:ADDITIONAL_PROMPT_MAX_CHARS]
    if runtime == before:
        return []
    manager["runtime"] = runtime
    manager["updated_at"] = _now()
    state["manager"] = manager
    return [
        _event(
            project_key=str(state["project_key"]),
            decision_id=decision_id,
            actor=actor,
            surface=surface,
            source_ref=source_ref,
            event=f"{MANAGER_EVENT_PREFIX}.runtime_changed",
            target={"kind": "manager", "id": MANAGER_ROLE},
            reason=reason or "Change Task Assistant runtime.",
            before=before,
            after=dict(runtime),
            cause="user_command",
        )
    ]


def _command_role(command: dict[str, object]) -> str:
    role = str(command.get("role", "") or "").strip()
    if not role:
        raise TeamControlPlaneError("Team command requires a role")
    if role not in ROLE_KEYS:
        raise TeamControlPlaneError(f"Unknown role: {role}")
    return role


# -------------------------------------------------------------- read models


def worker_runtime_eligible(provider: object, *, backend_doctor=None) -> bool:
    """Runtime eligibility: a known provider whose backend is actually usable."""
    value = normalize_provider(provider)
    if not value or value not in WORKER_PROVIDER_OPTIONS:
        return False
    doctor = backend_doctor
    if doctor is None:
        # Late binding keeps monkeypatched doctors effective in tests.
        doctor = worker_backend_doctor
    try:
        return bool(dict(doctor(value)).get("ready", False))
    except Exception:
        return False


def derive_role_capability(
    role: str,
    team_role_state: str,
    workers: list[dict[str, object]],
    *,
    backend_doctor=None,
) -> str:
    """Normalized operational capability for one TeamRole.

    - disabled: the TeamRole is disabled; the team does not have the capability.
    - unstaffed: enabled with no rostered Worker at all (legitimate state).
    - degraded: enabled and rostered, but no Worker is currently executable.
    - ready: enabled with at least one active, runtime-eligible Worker.
    """
    if team_role_state != "enabled":
        return "disabled"
    rostered = [w for w in workers if w["lifecycle"] in ROSTERED_LIFECYCLES]
    if not rostered:
        return "unstaffed"
    for worker in rostered:
        if worker["lifecycle"] == "active" and worker_runtime_eligible(
            dict(worker["runtime"]).get("provider"), backend_doctor=backend_doctor
        ):
            return "ready"
    return "degraded"


def build_team_capability_view(
    state: dict[str, object],
    *,
    backend_doctor=None,
) -> dict[str, object]:
    workers_by_role: dict[str, list[dict[str, object]]] = {role: [] for role in ROLE_KEYS}
    for worker in _workers(state):
        role = str(worker["role"])
        if role in workers_by_role and worker["lifecycle"] in ROSTERED_LIFECYCLES:
            # Current truth shows rostered members only; released/deleted
            # workers stay resolvable through the decision ledger.
            workers_by_role[role].append(worker)
    roles: list[dict[str, object]] = []
    roles_state = _roles(state)
    for catalog_item in ROLE_CATALOG:
        role = catalog_item["key"]
        team_role = dict(roles_state.get(role) or {"state": "enabled"})
        role_workers = sorted(workers_by_role[role], key=lambda w: (str(w["created_at"]), str(w["id"])))
        projected_workers = []
        for worker in role_workers:
            runtime = dict(worker["runtime"])
            projected_workers.append(
                {
                    "id": worker["id"],
                    "name": worker["name"],
                    "lifecycle": worker["lifecycle"],
                    "provider": runtime.get("provider", ""),
                    "model": runtime.get("model", ""),
                    "reasoning_depth": runtime.get("reasoning_depth", "default"),
                    "additional_prompt": runtime.get("additional_prompt", ""),
                    "dispatchable": bool(
                        worker["lifecycle"] == "active"
                        and worker_runtime_eligible(runtime.get("provider"), backend_doctor=backend_doctor)
                    ),
                    "updated_at": worker["updated_at"],
                }
            )
        binding = normalize_binding(
            dict(state.get("skill_bindings", {}) or {}).get(role_subject(role), {}),
            subject=role_subject(role),
            project_key=str(state["project_key"]),
        )
        roles.append(
            {
                "role": role,
                "label": catalog_item["label"],
                "state": team_role.get("state", "enabled"),
                "capability": derive_role_capability(
                    role,
                    str(team_role.get("state", "enabled")),
                    role_workers,
                    backend_doctor=backend_doctor,
                ),
                "skill_binding": project_binding(binding),
                "workers": projected_workers,
            }
        )
    manager = dict(state["manager"])  # type: ignore[arg-type]
    return {
        "schema_version": TEAM_CAPABILITY_VIEW_SCHEMA_VERSION,
        "project_key": state["project_key"],
        "revision": state["revision"],
        "roles": roles,
        "workflow_roles": [
            {
                "role": role,
                "label": "Project Intake" if role == "project_intake" else role_label(role),
                "provided_by_team_role": provider,
                "skill_binding": project_binding(
                    normalize_binding(
                        dict(state.get("skill_bindings", {}) or {}).get(workflow_role_subject(role), {}),
                        subject=workflow_role_subject(role),
                        project_key=str(state["project_key"]),
                    )
                ),
            }
            for role, provider in WORKFLOW_ROLE_PROVIDERS.items()
        ],
        "manager": {
            "role": MANAGER_ROLE,
            "label": "Task Assistant",
            "runtime": dict(manager.get("runtime", {}) or {}),
            "skill_binding": project_binding(
                normalize_binding(
                    dict(state.get("skill_bindings", {}) or {}).get(manager_subject(MANAGER_ROLE), {}),
                    subject=manager_subject(MANAGER_ROLE),
                    project_key=str(state["project_key"]),
                )
            ),
            "updated_at": manager.get("updated_at", ""),
        },
    }


def build_team_execution_snapshot(state: dict[str, object]) -> dict[str, object]:
    """Immutable execution-time truth for one durable run/execution epoch.

    Later Team changes (runtime edits, lifecycle moves, role changes, renames,
    deletes) must never rewrite what a historical run bound at start.
    """
    roles = {
        role: {"state": str(dict(roles_state).get("state", "enabled"))}
        for role, roles_state in _roles(state).items()
    }
    workers = {
        str(worker["id"]): {
            "name": worker["name"],
            "role": worker["role"],
            "lifecycle": worker["lifecycle"],
            "provider": dict(worker["runtime"]).get("provider", ""),
            "model": dict(worker["runtime"]).get("model", ""),
            "reasoning_depth": dict(worker["runtime"]).get("reasoning_depth", "default"),
            "additional_prompt": dict(worker["runtime"]).get("additional_prompt", ""),
        }
        for worker in _workers(state)
    }
    manager = dict(state["manager"])  # type: ignore[arg-type]
    return {
        "schema_version": TEAM_EXECUTION_SNAPSHOT_SCHEMA_VERSION,
        "revision": state["revision"],
        "captured_at": _now(),
        "roles": roles,
        "workers": workers,
        "manager": {"runtime": dict(manager.get("runtime", {}) or {})},
    }


# ------------------------------------------------------- dispatch resolution


def attached_team_state(target: object) -> dict[str, object] | None:
    """Return the control-plane state attached to a parsed project target."""
    if not isinstance(target, dict):
        return None
    state = target.get(TEAM_STATE_TARGET_KEY)
    return state if isinstance(state, dict) and state.get("schema_version") == TEAM_CONTROL_PLANE_SCHEMA_VERSION else None


def attach_team_control_plane(target: dict[str, object], config_path: Path | None) -> None:
    """Attach existing canonical team state to a parsed project target.

    Called from ``load_project_target`` so every consumer (dispatch, manager,
    console, doctor) sees the same current team truth. This is read-only by
    design: rejected/failing requests must never mutate the workspace. The
    lazy migration from legacy worker config happens only at explicit team
    touchpoints (Team surface, chat turn, workflow start, control-plane
    command) via :meth:`TeamControlPlaneStore.load_or_migrate`.
    """
    if config_path is None:
        return
    key = str(target.get("key", "") or "").strip()
    if not key or Path(config_path).parent.name != "projects":
        return
    try:
        workspace = Path(config_path).expanduser().resolve().parent.parent
    except OSError:
        return
    state = TeamControlPlaneStore(workspace, key).read()
    if state is not None:
        target[TEAM_STATE_TARGET_KEY] = state


def ensure_team_state(workspace: Path, project_key: str, config_path: Path | None) -> dict[str, object] | None:
    """Load team state at an explicit team touchpoint, migrating once if needed."""
    store = TeamControlPlaneStore(workspace, project_key)
    if store.state_file_exists():
        return store.read()
    legacy_target = None
    if config_path is not None and Path(config_path).is_file():
        from goodteam_os.projects import load_project_target

        legacy_target = load_project_target(config_path)
    return store.load_or_migrate(legacy_target=legacy_target, config_path=config_path)


def active_workers_for_role(state: dict[str, object], role: str) -> list[dict[str, object]]:
    return [
        worker
        for worker in _workers(state)
        if str(worker["role"]) == role and worker["lifecycle"] in DISPATCHABLE_LIFECYCLES
    ]


def resolve_worker_for_role(target: dict[str, object], role: str) -> dict[str, object] | None:
    """Resolve the Worker that will execute a role, enforcing dispatch gates.

    One resolution carries the **complete execution runtime** (adapter, model,
    reasoning depth, additional prompt, pi thinking level, desired instance
    count) of the exact Worker that will run, so callers can never mix one
    Worker's identity with another Worker's settings.

    Returns ``None`` when the target carries no team state (legacy/unmigrated
    project), letting callers fall back to legacy resolution. Raises
    :class:`TeamDispatchBlocked` when the TeamRole is disabled or no active,
    runtime-eligible Worker exists.
    """
    state = attached_team_state(target)
    if state is None:
        return None
    return resolve_worker_from_state(state, role)


def resolve_worker_from_state(state: dict[str, object], role: str) -> dict[str, object] | None:
    """Resolve a dispatching Worker directly from team state (gates included)."""
    if role == MANAGER_ROLE:
        # The Task Assistant is the single logical primary owner. It is not a
        # TeamRole and is never gated by role membership.
        return _manager_resolution(state)
    roles_state = _roles(state)
    team_role = dict(roles_state.get(role) or {"state": "enabled"})
    if str(team_role.get("state", "enabled")) != "enabled":
        raise TeamDispatchBlocked(
            "team_role_disabled",
            f"The {role_label(role)} capability is disabled for this team. Enable it before dispatching work.",
            role=role,
        )
    active = active_workers_for_role(state, role)
    eligible = [
        worker
        for worker in active
        if worker_runtime_eligible(dict(worker["runtime"]).get("provider"))
    ]
    if not eligible:
        raise TeamDispatchBlocked(
            "team_worker_unavailable",
            f"No active, runtime-eligible Worker provides the {role_label(role)} capability.",
            role=role,
        )
    eligible.sort(key=lambda w: (str(w["created_at"]), str(w["id"])))
    desired = min(max(len(active), 1), 8)
    return _worker_resolution(eligible[0], desired_instances=desired)


def _pi_level_for(adapter: str, reasoning_depth: str) -> str:
    from goodteam_os.workers import SESSION_BACKENDS

    return reasoning_depth if adapter in SESSION_BACKENDS and reasoning_depth != "default" else ""


def _worker_resolution(worker: dict[str, object], *, desired_instances: int = 1) -> dict[str, object]:
    runtime = dict(worker["runtime"])
    adapter = normalize_provider(runtime.get("provider", ""))
    reasoning_depth = str(runtime.get("reasoning_depth", "default") or "default")
    return {
        "worker_id": str(worker["id"]),
        "worker_name": str(worker["name"]),
        "role": str(worker["role"]),
        "adapter": adapter,
        "model": str(runtime.get("model", "") or ""),
        "reasoning_depth": reasoning_depth,
        "additional_prompt": str(runtime.get("additional_prompt", "") or ""),
        "pi_thinking_level": _pi_level_for(adapter, reasoning_depth),
        "desired_instances": desired_instances,
    }


def _manager_resolution(state: dict[str, object]) -> dict[str, object]:
    manager = dict(state["manager"])  # type: ignore[arg-type]
    runtime = dict(manager.get("runtime", {}) or {})
    adapter = normalize_provider(runtime.get("provider", ""))
    reasoning_depth = str(runtime.get("reasoning_depth", "default") or "default")
    return {
        "worker_id": MANAGER_ROLE,
        "worker_name": "Task Assistant",
        "role": MANAGER_ROLE,
        "adapter": adapter,
        "model": str(runtime.get("model", "") or ""),
        "reasoning_depth": reasoning_depth,
        "additional_prompt": str(runtime.get("additional_prompt", "") or ""),
        "pi_thinking_level": _pi_level_for(adapter, reasoning_depth),
        "desired_instances": 1,
    }


def execute_team_commands(
    workspace: Path,
    project_key: str,
    commands: list[dict[str, object]],
    *,
    actor: str,
    surface: str,
    reason: str = "",
    source_ref: dict[str, object] | None = None,
    decision_id: str | None = None,
    command_key: str = "",
    config_path: Path | None = None,
    expected_revision: int | None = None,
) -> dict[str, object]:
    """Execute one authorized team decision through the control plane.

    This is the single mutation entry point for every surface (Manager
    conversation, Team UI, API adapter, migration tooling).
    """
    store = TeamControlPlaneStore(workspace, project_key)
    if not store.state_file_exists():
        # A first team command on a legacy project materializes the state.
        ensure_team_state(workspace, project_key, config_path)
    return store.execute(
        commands,
        actor=actor,
        surface=surface,
        reason=reason,
        source_ref=source_ref,
        decision_id=decision_id,
        command_key=command_key,
        config_path=config_path,
        expected_revision=expected_revision,
    )


def load_team_view(workspace: Path, project_key: str) -> dict[str, object] | None:
    """Load the normalized capability view without mutating anything."""
    store = TeamControlPlaneStore(workspace, project_key)
    state = store.read()
    if state is None:
        return None
    return build_team_capability_view(state)


def team_view_for_config(workspace: Path, project_key: str, config_path: Path | None) -> dict[str, object] | None:
    """Capability view at a team touchpoint (chat turn), migrating once if needed."""
    state = ensure_team_state(workspace, project_key, config_path)
    if state is None:
        return None
    return build_team_capability_view(state)


# ----------------------------------------------- legacy compatibility view


def _sync_legacy_worker_config_view(config_path: Path, state: dict[str, object]) -> None:
    """Rewrite the legacy YAML worker blocks as a derived compatibility view.

    The canonical truth is the control-plane state. The legacy blocks
    (``worker_backend``/``workers``/``role_model_overrides``/
    ``worker_type_settings``) remain on disk only so diagnostics and any
    not-yet-migrated reader keep working. They are never authoritative.
    """
    from goodteam_os.projects import update_worker_backend_config

    default_provider = "codex"
    manager = dict(state["manager"])  # type: ignore[arg-type]
    manager_runtime = dict(manager.get("runtime", {}) or {})
    if manager_runtime.get("provider"):
        default_provider = str(manager_runtime["provider"])
    workers: dict[str, str] = {}
    role_models: dict[str, str] = {}
    type_settings: dict[str, dict[str, object]] = {}
    for role in ROLE_KEYS:
        active = [
            worker
            for worker in _workers(state)
            if str(worker["role"]) == role and worker["lifecycle"] in DISPATCHABLE_LIFECYCLES
        ]
        active.sort(key=lambda w: (str(w["created_at"]), str(w["id"])))
        if not active:
            continue
        runtime = dict(active[0]["runtime"])
        provider = str(runtime.get("provider", "") or default_provider)
        workers[role] = provider
        if runtime.get("model"):
            role_models[role] = f"{provider}:{runtime['model']}"
        type_settings[role] = {
            "reasoning_depth": runtime.get("reasoning_depth", "default"),
            "additional_prompt": runtime.get("additional_prompt", ""),
            "desired_instances": len(active),
        }
    workers[MANAGER_ROLE] = str(manager_runtime.get("provider", "") or default_provider)
    if manager_runtime.get("model"):
        role_models[MANAGER_ROLE] = f"{manager_runtime.get('provider', '')}:{manager_runtime['model']}"
    type_settings[MANAGER_ROLE] = {
        "reasoning_depth": manager_runtime.get("reasoning_depth", "default"),
        "additional_prompt": manager_runtime.get("additional_prompt", ""),
        "desired_instances": 1,
    }
    try:
        update_worker_backend_config(
            Path(config_path),
            mode=default_provider,
            workers=workers,
            role_model_overrides=role_models,
            worker_type_settings=type_settings,
        )
    except OSError:
        # The derived view is best-effort compatibility output only.
        return
