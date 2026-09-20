"""Versioned Skill identities. These records never grant runtime authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import urlsplit


class SkillError(ValueError):
    """Stable, non-secret diagnostics for package/store callers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SkillConflict(SkillError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def identifier(value: str, prefix: str) -> str:
    if not isinstance(value, str) or re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", value) is None:
        raise SkillError("invalid_identity", "Invalid Skill identity.")
    return value


def bounded_text(value: str, *, maximum: int = 256, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SkillError("invalid_identity", "Invalid identity text.")
    return value


def resource_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SkillError("unsafe_path", "Invalid package resource path.")
    try:
        if len(value.encode("utf-8")) > 2048:
            raise ValueError("Path too long")
    except (ValueError, UnicodeError) as exc:
        raise SkillError("unsafe_path", "Invalid package resource path.") from exc
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute() or any(part in {"", ".", ".."} for part in parts)
            or "\\" in value or ":" in value or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SkillError("unsafe_path", "Package paths must remain relative to their root.")
    return value


@dataclass(frozen=True)
class SkillScope:
    kind: str
    owner_id: str
    project_key: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"user", "project", "builtin"}:
            raise SkillError("invalid_scope", "Unknown Skill scope.")
        bounded_text(self.owner_id, maximum=128)
        bounded_text(self.project_key, maximum=128, empty=True)
        if (self.kind == "project") != bool(self.project_key):
            raise SkillError("invalid_scope", "Project scope requires exactly one project identity.")

    @property
    def namespace(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class SkillSource:
    kind: str
    locator: str
    subdirectory: str = ""
    revision: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"local", "git", "builtin"}:
            raise SkillError("invalid_source", "Unknown Skill source.")
        bounded_text(self.locator, maximum=2048)
        bounded_text(self.revision, maximum=256, empty=True)
        if self.subdirectory:
            resource_path(self.subdirectory)
        if self.kind == "git":
            try:
                url = urlsplit(self.locator)
                valid = (url.scheme == "https" and url.hostname and not url.username and not url.password
                         and not url.query and not url.fragment and bool(self.revision)
                         and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.revision) is not None)
            except ValueError:
                valid = False
            if not valid:
                raise SkillError("invalid_source", "Git package records require a credential-free HTTPS source and resolved commit.")

    @property
    def logical_identity(self) -> dict[str, str]:
        return {"kind": self.kind, "locator": self.locator, "subdirectory": self.subdirectory}


def installed_id(scope: SkillScope, source: SkillSource) -> str:
    raw = canonical_json({"scope": asdict(scope), "source": source.logical_identity})
    return "skill-" + hashlib.sha256(raw.encode()).hexdigest()


def version_id(skill_id: str, digest: str, revision: str) -> str:
    identifier(skill_id, "skill-")
    identifier(digest, "sha256:")
    return "version-" + hashlib.sha256(canonical_json([skill_id, digest, revision]).encode()).hexdigest()


@dataclass(frozen=True)
class SkillDiagnostic:
    code: str
    message: str
    level: str = "warning"


@dataclass(frozen=True)
class PackageLimits:
    max_files: int = 512
    max_entries: int = 1024
    max_depth: int = 16
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    max_skill_bytes: int = 256 * 1024
    max_frontmatter_bytes: int = 32 * 1024

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise SkillError("invalid_limits", "Package limits must be positive integers.")


@dataclass(frozen=True)
class InstalledSkill:
    skill_id: str
    name: str
    source: SkillSource
    scope: SkillScope
    version_ids: tuple[str, ...]
    status: str = "installed"


@dataclass(frozen=True)
class SkillPackageVersion:
    version_id: str
    skill_id: str
    package_digest: str
    source: SkillSource
    metadata_json: str
    diagnostics: tuple[SkillDiagnostic, ...]
    created_at: str

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)


@dataclass(frozen=True)
class Publication:
    installed: InstalledSkill
    version: SkillPackageVersion
    revision: int
    replayed: bool
