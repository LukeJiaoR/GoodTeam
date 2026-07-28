from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path, PurePosixPath

from goodteam_os.repository_access import (
    DEFAULT_IGNORED_DIRECTORIES,
    AnchoredRepository,
    RepositoryAccessError,
    RepositoryEntry,
)
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    verify_repository_snapshot,
)


MAX_ENTRIES = 1_500
MAX_DEPTH = 4
MAX_READ_BYTES = 256 * 1024
SKIPPED_DIRECTORY_NAMES = DEFAULT_IGNORED_DIRECTORIES
DOCUMENT_NAMES = {
    "README",
    "README.md",
    "README.rst",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
}
ENTRY_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "manage.py",
    "index.js",
    "index.ts",
    "main.js",
    "main.ts",
    "main.tsx",
    "main.jsx",
}
MANIFEST_TECH = {
    "package.json": "Node.js / JavaScript",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java / Maven",
    "build.gradle": "Java / Gradle",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
}


def build_repository_inventory(
    root: Path,
    snapshot: RepositorySnapshot,
    *,
    max_entries: int = MAX_ENTRIES,
    max_depth: int = MAX_DEPTH,
    max_read_bytes: int = MAX_READ_BYTES,
) -> dict[str, object]:
    _validate_limits(max_entries, max_depth, max_read_bytes)
    root = Path(root).expanduser()
    verify_repository_snapshot(root, snapshot)
    with AnchoredRepository(
        root,
        expected_identity=snapshot.root_identity,
    ) as repository:
        inventory = _scan_repository(
            repository,
            max_entries=max_entries,
            max_depth=max_depth,
            max_read_bytes=max_read_bytes,
        )
    verify_repository_snapshot(root, snapshot)
    inventory.update(
        {
            "schema": "repository_inventory.v1",
            "snapshot_id": snapshot.snapshot_id,
        }
    )
    inventory["inventory_digest"] = _inventory_digest(inventory)
    return inventory


def compute_repository_inventory_digest(inventory: dict[str, object]) -> str:
    value = dict(inventory)
    value.pop("inventory_digest", None)
    return _inventory_digest(value)


def _scan_repository(
    repository: AnchoredRepository,
    *,
    max_entries: int,
    max_depth: int,
    max_read_bytes: int,
) -> dict[str, object]:
    files: list[str] = []
    directories: list[str] = []
    symlinks: list[str] = []
    manifests: dict[str, str] = {}
    document_contents: dict[str, str] = {}
    documents: list[str] = []
    entrypoints: list[str] = []
    extension_counts: dict[str, int] = {}
    entry_count = 0
    truncated = False

    def visit(prefix: str, depth: int) -> None:
        nonlocal entry_count, truncated
        if entry_count >= max_entries or depth > max_depth:
            truncated = True
            return
        remaining = max_entries - entry_count
        listed = repository.list_directory(prefix, max_entries=remaining + 1)
        if len(listed) > remaining:
            truncated = True
            listed = listed[:remaining]
        for entry in listed:
            if entry_count >= max_entries:
                truncated = True
                return
            entry_count += 1
            name = PurePosixPath(entry.path).name
            if entry.kind == "symlink":
                symlinks.append(entry.path)
                continue
            if entry.kind == "directory":
                directories.append(entry.path)
                if name not in SKIPPED_DIRECTORY_NAMES:
                    visit(entry.path, depth + 1)
                continue
            if entry.kind != "file":
                continue
            files.append(entry.path)
            suffix = Path(name).suffix.lower()
            if suffix:
                extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
            if name in ENTRY_NAMES:
                entrypoints.append(entry.path)
            parent = PurePosixPath(entry.path).parent.as_posix()
            if name in DOCUMENT_NAMES or (
                parent == "docs" and suffix in {".md", ".rst", ".txt"}
            ):
                documents.append(entry.path)
                content = _read_inventory_text(
                    repository,
                    entry,
                    max_read_bytes=max_read_bytes,
                )
                if content is not None:
                    document_contents[entry.path] = content
            if name in MANIFEST_TECH or name in {"Makefile", "justfile"}:
                content = _read_inventory_text(
                    repository,
                    entry,
                    max_read_bytes=max_read_bytes,
                )
                if content is not None:
                    manifests[entry.path] = content

    visit("", 0)
    stack = sorted(
        {
            MANIFEST_TECH[PurePosixPath(path).name]
            for path in manifests
            if PurePosixPath(path).name in MANIFEST_TECH
        }
    )
    commands = _discover_commands(manifests)
    constraints, constraint_candidates = _extract_constraints(document_contents)
    entrypoint_candidates = [
        {
            "path": path,
            "basis": "conventional_entrypoint_filename",
            "classification": "heuristic",
        }
        for path in entrypoints[:80]
    ]
    risks: list[str] = []
    if symlinks:
        risks.append(
            f"Skipped {len(symlinks)} symbolic link(s) during the read-only scan."
        )
    if truncated:
        risks.append(
            "Repository scan reached the bounded entry limit; deeper indexing is recommended."
        )
    if not commands:
        risks.append(
            "No run, test, lint, build, format, or check commands were discovered safely."
        )
    return {
        "technology_stack": stack or _stack_from_extensions(extension_counts),
        "directory_structure": directories[:120],
        "entry_points": entrypoints[:80],
        "entrypoint_candidates": entrypoint_candidates,
        "commands": commands,
        "key_documents": documents[:80],
        "constraints": constraints,
        "constraint_candidates": constraint_candidates,
        "risks": risks,
        "next_steps": [
            "Confirm the discovered commands and project constraints.",
            "Run Worker preflight after confirmation.",
        ],
        "scan": {
            "files_seen": len(files),
            "directories_seen": len(directories),
            "entries_seen": entry_count,
            "symlinks_skipped": symlinks[:80],
            "max_depth": max_depth,
            "max_entries": max_entries,
            "read_only": True,
        },
    }


