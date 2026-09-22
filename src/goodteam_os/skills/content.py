"""Non-activated S5 content contract; preparation is never Runtime execution.

The production factory is closed until #440 selects and verifies an adapter.
Tests inject a control-plane-owned authority, not a job/config/user allow flag.
This module has no process, HTTP, script execution or model invocation channel.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import json
import os
import stat
from typing import Any, Protocol
from uuid import uuid4

from .filesystem import directory, regular_file, write_new
from .models import SkillConflict, SkillError, bounded_text, canonical_json, content_digest, identifier, resource_path
from .task_locks import TaskSkillIdentity
from . import worker_requests as requests

CONTENT_SCHEMA = "skill_content_context.v1"
CONTENT_REF_SCHEMA = "skill_content_reference.v1"
MAX_CONTEXT_BYTES = 16 * 1024 * 1024
OBSERVED_STAGES = ("materialized", "catalog_presented", "content_delivered",
                   "resource_read_observed", "script_execution_observed")


@dataclass(frozen=True)
class ContentLimits:
    max_input_bytes: int = 1024 * 1024
    max_catalog_bytes: int = 128 * 1024
    max_resource_bytes: int = 8 * 1024 * 1024
    max_packages: int = 32

    def __post_init__(self):
        if (any(type(value) is not int or value <= 0 for value in asdict(self).values())
                or self.max_input_bytes > 8 * 1024 * 1024 or self.max_catalog_bytes > 1024 * 1024
                or self.max_resource_bytes > 8 * 1024 * 1024 or self.max_packages > 32):
            raise SkillError("content_budget_invalid", "Content limits must be explicit bounded positive integers.")


@dataclass(frozen=True)
class ContentSession:
    """Identity returned by a trusted adapter authority, never by request JSON.

    Recording an identity does not verify a binary or grant sandbox permissions.
    The authority must do that itself on every operation. No production authority
    is enabled in this change. Contract fixtures identify themselves as such.
    """
    adapter: str
    adapter_version: str
    binary_version: str
    binary_digest: str
    model: str
    config_digest: str
    session_id: str
    evidence_ref: str
    verification_kind: str

    def __post_init__(self):
        for value in asdict(self).values():
            bounded_text(value)
        identifier(self.binary_digest, "sha256:")
        identifier(self.config_digest, "sha256:")
        if self.verification_kind not in {"contract_fixture", "verified_runtime"}:
            raise SkillError("runtime_unverified", "Unknown Runtime verification provenance.")


class ContentAuthority(Protocol):
    def authorize(self, snapshot: dict[str, Any], *, context_id: str, operation: str) -> ContentSession:
        """Recheck actual adapter capabilities and return its bound session."""
        ...


class ClosedContentAuthority:
    def authorize(self, snapshot, *, context_id, operation):
        raise SkillError("runtime_conformance_unverified", "Skill content requires independently verified Runtime isolation (#440).")


def _bytes(value):
    try:
        return canonical_json(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("content_input_invalid", "Content identities and input must be finite UTF-8 JSON.") from exc


def _decode(data):
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
        _bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("content_context_corrupt", "Stored content context is invalid.") from exc


def _publish_immutable(fd, name, data, *, code):
    try:
        previous = regular_file(fd, name, maximum=MAX_CONTEXT_BYTES)[0]
    except FileNotFoundError:
        temporary = ".stage-content-" + uuid4().hex
        try:
            write_new(fd, temporary, data)
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    else:
        if previous != data:
            raise SkillConflict(code, "This content operation already has different immutable inputs.")
    os.fsync(fd)


class SkillContextProvider:
    """Exact task/Worker-backed content and broker-read contracts.

    ``authority`` is an internal composition dependency only. Never instantiate
    it from a Skill package, public request, model output, or stored allow flag.
    Normal construction is closed; existing Worker dispatch remains closed
    independently and is not wired to this contract yet.
    """
    def __init__(self, workers, job_id: str, *, authority: ContentAuthority | None = None,
                 limits: ContentLimits | None = None):
        if not isinstance(job_id, str) or requests.SKILL_JOB_RE.fullmatch(job_id) is None:
            raise SkillError("worker_skill_identity_invalid", "Content requires a registered Skill Worker identity.")
        self.workers, self.job_id = workers, job_id
        self.authority = authority if authority is not None else ClosedContentAuthority()
        self.limits = limits or ContentLimits()

    def _validated(self):
        root, operator, locks, availability = requests._services(self.workers)
        with requests._request_lock(operator, self.job_id) as fd:
            registered = locks.find_snapshot(command_key=self.job_id)
            if registered is None:
                raise SkillError("snapshot_missing", "Skill content has no registered Worker snapshot.")
            snapshot, ref = registered["snapshot"], registered["ref"]
            if (snapshot["job_input"].get("schema_version") != requests.WORKER_REQUEST_SCHEMA
                    or snapshot["job_input"].get("worker_root") != str(root)):
                raise SkillError("snapshot_scope_mismatch", "Content belongs to another Worker scope.")
            expected = requests._metadata(self.job_id, ref, snapshot)
            if not requests._read_publication(fd, self.job_id, expected):
                raise SkillError("worker_skill_publication_missing", "Content requires a completely published Worker.")
            with directory(root / ".goodteam" / "workers" / "jobs" / self.job_id) as job_fd:
                requests._verify_files(job_fd, expected, snapshot)
            checked = locks.preflight(ref, task=TaskSkillIdentity(**snapshot["task"]), command_key=self.job_id,
                                      job_input=snapshot["job_input"], supported_loaders=(requests.WORKER_METADATA_LOADER,),
                                      current_availability=availability.current_status)
        return operator, locks.packages, ref, checked

    def _session(self, snapshot, context_id, operation):
        bounded_text(context_id, maximum=128)
        session = self.authority.authorize(deepcopy(snapshot), context_id=context_id, operation=operation)
        if not isinstance(session, ContentSession):
            raise SkillError("runtime_unverified", "A Runtime allow flag is not verified session identity.")
        original = snapshot["job_input"]["request"]
        if session.adapter != original["adapter"] or session.model != original["model"]:
            raise SkillError("runtime_identity_changed", "Runtime differs from the original task request.")
        return asdict(session)

    @contextmanager
    def _context_store(self, operator, context_id, *, create=False):
        name = content_digest(_bytes([self.job_id, context_id]))[7:]
        # The Operator root was checked against the actual Worker root by _services.
        with directory(operator / "skills" / "content-contexts" / self.job_id, create=create) as fd:
            lock = os.open(name + ".lock", (os.O_CREAT if create else 0) | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SkillError("unsafe_file", "Unsafe content context lock.")
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield fd, name + ".json"
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)

    def _reference(self, record):
        return {"schema_version": CONTENT_REF_SCHEMA, "job_id": self.job_id,
                "context_id": record["context_id"], "snapshot_ref": record["snapshot_ref"],
                "digest": content_digest(_bytes(record))}

    def prepare(self, context_id: str) -> dict[str, Any]:
        """Prepare complete private input without invoking or claiming delivery.

        Each new context obtains required bytes again from the SAME task snapshot.
        Replay revalidates current authority and bytes; it never repairs old records.
        Budgets count the complete serialized original input plus required/catalog
        payload, not only the added method body. No silent truncation is allowed.
        """
        operator, packages, ref, snapshot = self._validated()
        session = self._session(snapshot, context_id, "prepare")
        items = snapshot["methods"]["items"]
        if len(items) > self.limits.max_packages:
            raise SkillError("content_budget_exceeded", "Too many bound packages for this context.")
        catalog, required = [], []
        for item in items:
            package = packages.read_package(item["skill_id"], item["version_id"])
            if package.digest != item["package_digest"]:
                raise SkillError("snapshot_corrupt", "Content digest differs from its task lock.")
            blockers = [part.code for part in package.diagnostics
                        if part.code in {"runtime_semantics_unverified", "dynamic_command_unverified", "empty_instructions"}]
            if blockers:
                raise SkillError("skill_content_unsupported", "Package requirements are unsupported: " + ", ".join(sorted(set(blockers))))
            metadata = package.metadata
            entry = {"skill_id": item["skill_id"], "version_id": item["version_id"],
                     "package_digest": package.digest, "name": metadata["name"], "description": metadata["description"],
                     "activation": item["activation"], "resources": package.manifest["files"],
                     "warnings": [asdict(part) for part in package.diagnostics]}
            catalog.append(entry)
            if len(_bytes(catalog)) > self.limits.max_catalog_bytes:
                raise SkillError("content_budget_exceeded", "The complete method catalog exceeds its byte budget.")
            if item["activation"] == "required" or item["skill_id"] in snapshot["required_skill_ids"]:
                required.append({"skill_id": item["skill_id"], "version_id": item["version_id"],
                                 "path": "SKILL.md", "digest": content_digest(package.resource("SKILL.md")),
                                 "text": package.resource("SKILL.md").decode("utf-8")})
            elif item["activation"] != "on_demand":
                raise SkillError("skill_content_unsupported", "Unknown method activation policy.")
        payload = {"original_job_input": snapshot["job_input"], "catalog": catalog, "required": required}
        if len(_bytes(payload)) > self.limits.max_input_bytes:
            raise SkillError("content_budget_exceeded", "Complete generation input exceeds its byte budget; nothing was truncated.")
        record = {"schema_version": CONTENT_SCHEMA, "job_id": self.job_id, "context_id": context_id,
                  "snapshot_ref": ref, "runtime_session": session, "limits": asdict(self.limits), "input": payload}
        encoded = _bytes(record)
        if len(encoded) > MAX_CONTEXT_BYTES:
            raise SkillError("content_budget_exceeded", "Private content context exceeds its storage bound.")
        with self._context_store(operator, context_id, create=True) as (fd, name):
            # Recheck after content capture and before publication, including revocation.
            _operator, _packages, final_ref, final_snapshot = self._validated()
            if final_ref != ref or self._session(final_snapshot, context_id, "prepare") != session:
                raise SkillConflict("runtime_identity_changed", "Content authority changed during preparation.")
            _publish_immutable(fd, name, encoded, code="content_context_conflict")
        return self._reference(record)

    def _read(self, ref):
        if (not isinstance(ref, dict) or set(ref) != {"schema_version", "job_id", "context_id", "snapshot_ref", "digest"}
                or ref["schema_version"] != CONTENT_REF_SCHEMA or ref["job_id"] != self.job_id):
            raise SkillError("content_context_invalid", "Content reference belongs to another schema or Worker.")
        bounded_text(ref["context_id"], maximum=128)
        identifier(ref["digest"], "sha256:")
        operator, packages, snapshot_ref, snapshot = self._validated()
        if snapshot_ref != ref["snapshot_ref"]:
            raise SkillError("snapshot_scope_mismatch", "Content reference belongs to another snapshot.")
        with self._context_store(operator, ref["context_id"]) as (fd, name):
            data = regular_file(fd, name, maximum=MAX_CONTEXT_BYTES)[0]
            record = _decode(data)
            try:
                valid = (self._reference(record) == ref and record.get("schema_version") == CONTENT_SCHEMA
                         and record.get("job_id") == self.job_id and record.get("context_id") == ref["context_id"])
            except (KeyError, TypeError):
                valid = False
            if not valid:
                raise SkillError("content_context_corrupt", "Content does not match its immutable reference.")
        return operator, packages, snapshot, record

    def private_input(self, ref):
        """Internal adapter input; never a public status/HTTP response."""
        _operator, _packages, snapshot, record = self._read(ref)
        if len(_bytes(record["input"])) > self.limits.max_input_bytes:
            raise SkillError("content_budget_exceeded", "Complete input exceeds the current context budget.")
        if self._session(snapshot, record["context_id"], "input") != record["runtime_session"]:
            raise SkillConflict("runtime_identity_changed", "Context cannot be reused by another Runtime session.")
        # Input capture or adapter authorization may yield while an operator
        # revokes a package. Recheck before returning complete private content.
        _operator, _packages, final_ref, _snapshot = self._validated()
        if final_ref != record["snapshot_ref"]:
            raise SkillError("snapshot_scope_mismatch", "Content authority changed before input return.")
        return record["input"]  # Freshly decoded value, never shared mutable state.

    def read_resource(self, ref, *, skill_id: str, relative: str, call_id: str) -> bytes:
        """Controlled broker read, not proof a model read/used the returned bytes.

        The caller is the trusted adapter channel, not an arbitrary user path or
        a model-written JSON claim. No transport is connected in production yet.
        A durable broker receipt is intentionally distinct from Runtime evidence.
        """
        identifier(skill_id, "skill-")
        resource_path(relative)
        bounded_text(call_id, maximum=128)
        operator, packages, snapshot, record = self._read(ref)
        session = self._session(snapshot, record["context_id"], "resource")
        if session != record["runtime_session"]:
            raise SkillConflict("runtime_identity_changed", "Resource channel belongs to another session.")
        item = next((item for item in record["input"]["catalog"] if item["skill_id"] == skill_id), None)
        if item is None:
            raise SkillError("skill_not_locked", "Resource request is outside this Role's method subset.")
        resource = next((part for part in item["resources"] if part["path"] == relative), None)
        if resource is None:
            raise SkillError("resource_missing", "Resource does not exist in the fixed package.")
        budget = min(self.limits.max_resource_bytes, record["limits"]["max_resource_bytes"])
        if resource["size"] > budget:
            raise SkillError("content_budget_exceeded", "Resource exceeds its byte budget; no partial read is returned.")
        data = packages.read_resource(skill_id, item["version_id"], relative)
        if len(data) != resource["size"] or content_digest(data) != resource["digest"]:
            raise SkillError("snapshot_corrupt", "Resource differs from its immutable catalog.")
        receipt = {"schema_version": "skill_broker_read.v1", "context_ref": ref, "call_id": call_id,
                   "skill_id": skill_id, "version_id": item["version_id"], "path": relative,
                   "digest": content_digest(data), "size": len(data), "runtime_session": session,
                   "broker_read_completed": True, "runtime_read_observed": False}
        with self._context_store(operator, record["context_id"]) as (fd, _name):
            _operator, _packages, _ref, final_snapshot = self._validated()
            if self._session(final_snapshot, record["context_id"], "resource") != session:
                raise SkillConflict("runtime_identity_changed", "Resource authority changed before return.")
            name = "read-" + content_digest(_bytes([record["context_id"], call_id]))[7:] + ".json"
            encoded = _bytes(receipt)
            _publish_immutable(fd, name, encoded, code="content_call_conflict")
        return data

    def describe(self, ref):
        """Narrow read-only preparation detail, never a Runtime PASS or body dump.

        Current preflight remains mandatory. Historical views after revocation
        need the separate audit path; this API does not expose revoked content.
        """
        _operator, _packages, _snapshot, record = self._read(ref)
        return {"schema_version": "skill_content_detail.v1", "context_ref": self._reference(record),
                "status": "prepared_only", "runtime_verification": record["runtime_session"]["verification_kind"],
                "stages": {name: "unobserved" for name in OBSERVED_STAGES},
                "items": [{key: item[key] for key in ("skill_id", "version_id", "package_digest", "activation")}
                          for item in record["input"]["catalog"]]}
