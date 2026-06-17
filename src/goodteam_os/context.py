from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


CONTEXT_DIR_NAME = ".goodteam/context"
UNDERSTANDING_FILE = "understanding.md"
INDEX_FILE = "index.md"
SOURCES_FILE = "sources.yaml"

_IGNORED_DIRS = {".git", ".goodteam", ".venv", "__pycache__", "node_modules", "dist", "build", ".next"}
_TEXT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}


def read_context_entry(root: Path) -> dict[str, object] | None:
    context_dir = root / CONTEXT_DIR_NAME
    understanding_path = context_dir / UNDERSTANDING_FILE
    try:
        understanding = understanding_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not understanding.strip():
        return None
    index = _read_text_if_present(context_dir / INDEX_FILE)
    sources = _parse_sources_yaml(context_dir / SOURCES_FILE)
    return {
        "understanding": understanding,
        "index": index,
        "sources": sources,
        "source": "l1_cache",
    }


def estimate_context_tokens(sources: list[dict]) -> dict[str, int]:
    total = 0
    for source in sources:
        content = source.get("content")
        if isinstance(content, str):
            total += len(content.encode("utf-8"))
            continue
        path_value = source.get("path")
        if path_value:
            try:
                path = Path(str(path_value))
                if path.is_file():
                    total += path.stat().st_size
                    continue
            except OSError:
                pass
        summary = source.get("summary")
        if isinstance(summary, str):
            total += len(summary.encode("utf-8"))
    return {"bytes": total, "est_tokens": total // 4, "source_count": len(sources)}


def build_code_skeleton(root: Path, *, max_files: int = 400, max_bytes: int = 1_000_000) -> str:
    root = root.resolve()
    lines = ["# Directory Tree", _render_directory_tree(root), "", "# File Inventory"]
    scanned = 0
    consumed = 0
    for path in _iter_skeleton_files(root):
        if scanned >= max_files or consumed >= max_bytes:
            lines.append("- ...")
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if consumed + size > max_bytes:
            break
        signatures = _file_signatures(path)
        if not signatures:
            continue
        scanned += 1
        consumed += size
        relative = path.relative_to(root)
        lines.append(f"- {relative}")
        lines.extend(f"  - {signature}" for signature in signatures)
    return "\n".join(line for line in lines if line is not None)


def generate_context_entry(
    *,
    sources: list[dict],
    root: Path,
    generator: Callable[[str], str] | None = None,
) -> dict[str, str]:
    context_dir = root / CONTEXT_DIR_NAME
    context_dir.mkdir(parents=True, exist_ok=True)
    if generator is not None:
        prompt = _generation_prompt(sources)
        understanding = generator(prompt)
    else:
        understanding = _deterministic_understanding(sources)
    index = _deterministic_index(sources)
    source_record = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": [_source_record(source) for source in sources],
    }

    understanding_path = context_dir / UNDERSTANDING_FILE
    index_path = context_dir / INDEX_FILE
    understanding_path.write_text(understanding, encoding="utf-8")
    index_path.write_text(index, encoding="utf-8")
    (context_dir / SOURCES_FILE).write_text(_dump_sources_yaml(source_record), encoding="utf-8")
    return {
        "understanding_path": str(understanding_path),
        "index_path": str(index_path),
        "source": "generated",
    }


def load_or_generate_context(
    *,
    project_config: Path | None,
    root: Path,
    generate: bool = False,
    generator: Callable[[str], str] | None = None,
) -> dict[str, object]:
    entry = read_context_entry(root)
    if entry is not None:
        return {"entry": entry, "estimate": None, "generated": False, "source": "l1_cache"}

    from goodteam_os.graph import collect_project_context

    sources = collect_project_context(project_config)
    if not sources:
        sources = [
            {
                "repo": "root",
                "path": str(root),
                "purpose": "Code skeleton",
                "summary": build_code_skeleton(root),
            }
        ]
    estimate = estimate_context_tokens(sources)
    if not generate:
        return {"entry": None, "estimate": estimate, "generated": False, "needs_generation": True}
    generate_context_entry(sources=sources, root=root, generator=generator)
    return {"entry": read_context_entry(root), "estimate": estimate, "generated": True}


