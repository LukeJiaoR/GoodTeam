"""User-facing local Skill library service.

Preview and install are deliberately separate. A preview captures an explicitly
authorized server-side directory into an owned immutable proposal. Confirmation
publishes only those captured bytes; it never reopens the source directory.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .models import SkillConflict, SkillError, SkillScope, SkillSource, bounded_text, canonical_json
from .package import SkillPackage, capture_package, package_from_files
from .store import SkillStore

PROPOSAL_SCHEMA = "skill_install_proposal.v1"
LOCAL_SCOPE_OWNER = "local-operator"
PROPOSAL_RE = re.compile(r"proposal-[0-9a-f]{32}")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_json(payload) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _safe_proposal_id(value: str) -> str:
    if not isinstance(value, str) or PROPOSAL_RE.fullmatch(value) is None:
        raise SkillError("invalid_proposal", "Invalid Skill install proposal.")
    return value


def _source_root(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\x00" in value:
        raise SkillError("invalid_source", "A local Skill directory is required.")
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillError("source_unavailable", "The selected Skill directory is unavailable.") from exc
    if not resolved.is_dir():
        raise SkillError("source_unavailable", "The selected Skill source must be a directory.")
    return resolved


def _public_source(source: SkillSource) -> dict[str, str]:
    label = Path(source.locator).name if source.kind == "local" else source.locator
    return {"kind": source.kind, "label": label, "revision": source.revision}


def _diagnostics(items) -> list[dict[str, str]]:
    return [asdict(item) for item in items]


def _copy_package(package: SkillPackage, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for relative in sorted(package.directories, key=lambda item: (item.count("/"), item)):
        target = root / relative
        target.mkdir(parents=True, exist_ok=False)
        target.chmod(0o700)
    for item in package.files:
        target = root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(target, flags, 0o500 if item.executable else 0o400)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(item.data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fchmod(fd, 0o500 if item.executable else 0o400)
        finally:
            os.close(fd)


class SkillLibraryService:
    """Single-operator library facade over the immutable SkillStore."""

    def __init__(self, state_root: Path, *, worker_roots: tuple[Path, ...] = ()):
        self.root = Path(state_root).expanduser().absolute() / "skills"
        self.proposals = self.root / "proposals"
        self.proposals.mkdir(parents=True, exist_ok=True)
        scope = SkillScope(kind="user", owner_id=LOCAL_SCOPE_OWNER)
        self.store = SkillStore(self.root / "store", scope, worker_roots=worker_roots)

    @staticmethod
    def _proposal_id(command_key: str) -> str:
        bounded_text(command_key, maximum=128)
        return "proposal-" + hashlib.sha256(command_key.encode("utf-8")).hexdigest()[:32]

    def _proposal_path(self, proposal_id: str) -> Path:
        return self.proposals / _safe_proposal_id(proposal_id)

    def _load_proposal(self, proposal_id: str) -> dict[str, Any]:
        path = self._proposal_path(proposal_id) / "proposal.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SkillError("proposal_missing", "Skill install proposal was not found.") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillError("proposal_unavailable", "Skill install proposal is unavailable.") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != PROPOSAL_SCHEMA:
            raise SkillError("unsupported_schema", "Unknown Skill install proposal schema.")
        return payload

    def _public_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": payload["schema_version"],
            "proposal_id": payload["proposal_id"],
            "status": payload["status"],
            "source": {"kind": "local", "label": payload["source_label"]},
            "metadata": dict(payload["metadata"]),
            "package_digest": payload["package_digest"],
            "manifest": dict(payload["manifest"]),
            "diagnostics": list(payload["diagnostics"]),
            "installed": dict(payload.get("installed") or {}),
        }

    def preview_local(self, source_path: str, *, command_key: str) -> dict[str, Any]:
        proposal_id = self._proposal_id(command_key)
        final = self._proposal_path(proposal_id)
        if final.exists():
            previous = self._load_proposal(proposal_id)
            if previous.get("requested_source") != source_path:
                raise SkillConflict("operation_conflict", "Install preview key belongs to another source.")
            return self._public_proposal(previous)

        source = _source_root(source_path)
        package = capture_package(source)
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.proposals))
        try:
            content = stage / "content"
            _copy_package(package, content)
            source_record = SkillSource(kind="local", locator=str(source))
            payload = {
                "schema_version": PROPOSAL_SCHEMA,
                "proposal_id": proposal_id,
                "status": "awaiting_confirmation",
                "requested_source": source_path,
                "source": asdict(source_record),
                "source_label": source.name,
                "metadata": package.metadata,
                "root_name": package.root_name,
                "package_digest": package.digest,
                "manifest": package.manifest,
                "diagnostics": _diagnostics(package.diagnostics),
                "installed": {},
            }
            _atomic_json(stage / "proposal.json", payload)
            try:
                os.rename(stage, final)
            except FileExistsError:
                previous = self._load_proposal(proposal_id)
                if previous.get("requested_source") != source_path:
                    raise SkillConflict("operation_conflict", "Install preview key belongs to another source.")
                return self._public_proposal(previous)
            parent = os.open(self.proposals, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            return self._public_proposal(payload)
        finally:
            if stage.exists():
                import shutil
                shutil.rmtree(stage, ignore_errors=True)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self._public_proposal(self._load_proposal(proposal_id))

    def install(self, proposal_id: str) -> dict[str, Any]:
        payload = self._load_proposal(proposal_id)
        if payload["status"] == "installed":
            return self._public_proposal(payload)
        proposal_root = self._proposal_path(proposal_id)
        captured = capture_package(proposal_root / "content")
        package = package_from_files(
            captured.files,
            captured.directories,
            root_name=str(payload.get("root_name", "") or ""),
        )
        if package.digest != payload["package_digest"] or package.manifest != payload["manifest"]:
            raise SkillError("proposal_changed", "Skill install proposal bytes changed; re-preview the source.")
        source = SkillSource(**payload["source"])
        publication = self.store.publish(package, source, command_key=f"local-install:{proposal_id}")
        payload["status"] = "installed"
        payload["installed"] = {
            "skill_id": publication.installed.skill_id,
            "version_id": publication.version.version_id,
            "revision": publication.revision,
            "replayed": publication.replayed,
        }
        _atomic_json(proposal_root / "proposal.json", payload)
        return self._public_proposal(payload)

    def list_library(self) -> dict[str, Any]:
        items = []
        for installed in self.store.list_installed():
            versions = self.store.list_versions(installed.skill_id)
            items.append({
                "skill_id": installed.skill_id,
                "name": installed.name,
                "status": installed.status,
                "source": _public_source(installed.source),
                "versions": [{
                    "version_id": version.version_id,
                    "package_digest": version.package_digest,
                    "metadata": version.metadata,
                    "diagnostics": _diagnostics(version.diagnostics),
                    "created_at": version.created_at,
                    "source": _public_source(version.source),
                } for version in versions],
            })
        return {"schema_version": "skill_library.v1", "scope": "local-operator", "skills": items}
