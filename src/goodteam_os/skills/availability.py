"""Trusted current Skill availability and irreversible security revocation.

This is a single-operator control-plane service, not an HTTP authorization layer.
Callers authorize the root and actor before invoking a mutation. Package text,
bindings and job payloads never select this store or supply its status response.
Initialization is explicit; reads never initialize or repair missing metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any
from uuid import uuid4

from .filesystem import directory, regular_file, subdirectory, write_new
from .models import SkillConflict, SkillError, SkillScope, bounded_text, canonical_json, content_digest, identifier
from .store import SkillStore

AVAILABILITY_SCHEMA = "skill_availability.v1"
MAX_AVAILABILITY_BYTES = 8 * 1024 * 1024


def _digest(value: Any) -> str:
    return content_digest(canonical_json(value).encode("utf-8"))


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
        canonical_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("availability_corrupt", "Current Skill availability metadata is invalid.") from exc


class SkillAvailabilityStore:
    """Current security state, independent of frozen task locks and package pins.

    Revocation is terminal for an exact version. It neither deletes package
    bytes nor releases pins. A future ordinary uninstall must retain those bytes;
    it must not use security revocation to implement package removal.
    """

    def __init__(self, root: Path, packages: SkillStore, *, worker_roots: tuple[Path, ...] = ()):
        self.root = Path(root).expanduser().absolute()
        self.packages = packages
        self.scope = packages.scope
        for worker_root in worker_roots:
            if self.root.resolve().is_relative_to(Path(worker_root).expanduser().resolve()):
                raise SkillError("unsafe_store_root", "Skill availability must be outside Worker-writable roots.")
        with directory(self.root, create=True) as root_fd:
            with subdirectory(root_fd, self.scope.namespace, create=True):
                pass

    @contextmanager
    def _locked(self):
        with directory(self.root) as root_fd:
            with subdirectory(root_fd, self.scope.namespace) as fd:
                lock = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SkillError("unsafe_file", "Skill availability lock is unsafe.")
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    yield fd
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    os.close(lock)

    def _check_scope(self, scope: SkillScope) -> None:
        if not isinstance(scope, SkillScope) or scope != self.scope:
            raise SkillError("availability_scope_mismatch", "Current Skill status belongs to another authorized scope.")

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": AVAILABILITY_SCHEMA, "scope": asdict(self.scope),
                "revision": 0, "revocations": {}, "commands": {}}

    def _version_key(self, skill_id: str, version_id: str) -> str:
        identifier(skill_id, "skill-")
        identifier(version_id, "version-")
        return _digest({"scope": asdict(self.scope), "skill_id": skill_id, "version_id": version_id})[7:]

    def _validate(self, value: dict[str, Any]) -> None:
        try:
            if (set(value) != set(self._empty()) or value["schema_version"] != AVAILABILITY_SCHEMA
                    or value["scope"] != asdict(self.scope) or type(value["revision"]) is not int
                    or value["revision"] < 0 or not isinstance(value["revocations"], dict)
                    or not isinstance(value["commands"], dict)):
                raise ValueError("Invalid registry")
            if len(value["commands"]) != value["revision"]:
                raise ValueError("Missing command receipt")
            event_revisions = set()
            for key, event in value["revocations"].items():
                if (not isinstance(event, dict) or set(event) != {
                        "id", "skill_id", "version_id", "actor", "reason", "created_at", "revision"}
                        or key != self._version_key(event["skill_id"], event["version_id"])
                        or event["id"] != "revocation-" + key or type(event["revision"]) is not int
                        or not 1 <= event["revision"] <= value["revision"]):
                    raise ValueError("Invalid revocation")
                bounded_text(event["actor"], maximum=128)
                bounded_text(event["reason"], maximum=1024)
                bounded_text(event["created_at"], maximum=128)
                if event["revision"] in event_revisions:
                    raise ValueError("Duplicate revocation revision")
                event_revisions.add(event["revision"])
            revisions = set()
            original_events = set()
            for operation, receipt in value["commands"].items():
                identifier(operation, "")
                if not isinstance(receipt, dict) or set(receipt) != {"input", "fingerprint", "result"}:
                    raise ValueError("Invalid command")
                request = receipt["input"]
                if not isinstance(request, dict) or set(request) != {"skill_id", "version_id", "actor", "reason"}:
                    raise ValueError("Invalid command input")
                bounded_text(request["actor"], maximum=128)
                bounded_text(request["reason"], maximum=1024)
                event_key = self._version_key(request["skill_id"], request["version_id"])
                if receipt["fingerprint"] != _digest(request) or event_key not in value["revocations"]:
                    raise ValueError("Invalid command fingerprint or event")
                result = receipt["result"]
                event = value["revocations"][event_key]
                if (not isinstance(result, dict) or set(result) != {"revision", "changed", "revocation_id"}
                        or type(result["revision"]) is not int or type(result["changed"]) is not bool
                        or not event["revision"] <= result["revision"] <= value["revision"]
                        or result["revocation_id"] != event["id"] or result["revision"] in revisions):
                    raise ValueError("Invalid command result")
                revisions.add(result["revision"])
                if result["changed"]:
                    if (result["revision"] != event["revision"] or event_key in original_events
                            or any(request[key] != event[key] for key in ("actor", "reason"))):
                        raise ValueError("Invalid original event receipt")
                    original_events.add(event_key)
                elif result["revision"] == event["revision"]:
                    raise ValueError("No-op command cannot create an event")
            if original_events != set(value["revocations"]):
                raise ValueError("Missing revocation receipt")
        except (ValueError, TypeError, KeyError) as exc:
            raise SkillError("availability_corrupt", "Current Skill availability schema, owner or audit receipts are invalid.") from exc

    def _read(self, fd: int) -> dict[str, Any]:
        try:
            with subdirectory(fd, "registry") as registry_fd:
                data, _ = regular_file(registry_fd, "index.json", maximum=MAX_AVAILABILITY_BYTES)
        except FileNotFoundError as exc:
            raise SkillError("availability_unavailable", "Current Skill availability is not initialized or its metadata is missing.") from exc
        result = _decode(data)
        self._validate(result)
        return result

    def initialize(self) -> dict[str, Any]:
        """Explicit trusted setup only; never called from status/preflight reads.

        Publish the initial directory with its complete index atomically. An
        existing directory with a missing/corrupt index is an error, not a reset.
        """
        with self._locked() as fd:
            try:
                os.stat("registry", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                staging = ".bootstrap-" + uuid4().hex
                os.mkdir(staging, mode=0o700, dir_fd=fd)
                try:
                    with subdirectory(fd, staging) as staged:
                        os.fchmod(staged, 0o700)
                        write_new(staged, "index.json", canonical_json(self._empty()).encode("utf-8"))
                        os.fsync(staged)
                    os.rename(staging, "registry", src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                finally:
                    try:
                        with subdirectory(fd, staging) as staged:
                            try:
                                os.unlink("index.json", dir_fd=staged)
                            except FileNotFoundError:
                                pass
                        os.rmdir(staging, dir_fd=fd)
                    except FileNotFoundError:
                        pass
            result = self._read(fd)
            # Replay also completes durability after rename-before-fsync failure.
            os.fsync(fd)
            return {"scope": asdict(self.scope), "revision": result["revision"]}

    def _write(self, fd: int, value: dict[str, Any]) -> None:
        self._validate(value)
        data = canonical_json(value).encode("utf-8")
        if len(data) > MAX_AVAILABILITY_BYTES:
            raise SkillError("availability_limit", "Current Skill availability metadata limit reached; no change committed.")
        with subdirectory(fd, "registry") as registry_fd:
            temporary = ".index-" + uuid4().hex
            try:
                write_new(registry_fd, temporary, data)
                os.replace(temporary, "index.json", src_dir_fd=registry_fd, dst_dir_fd=registry_fd)
                os.fsync(registry_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=registry_fd)
                except FileNotFoundError:
                    pass

    def current_status(self, scope: SkillScope, skill_id: str, version_id: str) -> dict[str, Any]:
        """Concrete TaskSkillLockStore availability reader; no cached allow state."""
        self._check_scope(scope)
        version_key = self._version_key(skill_id, version_id)
        with self._locked() as fd:
            value = self._read(fd)
            revoked = version_key in value["revocations"]
            available = False
            diagnostic = "security_revoked" if revoked else ""
            if not revoked:
                try:
                    self.packages.get_version(skill_id, version_id)
                    available = True
                except SkillError as exc:
                    diagnostic = exc.code
            return {"scope": asdict(self.scope), "skill_id": skill_id, "version_id": version_id,
                    "revision": value["revision"], "available": available, "revoked": revoked,
                    "diagnostic": diagnostic}

    def revision(self) -> int:
        with self._locked() as fd:
            return self._read(fd)["revision"]

    def revoke(self, skill_id: str, version_id: str, *, actor: str, reason: str,
               command_key: str, expected_revision: int) -> dict[str, Any]:
        """Revoke a known exact version through a serialized, idempotent decision.

        The caller supplies a server-authenticated actor, not a package claim.
        Expected revision is a CAS precondition, not part of the semantic replay
        fingerprint. A matching retry returns its original receipt even if the
        current revision has advanced. No un-revoke or pin release is provided.
        """
        version_key = self._version_key(skill_id, version_id)
        bounded_text(actor, maximum=128)
        bounded_text(reason, maximum=1024)
        bounded_text(command_key, maximum=256)
        if type(expected_revision) is not int or expected_revision < 0:
            raise SkillError("invalid_revision", "A non-negative current availability revision is required.")
        operation = _digest(command_key)[7:]
        request = {"skill_id": skill_id, "version_id": version_id, "actor": actor, "reason": reason}
        fingerprint = _digest(request)
        with self._locked() as fd:
            value = self._read(fd)
            previous = value["commands"].get(operation)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise SkillConflict("operation_conflict", "Revocation key belongs to different inputs.")
                with subdirectory(fd, "registry") as registry_fd:
                    os.fsync(registry_fd)
                return {**deepcopy(previous["result"]), "replayed": True,
                        "revocation": deepcopy(value["revocations"][version_key])}
            if expected_revision != value["revision"]:
                raise SkillConflict("availability_changed", "Current Skill availability changed; refresh before confirming.")
            # Metadata ownership is enough to revoke; corrupt package content
            # must not prevent an operator from recording its security revocation.
            versions = self.packages.list_versions(skill_id)
            if not any(version.version_id == version_id for version in versions):
                raise SkillError("version_missing", "Cannot revoke a version outside this authorized package scope.")
            changed = version_key not in value["revocations"]
            value["revision"] += 1
            if changed:
                value["revocations"][version_key] = {
                    "id": "revocation-" + version_key, "skill_id": skill_id, "version_id": version_id,
                    "actor": actor, "reason": reason, "created_at": datetime.now(UTC).isoformat(),
                    "revision": value["revision"],
                }
            result = {"revision": value["revision"], "changed": changed,
                      "revocation_id": value["revocations"][version_key]["id"]}
            value["commands"][operation] = {"input": request, "fingerprint": fingerprint, "result": result}
            self._write(fd, value)
            return {**deepcopy(result), "replayed": False, "revocation": deepcopy(value["revocations"][version_key])}