def _read_text_if_present(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _parse_sources_yaml(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    result: dict[str, object] = {}
    sources: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "sources:":
            result["sources"] = sources
            continue
        if stripped.startswith("- "):
            current = {}
            sources.append(current)
            item = stripped[2:]
            if ": " in item:
                key, value = item.split(": ", 1)
                current[key] = _unquote_yaml_scalar(value)
            continue
        if raw_line.startswith("  ") and current is not None and ": " in stripped:
            key, value = stripped.split(": ", 1)
            current[key] = _unquote_yaml_scalar(value)
            continue
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            result[key] = _unquote_yaml_scalar(value)
    if sources and "sources" not in result:
        result["sources"] = sources
    return result


def _dump_sources_yaml(record: dict[str, object]) -> str:
    lines = [f"generated_at: {_quote_yaml_scalar(str(record.get('generated_at', '')))}", "sources:"]
    for source in record.get("sources", []):
        if not isinstance(source, dict):
            continue
        first = True
        for key in ["repo", "path", "url", "purpose", "topic", "kind", "value"]:
            value = source.get(key)
            if value is None or value == "":
                continue
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{key}: {_quote_yaml_scalar(str(value))}")
            first = False
        if first:
            lines.append("  - value: ''")
    return "\n".join(lines) + "\n"


def _quote_yaml_scalar(value: str) -> str:
    if not value or any(char in value for char in [":", "#", "\n", "'", '"']):
        return repr(value)
    return value


def _unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _iter_skeleton_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
        if any(part in _IGNORED_DIRS or part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and (path.suffix == ".py" or path.suffix in _TEXT_SUFFIXES):
            yield path


def _render_directory_tree(root: Path) -> str:
    try:
        from goodteam_os.graph import render_shallow_directory_tree

        tree = render_shallow_directory_tree(root)
        if tree:
            return tree
    except Exception:
        pass
    lines = [f"{root.name}/"]
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return "\n".join(lines)
    for child in children:
        if child.name in _IGNORED_DIRS or child.name.startswith("."):
            continue
        lines.append(f"- {child.name}{'/' if child.is_dir() else ''}")
    return "\n".join(lines)


def _file_signatures(path: Path) -> list[str]:
    if path.suffix == ".py":
        return _python_signatures(path)
    if path.suffix in _TEXT_SUFFIXES:
        return _js_ts_signatures(path)
    return []


def _python_signatures(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(base) for base in node.bases]
            suffix = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {node.name}{suffix}:")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"{prefix} {node.name}({_format_arguments(node.args)}):")
    return lines


def _format_arguments(args: ast.arguments) -> str:
    parts = [arg.arg for arg in args.posonlyargs + args.args]
    if args.vararg is not None:
        parts.append(f"*{args.vararg.arg}")
    parts.extend(arg.arg for arg in args.kwonlyargs)
    if args.kwarg is not None:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)


def _js_ts_signatures(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    signatures: list[str] = []
    patterns = [
        re.compile(r"^\s*export\s+(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)"),
        re.compile(r"^\s*export\s+(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)"),
    ]
    for line in lines:
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                signatures.append(match.group(0).strip())
                break
    return signatures


def _deterministic_understanding(sources: list[dict]) -> str:
    sections = ["# GoodTeam Project Understanding", ""]
    for index, source in enumerate(sources, start=1):
        purpose = str(source.get("purpose") or source.get("topic") or "Context source")
        location = str(source.get("path") or source.get("url") or "")
        summary = _source_summary(source)
        sections.extend([f"## {index}. {purpose}", "", f"- Source: {location or 'inline'}", f"- Summary: {summary}", ""])
    return "\n".join(sections).strip() + "\n"


def _deterministic_index(sources: list[dict]) -> str:
    lines = ["# Context Index", ""]
    for source in sources:
        topic = str(source.get("topic") or source.get("purpose") or "Context source")
        location = str(source.get("path") or source.get("url") or "inline")
        lines.append(f"- {topic}: {location}")
    return "\n".join(lines).strip() + "\n"


def _source_summary(source: dict) -> str:
    summary = source.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _cap(summary.strip())
    content = source.get("content")
    if isinstance(content, str) and content.strip():
        return _summarize_text(content)
    path_value = source.get("path")
    if path_value:
        try:
            path = Path(str(path_value))
            if path.is_file():
                return _summarize_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            pass
    return "No readable summary available."


def _summarize_text(content: str) -> str:
    try:
        from goodteam_os.graph import summarize_context_file

        return _cap(summarize_context_file(content).strip())
    except Exception:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return _cap(" ".join(lines[:6]))


def _cap(value: str, limit: int = 1200) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _source_record(source: dict) -> dict[str, str]:
    return {
        "repo": str(source.get("repo", "")),
        "path": str(source.get("path", "")),
        "url": str(source.get("url", "")),
        "purpose": str(source.get("purpose", "")),
        "topic": str(source.get("topic", "")),
        "kind": str(source.get("kind", "")),
        "value": str(source.get("value", "")),
    }


def _generation_prompt(sources: list[dict]) -> str:
    return _deterministic_understanding(sources)
