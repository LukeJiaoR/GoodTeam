"""Local Skill package store with atomic metadata receipts and retention pins.

Callers must authorize the scope/root before constructing a store. This is not an
HTTP authorization layer or a sandbox. Keep its root outside Worker-writable
mounts; the Runtime adapter must enforce that boundary. No package code is run.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Iterator
from uuid import uuid4

from .filesystem import directory, regular_file, subdirectory, write_new
from .models import (
    InstalledSkill, PackageLimits, Publication, SkillConflict, SkillDiagnostic,
    SkillError, SkillPackageVersion, SkillScope, SkillSource, bounded_text,
    canonical_json, content_digest, identifier, installed_id, version_id,
)
from .package import SkillPackage, capture_directory_fd, package_from_files

INDEX_SCHEMA = "skill_library_index.v1"
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _decode(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SkillError("store_corrupt", "Duplicate stored metadata key.")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        canonical_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SkillError):
            raise
        raise SkillError("store_corrupt", "Skill store metadata is unreadable.") from exc


def _remove_staging(parent_fd: int, name: str) -> None:
    """Only caller-owned temporary objects; never follow a replacement link."""
    def remove(parent: int, leaf: str):
        try:
            info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode):
            with subdirectory(parent, leaf) as child:
                with os.scandir(child) as entries:
                    for entry in entries:
                        remove(child, entry.name)
            os.rmdir(leaf, dir_fd=parent)
        else:
            os.unlink(leaf, dir_fd=parent)

    if not name.startswith(".stage-") or "/" in name:
        raise SkillError("unsafe_path", "Cleanup is limited to owned staging objects.")
    remove(parent_fd, name)


class SkillStore:
    def __init__(self, root: Path, scope: SkillScope, *, limits: PackageLimits | None = None,
                 worker_roots: tuple[Path, ...] = ()):
        self.root = Path(root).expanduser().absolute()
        self.scope = scope
        self.limits = limits or PackageLimits()
        for worker_root in worker_roots:
            if self.root.resolve().is_relative_to(Path(worker_root).expanduser().resolve()):
                raise SkillError("unsafe_store_root", "Skill storage cannot be inside a declared Worker-writable root.")
        # No global home scanning or implicit source trust; only initialize our root.
        with directory(self.root, create=True) as fd:
            with subdirectory(fd, self.scope.namespace, create=True) as namespace:
                with subdirectory(namespace, "objects", create=True):
                    pass

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with directory(self.root) as root:
            with subdirectory(root, self.scope.namespace) as fd:
                lock = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SkillError("unsafe_file", "Skill store lock is unsafe.")
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    yield fd
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    os.close(lock)

    def _empty(self) -> dict:
        return {"schema_version": INDEX_SCHEMA, "scope": asdict(self.scope), "revision": 0,
                "installed": {}, "versions": {}, "operations": {}, "pins": {}}

    def _read_index(self, fd: int) -> dict:
        try:
            data, _ = regular_file(fd, "index.json", maximum=MAX_INDEX_BYTES)
        except FileNotFoundError:
            return self._empty()
        value = _decode(data)
        try:
            self._validate_index(value)
        except (TypeError, KeyError, ValueError) as exc:
            if isinstance(exc, SkillError):
                raise
            raise SkillError("store_corrupt", "Skill store identity records are invalid.") from exc
        return value

    def _validate_index(self, value: dict) -> None:
        if set(value) != set(self._empty()) or value["schema_version"] != INDEX_SCHEMA:
            raise SkillError("unsupported_schema", "Unknown Skill store schema; refusing to rewrite it.")
        if value["scope"] != asdict(self.scope) or type(value["revision"]) is not int or value["revision"] < 0:
            raise SkillError("scope_mismatch", "Skill store scope/revision does not match this caller.")
        if any(not isinstance(value[key], dict) for key in ("installed", "versions", "operations", "pins")):
            raise SkillError("store_corrupt", "Skill store collections must be objects.")
        for key, raw in value["installed"].items():
            if set(raw) != {"skill_id", "name", "source", "version_ids", "status"}:
                raise SkillError("store_corrupt", "Malformed installed Skill record.")
            source = SkillSource(**raw["source"])
            if key != raw["skill_id"] or key != installed_id(self.scope, source) or raw["status"] != "installed":
                raise SkillError("store_corrupt", "Installed Skill identity mismatch.")
            bounded_text(raw["name"], maximum=256)
            versions = raw["version_ids"]
            if not isinstance(versions, list) or not versions or len(set(versions)) != len(versions):
                raise SkillError("store_corrupt", "Installed Skill versions are invalid.")
            for selected in versions:
                identifier(selected, "version-")
                if selected not in value["versions"] or value["versions"][selected]["skill_id"] != key:
                    raise SkillError("store_corrupt", "Installed Skill has a dangling version reference.")
        for key, raw in value["versions"].items():
            record = self._version(raw)
            installed = value["installed"].get(record.skill_id)
            if (key != record.version_id or installed is None or key not in installed["version_ids"]
                    or installed_id(self.scope, record.source) != record.skill_id):
                raise SkillError("store_corrupt", "Skill version ownership mismatch.")
        for key, raw in value["pins"].items():
            if set(raw) != {"holder_id", "skill_id", "version_id"}:
                raise SkillError("store_corrupt", "Malformed Skill retention pin.")
            bounded_text(raw["holder_id"])
            if key != hashlib.sha256(raw["holder_id"].encode()).hexdigest():
                raise SkillError("store_corrupt", "Skill retention identity mismatch.")
            self._find_version(value, raw["skill_id"], raw["version_id"])
        for key, raw in value["operations"].items():
            identifier(key, "")
            if set(raw) != {"kind", "fingerprint", "result"} or raw["kind"] not in {"publish", "pin", "release_pin"}:
                raise SkillError("store_corrupt", "Malformed Skill operation receipt.")
            identifier(raw["fingerprint"], "sha256:")
            if not isinstance(raw["result"], dict) or type(raw["result"].get("revision")) is not int:
                raise SkillError("store_corrupt", "Malformed Skill operation result.")
            if not 1 <= raw["result"]["revision"] <= value["revision"]:
                raise SkillError("store_corrupt", "Skill receipt revision mismatch.")
            if raw["kind"] == "publish":
                self._find_version(value, raw["result"]["skill_id"], raw["result"]["version_id"])

    def _write_index(self, fd: int, value: dict) -> None:
        self._validate_index(value)
        data = canonical_json(value).encode()
        if len(data) > MAX_INDEX_BYTES:
            raise SkillError("store_limit", "Skill metadata limit reached; no mutation committed.")
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

    @staticmethod
    def _version(raw: dict) -> SkillPackageVersion:
        if set(raw) != {"version_id", "skill_id", "package_digest", "source", "metadata_json", "diagnostics", "created_at"}:
            raise SkillError("store_corrupt", "Malformed Skill package version.")
        source = SkillSource(**raw["source"])
        if raw["version_id"] != version_id(raw["skill_id"], raw["package_digest"], source.revision):
            raise SkillError("store_corrupt", "Skill version fingerprint mismatch.")
        if not isinstance(raw["metadata_json"], str) or not isinstance(_decode(raw["metadata_json"].encode()), dict):
            raise SkillError("store_corrupt", "Malformed Skill metadata projection.")
        bounded_text(raw["created_at"], maximum=128)
        diagnostics = []
        if not isinstance(raw["diagnostics"], list):
            raise SkillError("store_corrupt", "Malformed Skill diagnostics.")
        for item in raw["diagnostics"]:
            if set(item) != {"code", "message", "level"} or not all(isinstance(part, str) for part in item.values()):
                raise SkillError("store_corrupt", "Malformed Skill diagnostic.")
            diagnostics.append(SkillDiagnostic(**item))
        return SkillPackageVersion(raw["version_id"], raw["skill_id"], raw["package_digest"], source,
                                   raw["metadata_json"], tuple(diagnostics), raw["created_at"])

    def _installed(self, raw: dict) -> InstalledSkill:
        return InstalledSkill(raw["skill_id"], raw["name"], SkillSource(**raw["source"]), self.scope,
                              tuple(raw["version_ids"]), raw["status"])

    def _find_version(self, index: dict, skill_id: str, selected: str) -> SkillPackageVersion:
        identifier(skill_id, "skill-")
        identifier(selected, "version-")
        raw = index["versions"].get(selected)
        if raw is None or raw["skill_id"] != skill_id or skill_id not in index["installed"]:
            raise SkillError("version_missing", "Skill version is not installed in this scope.")
        return self._version(raw)

    @staticmethod
    def _operation(index: dict, key: str, kind: str, payload: dict):
        bounded_text(key, maximum=128)
        operation_id = hashlib.sha256(key.encode()).hexdigest()
        fingerprint = content_digest(canonical_json({"kind": kind, "input": payload}).encode())
        previous = index["operations"].get(operation_id)
        if previous is not None and (previous["kind"] != kind or previous["fingerprint"] != fingerprint):
            raise SkillConflict("operation_conflict", "Operation key already belongs to different Skill inputs.")
        return operation_id, fingerprint, previous

    def _commit(self, fd: int, index: dict, operation_id: str, kind: str, fingerprint: str, result: dict):
        index["revision"] += 1
        result = {**result, "revision": index["revision"]}
        index["operations"][operation_id] = {"kind": kind, "fingerprint": fingerprint, "result": result}
        self._write_index(fd, index)
        return result

    def _load_object(self, fd: int, digest: str) -> SkillPackage:
        identifier(digest, "sha256:")
        try:
            with subdirectory(fd, "objects/" + digest[7:]) as obj:
                manifest, _ = regular_file(obj, "manifest.json", maximum=MAX_MANIFEST_BYTES)
                with subdirectory(obj, "content") as content:
                    package = capture_directory_fd(content, limits=self.limits)
                if _decode(manifest) != package.manifest or package.digest != digest:
                    raise SkillError("package_corrupt", "Stored Skill bytes do not match their fixed manifest.")
                return package
        except FileNotFoundError as exc:
            raise SkillError("package_missing", "Fixed Skill package bytes are missing; no fallback is allowed.") from exc

    def _publish_object(self, fd: int, package: SkillPackage) -> None:
        with subdirectory(fd, "objects") as objects:
            try:
                os.stat(package.digest[7:], dir_fd=objects, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                self._load_object(fd, package.digest)
                return
            name = ".stage-" + uuid4().hex
            os.mkdir(name, mode=0o700, dir_fd=objects)
            try:
                with subdirectory(objects, name) as staging:
                    os.fchmod(staging, 0o700)
                    with subdirectory(staging, "content", create=True) as content:
                        for relative in package.directories:
                            with subdirectory(content, relative, create=True) as child:
                                os.fsync(child)
                        for item in package.files:
                            parent, _, leaf = item.path.rpartition("/")
                            if parent:
                                with subdirectory(content, parent) as child:
                                    write_new(child, leaf, item.data, mode=0o500 if item.executable else 0o400)
                                    os.fsync(child)
                            else:
                                write_new(content, leaf, item.data, mode=0o500 if item.executable else 0o400)
                        # Children must be durable before their parent entries.
                        for relative in sorted(package.directories, key=lambda value: value.count("/"), reverse=True):
                            with subdirectory(content, relative) as child:
                                os.fsync(child)
                        os.fsync(content)
                    write_new(staging, "manifest.json", canonical_json(package.manifest).encode(), mode=0o400)
                    os.fsync(staging)
                os.rename(name, package.digest[7:], src_dir_fd=objects, dst_dir_fd=objects)
                os.fsync(objects)
            finally:
                _remove_staging(objects, name)
        self._load_object(fd, package.digest)

    def publish(self, package: SkillPackage, source: SkillSource, *, command_key: str) -> Publication:
        # Rebuild from immutable bytes rather than trusting derived caller metadata.
        validated = package_from_files(package.files, package.directories, root_name=package.root_name, limits=self.limits)
        if validated.metadata_json != package.metadata_json or validated.digest != package.digest:
            raise SkillError("invalid_package", "Package metadata does not match its captured bytes.")
        skill = installed_id(self.scope, source)
        selected = version_id(skill, validated.digest, source.revision)
        payload = {"source": asdict(source), "package_digest": validated.digest}
        with self._locked() as fd:
            index = self._read_index(fd)
            operation_id, fingerprint, previous = self._operation(index, command_key, "publish", payload)
            if previous is not None:
                receipt = previous["result"]
                record = self._find_version(index, receipt["skill_id"], receipt["version_id"])
                self._load_object(fd, record.package_digest)
                return Publication(self._installed(index["installed"][skill]), record, receipt["revision"], True)
            self._publish_object(fd, validated)
            raw = index["installed"].setdefault(skill, {
                "skill_id": skill, "name": validated.metadata["name"], "source": asdict(source),
                "version_ids": [], "status": "installed",
            })
            if selected not in raw["version_ids"]:
                raw["version_ids"].append(selected)
                raw["name"] = validated.metadata["name"]
                index["versions"][selected] = {
                    "version_id": selected, "skill_id": skill, "package_digest": validated.digest,
                    "source": asdict(source), "metadata_json": validated.metadata_json,
                    "diagnostics": [asdict(item) for item in validated.diagnostics],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            receipt = self._commit(fd, index, operation_id, "publish", fingerprint,
                                   {"skill_id": skill, "version_id": selected})
            return Publication(self._installed(raw), self._version(index["versions"][selected]), receipt["revision"], False)

    def list_installed(self) -> tuple[InstalledSkill, ...]:
        with self._locked() as fd:
            index = self._read_index(fd)
            return tuple(self._installed(raw) for _, raw in sorted(index["installed"].items()))

    def list_versions(self, skill_id: str) -> tuple[SkillPackageVersion, ...]:
        identifier(skill_id, "skill-")
        with self._locked() as fd:
            index = self._read_index(fd)
            raw = index["installed"].get(skill_id)
            if raw is None:
                raise SkillError("skill_missing", "Skill is not installed in this scope.")
            return tuple(self._find_version(index, skill_id, selected) for selected in raw["version_ids"])

    def get_version(self, skill_id: str, selected: str) -> SkillPackageVersion:
        with self._locked() as fd:
            index = self._read_index(fd)
            record = self._find_version(index, skill_id, selected)
            package = self._load_object(fd, record.package_digest)
            if package.metadata_json != record.metadata_json:
                raise SkillError("package_corrupt", "Stored metadata does not match package instructions.")
            return record

    def read_package(self, skill_id: str, selected: str) -> SkillPackage:
        """Read immutable fixed package bytes; no source paths or execution."""
        with self._locked() as fd:
            record = self._find_version(self._read_index(fd), skill_id, selected)
            package = self._load_object(fd, record.package_digest)
            if package.metadata_json != record.metadata_json:
                raise SkillError("package_corrupt", "Stored metadata does not match package instructions.")
            return package

    def read_resource(self, skill_id: str, selected: str, relative: str) -> bytes:
        with self._locked() as fd:
            record = self._find_version(self._read_index(fd), skill_id, selected)
            return self._load_object(fd, record.package_digest).resource(relative)

    def pin(self, skill_id: str, selected: str, *, holder_id: str, command_key: str) -> dict:
        bounded_text(holder_id)
        payload = {"holder_id": holder_id, "skill_id": skill_id, "version_id": selected}
        holder = hashlib.sha256(holder_id.encode()).hexdigest()
        with self._locked() as fd:
            index = self._read_index(fd)
            record = self._find_version(index, skill_id, selected)
            operation_id, fingerprint, previous = self._operation(index, command_key, "pin", payload)
            if previous is not None:
                return {**previous["result"], "replayed": True}
            self._load_object(fd, record.package_digest)
            old = index["pins"].get(holder)
            if old is not None and old != payload:
                raise SkillConflict("pin_conflict", "Retention holder already pins a different Skill version.")
            index["pins"][holder] = payload
            return {**self._commit(fd, index, operation_id, "pin", fingerprint, payload), "replayed": False}

    def release_pin(self, skill_id: str, selected: str, *, holder_id: str, command_key: str) -> dict:
        bounded_text(holder_id)
        identifier(skill_id, "skill-")
        identifier(selected, "version-")
        payload = {"holder_id": holder_id, "skill_id": skill_id, "version_id": selected}
        holder = hashlib.sha256(holder_id.encode()).hexdigest()
        with self._locked() as fd:
            index = self._read_index(fd)
            operation_id, fingerprint, previous = self._operation(index, command_key, "release_pin", payload)
            if previous is not None:
                return {**previous["result"], "replayed": True}
            old = index["pins"].get(holder)
            if old is not None and old != payload:
                raise SkillConflict("pin_conflict", "Retention pin changed; refusing to release another version.")
            index["pins"].pop(holder, None)
            return {**self._commit(fd, index, operation_id, "release_pin", fingerprint,
                                  {**payload, "released": old is not None}), "replayed": False}

    def retention_pins(self, skill_id: str, selected: str) -> tuple[dict, ...]:
        with self._locked() as fd:
            index = self._read_index(fd)
            self._find_version(index, skill_id, selected)
            return tuple(dict(raw) for _, raw in sorted(index["pins"].items())
                         if raw["skill_id"] == skill_id and raw["version_id"] == selected)
