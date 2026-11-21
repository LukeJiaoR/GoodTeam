"""Skill package parsing, validation, and safe path utilities."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any
import yaml


MAX_SKILL_BYTES = 5 * 1024 * 1024
MAX_SKILL_FILES = 200
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


class SkillError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class SkillManifest:
    slug: str
    name: str
    description: str
    body: str
    raw: str
    metadata: dict[str, Any]


def slugify(text: str) -> str:
    cleaned = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return cleaned[:64] or "skill"


def parse_skill_markdown(text: str, fallback_name: str = "") -> SkillManifest:
    if not text.strip():
        raise SkillError("invalid_skill", "SKILL.md cannot be empty.")
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---\n") or text.startswith("---\r\n"):
        lines = text.splitlines(keepends=True)
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is not None:
            front = "".join(lines[1:end_idx])
            body = "".join(lines[end_idx + 1 :]).lstrip("\r\n")
            try:
                loaded = yaml.safe_load(front) or {}
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError as exc:
                raise SkillError("invalid_frontmatter", f"Invalid SKILL.md YAML frontmatter: {exc}") from exc
    name = str(meta.get("name") or fallback_name or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name:
        for line in body.splitlines():
            if line.startswith("# "):
                name = line[2:].strip()
                break
    if not name:
        raise SkillError("missing_skill_name", "SKILL.md requires a 'name' in frontmatter or a title heading.")
    if not description:
        for line in body.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                description = s[:200]
                break
    return SkillManifest(
        slug=slugify(name),
        name=name[:100],
        description=description[:500],
        body=body,
        raw=text,
        metadata=meta,
    )


def validate_skill_dir(root: Path) -> SkillManifest:
    resolved = root.resolve()
    skill_md = resolved / "SKILL.md"
    if not resolved.is_dir() or not skill_md.is_file() or skill_md.is_symlink():
        raise SkillError("skill_not_found", "Directory must contain a regular SKILL.md file.")
    total_bytes = 0
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(resolved, followlinks=False):
        current = Path(dirpath)
        for dname in dirnames:
            if (current / dname).is_symlink():
                raise SkillError("unsafe_skill", "Symlinks are not permitted inside skill packages.")
        for fname in filenames:
            fpath = current / fname
            if fpath.is_symlink():
                raise SkillError("unsafe_skill", "Symlinks are not permitted inside skill packages.")
            file_count += 1
            if file_count > MAX_SKILL_FILES:
                raise SkillError("skill_too_large", f"Skill package exceeds {MAX_SKILL_FILES} files.")
            total_bytes += fpath.stat().st_size
            if total_bytes > MAX_SKILL_BYTES:
                raise SkillError("skill_too_large", f"Skill package exceeds {MAX_SKILL_BYTES // (1024 * 1024)}MB.")
    try:
        content = skill_md.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError("invalid_skill", "SKILL.md must be valid UTF-8 text.") from exc
    return parse_skill_markdown(content, fallback_name=resolved.name)


def safe_subpath(root: Path, rel_path: str) -> Path:
    if not rel_path or rel_path.startswith(("/", "\\")) or "\x00" in rel_path:
        raise SkillError("invalid_path", "Invalid resource path.")
    base = root.resolve()
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise SkillError("path_traversal", "Resource path escapes the skill directory.") from None
    if target.is_symlink() or not target.is_file():
        raise SkillError("resource_not_found", "Requested skill resource does not exist.", 404)
    return target