def _read_inventory_text(
    repository: AnchoredRepository,
    entry: RepositoryEntry,
    *,
    max_read_bytes: int,
) -> str | None:
    if entry.size > max_read_bytes:
        return None
    try:
        result = repository.read_text(entry.path, max_bytes=max_read_bytes)
    except RepositoryAccessError as exc:
        if exc.code in {"repository_binary_file", "repository_non_text_file"}:
            return None
        raise
    return None if result.truncated else result.text


def _extract_constraints(
    document_contents: dict[str, str],
) -> tuple[list[str], list[dict[str, str]]]:
    constraint_names = {"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "SECURITY.md"}
    constraints: list[str] = []
    candidates: list[dict[str, str]] = []
    for path, content in sorted(document_contents.items()):
        if PurePosixPath(path).name not in constraint_names:
            continue
        excerpts: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip().lstrip("#*- ").strip()
            if not line or line.startswith(("![", "[![")):
                continue
            excerpts.append(line[:240])
            if len(excerpts) >= 12:
                break
        if not excerpts:
            text = "review this project constraint document"
            constraints.append(f"{path}: {text}")
            candidates.append(_constraint_candidate(path, text))
        else:
            for text in excerpts:
                constraints.append(f"{path}: {text}")
                candidates.append(_constraint_candidate(path, text))
    return constraints[:80], candidates[:80]


def _constraint_candidate(path: str, text: str) -> dict[str, str]:
    return {
        "path": path,
        "text": text,
        "basis": "instruction_document",
        "classification": "heuristic",
    }


def _discover_commands(manifests: dict[str, str]) -> list[dict[str, object]]:
    commands: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        path: str,
        kind: str,
        command: str,
        *,
        declared_command: str | None = None,
    ) -> None:
        identity = (path, command)
        if identity in seen:
            return
        seen.add(identity)
        candidate: dict[str, object] = {
            "id": hashlib.sha256(
                f"readiness-command:v1\0{path}\0{command}".encode()
            ).hexdigest()[:16],
            "kind": kind,
            "command": command,
            "source": path,
            "classification": "heuristic",
            "requires_confirmation": True,
        }
        if declared_command is not None:
            candidate["declared_command"] = declared_command
        commands.append(candidate)

    def safe_name(value: str) -> bool:
        return bool(value) and value.replace("-", "").replace("_", "").replace(
            ".", ""
        ).isalnum()

    for path, content in sorted(manifests.items()):
        name = PurePosixPath(path).name
        if name == "package.json":
            try:
                scripts = dict(json.loads(content).get("scripts", {}) or {})
            except (json.JSONDecodeError, AttributeError, TypeError):
                scripts = {}
            for script_name, value in sorted(scripts.items()):
                kind = _command_kind(str(script_name))
                if kind is not None:
                    add(
                        path,
                        kind,
                        f"npm run {script_name}",
                        declared_command=str(value),
                    )
        elif name == "pyproject.toml":
            try:
                project = tomllib.loads(content)
            except (tomllib.TOMLDecodeError, TypeError):
                project = {}
            project_metadata = project.get("project", {})
            scripts = (
                project_metadata.get("scripts", {})
                if isinstance(project_metadata, dict)
                else {}
            )
            if isinstance(scripts, dict):
                for script_name, entry_point in sorted(scripts.items()):
                    if (
                        isinstance(script_name, str)
                        and safe_name(script_name)
                        and isinstance(entry_point, str)
                    ):
                        add(path, "run", script_name, declared_command=entry_point)
            tools = project.get("tool", {})
            if isinstance(tools, dict):
                if isinstance(tools.get("pytest"), dict):
                    add(path, "test", "python -m pytest")
                if isinstance(tools.get("ruff"), dict):
                    add(path, "lint", "python -m ruff check .")
                    if isinstance(tools["ruff"].get("format"), dict):
                        add(path, "format", "python -m ruff format --check .")
                if isinstance(tools.get("mypy"), dict):
                    add(path, "check", "python -m mypy .")
            if isinstance(project.get("build-system"), dict):
                add(path, "build", "python -m build")
        elif name == "Cargo.toml":
            try:
                cargo = tomllib.loads(content)
            except (tomllib.TOMLDecodeError, TypeError):
                cargo = {}
            if isinstance(cargo.get("package"), dict) or isinstance(
                cargo.get("workspace"), dict
            ):
                add(path, "check", "cargo check")
                add(path, "test", "cargo test")
                add(path, "build", "cargo build")
                add(path, "format", "cargo fmt --check")
                add(path, "lint", "cargo clippy --all-targets --all-features")
                binaries = cargo.get("bin", [])
                if isinstance(binaries, list):
                    for binary in binaries:
                        if not isinstance(binary, dict):
                            continue
                        binary_name = binary.get("name")
                        if isinstance(binary_name, str) and safe_name(binary_name):
                            add(path, "run", f"cargo run --bin {binary_name}")
        elif name == "go.mod":
            module_lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            if (
                module_lines
                and module_lines[0].startswith("module ")
                and module_lines[0].removeprefix("module ").strip()
            ):
                add(path, "test", "go test ./...")
                add(path, "check", "go vet ./...")
                add(path, "build", "go build ./...")
                add(path, "format", "gofmt -l .")
        elif name == "Makefile":
            for line in content.splitlines():
                if not line or line.startswith(("\t", " ", "#", ".")) or ":" not in line:
                    continue
                target = line.split(":", 1)[0].strip()
                kind = _command_kind(target)
                if kind is not None and target.replace("-", "").replace("_", "").isalnum():
                    add(path, kind, f"make {target}")
        elif name == "justfile":
            for line in content.splitlines():
                if not line or line.startswith((" ", "\t", "#")) or ":" not in line:
                    continue
                target = line.split(":", 1)[0].strip()
                kind = _command_kind(target)
                if kind:
                    add(path, kind, f"just {target}")
    return commands[:120]


def _command_kind(name: str) -> str | None:
    lowered = name.lower()
    for kind in ("test", "lint", "format", "build", "check"):
        if kind in lowered:
            return kind
    if any(token in lowered for token in ("start", "serve", "dev", "run")):
        return "run"
    return None


def _stack_from_extensions(counts: dict[str, int]) -> list[str]:
    labels = {
        ".py": "Python",
        ".js": "JavaScript",
        ".jsx": "React / JavaScript",
        ".ts": "TypeScript",
        ".tsx": "React / TypeScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
    }
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [labels[suffix] for suffix, _count in ranked if suffix in labels][:8]


def _inventory_digest(inventory: dict[str, object]) -> str:
    encoded = json.dumps(
        inventory,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_limits(max_entries: int, max_depth: int, max_read_bytes: int) -> None:
    if max_entries <= 0 or max_depth < 0 or max_read_bytes <= 0:
        raise ValueError("Repository inventory limits are invalid.")
