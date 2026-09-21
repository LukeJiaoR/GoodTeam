"""Immutable task method locks and job snapshots; never a Runtime authority.

Callers authorize the Operator root and task identity before using this module.
Only an explicit task-start command calls ``begin``. Reads and job derivation
never resolve current project bindings. Production dispatch integration is kept
separate; a saved snapshot does not establish that any Skill was executed.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any
from uuid import uuid4

from .bindings import normalize_binding, normalize_items
from .filesystem import directory, regular_file, subdirectory, write_new
from .models import SkillConflict, SkillError, SkillScope, bounded_text, canonical_json, content_digest, identifier
from .store import SkillStore

LOCK_SCHEMA = "task_skill_lock.v1"
SNAPSHOT_SCHEMA = "skill_execution_snapshot.v1"
REF_SCHEMA = "skill_resource_ref.v1"
REGISTRY_SCHEMA = "task_skill_registry.v1"
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_REGISTRY_BYTES = 16 * 1024 * 1024
TASK_KINDS = frozenset({"workflow_run", "issue_execution", "direct_task", "manager_message"})


def _digest(value: Any) -> str:
    try:
        return content_digest(canonical_json(value).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("invalid_snapshot", "Skill snapshot inputs must be finite JSON values.") from exc


def _decode(data: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        _digest(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("snapshot_corrupt", "Stored Skill snapshot metadata is invalid.") from exc


@dataclass(frozen=True)
class TaskSkillIdentity:
    """Caller-owned bounded identity; a shared Manager run ID is not a turn ID."""

    kind: str
    project_key: str
    logical_id: str

    def __post_init__(self):
        if self.kind not in TASK_KINDS:
            raise SkillError("task_scope_unknown", "Unknown bounded task kind.")
        bounded_text(self.project_key, maximum=128)
        bounded_text(self.logical_id, maximum=256)
        if self.kind == "manager_message" and self.logical_id.startswith("task-assistant:"):
            raise SkillError("task_scope_ambiguous", "Manager Skills require a logical message or intent identity.")


@dataclass(frozen=True)
class TaskSkillPlan:
    """One coherent, server-resolved Team revision captured at task start.

    Defaults must be explicitly resolved by the caller, never inferred here.
    An unresolved preset is recorded as blocked, not substituted at job start.
    Role contracts contain the thin duty/input/output contract, not permissions.
    """

    team_revision: int
    bindings: Mapping[str, dict[str, Any]]
    role_contracts: Mapping[str, dict[str, Any]]
    default_items: Mapping[str, list[dict[str, Any]]] = field(default_factory=dict)


class TaskSkillLockStore:
    """Task-start prepare/pin/commit protocol over the existing immutable store.

    A preparing registry entry already owns its task and command key. Recovery
    uses its frozen resource and deterministic retention holders, so a crash
    between pins cannot recapture the latest binding. The final registry commit
    publishes readiness only after every planned retention pin is durable.
    """

    def __init__(self, root: Path, packages: SkillStore, *, worker_roots: tuple[Path, ...] = ()):
        self.root = Path(root).expanduser().absolute()
        self.packages = packages
        self.scope = packages.scope
        for worker_root in worker_roots:
            if self.root.resolve().is_relative_to(Path(worker_root).expanduser().resolve()):
                raise SkillError("unsafe_store_root", "Task Skill locks must be outside Worker-writable roots.")
        with directory(self.root, create=True) as root_fd:
            with subdirectory(root_fd, self.scope.namespace, create=True) as namespace:
                with subdirectory(namespace, "objects", create=True):
                    pass

    @contextmanager
    def _locked(self):
        with directory(self.root) as root_fd:
            with subdirectory(root_fd, self.scope.namespace) as fd:
                lock = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SkillError("unsafe_file", "Task Skill lock file is unsafe.")
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    yield fd
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    os.close(lock)

    def _registry(self, fd: int) -> dict[str, Any]:
        try:
            data, _ = regular_file(fd, "index.json", maximum=MAX_REGISTRY_BYTES)
        except FileNotFoundError:
            return {"schema_version": REGISTRY_SCHEMA, "scope": asdict(self.scope), "tasks": {}, "commands": {}, "jobs": {}}
        result = _decode(data)
        if (set(result) != {"schema_version", "scope", "tasks", "commands", "jobs"}
                or result["schema_version"] != REGISTRY_SCHEMA or result["scope"] != asdict(self.scope)
                or any(not isinstance(result[key], dict) for key in ("tasks", "commands", "jobs"))):
            raise SkillError("snapshot_corrupt", "Task Skill registry schema or owner does not match.")
        for task_id, entry in result["tasks"].items():
            identifier(task_id, "task-")
            if (not isinstance(entry, dict) or set(entry) != {"ref", "fingerprint", "operation", "status"}
                    or entry["status"] not in {"preparing", "committed"}):
                raise SkillError("snapshot_corrupt", "Task Skill registry entry is invalid.")
            identifier(entry["fingerprint"], "sha256:")
            identifier(entry["operation"], "")
            self._validate_ref(entry["ref"], LOCK_SCHEMA)
            if entry["ref"]["id"] != task_id:
                raise SkillError("snapshot_corrupt", "Task Skill registry identity is invalid.")
            if result["commands"].get(entry["operation"]) != task_id:
                raise SkillError("snapshot_corrupt", "Task Skill command receipt is missing.")
        for operation, task_id in result["commands"].items():
            identifier(operation, "")
            if task_id not in result["tasks"] or result["tasks"][task_id]["operation"] != operation:
                raise SkillError("snapshot_corrupt", "Task Skill command receipt is invalid.")
        for key, entry in result["jobs"].items():
            identifier(key, "")
            if not isinstance(entry, dict) or set(entry) != {"ref", "fingerprint"}:
                raise SkillError("snapshot_corrupt", "Skill job receipt is invalid.")
            identifier(entry["fingerprint"], "sha256:")
            self._validate_ref(entry["ref"], SNAPSHOT_SCHEMA)
        return result

    @staticmethod
    def _write_registry(fd: int, payload: dict[str, Any]) -> None:
        data = (canonical_json(payload) + "\n").encode("utf-8")
        if len(data) > MAX_REGISTRY_BYTES:
            raise SkillError("snapshot_limit", "Task Skill registry size limit reached.")
        temporary = ".index-" + uuid4().hex
        try:
            write_new(fd, temporary, data)
            os.replace(temporary, "index.json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass

    def _validate_ref(self, ref: object, schema: str) -> dict[str, Any]:
        if (not isinstance(ref, dict) or set(ref) != {"schema_version", "resource_schema", "scope", "id", "digest"}
                or ref["schema_version"] != REF_SCHEMA or ref["resource_schema"] != schema
                or ref["scope"] != asdict(self.scope)):
            raise SkillError("snapshot_scope_mismatch", "Skill resource reference does not match the authorized owner/schema.")
        identifier(ref["digest"], "sha256:")
        identifier(ref["id"], "task-" if schema == LOCK_SCHEMA else "snapshot-")
        return ref

    def _publish(self, fd: int, payload: dict[str, Any]) -> dict[str, Any]:
        digest = _digest(payload)
        data = canonical_json(payload).encode("utf-8")
        if len(data) > MAX_RESOURCE_BYTES:
            raise SkillError("snapshot_limit", "Task Skill snapshot size limit reached.")
        name = digest[7:] + ".json"
        with subdirectory(fd, "objects") as objects:
            try:
                existing, _ = regular_file(objects, name, maximum=MAX_RESOURCE_BYTES)
            except FileNotFoundError:
                temporary = ".object-" + uuid4().hex
                try:
                    write_new(objects, temporary, data, mode=0o400)
                    os.replace(temporary, name, src_dir_fd=objects, dst_dir_fd=objects)
                    os.fsync(objects)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=objects)
                    except FileNotFoundError:
                        pass
            else:
                if existing != data:
                    raise SkillError("snapshot_corrupt", "Existing immutable snapshot bytes do not match.")
                # Re-establish directory durability after an interrupted publish.
                os.fsync(objects)
        return {"schema_version": REF_SCHEMA, "resource_schema": payload["schema_version"],
                "scope": asdict(self.scope), "id": payload["id"], "digest": digest}

    def _resource(self, fd: int, ref: dict[str, Any], schema: str) -> dict[str, Any]:
        self._validate_ref(ref, schema)
        with subdirectory(fd, "objects") as objects:
            try:
                data, _ = regular_file(objects, ref["digest"][7:] + ".json", maximum=MAX_RESOURCE_BYTES)
            except FileNotFoundError as exc:
                raise SkillError("snapshot_missing", "Pinned Skill snapshot resource is missing; no fallback is allowed.") from exc
        value = _decode(data)
        if (content_digest(data) != ref["digest"] or value.get("schema_version") != schema
                or value.get("scope") != asdict(self.scope) or value.get("id") != ref["id"]):
            raise SkillError("snapshot_corrupt", "Skill snapshot bytes or identity do not match their reference.")
        task = value.get("task")
        if not isinstance(task, dict) or set(task) != {"kind", "project_key", "logical_id"}:
            raise SkillError("snapshot_corrupt", "Skill snapshot has no bounded task identity.")
        self._task_id(TaskSkillIdentity(**task))
        return value

    def _task_id(self, task: TaskSkillIdentity) -> str:
        if not isinstance(task, TaskSkillIdentity):
            raise SkillError("task_scope_unknown", "An explicit bounded task identity is required.")
        if self.scope.kind == "project" and self.scope.project_key != task.project_key:
            raise SkillError("snapshot_scope_mismatch", "Task project does not match the package owner scope.")
        return "task-" + _digest({"scope": asdict(self.scope), "task": asdict(task)})[7:]

    @staticmethod
    def _holder(task_id: str, subject: str, skill_id: str) -> str:
        return "task-skill-" + _digest([task_id, subject, skill_id])[7:]

    def _capture(self, task: TaskSkillIdentity, plan: TaskSkillPlan) -> dict[str, Any]:
        if (not isinstance(plan, TaskSkillPlan) or type(plan.team_revision) is not int or plan.team_revision < 0
                or not isinstance(plan.bindings, Mapping) or not plan.bindings or len(plan.bindings) > 64):
            raise SkillError("invalid_snapshot", "Task start requires a bounded, coherent Team Skill plan.")
        plan = deepcopy(plan)
        subjects = {}
        for subject, raw in plan.bindings.items():
            bounded_text(subject)
            binding = normalize_binding(raw, subject=subject, project_key=task.project_key)
            contract = deepcopy(plan.role_contracts.get(subject))
            if not isinstance(contract, dict) or contract.get("ref") != binding["role_contract_ref"]:
                raise SkillError("invalid_snapshot", "Every captured subject requires its exact thin Role contract.")
            effective = binding["items"] if binding["mode"] == "custom" else plan.default_items.get(subject, [])
            effective = [item for item in normalize_items(effective) if item["enabled"]]
            diagnostics = []
            package_diagnostics = {}
            pinnable = []
            if not effective:
                diagnostics.append({"code": "methods_unconfigured" if binding["mode"] == "custom" else "default_unresolved"})
            for item in effective:
                try:
                    version = self.packages.get_version(item["skill_id"], item["version_id"])
                    if version.package_digest != item["package_digest"]:
                        raise SkillError("binding_version_changed", "Captured package digest differs from its installed version.")
                except SkillError as exc:
                    diagnostics.append({"code": exc.code, "skill_id": item["skill_id"], "version_id": item["version_id"]})
                else:
                    pinnable.append(item["skill_id"])
                    package_diagnostics[item["version_id"]] = [asdict(value) for value in version.diagnostics]
            subjects[subject] = {"binding": binding, "role_contract": contract, "items": effective,
                                 "pinnable": pinnable, "diagnostics": diagnostics,
                                 "package_diagnostics": package_diagnostics}
        return {"schema_version": LOCK_SCHEMA, "id": self._task_id(task), "scope": asdict(self.scope),
                "task": asdict(task), "team_revision": plan.team_revision, "subjects": subjects,
                "created_at": datetime.now(UTC).isoformat()}

    def _finish(self, fd: int, registry: dict[str, Any], task_id: str) -> dict[str, Any]:
        entry = registry["tasks"][task_id]
        lock = self._resource(fd, entry["ref"], LOCK_SCHEMA)
        if entry["status"] != "committed":
            for subject, frozen in lock["subjects"].items():
                for item in frozen["items"]:
                    if item["skill_id"] not in frozen["pinnable"]:
                        continue
                    holder = self._holder(task_id, subject, item["skill_id"])
                    self.packages.pin(item["skill_id"], item["version_id"], holder_id=holder, command_key=holder)
                    version = self.packages.get_version(item["skill_id"], item["version_id"])
                    pins = self.packages.retention_pins(item["skill_id"], item["version_id"])
                    if version.package_digest != item["package_digest"] or not any(pin["holder_id"] == holder for pin in pins):
                        raise SkillError("snapshot_pin_missing", "Task pin or package changed during snapshot preparation.")
            entry["status"] = "committed"
            self._write_registry(fd, registry)
        # A previous final replace may have succeeded before directory fsync failed.
        os.fsync(fd)
        return deepcopy(entry["ref"])

    def begin(self, task: TaskSkillIdentity, *, command_key: str, task_input: dict[str, Any],
              resolve_plan: Callable[[], TaskSkillPlan]) -> dict[str, Any]:
        """Capture once at an authorized task start; retry never invokes resolver.

        ``task_input`` is the original confirmed task/intent, not the latest
        project settings. A changed task requires a new bounded identity.
        """
        task_id = self._task_id(task)
        bounded_text(command_key, maximum=256)
        if not isinstance(task_input, dict):
            raise SkillError("invalid_snapshot", "The confirmed task input must be an object.")
        task_input = deepcopy(task_input)
        operation = _digest(command_key)[7:]
        fingerprint = _digest({"task": asdict(task), "input": task_input})
        with self._locked() as fd:
            registry = self._registry(fd)
            previous_task = registry["commands"].get(operation)
            if previous_task is not None and previous_task != task_id:
                raise SkillConflict("operation_conflict", "Task-start key belongs to another bounded task.")
            entry = registry["tasks"].get(task_id)
            if entry is not None:
                if entry["fingerprint"] != fingerprint or entry["operation"] != operation:
                    raise SkillConflict("operation_conflict", "This task already owns different confirmed inputs or a different key.")
                return self._finish(fd, registry, task_id)
            lock = self._capture(task, resolve_plan())
            ref = self._publish(fd, lock)
            registry["tasks"][task_id] = {"ref": ref, "fingerprint": fingerprint, "operation": operation, "status": "preparing"}
            registry["commands"][operation] = task_id
            self._write_registry(fd, registry)
            return self._finish(fd, registry, task_id)

    def _read_lock(self, fd: int, registry: dict[str, Any], ref: dict[str, Any], task: TaskSkillIdentity) -> dict[str, Any]:
        self._validate_ref(ref, LOCK_SCHEMA)
        task_id = self._task_id(task)
        entry = registry["tasks"].get(task_id)
        if entry is None or entry["ref"] != ref:
            raise SkillError("snapshot_scope_mismatch", "Task Skill lock is not bound to this task.")
        if entry["status"] != "committed":
            raise SkillError("snapshot_incomplete", "Task Skill pins are incomplete; explicit task-start recovery is required.")
        lock = self._resource(fd, ref, LOCK_SCHEMA)
        if lock["task"] != asdict(task):
            raise SkillError("snapshot_scope_mismatch", "Task Skill lock belongs to another bounded task.")
        return lock

    def read(self, ref: dict[str, Any], *, task: TaskSkillIdentity) -> dict[str, Any]:
        """Read only. Never create pins, resolve bindings, or upgrade legacy tasks."""
        with self._locked() as fd:
            return self._read_lock(fd, self._registry(fd), ref, task)

    def snapshot(self, ref: dict[str, Any], *, task: TaskSkillIdentity, subject: str, command_key: str,
                 job_input: dict[str, Any], loader_version: str, required_skill_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        """Persist a subset of the original task lock under a stable job key.

        ``job_input`` includes the complete task/context references, runtime,
        output contract and execution policy. It is compared, not folded into
        the caller's key to manufacture a second job after an input change.
        """
        bounded_text(command_key, maximum=256)
        bounded_text(loader_version, maximum=128)
        if not isinstance(job_input, dict):
            raise SkillError("invalid_snapshot", "The complete job input must be an object.")
        ref = deepcopy(ref)
        job_input = deepcopy(job_input)
        required = sorted(set(required_skill_ids))
        for value in required:
            identifier(value, "skill-")
        fingerprint = _digest({"ref": ref, "task": asdict(task), "subject": subject, "job_input": job_input,
                               "loader_version": loader_version, "required_skill_ids": required})
        operation = _digest(command_key)[7:]
        with self._locked() as fd:
            registry = self._registry(fd)
            lock = self._read_lock(fd, registry, ref, task)
            entry = registry["jobs"].get(operation)
            if entry is not None:
                if entry["fingerprint"] != fingerprint:
                    raise SkillConflict("operation_conflict", "Job key belongs to different task, method, runtime or policy inputs.")
                self._resource(fd, entry["ref"], SNAPSHOT_SCHEMA)
                # A previous registry replace can be visible before its directory
                # sync completes. A matching retry must establish durability too.
                os.fsync(fd)
                return deepcopy(entry["ref"])
            frozen = lock["subjects"].get(subject)
            if frozen is None:
                raise SkillError("subject_not_locked", "Role was not captured at task start; an explicit bounded change is required.")
            if not set(required).issubset(item["skill_id"] for item in frozen["items"]):
                raise SkillError("skill_not_locked", "Task-required Skill is not in the original task lock.")
            snapshot = {"schema_version": SNAPSHOT_SCHEMA, "id": "snapshot-" + fingerprint[7:],
                        "scope": asdict(self.scope), "task": asdict(task), "task_lock_ref": ref,
                        "subject": subject, "methods": deepcopy(frozen), "job_input": deepcopy(job_input),
                        "loader_version": loader_version, "required_skill_ids": required,
                        "created_at": datetime.now(UTC).isoformat()}
            result = self._publish(fd, snapshot)
            registry["jobs"][operation] = {"ref": result, "fingerprint": fingerprint}
            self._write_registry(fd, registry)
            return result

    def preflight(self, ref: dict[str, Any], *, task: TaskSkillIdentity, command_key: str,
                  job_input: dict[str, Any], supported_loaders: tuple[str, ...],
                  current_availability: Callable[[SkillScope, str, str], dict[str, Any]]) -> dict[str, Any]:
        """Validate just before dispatch/load; does not call or authorize a provider.

        The current availability/revocation reader is mandatory and must be a
        trusted control-plane dependency, never a client-supplied allow flag.
        Missing/unknown status is blocked. Integration must also validate actual
        adapter capability and #440 conformance; this is not that evidence.
        """
        bounded_text(command_key, maximum=256)
        with self._locked() as fd:
            registry = self._registry(fd)
            self._validate_ref(ref, SNAPSHOT_SCHEMA)
            entry = registry["jobs"].get(_digest(command_key)[7:])
            if entry is None or entry["ref"] != ref:
                raise SkillError("snapshot_scope_mismatch", "Skill snapshot is not bound to this job key.")
            snapshot = self._resource(fd, ref, SNAPSHOT_SCHEMA)
            lock = self._read_lock(fd, registry, snapshot["task_lock_ref"], task)
            if snapshot["task"] != asdict(task) or snapshot["methods"] != lock["subjects"].get(snapshot["subject"]):
                raise SkillError("snapshot_scope_mismatch", "Job methods differ from the bounded task lock.")
            if _digest(snapshot["job_input"]) != _digest(job_input):
                raise SkillConflict("operation_conflict", "Job inputs changed after the Skill snapshot was committed.")
            if snapshot["loader_version"] not in supported_loaders:
                raise SkillError("loader_incompatible", "No compatible loader exists for this snapshot.")
            if snapshot["methods"]["diagnostics"]:
                raise SkillError("methods_blocked", "Captured methods are unresolved or unavailable; no fallback is allowed.")
            for item in snapshot["methods"]["items"]:
                status = current_availability(self.scope, item["skill_id"], item["version_id"])
                if (not isinstance(status, dict) or status.get("scope") != asdict(self.scope)
                        or status.get("skill_id") != item["skill_id"] or status.get("version_id") != item["version_id"]
                        or status.get("available") is not True or status.get("revoked") is not False
                        or type(status.get("revision")) is not int or status["revision"] < 0):
                    raise SkillError("skill_unavailable", "Skill is revoked, unavailable, or its current status cannot be verified.")
                version = self.packages.get_version(item["skill_id"], item["version_id"])
                if version.package_digest != item["package_digest"]:
                    raise SkillError("snapshot_corrupt", "Pinned package bytes no longer match the task.")
                holder = self._holder(lock["id"], snapshot["subject"], item["skill_id"])
                if not any(pin["holder_id"] == holder for pin in self.packages.retention_pins(item["skill_id"], item["version_id"])):
                    raise SkillError("snapshot_pin_missing", "Task retention pin is missing; preflight cannot recreate it.")
            return deepcopy(snapshot)
