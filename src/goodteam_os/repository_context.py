"""[INPUT]: 项目配置、仓库资料与项目记忆。
[OUTPUT]: 生成只读项目上下文。
[POS]: 供项目理解与上下文缓存的来源。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import re
from pathlib import Path

from goodteam_os import project_runtime
from goodteam_os.memory import ProjectMemoryStore, summarize_markdown
from goodteam_os.projects import load_project_target
from goodteam_os.roles import (
    context_file_specs,
)


# Bounds for single-root `docs/` context gathering. The cap is generous so
# doc-heavy repos (GoodTeam's own docs/ is 40+ files) are not silently
# truncated, while the byte budget keeps a pathological repo from blowing up
# the context packet.
MAX_CONTEXT_DOC_FILES = 200
MAX_CONTEXT_DOC_BYTES = 2_000_000

# Root-level instruction / agent-guidance files (matched case-insensitively).
# These are higher-signal than design docs, so they are loaded before docs/.
ROOT_INSTRUCTION_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "AGENT.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "CONTEXT.md",
    "GOODTEAM.md",
)

GENERATED_TAKEOVER_DOC_NAMES = {
    "takeover.md",
    "takeover-context.md",
}


def collect_project_context(
    project_config: Path | None = None,
) -> list[dict[str, str]]:
    if project_config is None:
        return []
    if not project_config.is_file():
        return []

    target = load_project_target(project_config)
    repos = dict(target.get("repos", {}))
    packets = []
    memory_root = select_project_memory_root(repos)
    if memory_root is not None:
        memory = ProjectMemoryStore(memory_root).read_project_memory(str(target.get("key", "")))
        if memory["content"]:
            packets.append(
                {
                    "repo": "project_memory",
                    "path": memory["path"],
                    "purpose": "Project-local GoodTeam memory",
                    "summary": memory["summary"],
                }
            )
    single_root = single_root_context_root(repos)
    if single_root is not None:
        from goodteam_os.context import read_context_entry

        entry = read_context_entry(single_root)
        if entry is not None:
            packets.append(
                {
                    "repo": "goodteam_context",
                    "path": str((single_root / ".goodteam" / "context" / "understanding.md").resolve()),
                    "purpose": "GoodTeam project understanding",
                    "summary": str(entry.get("understanding", "")),
                }
            )
        packets.extend(discover_single_root_context(single_root))
        return packets
    for repo_name, files in context_file_specs().items():
        repo_root = repos.get(repo_name)
        if not repo_root:
            continue
        for spec in files:
            relative_path = str(spec.get("path", ""))
            path = Path(str(repo_root)) / relative_path
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            packets.append(
                {
                    "repo": repo_name,
                    "path": str(path),
                    "purpose": str(spec.get("purpose", relative_path)),
                    "summary": summarize_context_file(content),
                }
            )
    return packets


def single_root_context_root(repos: dict[object, object]) -> Path | None:
    if len(repos) != 1:
        return None
    raw_root = next(iter(repos.values()), "")
    if not raw_root:
        return None
    root = Path(str(raw_root))
    return root.resolve() if root.is_dir() else None


def discover_single_root_context(root: Path) -> list[dict[str, str]]:
    packets: list[dict[str, str]] = []
    seen: set[Path] = set()
    for path, purpose in single_root_context_files(root):
        try:
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, RuntimeError, ValueError):
            continue
        seen.add(resolved)
        packets.append(
            {
                "repo": "root",
                "path": str(resolved),
                "purpose": purpose,
                "summary": summarize_context_path(resolved, content),
            }
        )
    tree_summary = render_shallow_directory_tree(root)
    if tree_summary:
        packets.append(
            {
                "repo": "root",
                "path": str(root),
                "purpose": "Shallow project directory overview",
                "summary": tree_summary,
            }
        )
    return packets


def single_root_context_files(root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(root.glob("README*"), key=lambda item: item.name.lower()):
        if path.is_file():
            files.append((path, "Top-level project README"))
    wanted_instructions = {name.lower() for name in ROOT_INSTRUCTION_FILES}
    instruction_files = [
        path
        for path in root.iterdir()
        if path.is_file() and path.name.lower() in wanted_instructions
    ]
    for path in sorted(instruction_files, key=lambda item: item.name.lower()):
        files.append((path, "Project instructions / agent guidance"))
    manifest_names = {
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.in",
        "Gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
    }
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and (path.name in manifest_names or re.fullmatch(r"requirements[-\w]*\.txt", path.name)):
            files.append((path, "Project manifest or dependency file"))
    workflows_root = root / ".github" / "workflows"
    if workflows_root.is_dir():
        for path in sorted(workflows_root.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}:
                files.append((path, "CI workflow"))
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        lowered = path.name.lower()
        if path.is_file() and (
            lowered.startswith("docker-compose")
            and path.suffix.lower() in {".yml", ".yaml"}
        ):
            files.append((path, "Deployment manifest"))
        if path.is_file() and lowered.startswith("dockerfile"):
            files.append((path, "Container build definition"))
    for pattern in ("*/Dockerfile*", "*/*/Dockerfile*"):
        for path in sorted(root.glob(pattern), key=lambda item: str(item).lower()):
            if path.is_file():
                files.append((path, "Container build definition"))
    docs_root = root / "docs"
    if docs_root.is_dir():
        docs = [
            path
            for path in docs_root.rglob("*")
            if path.is_file()
            and len(path.relative_to(docs_root).parts) <= 2
            and path.name.lower() not in GENERATED_TAKEOVER_DOC_NAMES
        ]
        doc_bytes = 0
        for path in sorted(docs, key=lambda item: str(item.relative_to(docs_root)).lower())[:MAX_CONTEXT_DOC_FILES]:
            try:
                doc_bytes += path.stat().st_size
            except OSError:
                continue
            if doc_bytes > MAX_CONTEXT_DOC_BYTES:
                break
            files.append((path, "Project documentation"))
    return files


def render_shallow_directory_tree(root: Path, *, max_entries: int = 80) -> str:
    ignored = {".git", ".goodteam", ".venv", "__pycache__", "node_modules", "dist", "build", ".next"}
    lines = [f"{root.name}/"]
    count = 0
    try:
        top_level = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return ""
    for path in top_level:
        if count >= max_entries:
            lines.append("- ...")
            break
        if path.name in ignored or path.name.startswith("."):
            continue
        suffix = "/" if path.is_dir() else ""
        lines.append(f"- {path.name}{suffix}")
        count += 1
        if not path.is_dir():
            continue
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children[:8]:
            if count >= max_entries:
                break
            if child.name in ignored or child.name.startswith("."):
                continue
            if child.name.lower() in GENERATED_TAKEOVER_DOC_NAMES:
                continue
            child_suffix = "/" if child.is_dir() else ""
            lines.append(f"  - {child.name}{child_suffix}")
            count += 1
    return "\n".join(lines)


def select_project_memory_root(repos: dict[object, object]) -> Path | None:
    runtime_root = project_runtime.resolve_project_runtime_root(repos)
    if runtime_root is not None:
        return runtime_root
    for name in ["docs", "backend", "frontend"]:
        value = repos.get(name)
        if value and Path(str(value)).is_dir():
            return Path(str(value))
    return None


def summarize_context_file(content: str) -> str:
    return summarize_markdown(content)


def summarize_context_path(path: Path, content: str) -> str:
    if path.parent.name == "workflows" and path.parent.parent.name == ".github":
        workflow_name = next(
            (
                match.group(1).strip()
                for line in content.splitlines()
                if (match := re.match(r"^name:\s*(.+?)\s*$", line))
            ),
            path.stem,
        )
        triggers = sorted(
            {
                match.group(1)
                for line in content.splitlines()
                if (match := re.match(r"^\s{2}(workflow_dispatch|push|pull_request|schedule):", line))
            }
        )
        steps = [
            match.group(1).strip()
            for line in content.splitlines()
            if (match := re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line))
        ]
        secret_names = sorted(set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", content)))
        variable_names = sorted(set(re.findall(r"vars\.([A-Z][A-Z0-9_]*)", content)))
        details = [f"Workflow: {workflow_name}"]
        if triggers:
            trigger_text = ", ".join(triggers)
            if triggers == ["workflow_dispatch"]:
                trigger_text += " (manual only)"
            details.append(f"Triggers: {trigger_text}")
        if steps:
            details.append("Steps: " + ", ".join(steps))
        if secret_names:
            details.append("Secret names: " + ", ".join(secret_names))
        if variable_names:
            details.append("Variable names: " + ", ".join(variable_names))
        return " | ".join(details)

    lowered_name = path.name.lower()
    if lowered_name.startswith("docker-compose") and path.suffix.lower() in {".yml", ".yaml"}:
        summary = summarize_context_file(content)
        sensitive_keys = sorted(
            {
                match.group(1)
                for line in content.splitlines()
                if (
                    match := re.match(
                        r"^\s*(MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|DATABASE_PASSWORD|PASSWORD|SECRET|TOKEN):\s*(.+?)\s*$",
                        line,
                    )
                )
                and match.group(2)
                and not match.group(2).lstrip().startswith(("${", "$"))
            }
        )
        if sensitive_keys:
            warning = "Warning: static sensitive-looking values detected for " + ", ".join(sensitive_keys)
            return f"{summary} | {warning}" if summary else warning
        return summary

    if path.name == "package.json":
        try:
            manifest = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return summarize_context_file(content)
        details = []
        package_name = str(manifest.get("name", "")).strip()
        if package_name:
            details.append(f"Package: {package_name}")
        package_manager = str(manifest.get("packageManager", "")).strip()
        if package_manager:
            details.append(f"Package manager: {package_manager}")
        scripts = manifest.get("scripts", {})
        if isinstance(scripts, dict) and scripts:
            details.append("Scripts: " + ", ".join(sorted(str(key) for key in scripts)))
        engines = manifest.get("engines", {})
        if isinstance(engines, dict) and engines:
            details.append(
                "Engines: "
                + ", ".join(f"{key} {value}" for key, value in sorted(engines.items()))
            )
        if details:
            return " | ".join(details)
    return summarize_context_file(content)
