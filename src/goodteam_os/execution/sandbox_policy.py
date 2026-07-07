from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from goodteam_os.execution.path_claims import normalize_repo_path


OUTPUT_EXCERPT_LIMIT = 1200
COMMAND_TIMEOUT_SECONDS = 30
ALLOWED_COMMANDS = {"python", "python3", "pytest"}
SECRET_ENV_NAMES = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}
SECRET_ENV_PREFIXES = ("AWS_", "GOOGLE_")
REDACTION_MARKER = "[REDACTED]"


class SandboxGitAdapter(Protocol):
    def create_worktree(
        self,
        *,
        repo_root: Path,
        worktree_path: Path,
        branch_name: str,
        base_ref: str,
    ) -> dict[str, object]:
        raise NotImplementedError

    def remove_worktree(self, *, repo_root: Path, worktree_path: Path) -> dict[str, object]:
        raise NotImplementedError


class SubprocessSandboxGitAdapter:
    def create_worktree(
        self,
        *,
        repo_root: Path,
        worktree_path: Path,
        branch_name: str,
        base_ref: str,
    ) -> dict[str, object]:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                base_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return {"status": "created", "stdout": completed.stdout, "stderr": completed.stderr}

    def remove_worktree(self, *, repo_root: Path, worktree_path: Path) -> dict[str, object]:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", str(worktree_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return {"status": "removed", "stdout": completed.stdout, "stderr": completed.stderr}


def command_env(extra: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    raw_env = {**os.environ, **extra}
    result, redacted_keys, secret_values = {}, [], []
    for key, value in raw_env.items():
        text = str(value)
        if is_secret_key(key):
            redacted_keys.append(key)
            if text:
                secret_values.append(text)
            result[key] = REDACTION_MARKER
        else:
            result[key] = text
    return result, sorted(redacted_keys), secret_values


def is_secret_key(key: str) -> bool:
    return key in SECRET_ENV_NAMES or any(key.startswith(prefix) for prefix in SECRET_ENV_PREFIXES)


def excerpt(value: str, secret_values: list[str]) -> str:
    text = scrub_text(value or "", secret_values)
    if len(text) <= OUTPUT_EXCERPT_LIMIT:
        return text
    return text[: OUTPUT_EXCERPT_LIMIT - 15] + "...<truncated>"


def scrub_text(value: str, secret_values: list[str]) -> str:
    text = value
    for secret in secret_values:
        text = text.replace(secret, REDACTION_MARKER)
    return text


def scrub_argv(argv: Sequence[str] | str, secret_values: list[str]) -> list[str] | str:
    if isinstance(argv, str):
        return scrub_text(argv, secret_values)
    return [scrub_text(str(item), secret_values) for item in argv]


def mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"workspace", "worktree"}:
        raise ValueError("sandbox mode must be one of: workspace, worktree")
    return normalized


def normalize_paths(paths: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = normalize_repo_path(raw_path)
        if path and path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


def paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def command_name(value: str) -> str:
    return Path(value).name


def is_shell_command(argv: list[str]) -> bool:
    return command_name(argv[0]) in {"sh", "bash", "zsh"} and len(argv) > 1 and argv[1] == "-c"


def command_cwd(metadata: dict[str, object], cwd: Path | str | None) -> Path:
    return Path(cwd) if cwd is not None else Path(str(metadata["workspace_path"]))


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
