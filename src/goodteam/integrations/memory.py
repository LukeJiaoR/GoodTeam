"""Project-local Markdown memory under <project_path>/.goodteam/memory/*.md."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any


MEMORY_REL_DIR = Path(".goodteam") / "memory"
DEFAULT_FILES = ("project.md", "decisions.md", "lessons.md")
_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}\.md$")


class MemoryError(ValueError):
    pass


def memory_dir(project_path: str | Path) -> Path:
    return Path(project_path).expanduser().resolve() / MEMORY_REL_DIR


def validate_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not _FILENAME_RE.fullmatch(name):
        raise MemoryError("Memory filename must match ^[a-z0-9][a-z0-9._-]{0,60}\\.md$.")
    return name


def _safe_file(project_path: str | Path, filename: str) -> Path:
    base = memory_dir(project_path)
    name = validate_filename(filename)
    target = (base / name).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise MemoryError("Memory file path escapes .goodteam/memory.") from None
    return target


def list_files(project_path: str | Path) -> list[dict[str, Any]]:
    root = memory_dir(project_path)
    existing: dict[str, Path] = {}
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_file() and not p.is_symlink() and _FILENAME_RE.fullmatch(p.name):
                existing[p.name] = p
    ordered_names = [*DEFAULT_FILES, *(k for k in sorted(existing) if k not in DEFAULT_FILES)]
    items = []
    for name in ordered_names:
        p = existing.get(name)
        items.append({
            "filename": name,
            "exists": p is not None,
            "bytes": p.stat().st_size if p else 0,
            "updated_at": p.stat().st_mtime if p else 0,
        })
    return items


def read_file(project_path: str | Path, filename: str) -> str:
    target = _safe_file(project_path, filename)
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8")


def write_file(project_path: str | Path, filename: str, content: str, *, mode: str = "replace") -> dict[str, Any]:
    target = _safe_file(project_path, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = str(content or "")
    if mode == "append":
        prior = target.read_text(encoding="utf-8") if target.is_file() else ""
        sep = "\n\n" if prior and not prior.endswith("\n\n") else ("\n" if prior and not prior.endswith("\n") else "")
        final = prior + sep + text.lstrip("\n")
    elif mode == "replace":
        final = text
    else:
        raise MemoryError("mode must be 'replace' or 'append'.")
    if final and not final.endswith("\n"):
        final += "\n"
    target.write_text(final, encoding="utf-8")
    return {"filename": target.name, "bytes": target.stat().st_size, "content": final}


def search(project_path: str | Path, query: str) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    if not needle:
        return []
    root = memory_dir(project_path)
    if not root.is_dir():
        return []
    hits = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.is_symlink() or not _FILENAME_RE.fullmatch(p.name):
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        matched = [f"L{idx}: {line}" for idx, line in enumerate(lines, 1) if needle in line.lower()]
        if matched:
            hits.append({"filename": p.name, "matches": matched[:20]})
    return hits


def summary_for_prompt(project_path: str | Path, *, max_chars: int = 4000) -> str:
    root = memory_dir(project_path)
    if not root.is_dir():
        return ""
    chunks: list[str] = []
    used = 0
    files = sorted(root.iterdir(), key=lambda p: (0 if p.name in DEFAULT_FILES else 1, p.name))
    for p in files:
        if not p.is_file() or p.is_symlink() or not _FILENAME_RE.fullmatch(p.name):
            continue
        body = p.read_text(encoding="utf-8", errors="replace").strip()
        if not body:
            continue
        block = f"### {p.name}\n{body}"
        if used + len(block) > max_chars:
            rem = max_chars - used
            if rem > 120:
                chunks.append(block[:rem] + "\n...[truncated]")
            break
        chunks.append(block)
        used += len(block) + 2
    return "\n\n".join(chunks)
