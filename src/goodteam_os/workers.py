import fcntl
import json
import os
import pty
import re
import select
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2, which
from typing import Any
from uuid import uuid4

from goodteam_os.skills.worker_creation_lock import serialized_worker_creation
from goodteam_os.memory import RoleMemoryStore, default_embedder
from goodteam_os.roles import role_definition, stable_role_prompt
from goodteam_os.pi_rpc import PiRpcError
from goodteam_os.worker_capabilities import BACKEND_REASONING_DEPTHS, REASONING_DEPTHS
from goodteam_os.session_runtimes import (
    SESSION_BACKENDS,
    PiSessionRuntime,
    capabilities_dict,
    pi_backend_doctor,
    pi_runtime_config,
)


DEFAULT_CODEX_PATH = os.getenv("GOODTEAM_CODEX_PATH", "/opt/homebrew/bin/codex")
DEFAULT_CODEX_MODEL = os.getenv("GOODTEAM_CODEX_MODEL", "gpt-5.6-sol")
CODEX_AUTH_ENV_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
DEFAULT_CLAUDE_PATH = os.getenv("GOODTEAM_CLAUDE_PATH", "claude")
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_AUTH_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
DEFAULT_GEMINI_PATH = os.getenv("GOODTEAM_GEMINI_PATH", "gemini")
DEFAULT_GEMINI_MODEL = "auto"
DEFAULT_AGY_PATH = os.getenv("GOODTEAM_AGY_PATH", "agy")
DEFAULT_AGY_MODEL = os.getenv("GOODTEAM_AGY_MODEL", "auto")
AGY_AUTH_ENV_NAMES = ()
DEFAULT_OPENCODE_PATH = os.getenv("GOODTEAM_OPENCODE_PATH", "opencode")
DEFAULT_OPENCODE_MODEL = os.getenv("GOODTEAM_OPENCODE_MODEL", "auto")
OPENCODE_AUTH_ENV_NAMES = ()
GEMINI_AUTH_ENV_NAMES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
)
CODEX_ISOLATED_HOME_ALLOWED_ENTRIES = (
    "auth.json",
    "config.toml",
    "version.json",
)
BUILTIN_WORKER_MODES = ("fake", "noop", "human")
WORKER_HEARTBEAT_INTERVAL_SECONDS = 10
WORKER_STALE_AFTER_SECONDS = 120
WORKER_MAX_ATTEMPTS = 3
WORKER_RETRY_BACKOFF_SECONDS = (2, 8, 30)
PI_OVERALL_TIMEOUT_SECONDS = 1800
WORKER_JOB_IDENTITY_FIELDS = (
    "profile_id",
    "approval",
    "gate",
    "phase",
    "artifact_key",
    "artifact_path",
    "artifact_version",
    "dispatch_group_id",
    "attempt",
    "idempotency_key",
    "original_failed_job_id",
    "retry_attempt",
    "retry_of_idempotency_key",
)
MEMORY_WRITEBACK_SECTION_HORIZONS = {
    "decisions": "long-term",
    "learned": "mid-term",
    "caveats": "mid-term",
    "open questions": "short-term",
}
MEMORY_WRITEBACK_MAX_ITEMS_PER_SECTION = 10

_MEMORY_WRITEBACK_SECTION_RE = re.compile(r"^\s{0,3}#{2,3}\s+(.+?)\s*$")
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MEMORY_WRITEBACK_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+?)\s*$")
_sleep = time.sleep


def classify_worker_failure(summary: str, exit_code: int | None, stderr: str = "") -> str:
    del exit_code  # phase-1 classifier is text-pattern based.
    text = f"{summary}\n{stderr}".lower()
    if any(
        token in text
        for token in (
            "invalid api key",
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "refresh token",
            "token_expired",
            "authentication",
        )
    ):
        return "auth"
    if any(token in text for token in ("rate limit", "429", "too many requests")):
        return "rate_limit"
    if any(
        token in text
        for token in (
            "5xx",
            "502",
            "503",
            "504",
            "econn",
            "network",
            "registry",
            "npmmirror",
            "timed out connecting",
        )
    ):
        return "network"
    if any(token in text for token in ("timed out", "timeout")):
        return "timeout"
    if "stream disconnected" in text:
        return "stream_disconnect"
    if any(token in text for token in ("better-sqlite3", "abi", "module not found", "cannot find module")):
        return "scaffold"
    return "unknown"


def is_transient(failure_class: str) -> bool:
    return failure_class in {"auth", "rate_limit", "network", "timeout", "stream_disconnect"}


def _worker_safe_sandbox_context(context: dict[str, Any]) -> dict[str, Any]:
    claimed_paths = context.get("claimed_paths", [])
    if not isinstance(claimed_paths, list):
        claimed_paths = []
    return {
        "available": bool(context.get("available")),
        "schema_version": str(context.get("schema_version", "")),
        "execution_id": str(context.get("execution_id", "")),
        "sandbox_id": str(context.get("sandbox_id", "")),
        "workspace_path": str(context.get("workspace_path", "")),
        "worktree_path": str(context.get("worktree_path", "")),
        "mode": str(context.get("mode", "")),
        "branch_name": str(context.get("branch_name", "")),
        "claimed_paths": [str(path) for path in claimed_paths],
    }


def _worker_sandbox_context_prompt(job: dict[str, Any]) -> str:
    context = job.get("sandbox_context")
    if not isinstance(context, dict):
        return "No sandbox context provided."
    return json.dumps(context, indent=2, sort_keys=True)


def parse_result_sections(text: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _MEMORY_WRITEBACK_SECTION_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        section_name = _normalize_memory_section_name(match.group(1))
        horizon = MEMORY_WRITEBACK_SECTION_HORIZONS.get(section_name)
        index += 1
        if horizon is None:
            continue
        section_lines: list[str] = []
        while index < len(lines) and _MARKDOWN_HEADING_RE.match(lines[index]) is None:
            section_lines.append(lines[index])
            index += 1
        parsed.extend(
            (horizon, item)
            for item in _parse_memory_section_items(section_lines)[:MEMORY_WRITEBACK_MAX_ITEMS_PER_SECTION]
        )
    return parsed


def _normalize_memory_section_name(value: str) -> str:
    name = value.strip()
    if name.endswith(":"):
        name = name[:-1]
    return " ".join(name.lower().split())


def _parse_memory_section_items(lines: list[str]) -> list[str]:
    bullets = []
    for line in lines:
        match = _MEMORY_WRITEBACK_BULLET_RE.match(line)
        if match is None:
            continue
        item = match.group(1).strip()
        if item:
            bullets.append(item)
    if bullets:
        return bullets

    paragraph = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped in {"-", "*", "•"}:
            continue
        paragraph.append(stripped)
    return [" ".join(paragraph)] if paragraph else []


def _role_prompt_for_job(role: str) -> tuple[dict[str, str], str]:
    try:
        role_config = role_definition(role)
        return role_config, stable_role_prompt(role)
    except ValueError:
        role_config = {
            "name": role,
            "key": role,
            "artifact": "result",
            "description": "Ad-hoc dispatch role.",
            "next_action": "Complete the assigned dispatch task.",
        }
        prompt = "\n".join(
            [
                f"# Role Prompt: {role}",
                "",
                f"You are the {role} dispatch role. Execute the assigned task inside the configured project root.",
                "",
                "## Role Boundary",
                "",
                f"- Key: `{role}`",
                "- Default artifact: `result`",
                "- Responsibility: Ad-hoc dispatch work.",
                "- Next action rule: Complete the assigned dispatch task.",
                "",
                "Concrete task instructions belong in task.md. Project evidence belongs in context.md.",
            ]
        )
        return role_config, f"{prompt}\n"


@dataclass(frozen=True)
class CliBackendSpec:
    """One agentic coding CLI worker backend. Register a spec in CLI_BACKENDS to
    add a new CLI (e.g. an antigravity/gemini CLI) without touching dispatch."""

    name: str
    which_name: str
    # default_path is a callable so it re-reads the (env-overridable, test-monkeypatchable)
    # module global on each run instead of freezing the value at import time.
    default_path: Callable[[], str]
    model_default: str
    models: tuple[str, ...]
    auth_env: tuple[str, ...]
    capture: str  # "output_file" | "stdout"
    label: str
    build_command: Callable[[str, str, Path, Path], list[str]]
    summarize: Callable[[int, str], str]
    not_available_message: str
    prompt_via: str = "stdin"
    isolated_home: bool = False
    # Some CLIs (e.g. agy's bubbletea TUI) require a controlling terminal and hang
    # on a plain pipe. needs_pty routes them through a real PTY in _run_cli_worker.
    needs_pty: bool = False


@dataclass(frozen=True)
class WorkerRuntimeContext:
    runtime_cwd: Path
    workspace_context_available: bool
    workspace_cwd_fallback: bool
    workspace_cwd_fallback_reason: str
    runtime_workspace_mode: str
    runtime_workspace_status: str


def _codex_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    return [
        binary,
        "exec",
        "--model",
        model,
        "--skip-git-repo-check",
        "--cd",
        str(project_root),
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-last-message",
        str(result_path),
        "-",
    ]


def _query_codex_models(command_path: str) -> dict[str, dict[str, Any]] | None:
    try:
        completed = subprocess.run(
            [command_path, "debug", "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None

    catalog: dict[str, dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        levels = model.get("supported_reasoning_levels")
        capabilities: dict[str, Any] = {}
        if isinstance(levels, list):
            capabilities["reasoning_depths"] = list(dict.fromkeys(
                level["effort"] for level in levels
                if isinstance(level, dict) and isinstance(level.get("effort"), str)
                and level["effort"] in REASONING_DEPTHS and level["effort"] != "default"
            ))
        default = model.get("default_reasoning_level")
        if isinstance(default, str) and default in capabilities.get("reasoning_depths", []):
            capabilities["reasoning_default"] = default
        catalog[slug.strip()] = capabilities
    return catalog or None


def _query_agy_models(command_path: str) -> tuple[str, ...] | None:
    try:
        completed = subprocess.run(
            [command_path, "models"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None

    output = completed.stdout.strip()
    if not output:
        return None

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        models = [line.strip() for line in output.splitlines() if line.strip()]
        return tuple(dict.fromkeys(models)) or None

    candidates = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list):
        return None

    models: list[str] = []
    for item in candidates:
        if isinstance(item, str) and item.strip():
            models.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in ("slug", "name", "label", "id"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                models.append(value.strip())
                break
    return tuple(dict.fromkeys(models)) or None


def _query_opencode_models(command_path: str) -> tuple[str, ...] | None:
    try:
        completed = subprocess.run(
            [command_path, "models", "--refresh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None

    models = [line.strip() for line in completed.stdout.splitlines() if "/" in line and line.strip()]
    return tuple(dict.fromkeys(models)) or None


def _claude_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    return [binary, "-p", "--model", model, "--dangerously-skip-permissions"]


def _gemini_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    return [binary, "-p", "--approval-mode", "yolo", "--model", model]


def _agy_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    # agy (Antigravity) CLI: `-p`/`--print` runs one prompt non-interactively,
    # `--dangerously-skip-permissions` auto-approves tool use (its YOLO), and
    # `--print-timeout` bounds the run. prompt_via="arg" inserts the prompt
    # right after `-p`.
    command = [binary, "-p", "--dangerously-skip-permissions", "--print-timeout", "30m"]
    model = model.strip()
    if model and model.lower() != "auto":
        command.extend(["--model", model])
    return command


def _opencode_build_command(binary: str, model: str, project_root: Path, result_path: Path) -> list[str]:
    command = [
        binary,
        "run",
        "--dir",
        str(project_root),
        "--dangerously-skip-permissions",
    ]
    model = model.strip()
    if model and model.lower() != "auto":
        command.extend(["-m", model])
    return command


def _command_with_prompt(command: list[str], prompt: str, *, prompt_via: str) -> list[str]:
    if prompt_via == "stdin":
        return list(command)
    if prompt_via == "positional_arg":
        return [*command, prompt]
    if prompt_via != "arg":
        raise ValueError(f"Unsupported prompt_via value: {prompt_via}")
    updated = list(command)
    try:
        prompt_index = updated.index("-p")
    except ValueError:
        try:
            prompt_index = updated.index("--prompt")
        except ValueError as exc:
            raise ValueError("prompt_via='arg' requires -p or --prompt in command.") from exc
    updated.insert(prompt_index + 1, prompt)
    return updated


def _command_with_reasoning_depth(
    command: list[str],
    *,
    adapter: str,
    reasoning_depth: str,
) -> list[str]:
    depth = str(reasoning_depth or "default").strip().lower()
    if depth == "default" or depth not in REASONING_DEPTHS:
        return list(command)
    normalized_adapter = {
        "anthropic": "claude",
        "antigravity": "agy",
    }.get(adapter, adapter)
    arguments = {
        "codex": ["--config", f'model_reasoning_effort="{depth}"'],
        "claude": ["--effort", depth],
        "agy": ["--effort", depth],
        "opencode": ["--variant", depth],
    }.get(normalized_adapter)
    if arguments is None:
        return list(command)
    updated = list(command)
    if normalized_adapter == "codex" and "-" in updated:
        prompt_index = updated.index("-")
        return [*updated[:prompt_index], *arguments, *updated[prompt_index:]]
    return [*updated, *arguments]


def _command_with_execution_policy(
    command: list[str],
    *,
    adapter: str,
    execution_policy: str,
) -> list[str]:
    if execution_policy != "read_only":
        return list(command)
    normalized_adapter = {
        "anthropic": "claude",
        "antigravity": "agy",
    }.get(adapter, adapter)
    updated = [
        value for value in command
        if value not in {"--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions"}
    ]
    if normalized_adapter == "codex":
        arguments = ["--sandbox", "read-only", "--ignore-user-config", "--ignore-rules", "--ephemeral"]
        if "-" in updated:
            prompt_index = updated.index("-")
            return [*updated[:prompt_index], *arguments, *updated[prompt_index:]]
        return [*updated, *arguments]
    if normalized_adapter == "claude":
        return [*updated, "--safe-mode", "--tools", ""]
    if normalized_adapter == "agy":
        return [*updated, "--mode", "plan", "--sandbox", "--disable-slash-commands"]
    if normalized_adapter == "gemini":
        try:
            approval_index = updated.index("--approval-mode")
            updated[approval_index + 1] = "plan"
        except (ValueError, IndexError):
            updated.extend(["--approval-mode", "plan"])
        return updated
    raise ValueError(f"Adapter does not support enforced read-only execution: {adapter}")


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _strip_terminal_control(text: str) -> str:
    """Remove ANSI escape sequences and stray carriage returns from PTY output."""
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    return cleaned.replace("\r", "")


def _run_command_in_pty(
    command: list[str],
    *,
    cwd: Path | str | None,
    env: dict[str, str] | None,
    process_input: str | None,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    """Run ``command`` attached to a real PTY so TUIs that require a controlling
    terminal (e.g. agy's bubbletea loop) don't hang on a plain pipe.

    Returns ``(returncode, stdout, stderr)``. With a single PTY the child's stdout
    and stderr are merged onto one stream, so the returned stderr is always "".
    The accumulated output has ANSI escape codes stripped. On timeout the child is
    killed and ``subprocess.TimeoutExpired`` is raised with the partial output, so
    callers can reuse their existing timeout handling.
    """
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=(subprocess.PIPE if process_input is not None else slave_fd),
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        close_fds=True,
    )
    os.close(slave_fd)
    if process_input is not None and proc.stdin is not None:
        try:
            proc.stdin.write(process_input.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    chunks: list[bytes] = []
    deadline = time.time() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                output = _strip_terminal_control(b"".join(chunks).decode("utf-8", errors="replace"))
                raise subprocess.TimeoutExpired(command, timeout_seconds, output=output)
            ready, _, _ = select.select([master_fd], [], [], min(remaining, 1.0))
            if not ready:
                continue
            try:
                data = os.read(master_fd, 65536)
            except OSError:
                # EIO is raised on the master fd when the child has exited.
                break
            if not data:
                break
            chunks.append(data)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
    try:
        returncode = proc.wait(timeout=max(deadline - time.time(), 1.0))
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return returncode, _strip_terminal_control(text), ""


def _codex_summary(exit_code: int, stderr: str) -> str:
    if exit_code == 0:
        return "Codex CLI exited with code 0."
    relevant_error = _last_relevant_error(stderr)
    normalized_relevant_error = relevant_error.lower()
    normalized = normalized_relevant_error or stderr.lower()
    if "invalid_api_key" in normalized or "invalid api key" in normalized:
        return f"Codex CLI exited with code {exit_code}: invalid API key."
    if "401 unauthorized" in normalized:
        return f"Codex CLI exited with code {exit_code}: 401 Unauthorized."
    status_match = re.search(r"unexpected status\s+(\d{3})\s+([^:,.\n]+(?:\s+[^:,.\n]+)?)", relevant_error, re.IGNORECASE)
    if status_match is None:
        status_match = re.search(r"last status:?\s*(\d{3})\s+([^\n,:]+)", relevant_error, re.IGNORECASE)
    if status_match:
        return f"Codex CLI exited with code {exit_code}: {status_match.group(1)} {status_match.group(2).strip()}."
    if "stream disconnected" in normalized:
        return f"Codex CLI exited with code {exit_code}: stream disconnected before response.completed."
    return f"Codex CLI exited with code {exit_code}."


def _claude_summary(exit_code: int, stderr: str) -> str:
    if exit_code == 0:
        return "Claude Code CLI exited with code 0."
    normalized = stderr.lower()
    if "invalid_api_key" in normalized or "invalid api key" in normalized or "authentication" in normalized:
        return f"Claude Code CLI exited with code {exit_code}: authentication failed (check ANTHROPIC_API_KEY)."
    if "401 unauthorized" in normalized:
        return f"Claude Code CLI exited with code {exit_code}: 401 Unauthorized."
    if "rate limit" in normalized or " 429 " in f" {normalized} ":
        return f"Claude Code CLI exited with code {exit_code}: rate limited."
    return f"Claude Code CLI exited with code {exit_code}."


def _gemini_summary(exit_code: int, stderr: str) -> str:
    if exit_code == 0:
        return "Gemini CLI exited with code 0."
    normalized = stderr.lower()
    if "invalid_api_key" in normalized or "invalid api key" in normalized or "authentication" in normalized:
        return f"Gemini CLI exited with code {exit_code}: authentication failed."
    if "401 unauthorized" in normalized:
        return f"Gemini CLI exited with code {exit_code}: 401 Unauthorized."
    if "rate limit" in normalized or " 429 " in f" {normalized} ":
        return f"Gemini CLI exited with code {exit_code}: rate limited."
    return f"Gemini CLI exited with code {exit_code}."


def _cli_preflight(spec: CliBackendSpec, binary: str, model: str) -> dict[str, Any]:
    executable = Path(binary)
    preflight: dict[str, Any] = {
        f"{spec.which_name}_command": binary,
        f"{spec.which_name}_available": bool(which(spec.which_name) or executable.is_file()),
        "model": model,
        "auth_env": {name: bool(os.getenv(name)) for name in spec.auth_env},
        "http_proxy": bool(os.getenv("http_proxy") or os.getenv("HTTP_PROXY")),
        "https_proxy": bool(os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")),
    }
    if spec.which_name == "codex":
        preflight["codex_home"] = os.getenv("CODEX_HOME", "")
    return preflight


CODEX_BACKEND = CliBackendSpec(
    name="codex",
    which_name="codex",
    default_path=lambda: DEFAULT_CODEX_PATH,
    model_default=DEFAULT_CODEX_MODEL,
    models=(DEFAULT_CODEX_MODEL, "gpt-5.4", "gpt-5.4-mini", "gpt-5.5"),
    auth_env=CODEX_AUTH_ENV_NAMES,
    capture="output_file",
    label="Codex CLI",
    build_command=_codex_build_command,
    summarize=_codex_summary,
    not_available_message="Codex CLI is not available. Install codex or add it to PATH before running codex workers.",
    isolated_home=True,
)

CLAUDE_BACKEND = CliBackendSpec(
    name="anthropic",
    which_name="claude",
    default_path=lambda: DEFAULT_CLAUDE_PATH,
    model_default=DEFAULT_CLAUDE_MODEL,
    models=(
        DEFAULT_CLAUDE_MODEL,
        "claude-opus-4-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "opus",
        "sonnet",
        "haiku",
    ),
    auth_env=CLAUDE_AUTH_ENV_NAMES,
    capture="stdout",
    label="Claude Code CLI",
    build_command=_claude_build_command,
    summarize=_claude_summary,
    not_available_message=(
        "Claude Code CLI is not available. Install `claude` or add it to PATH before running anthropic workers."
    ),
)

GEMINI_BACKEND = CliBackendSpec(
    name="gemini",
    which_name="gemini",
    default_path=lambda: DEFAULT_GEMINI_PATH,
    model_default=DEFAULT_GEMINI_MODEL,
    models=("auto", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"),
    auth_env=GEMINI_AUTH_ENV_NAMES,
    capture="stdout",
    label="Gemini CLI",
    build_command=_gemini_build_command,
    summarize=_gemini_summary,
    not_available_message="Gemini CLI is not available. Install gemini or add it to PATH before running gemini workers.",
    prompt_via="arg",
)

AGY_BACKEND = CliBackendSpec(
    name="agy",
    which_name="agy",
    default_path=lambda: DEFAULT_AGY_PATH,
    model_default=DEFAULT_AGY_MODEL,
    models=("auto",),
    auth_env=AGY_AUTH_ENV_NAMES,
    capture="stdout",
    label="Antigravity (agy) CLI",
    build_command=_agy_build_command,
    summarize=_gemini_summary,
    not_available_message="Antigravity CLI (agy) is not available. Install agy or add it to PATH before running agy workers.",
    prompt_via="arg",
    needs_pty=True,
)

OPENCODE_BACKEND = CliBackendSpec(
    name="opencode",
    which_name="opencode",
    default_path=lambda: DEFAULT_OPENCODE_PATH,
    model_default=DEFAULT_OPENCODE_MODEL,
    models=(DEFAULT_OPENCODE_MODEL,),
    auth_env=OPENCODE_AUTH_ENV_NAMES,
    capture="stdout",
    label="OpenCode CLI",
    build_command=_opencode_build_command,
    summarize=_gemini_summary,
    not_available_message="OpenCode CLI is not available. Install opencode or add it to PATH before running opencode workers.",
    prompt_via="positional_arg",
)

# Registry of agentic CLI worker backends. Add a CliBackendSpec here to support a
# new CLI. Aliases point at the same backend spec.
CLI_BACKENDS: dict[str, CliBackendSpec] = {
    "codex": CODEX_BACKEND,
    "anthropic": CLAUDE_BACKEND,
    "claude": CLAUDE_BACKEND,
    "gemini": GEMINI_BACKEND,
    "agy": AGY_BACKEND,
    "antigravity": AGY_BACKEND,
    "opencode": OPENCODE_BACKEND,
}

PROVIDER_BY_ADAPTER = {
    "codex": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "google",
    "agy": "antigravity",
    "antigravity": "antigravity",
    "opencode": "opencode",
}

INVOCATION_ATTEMPT_REASON_BY_ADAPTER = {
    "codex": "Codex CLI invocation attempted",
    "anthropic": "Claude Code CLI invocation attempted",
    "claude": "Claude Code CLI invocation attempted",
    "gemini": "Gemini CLI invocation attempted",
    "agy": "Antigravity CLI invocation attempted",
    "antigravity": "Antigravity CLI invocation attempted",
    "opencode": "OpenCode CLI invocation attempted",
}

BUILTIN_INVOCATION_EVIDENCE = {
    "fake": {
        "kind": "builtin",
        "provider": "none",
        "external_invocation": False,
        "reason": "fake adapter completed without external invocation",
    },
    "noop": {
        "kind": "builtin",
        "provider": "none",
        "external_invocation": False,
        "reason": "noop adapter completed without external invocation",
    },
    "human": {
        "kind": "manual",
        "provider": "none",
        "external_invocation": False,
        "reason": "human/manual adapter waits for operator-provided result",
    },
}


def _invocation_evidence(job: dict[str, Any], *, summary: str, extra: dict[str, Any]) -> dict[str, Any]:
    adapter = str(job.get("adapter", ""))
    if adapter in BUILTIN_INVOCATION_EVIDENCE:
        return {
            **BUILTIN_INVOCATION_EVIDENCE[adapter],
            "adapter": adapter,
            "command": "",
            "command_available": None,
            "model": str(job.get("model", "")),
        }

    command = _invocation_command(extra.get("command"))
    preflight = extra.get("preflight") if isinstance(extra.get("preflight"), dict) else {}
    spec = CLI_BACKENDS.get(adapter)
    available_key = f"{spec.which_name}_available" if spec else ""
    command_available = preflight.get(available_key) if available_key else None
    if not isinstance(command_available, bool):
        command_available = None
    provider = PROVIDER_BY_ADAPTER.get(adapter, adapter or "unknown")
    model = str(extra.get("model") or job.get("model") or "")
    unavailable = command_available is False
    return {
        "kind": "unavailable" if unavailable else "provider",
        "adapter": adapter,
        "provider": provider,
        "external_invocation": not unavailable,
        "command": command,
        "command_available": command_available,
        "model": model,
        "reason": summary
        if unavailable
        else INVOCATION_ATTEMPT_REASON_BY_ADAPTER.get(adapter, "provider invocation attempted"),
    }


def _invocation_command(command: object) -> str:
    if isinstance(command, list) and command:
        return str(command[0])
    if isinstance(command, str):
        return command
    return ""


_NO_TASK_SKILL_CONTEXT = object()


class WorkerJobStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.root = project_root / ".goodteam" / "workers" / "jobs"

    @serialized_worker_creation
    def create_job(
        self,
        *,
        run_id: str,
        role: str,
        adapter: str,
        model: str = "",
        task: str,
        context: str,
        task_source: str = "",
        constraints_source: str = "",
        research_report_source: str = "",
        constraints: list[str] | None = None,
        expected_artifacts: list[str] | None = None,
        profile_id: str = "",
        approval: str = "",
        gate: str = "",
        phase: str = "",
        artifact_key: str = "",
        artifact_path: str = "",
        artifact_version: str = "",
        dispatch_group_id: str = "",
        attempt: str | int = "",
        idempotency_key: str = "",
        original_failed_job_id: str = "",
        retry_attempt: str | int = "",
        retry_of_idempotency_key: str = "",
        session_policy: str = "new",
        worker_session_ref: str = "",
        execution_id: str = "",
        project_key: str = "",
        pi_path: str = "",
        pi_provider: str = "",
        pi_model: str = "",
        pi_thinking_level: str = "",
        pi_session_dir: str = "",
        reasoning_depth: str = "",
        additional_prompt: str = "",
        desired_instances: int = 1,
        execution_policy: str = "standard",
        worker_id: str = "",
        worker_name: str = "",
        task_skill_context: object = _NO_TASK_SKILL_CONTEXT,
        skill_subject: str = "",
        skill_loader_version: str = "goodteam.worker-metadata.v1",
        required_skill_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if task_skill_context is not _NO_TASK_SKILL_CONTEXT:
            skill_request = locals().copy()
            from goodteam_os.skills.worker_requests import create_skill_worker_job

            return create_skill_worker_job(self, skill_request)[0]
        if skill_subject or required_skill_ids or skill_loader_version != "goodteam.worker-metadata.v1":
            raise ValueError("Skill Worker input requires an explicit task Skill context; legacy fallback is forbidden.")
        if idempotency_key:
            from goodteam_os.skills.worker_requests import reject_legacy_key_collision

            reject_legacy_key_collision(self, idempotency_key)
        from goodteam_os.skills.legacy_task_guard import reject_legacy_task_downgrade

        reject_legacy_task_downgrade(self, {"run_id": run_id, "project_key": project_key, "execution_id": execution_id})
        job_id = f"job-{uuid4()}"
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        now = self._now()
        job = {
            "id": job_id,
            "run_id": run_id,
            "role": role,
            "adapter": adapter,
            "model": _codex_model_name(model) if model else "",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "task_source": task_source,
            "constraints_source": constraints_source,
            "research_report_source": research_report_source,
            "expected_artifacts": expected_artifacts or [],
            "session_policy": session_policy,
            "worker_session_ref": worker_session_ref,
            "execution_id": execution_id,
            "project_key": project_key,
            "reasoning_depth": reasoning_depth,
            "additional_prompt": additional_prompt,
            "desired_instances": desired_instances,
            "execution_policy": execution_policy,
        }
        for key, value in {
            "pi_path": pi_path,
            "pi_provider": pi_provider,
            "pi_model": pi_model,
            "pi_thinking_level": pi_thinking_level,
            "pi_session_dir": pi_session_dir,
            "worker_id": worker_id,
            "worker_name": worker_name,
        }.items():
            if value:
                job[key] = value
        job.update(
            _clean_identity_fields(
                {
                    "profile_id": profile_id,
                    "approval": approval,
                    "gate": gate,
                    "phase": phase,
                    "artifact_key": artifact_key,
                    "artifact_path": artifact_path,
                    "artifact_version": artifact_version,
                    "dispatch_group_id": dispatch_group_id,
                    "attempt": attempt,
                    "idempotency_key": idempotency_key,
                    "original_failed_job_id": original_failed_job_id,
                    "retry_attempt": retry_attempt,
                    "retry_of_idempotency_key": retry_of_idempotency_key,
                }
            )
        )
        self._write_json(job_root / "job.json", job)
        role_config, role_prompt = _role_prompt_for_job(role)
        (job_root / "role_prompt.md").write_text(role_prompt, encoding="utf-8")
        role_memory_store = RoleMemoryStore(self.project_root, embedder=default_embedder())
        role_memory_paths = role_memory_store.ensure_role_memory(
            role_key=role,
            role_name=str(role_config.get("name", role)),
        )
        self._write_json(
            job_root / "role_memory_paths.json",
            {horizon: str(path) for horizon, path in role_memory_paths.items()},
        )
        self._write_json(
            job_root / "role_pickup_packet.json",
            role_memory_store.build_pickup_packet(role, query=task),
        )
        (job_root / "task.md").write_text(task, encoding="utf-8")
        research_report_path = Path(research_report_source) if research_report_source else None
        if research_report_path and research_report_path.is_file():
            (job_root / "research-report.md").write_text(research_report_path.read_text(encoding="utf-8"), encoding="utf-8")
        (job_root / "context.md").write_text(context, encoding="utf-8")
        (job_root / "constraints.md").write_text("\n".join(f"- {item}" for item in constraints or []), encoding="utf-8")
        (job_root / "expected_artifacts.json").write_text(json.dumps(expected_artifacts or [], indent=2), encoding="utf-8")
        (job_root / "result.md").write_text("", encoding="utf-8")
        return job

    @serialized_worker_creation
    def create_or_reuse_job(self, **job_spec: Any) -> tuple[dict[str, Any], bool]:
        if job_spec.get("task_skill_context") is not None:
            import inspect
            from goodteam_os.skills.worker_requests import create_skill_worker_job

            bound = inspect.signature(type(self).create_job).bind(self, **job_spec)
            bound.apply_defaults()
            return create_skill_worker_job(self, dict(bound.arguments))
        from goodteam_os.skills.worker_requests import has_skill_fields, is_skill_job_id

        if has_skill_fields(job_spec) or job_spec.get("required_skill_ids"):
            raise ValueError("Skill Worker input requires its explicit original task context.")
        from goodteam_os.skills.legacy_task_guard import reject_legacy_task_downgrade

        reject_legacy_task_downgrade(self, job_spec)
        idempotency_key = str(job_spec.get("idempotency_key", "") or "").strip()
        if idempotency_key:
            from goodteam_os.skills.worker_requests import reject_legacy_key_collision

            reject_legacy_key_collision(self, idempotency_key)
            existing = self.find_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                if is_skill_job_id(existing.get("id")) or has_skill_fields(existing):
                    raise ValueError("A Skill-backed job cannot be replayed as legacy input.")
                return existing, False
        return self.create_job(**job_spec), True

    def find_job_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        wanted = str(idempotency_key).strip()
        if not wanted:
            return None
        for job in self.list_jobs():
            if str(job.get("idempotency_key", "") or "").strip() == wanted:
                return job
        return None

    def list_jobs(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        jobs = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            job_path = path / "job.json"
            if not job_path.is_file():
                continue
            job = self._read_json(job_path)
            # Routing keeps the protected Skill identity even if job.json was altered.
            if path.name.startswith("job-skill-"):
                job["id"] = path.name
            status_path = path / "status.json"
            if status_path.is_file():
                status_payload = self._read_json(status_path)
                job = {
                    **job,
                    **{
                        key: value
                        for key, value in status_payload.items()
                        if key
                        in {
                            "status",
                            "summary",
                            "updated_at",
                            "heartbeat_at",
                            "pid",
                            "stdout_path",
                            "stderr_path",
                            "result_path",
                            "changed_files",
                            "exit_code",
                            "failure_class",
                            "disposition",
                            "invocation_evidence",
                            "profile_id",
                            "approval",
                            "gate",
                            "phase",
                            "artifact_key",
                            "artifact_path",
                            "artifact_version",
                            "dispatch_group_id",
                            "attempt",
                            "idempotency_key",
                            "original_failed_job_id",
                            "retry_attempt",
                            "retry_of_idempotency_key",
                            "skill_preflight_status",
                            "skill_preflight_attempt_id",
                            "skill_error_code",
                            "skill_runtime_status",
                            "sandbox_context",
                            "runtime_cwd",
                            "runtime_adapter",
                            "runtime_binary",
                            "runtime_version",
                            "runtime_version_warning",
                            "runtime_version_warning_reason",
                            "runtime_version_failure_reason",
                            "runtime_model",
                            "runtime_workspace_mode",
                            "runtime_workspace_status",
                            "runtime_sandbox_mode",
                            "runtime_approval_policy",
                            "runtime_unsafe_mode",
                            "runtime_policy_warning",
                            "runtime_policy_warning_reason",
                            "runtime_transcript_path",
                            "workspace_context_available",
                            "workspace_cwd_fallback",
                            "workspace_cwd_fallback_reason",
                            "pythonpath_workspace_src",
                            "runtime_kind",
                            "runtime_provider",
                            "runtime_thinking_level",
                            "runtime_capabilities",
                            "runtime_events_path",
                            "runtime_session_metadata_path",
                            "pi_session_id",
                            "pi_session_file",
                            "session_status",
                            "last_entry_cursor",
                            "isStreaming",
                            "isCompacting",
                            "pendingMessageCount",
                            "process_alive",
                        }
                    },
                }
                if (
                    status_payload.get("status") == "running"
                    and _has_worker_runtime_paths(status_payload)
                    and _is_stale_heartbeat(_heartbeat_reference(status_payload))
                ):
                    job = reconcile_stale_job(job, status_payload)
            jobs.append(job)
        return jobs

    def run_job(self, job_id: str, *, sandbox_context: dict[str, Any] | None = None) -> dict[str, Any]:
        from goodteam_os.skills.worker_requests import has_skill_fields, is_skill_job_id, run_skill_worker_job

        if is_skill_job_id(job_id):
            return run_skill_worker_job(self, job_id, sandbox_context=sandbox_context)
        job_root = self.root / job_id
        job = self._read_json(job_root / "job.json")
        if has_skill_fields(job):
            raise ValueError("Unregistered Skill Worker input cannot enter the legacy Runtime path.")
        from goodteam_os.skills.legacy_task_guard import reject_legacy_task_downgrade

        reject_legacy_task_downgrade(self, job)
        if sandbox_context is not None:
            job = self._attach_sandbox_context(job_root, job, sandbox_context)
        memory_citations = self._memory_citations_from_pickup_packet(job_root)
        adapter = str(job["adapter"])
        if str(job.get("execution_policy", "standard")) == "read_only" and (
            adapter in SESSION_BACKENDS or adapter == "opencode"
        ):
            status_payload = self._write_status(
                job_root,
                job,
                status="failed",
                summary=f"Adapter does not support enforced read-only execution: {adapter}",
            )
        elif adapter == "noop":
            status_payload = self._complete_noop(job_root, job)
        elif adapter == "fake":
            status_payload = self._complete_fake(job_root, job)
        elif adapter == "human":
            status_payload = self._wait_for_human(job_root, job)
        elif adapter in SESSION_BACKENDS:
            status_payload = self._run_session_backend(job_root, job)
        else:
            spec = CLI_BACKENDS.get(adapter)
            if spec is None:
                raise ValueError(f"Unsupported worker adapter: {adapter}")
            attempts = 0
            retried = False
            last_failure_class = "unknown"
            while True:
                attempts += 1
                status_payload = self._run_cli_backend(job_root, job, spec)
                if status_payload.get("status") == "completed":
                    status_payload["attempts"] = attempts
                    if retried:
                        status_payload["failure_class"] = last_failure_class
                        status_payload["disposition"] = "recovered"
                    break
                stderr = ""
                stderr_path = status_payload.get("stderr_path")
                if isinstance(stderr_path, str) and stderr_path:
                    try:
                        stderr = Path(stderr_path).read_text(encoding="utf-8")
                    except OSError:
                        stderr = ""
                last_failure_class = classify_worker_failure(
                    str(status_payload.get("summary", "")),
                    status_payload.get("exit_code"),
                    stderr=stderr,
                )
                status_payload["failure_class"] = last_failure_class
                status_payload["attempts"] = attempts
                if not is_transient(last_failure_class) or attempts >= WORKER_MAX_ATTEMPTS:
                    status_payload["disposition"] = "escalated"
                    break
                retried = True
                backoff = WORKER_RETRY_BACKOFF_SECONDS[min(attempts - 1, len(WORKER_RETRY_BACKOFF_SECONDS) - 1)]
                _sleep(backoff)
        status_payload["memory_citations"] = memory_citations
        if status_payload.get("status") == "completed":
            try:
                status_payload["memory_writeback"] = self._write_back_memory(job_root, job)
            except Exception as exc:  # pragma: no cover - exercised via monkeypatch safety test
                status_payload["memory_writeback"] = {"status": "error", "error": str(exc)}
            try:
                provenance_job = dict(job)
                provenance_job["status"] = status_payload.get("status")
                if "failure_class" in status_payload:
                    provenance_job["failure_class"] = status_payload["failure_class"]
                status_payload["memory_provenance"] = self._write_back_provenance(job_root, provenance_job)
            except Exception as exc:  # pragma: no cover - exercised via monkeypatch safety test
                status_payload["memory_provenance"] = {"status": "error", "error": str(exc)}
        self._write_json(job_root / "status.json", status_payload)
        return status_payload

    def _run_session_backend(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        """Run a persistent session backend without entering the one-shot CLI loop."""
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        runtime_context = self._worker_runtime_cwd(job)
        config = pi_runtime_config(self.project_root, job)
        preflight = pi_backend_doctor(self.project_root, job)
        result_path = job_root / "result.md"
        stdout_path = job_root / "stdout.txt"
        stderr_path = job_root / "stderr.txt"
        protocol_path = job_root / "pi_protocol.jsonl"
        events_path = job_root / "pi_events.jsonl"
        metadata_path = job_root / "pi_session.json"
        command = [config.binary, "--mode", "rpc", "--name", f"goodteam-{job['id']}", "--session-dir", str(config.session_dir)]
        if config.provider:
            command.extend(["--provider", config.provider])
        if config.model:
            command.extend(["--model", config.model])
        runtime_evidence = {
            **_worker_runtime_evidence(
                runtime_context,
                pythonpath_workspace_src=(runtime_context.runtime_cwd / "src").is_dir(),
            ),
            "runtime_adapter": "pi",
            "runtime_kind": "persistent_session",
            "runtime_binary": str(preflight.get("command_path") or config.binary),
            "runtime_version": str(preflight.get("version") or "unknown"),
            "runtime_version_warning": not bool(preflight.get("version")),
            "runtime_version_warning_reason": "" if preflight.get("version") else "runtime_version_unavailable",
            "runtime_version_failure_reason": "" if preflight.get("version") else "version command unavailable",
            "runtime_model": config.model or "auto",
            "runtime_provider": config.provider,
            "runtime_thinking_level": config.thinking_level or "default",
            "runtime_capabilities": list(capabilities_dict()),
            "runtime_sandbox_mode": "goodteam_worktree_or_project_root",
            "runtime_approval_policy": "unknown",
            "runtime_unsafe_mode": False,
            "runtime_policy_warning": True,
            "runtime_policy_warning_reason": "pi_has_no_builtin_permission_boundary",
            "runtime_transcript_path": str(protocol_path),
            "runtime_events_path": str(events_path),
            "runtime_session_metadata_path": str(metadata_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "result_path": str(result_path),
        }
        base_extra = {
            "command": command,
            "model": config.model or "auto",
            "preflight": preflight,
            **runtime_evidence,
            "invocation_evidence": {
                **runtime_evidence,
                "kind": "session_runtime",
                "provider": config.provider or "pi-configured",
                "external_invocation": bool(preflight.get("available")),
                "command_available": bool(preflight.get("available")),
                "reason": "Pi RPC session invocation attempted",
            },
        }
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        if not preflight.get("ready"):
            summary = str(preflight.get("hint") or "Pi preflight failed")
            return self._write_status(
                job_root,
                job,
                "failed",
                summary,
                extra={**base_extra, "exit_code": None, "failure_class": "pi_not_installed" if not preflight.get("available") else "pi_preflight_failed"},
            )
        runtime = PiSessionRuntime(
            project_root=self.project_root,
            job_root=job_root,
            job=job,
            runtime_cwd=runtime_context.runtime_cwd,
            config=config,
            stderr_path=stderr_path,
        )
        stop_heartbeat = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        running_summary = "Pi RPC session is running."
        started_at = self._now()
        try:
            state = runtime.start_or_resume()
            session_extra = {
                **base_extra,
                "started_at": started_at,
                "pid": runtime.rpc.pid if runtime.rpc else None,
                "heartbeat_at": self._now(),
                "pi_session_id": str(state.get("sessionId") or ""),
                "pi_session_file": _relative_session_evidence_path(state.get("sessionFile"), config.session_dir),
                "session_status": "running",
                "isStreaming": bool(state.get("isStreaming")),
                "isCompacting": bool(state.get("isCompacting")),
                "pendingMessageCount": int(state.get("pendingMessageCount") or 0),
            }
            self._write_status(job_root, job, "running", running_summary, extra=session_extra)
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_until_stopped,
                args=(job_root, job, stop_heartbeat),
                kwargs={"summary": running_summary, "extra": session_extra},
                daemon=True,
            )
            heartbeat_thread.start()
            if not runtime.already_settled:
                if str(job.get("worker_session_ref") or "").strip():
                    runtime.send_resume_prompt(self._worker_prompt(job_root, job, model=config.model or "auto"))
                else:
                    runtime.send_prompt(self._worker_prompt(job_root, job, model=config.model or "auto"))

            def tick() -> None:
                self._process_pi_controls(job_root, job, runtime)

            runtime.wait_until_settled(PI_OVERALL_TIMEOUT_SECONDS, tick=tick)
            self._fail_pending_pi_controls(job_root, job, "Pi session settled before RPC acknowledgement")
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)
            if runtime.aborted:
                aborted = PiRpcError("Pi session was aborted by operator")
                aborted.failure_class = "pi_aborted"
                raise aborted
            state = runtime.get_state()
            stats = runtime.get_session_stats()
            runtime.get_entries()
            assistant_text = runtime.get_last_assistant_text()
            if assistant_text:
                result_path.write_text(assistant_text.rstrip() + "\n", encoding="utf-8")
            else:
                result_path.write_text(
                    "# Worker Result\n\nStatus: completed-without-assistant-text\n\nPi settled but returned no assistant text. Inspect local Pi evidence files.\n",
                    encoding="utf-8",
                )
            stdout_path.write_text(
                json.dumps({"session_state": state, "session_stats": stats}, indent=2), encoding="utf-8"
            )
            metadata = runtime.store.read_metadata() or {}
            return self._write_status(
                job_root,
                job,
                "completed",
                "Pi RPC session settled successfully.",
                extra={
                    **base_extra,
                    "started_at": started_at,
                    "exit_code": 0,
                    "pid": runtime.rpc.pid if runtime.rpc else None,
                    "pi_session_id": str(metadata.get("session_id") or ""),
                    "pi_session_file": _relative_session_evidence_path(metadata.get("session_file"), config.session_dir),
                    "session_status": str(metadata.get("status") or "settled"),
                    "last_entry_cursor": str(metadata.get("last_entry_id") or ""),
                    "pi_session_stats": stats,
                    "process_alive": False,
                },
            )
        except (PiRpcError, TimeoutError, ValueError, FileNotFoundError) as exc:
            self._fail_pending_pi_controls(job_root, job, f"Pi session stopped before RPC acknowledgement: {exc}")
            stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
            failure_class = getattr(exc, "failure_class", "pi_overall_timeout" if isinstance(exc, TimeoutError) else "pi_session_mismatch")
            summary = f"Pi session failed [{failure_class}]: {exc}"
            result_path.write_text(f"# Worker Result\n\nStatus: failed\n\n{summary}\n", encoding="utf-8")
            metadata = runtime.store.read_metadata() or {}
            metadata.update({"status": "aborted" if runtime.aborted else "failed", "last_error": str(exc)})
            runtime.store.write_metadata(metadata)
            runtime.store.append_event("failure", {"failure_class": failure_class, "summary": str(exc)[:500]})
            return self._write_status(
                job_root,
                job,
                "failed",
                summary,
                extra={
                    **base_extra,
                    "started_at": started_at,
                    "exit_code": None,
                    "pid": runtime.rpc.pid if runtime.rpc else None,
                    "failure_class": failure_class,
                    "session_status": metadata.get("status", "failed"),
                    "process_alive": False,
                },
            )
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None and heartbeat_thread is not threading.current_thread():
                heartbeat_thread.join(timeout=1)
            runtime.close()

    def _process_pi_controls(
        self,
        job_root: Path,
        job: dict[str, Any],
        runtime: PiSessionRuntime,
    ) -> None:
        path = job_root / "pi_control_queue.jsonl"
        if not path.is_file():
            return
        states = self._pi_control_ack_states(job_root)
        lines = path.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            control_id = ""
            control: dict[str, Any] = {}
            try:
                control = json.loads(raw)
                control_id = str(control.get("id") or "")
                if not control_id:
                    raise ValueError("Pi control command is missing an id")
                self._reconcile_pi_control_terminal_audit(job_root, job, control)
                states = self._pi_control_ack_states(job_root)
                current_state = str(states.get(control_id, {}).get("state") or "queued")
                if current_state in {"rpc_accepted", "failed"}:
                    self._record_pi_control_terminal(
                        job_root,
                        job,
                        control,
                        current_state,
                        error=str(states.get(control_id, {}).get("error") or ""),
                        states=states,
                    )
                    continue
                if current_state == "processing":
                    self._record_pi_control_terminal(
                        job_root,
                        job,
                        control,
                        "failed",
                        error="Pi control acknowledgement is ambiguous after worker restart",
                        states=states,
                    )
                    continue
                self._append_pi_control_ack(job_root, control_id, "processing")
                states[control_id] = {"state": "processing"}
                action = str(control.get("action") or "")
                if action == "steer":
                    runtime.steer(str(control.get("message") or ""))
                elif action == "follow_up":
                    runtime.follow_up(str(control.get("message") or ""))
                elif action == "abort":
                    runtime.abort()
                else:
                    raise ValueError(f"unknown Pi control action: {action}")
                self._record_pi_control_terminal(
                    job_root,
                    job,
                    control,
                    "rpc_accepted",
                    states=states,
                )
                runtime.store.append_event(
                    "operator_control_accepted",
                    {
                        "control_id": control_id,
                        "action": action,
                        "actor": str(control.get("actor") or "cli"),
                        "source": str(control.get("source") or "cli"),
                        "job_id": str(job.get("id") or ""),
                        "execution_id": str(job.get("execution_id") or job.get("run_id") or ""),
                        "reason": str(control.get("reason") or ""),
                        **(
                            {
                                str(key): str(value)
                                for key, value in control.get("audit_context", {}).items()
                            }
                            if isinstance(control.get("audit_context"), dict)
                            else {}
                        ),
                    },
                )
            except (json.JSONDecodeError, PiRpcError, ValueError) as exc:
                if control_id:
                    self._record_pi_control_terminal(
                        job_root,
                        job,
                        control,
                        "failed",
                        error=str(exc)[:500],
                        states=states,
                    )
                runtime.store.append_event(
                    "operator_control_failed",
                    {"control_id": control_id, "error": str(exc)[:500]},
                )

    def _fail_pending_pi_controls(self, job_root: Path, job: dict[str, Any], error: str) -> None:
        path = job_root / "pi_control_queue.jsonl"
        if not path.is_file():
            return
        states = self._pi_control_ack_states(job_root)
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                control = json.loads(raw)
            except json.JSONDecodeError:
                continue
            control_id = str(control.get("id") or "")
            self._reconcile_pi_control_terminal_audit(job_root, job, control)
            states = self._pi_control_ack_states(job_root)
            state = str(states.get(control_id, {}).get("state") or "queued")
            if not control_id:
                continue
            terminal_state = state if state in {"rpc_accepted", "failed"} else "failed"
            terminal_error = str(states.get(control_id, {}).get("error") or "") if state == "failed" else ""
            if state not in {"rpc_accepted", "failed"}:
                terminal_error = error[:500]
            self._record_pi_control_terminal(
                job_root,
                job,
                control,
                terminal_state,
                error=terminal_error,
                states=states,
            )

    def _record_pi_control_terminal(
        self,
        job_root: Path,
        job: dict[str, Any],
        control: dict[str, Any],
        state: str,
        *,
        error: str = "",
        states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if state not in {"rpc_accepted", "failed"}:
            raise ValueError(f"Pi control terminal state is invalid: {state}")
        control_id = str(control.get("id") or "")
        if not control_id:
            return
        lock_fd = os.open(job_root / ".pi_control_terminal.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            current_states = self._pi_control_ack_states(job_root)
            current = current_states.get(control_id, {})
            current_state = str(current.get("state") or "queued")
            audit = self._telegram_terminal_control(job_root, control_id)

            if audit is not None:
                audit_state = str(audit.get("control_state") or "")
                if current_state in {"rpc_accepted", "failed"} and current_state != audit_state:
                    raise ValueError("Pi control terminal acknowledgement conflicts with its Telegram audit")
                if (
                    current_state == audit_state == "failed"
                    and str(current.get("error") or "") != str(audit.get("error") or "")
                ):
                    raise ValueError("Pi control terminal acknowledgement error conflicts with its Telegram audit")
                if state != audit_state:
                    raise ValueError("Pi control terminal state conflicts with its durable Telegram audit")
                state = audit_state
                error = str(audit.get("error") or "")
                if current_state not in {"rpc_accepted", "failed"}:
                    self._append_pi_control_ack(job_root, control_id, state, error=error)
                    current_states[control_id] = {
                        "control_id": control_id,
                        "state": state,
                        **({"error": error} if error else {}),
                    }
            elif current_state in {"rpc_accepted", "failed"}:
                if state != current_state:
                    raise ValueError("Pi control terminal state conflicts with its durable acknowledgement")
                state = current_state
                error = str(current.get("error") or "")
            else:
                self._append_pi_control_ack(job_root, control_id, state, error=error)
                current_states[control_id] = {
                    "control_id": control_id,
                    "state": state,
                    **({"error": error} if error else {}),
                }

            if states is not None:
                states[control_id] = current_states[control_id]
            if str(control.get("source") or "") != "telegram" or audit is not None:
                return
            audit_context = control.get("audit_context", {})
            payload = {
                **(
                    {str(key): str(value) for key, value in audit_context.items()}
                    if isinstance(audit_context, dict)
                    else {}
                ),
                "recorded_at": self._now(),
                "event": "telegram_runtime_control",
                "project_key": str(job.get("project_key") or ""),
                "job_id": str(job.get("id") or ""),
                "execution_id": str(job.get("execution_id") or job.get("run_id") or ""),
                "action": str(control.get("action") or ""),
                "control_id": control_id,
                "control_state": state,
                "actor_ref": str(control.get("actor") or ""),
            }
            if error:
                payload["error"] = error[:500]
            self._append_jsonl(job_root / "pi_events.jsonl", payload)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _reconcile_pi_control_terminal_audit(
        self,
        job_root: Path,
        job: dict[str, Any],
        control: dict[str, Any],
    ) -> None:
        if str(control.get("source") or "") != "telegram":
            return
        control_id = str(control.get("id") or "")
        if not control_id:
            return
        audit = self._telegram_terminal_control(job_root, control_id)
        if audit is None:
            return
        self._record_pi_control_terminal(
            job_root,
            job,
            control,
            str(audit.get("control_state") or ""),
            error=str(audit.get("error") or ""),
        )

    @staticmethod
    def _telegram_terminal_control(job_root: Path, control_id: str) -> dict[str, Any] | None:
        path = job_root / "pi_events.jsonl"
        if not path.is_file():
            return None
        matches: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (
                event.get("event") == "telegram_runtime_control"
                and event.get("control_state") in {"rpc_accepted", "failed"}
                and str(event.get("control_id") or "") == control_id
            ):
                matches.append(event)
        if len(matches) > 1:
            raise ValueError("Telegram control has multiple durable terminal audit events")
        return matches[0] if matches else None

    def _pi_control_ack_states(self, job_root: Path) -> dict[str, dict[str, Any]]:
        path = job_root / "pi_control_acks.jsonl"
        if not path.is_file():
            return {}
        states: dict[str, dict[str, Any]] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            control_id = str(record.get("control_id") or "")
            if control_id:
                states[control_id] = record
        return states

    def _append_pi_control_ack(
        self,
        job_root: Path,
        control_id: str,
        state: str,
        *,
        error: str = "",
    ) -> None:
        payload = {
            "control_id": control_id,
            "state": state,
            "recorded_at": self._now(),
        }
        if error:
            payload["error"] = error
        self._append_jsonl(job_root / "pi_control_acks.jsonl", payload)

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)

    def session_status(self, job_id: str) -> dict[str, Any]:
        job_root, job = self._session_job(job_id)
        status = self._read_json(job_root / "status.json") if (job_root / "status.json").is_file() else {}
        metadata = self._read_json(job_root / "pi_session.json") if (job_root / "pi_session.json").is_file() else {}
        return {
            "job_id": job_id,
            "execution_id": str(metadata.get("execution_id") or job.get("execution_id") or job.get("run_id") or ""),
            "pi_session_id": str(metadata.get("session_id") or ""),
            "session_status": str(metadata.get("status") or status.get("status") or "unknown"),
            "isStreaming": bool(metadata.get("isStreaming")),
            "isCompacting": bool(metadata.get("isCompacting")),
            "pendingMessageCount": int(metadata.get("pendingMessageCount") or 0),
            "model": str(metadata.get("model") or job.get("model") or "auto"),
            "provider": str(metadata.get("provider") or ""),
            "runtime_cwd": str(metadata.get("runtime_cwd") or status.get("runtime_cwd") or ""),
            "last_entry_cursor": str(metadata.get("last_entry_id") or ""),
            "heartbeat": str(status.get("heartbeat_at") or ""),
            "process_alive": (
                _pid_alive(status.get("pid"))
                and status.get("status") == "running"
                and not _is_stale_heartbeat(_heartbeat_reference(status))
            ),
        }

    def session_control(
        self,
        job_id: str,
        action: str,
        *,
        message: str = "",
        reason: str = "",
        actor: str = "cli",
        source: str = "cli",
        control_id: str = "",
        audit_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        job_root, job = self._session_job(job_id)
        if action in {"steer", "follow_up"} and not message.strip():
            raise ValueError(f"{action} message must not be empty")
        requested_control_id = control_id.strip()
        if requested_control_id and (
            Path(requested_control_id).name != requested_control_id
            or not re.fullmatch(r"control-[A-Za-z0-9_-]{8,128}", requested_control_id)
        ):
            raise ValueError("control_id is invalid")
        if requested_control_id:
            existing = self._pi_control_request(job_root, requested_control_id)
            if existing is not None:
                if str(existing.get("action") or "") != action:
                    raise ValueError("control_id is already bound to another action")
                self._reconcile_pi_control_terminal_audit(job_root, job, existing)
                acknowledgement = self._pi_control_ack_states(job_root).get(requested_control_id, {})
                acknowledgement_state = str(acknowledgement.get("state") or "queued")
                if acknowledgement_state in {"rpc_accepted", "failed"}:
                    self._record_pi_control_terminal(
                        job_root,
                        job,
                        existing,
                        acknowledgement_state,
                        error=str(acknowledgement.get("error") or ""),
                    )
                return self._wait_for_pi_control_ack(
                    job_id,
                    job_root,
                    requested_control_id,
                    action,
                    idempotent=True,
                )

        status = self.session_status(job_id)
        if status["session_status"] != "running" or not status["process_alive"]:
            if action == "abort" and status["session_status"] == "aborted":
                return {**status, "accepted": True, "idempotent": True}
            raise ValueError(f"Pi session for job {job_id} is not active ({status['session_status']})")
        payload = {
            "id": requested_control_id or f"control-{uuid4()}",
            "created_at": self._now(),
            "action": action,
            "message": message,
            "reason": reason,
            "actor": actor,
            "source": source,
            "job_id": job_id,
            "execution_id": str(job.get("execution_id") or job.get("run_id") or ""),
        }
        if audit_context:
            payload["audit_context"] = {
                str(key): str(value)
                for key, value in audit_context.items()
                if value and key in {
                    "telegram_chat_id",
                    "telegram_message_id",
                    "telegram_user_id",
                    "command",
                    "project_key",
                }
            }
        self._append_jsonl(job_root / "pi_control_queue.jsonl", payload)
        return self._wait_for_pi_control_ack(job_id, job_root, payload["id"], action)

    def _pi_control_request(self, job_root: Path, control_id: str) -> dict[str, Any] | None:
        path = job_root / "pi_control_queue.jsonl"
        if not path.is_file():
            return None
        matches: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if str(record.get("id") or "") == control_id:
                matches.append(record)
        if len(matches) > 1:
            raise ValueError("Pi control identity is ambiguous within the worker job")
        return matches[0] if matches else None

    def find_project_session_control(
        self,
        control_id: str,
        *,
        project_key: str,
    ) -> tuple[str, dict[str, Any]] | None:
        matches: list[tuple[str, dict[str, Any]]] = []
        for job in self.list_jobs():
            if str(job.get("adapter") or "") not in SESSION_BACKENDS:
                continue
            if str(job.get("project_key") or "") != project_key:
                continue
            job_id = str(job.get("id") or "")
            request = self._pi_control_request(self.root / job_id, control_id)
            if request is not None:
                matches.append((job_id, request))
        if len(matches) > 1:
            raise ValueError("Telegram control identity is ambiguous within the configured project")
        return matches[0] if matches else None

    def find_session_control(
        self,
        control_id: str,
        *,
        project_key: str,
        run_id: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Compatibility wrapper; Telegram control lookup must be project-wide."""
        if run_id is not None:
            raise ValueError("run-scoped Telegram control lookup is unsafe")
        return self.find_project_session_control(control_id, project_key=project_key)

    def _wait_for_pi_control_ack(
        self,
        job_id: str,
        job_root: Path,
        control_id: str,
        action: str,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        timeout = float(os.getenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "5"))
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            acknowledgement = self._pi_control_ack_states(job_root).get(control_id, {})
            state = str(acknowledgement.get("state") or "queued")
            if state == "rpc_accepted":
                return {
                    **self.session_status(job_id),
                    "accepted": True,
                    "control_id": control_id,
                    "action": action,
                    "control_state": state,
                    "idempotent": idempotent,
                }
            if state == "failed":
                return {
                    **self.session_status(job_id),
                    "accepted": False,
                    "control_id": control_id,
                    "action": action,
                    "control_state": state,
                    "error": str(acknowledgement.get("error") or "Pi rejected the control command"),
                    "idempotent": idempotent,
                }
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        return {
            **self.session_status(job_id),
            "accepted": False,
            "control_id": control_id,
            "action": action,
            "control_state": state,
            "idempotent": idempotent,
        }

    def _session_job(self, job_id: str) -> tuple[Path, dict[str, Any]]:
        if not job_id or Path(job_id).name != job_id:
            raise ValueError("job_id is invalid")
        job_root = self.root / job_id
        path = job_root / "job.json"
        if not path.is_file():
            raise ValueError(f"Worker job not found: {job_id}")
        job = self._read_json(path)
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        if str(job.get("adapter")) not in SESSION_BACKENDS:
            raise ValueError(f"Worker job {job_id} is not a persistent-session backend")
        return job_root, job

    def _memory_citations_from_pickup_packet(self, job_root: Path) -> list[str]:
        packet_path = job_root / "role_pickup_packet.json"
        if not packet_path.is_file():
            return []
        try:
            packet = self._read_json(packet_path)
        except (OSError, json.JSONDecodeError):
            return []
        relevant_events = packet.get("relevant_events")
        if not isinstance(relevant_events, list):
            return []
        citations: list[str] = []
        for event in relevant_events:
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                citations.append(event_id)
        return citations

    def _attach_sandbox_context(
        self,
        job_root: Path,
        job: dict[str, Any],
        sandbox_context: dict[str, Any],
    ) -> dict[str, Any]:
        safe_context = _worker_safe_sandbox_context(sandbox_context)
        updated = {**job, "sandbox_context": safe_context}
        self._write_json(job_root / "sandbox_context.json", safe_context)
        self._write_json(job_root / "job.json", updated)
        return updated

    def _write_back_memory(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        result_path = job_root / "result.md"
        result_text = result_path.read_text(encoding="utf-8")
        parsed_items = parse_result_sections(result_text)
        source = f"{job['id']}/result.md"
        if not parsed_items:
            return {
                "status": "skipped",
                "reason": "no_sections",
                "source": source,
                "parsed": 0,
                "appended": 0,
                "skipped_duplicates": 0,
            }

        role = str(job["role"])
        store = RoleMemoryStore(self.project_root)
        existing_texts = store.event_texts(role)
        appended = 0
        skipped_duplicates = 0
        for horizon, text in parsed_items:
            if text in existing_texts:
                skipped_duplicates += 1
                continue
            store.append_memory_event(role, horizon=horizon, text=text, source=source)
            existing_texts.add(text)
            appended += 1

        return {
            "status": "ok" if appended else "skipped",
            "source": source,
            "parsed": len(parsed_items),
            "appended": appended,
            "skipped_duplicates": skipped_duplicates,
        }

    def _write_back_provenance(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        source = f"{job['id']}/provenance"
        task_excerpt = ""
        task_path = job_root / "task.md"
        if task_path.is_file():
            task_text = task_path.read_text(encoding="utf-8")
            task_excerpt = " ".join(task_text.split())
            if len(task_excerpt) > 200:
                task_excerpt = f"{task_excerpt[:200].rstrip()}..."
        if not task_excerpt:
            task_excerpt = "(task unavailable)"

        status = str(job.get("status", "completed"))
        failure_class = str(job.get("failure_class", "")).strip()
        failure_segment = f", failure_class={failure_class}" if failure_class else ""
        task_source = str(job.get("task_source", "")).strip()
        constraints_source = str(job.get("constraints_source", "")).strip()
        research_report_source = str(job.get("research_report_source", "")).strip()
        source_segments = []
        if task_source:
            source_segments.append(f"task_source={task_source}")
        if constraints_source:
            source_segments.append(f"constraints_source={constraints_source}")
        if research_report_source:
            source_segments.append(f"research_report_source={research_report_source}")
        source_segment = f" Sources: {'; '.join(source_segments)}." if source_segments else ""
        text = (
            f"Dispatched job {job['id']} (run {job['run_id']}, adapter={job['adapter']}, model={job['model']}) "
            f"for task: {task_excerpt}. Outcome: {status}{failure_segment}.{source_segment}"
        )

        role = str(job["role"])
        store = RoleMemoryStore(self.project_root)
        existing_texts = store.event_texts(role)
        if text in existing_texts:
            return {
                "status": "skipped",
                "source": source,
                "appended": 0,
                "skipped_duplicates": 1,
            }

        store.append_memory_event(role, horizon="short-term", text=text, source=source)
        return {
            "status": "ok",
            "source": source,
            "appended": 1,
            "skipped_duplicates": 0,
        }

    def _complete_noop(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        summary = "Completed by noop adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(f"# Worker Result\n\n{summary}\n", encoding="utf-8")
        return self._write_status(job_root, job, "completed", summary)

    def _complete_fake(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        summary = "Completed by fake smoke adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(
            f"# Worker Result\n\nStatus: pass\n\n{summary}\n",
            encoding="utf-8",
        )
        return self._write_status(job_root, job, "completed", summary)

    def _wait_for_human(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        summary = "Fill result.md manually, then update status.json when ready."
        return self._write_status(job_root, job, "needs_review", summary)

    def _run_cli_backend(
        self,
        job_root: Path,
        job: dict[str, Any],
        spec: "CliBackendSpec",
    ) -> dict[str, Any]:
        """Run any registered agentic coding CLI (codex, claude, ...). Adding a
        new CLI means adding a CliBackendSpec to CLI_BACKENDS — no new branch."""
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        binary = which(spec.which_name) or spec.default_path()
        raw = str(job.get("model", ""))
        bare = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()
        model = bare or spec.model_default
        result_path = job_root / "result.md"
        runtime_context = self._worker_runtime_cwd(job)
        worker_env = self._worker_env(job_root, spec, runtime_context.runtime_cwd)
        pythonpath_workspace_src = (runtime_context.runtime_cwd / "src").is_dir()
        preflight = _cli_preflight(spec, binary, model)
        adapter = str(job.get("adapter", ""))
        command = _command_with_reasoning_depth(
            _command_with_execution_policy(
                spec.build_command(binary, model, runtime_context.runtime_cwd, result_path),
                adapter=adapter,
                execution_policy=str(job.get("execution_policy", "standard") or "standard"),
            ),
            adapter=adapter,
            reasoning_depth=str(job.get("reasoning_depth", "") or ""),
        )
        runtime_evidence = {
            **_worker_runtime_evidence(
                runtime_context,
                pythonpath_workspace_src=pythonpath_workspace_src,
            ),
            **_cli_runtime_evidence(
                adapter=adapter,
                spec=spec,
                binary=binary,
                model=model,
                command=command,
                preflight=preflight,
                result_path=result_path,
                detect_version=True,
            ),
        }
        if not preflight[f"{spec.which_name}_available"]:
            return self._write_status(
                job_root,
                job,
                "failed",
                spec.not_available_message,
                extra={
                    "command": command,
                    "model": model,
                    "exit_code": None,
                    "preflight": preflight,
                    **runtime_evidence,
                    "invocation_evidence": runtime_evidence,
                },
            )
        return self._run_cli_worker(
            job_root,
            job,
            command=command,
            model=model,
            preflight=preflight,
            label=spec.label,
            capture=spec.capture,
            summarize=spec.summarize,
            prompt_via=spec.prompt_via,
            worker_env=worker_env,
            runtime_context=runtime_context,
            runtime_evidence=runtime_evidence,
            use_pty=spec.needs_pty,
        )

    def _worker_runtime_cwd(self, job: dict[str, Any]) -> WorkerRuntimeContext:
        context = job.get("sandbox_context")
        if not isinstance(context, dict) or not context.get("available"):
            return WorkerRuntimeContext(
                runtime_cwd=self.project_root,
                workspace_context_available=False,
                workspace_cwd_fallback=False,
                workspace_cwd_fallback_reason="",
                runtime_workspace_mode="project_root",
                runtime_workspace_status="none",
            )
        workspace_path = Path(str(context.get("workspace_path", "")))
        workspace_mode = str(context.get("mode") or "workspace")
        workspace_status = str(context.get("status") or "unknown")
        if _is_repo_workspace(workspace_path):
            return WorkerRuntimeContext(
                runtime_cwd=workspace_path,
                workspace_context_available=True,
                workspace_cwd_fallback=False,
                workspace_cwd_fallback_reason="",
                runtime_workspace_mode=workspace_mode,
                runtime_workspace_status=workspace_status,
            )
        return WorkerRuntimeContext(
            runtime_cwd=self.project_root,
            workspace_context_available=True,
            workspace_cwd_fallback=True,
            workspace_cwd_fallback_reason="workspace_missing_or_not_repo",
            runtime_workspace_mode=workspace_mode,
            runtime_workspace_status=workspace_status,
        )

    def _worker_env(self, job_root: Path, spec: "CliBackendSpec", runtime_cwd: Path) -> dict[str, str] | None:
        workspace_src = runtime_cwd / "src"
        needs_pythonpath = workspace_src.is_dir()
        if not spec.isolated_home and not needs_pythonpath:
            return None
        worker_env = dict(os.environ)
        if spec.isolated_home:
            isolated_home = self._prepare_isolated_codex_home(job_root)
            worker_env["CODEX_HOME"] = str(isolated_home)
        if needs_pythonpath:
            existing = worker_env.get("PYTHONPATH", "")
            worker_env["PYTHONPATH"] = str(workspace_src) if not existing else f"{workspace_src}{os.pathsep}{existing}"
        return worker_env

    def _prepare_isolated_codex_home(self, job_root: Path) -> Path:
        source_home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        isolated_home = job_root / ".codex-home"
        isolated_home.mkdir(parents=True, exist_ok=True)
        for entry_name in CODEX_ISOLATED_HOME_ALLOWED_ENTRIES:
            source_path = source_home / entry_name
            if not source_path.exists():
                continue
            target_path = isolated_home / entry_name
            if target_path.exists() or target_path.is_symlink():
                continue
            try:
                target_path.symlink_to(source_path)
            except OSError:
                copy2(source_path, target_path)
        return isolated_home

    def _run_cli_worker(
        self,
        job_root: Path,
        job: dict[str, Any],
        *,
        command: list[str],
        model: str,
        preflight: dict[str, Any],
        label: str,
        capture: str,
        summarize: Any,
        prompt_via: str = "stdin",
        worker_env: dict[str, str] | None = None,
        runtime_context: WorkerRuntimeContext | None = None,
        runtime_evidence: dict[str, Any] | None = None,
        timeout_seconds: int = 1800,
        use_pty: bool = False,
    ) -> dict[str, Any]:
        """Run an agentic coding CLI (codex/claude) over the job protocol files.

        ``capture`` controls where the worker result comes from: ``output_file``
        means the CLI wrote it (codex ``--output-last-message``); ``stdout`` means
        the final message is printed to stdout (claude ``-p``)."""
        from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry

        reject_unverified_runtime_entry(job_root, job)
        result_path = job_root / "result.md"
        stdout_path = job_root / "stdout.txt"
        stderr_path = job_root / "stderr.txt"
        runtime_context = runtime_context or self._worker_runtime_cwd(job)
        runtime_evidence = runtime_evidence or _worker_runtime_evidence(
            runtime_context,
            pythonpath_workspace_src=(runtime_context.runtime_cwd / "src").is_dir(),
        )
        runtime_evidence = _ensure_cli_runtime_evidence(
            job=job,
            runtime_evidence=runtime_evidence,
            command=command,
            model=model,
            preflight=preflight,
            result_path=result_path,
        )
        runtime_evidence = {
            **runtime_evidence,
            **_runtime_artifact_paths(stdout_path=stdout_path, stderr_path=stderr_path, result_path=result_path),
        }
        prompt = self._worker_prompt(job_root, job, model=model)
        process_command = _command_with_prompt(command, prompt, prompt_via=prompt_via)
        process_input = None if prompt_via == "arg" else prompt
        started_at = self._now()
        running_summary = f"{label} is running."
        base_extra = {
            "command": process_command,
            "model": model,
            "started_at": started_at,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "result_path": str(result_path),
            "preflight": preflight,
            **runtime_evidence,
            "invocation_evidence": runtime_evidence,
        }
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        process: subprocess.Popen[str] | None = None
        stop_heartbeat = threading.Event()
        returncode = 1
        try:
            if use_pty:
                # The PTY path manages its own child process internally; we don't
                # hold a Popen here. Status starts running before the (blocking) run.
                running_extra = {**base_extra, "exit_code": None, "timeout_seconds": timeout_seconds, "pid": None}
                self._write_status(
                    job_root,
                    job,
                    "running",
                    running_summary,
                    extra={**running_extra, "heartbeat_at": self._now()},
                )
                heartbeat_thread = threading.Thread(
                    target=self._heartbeat_until_stopped,
                    args=(job_root, job, stop_heartbeat),
                    kwargs={"summary": running_summary, "extra": running_extra},
                    daemon=True,
                )
                heartbeat_thread.start()
                returncode, stdout, stderr = _run_command_in_pty(
                    process_command,
                    cwd=runtime_context.runtime_cwd,
                    env=worker_env,
                    process_input=process_input,
                    timeout_seconds=timeout_seconds,
                )
            else:
                process = subprocess.Popen(
                    process_command,
                    cwd=runtime_context.runtime_cwd,
                    env=worker_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                running_extra = {**base_extra, "exit_code": None, "timeout_seconds": timeout_seconds, "pid": process.pid}
                self._write_status(
                    job_root,
                    job,
                    "running",
                    running_summary,
                    extra={**running_extra, "heartbeat_at": self._now()},
                )
                heartbeat_thread = threading.Thread(
                    target=self._heartbeat_until_stopped,
                    args=(job_root, job, stop_heartbeat),
                    kwargs={"summary": running_summary, "extra": running_extra},
                    daemon=True,
                )
                heartbeat_thread.start()
                stdout, stderr = process.communicate(input=process_input, timeout=timeout_seconds)
                returncode = int(process.returncode if process.returncode is not None else 1)
        except subprocess.TimeoutExpired as exc:
            stop_heartbeat.set()
            stdout_path.write_text(_timeout_text(exc.output), encoding="utf-8")
            stderr_path.write_text(_timeout_text(exc.stderr), encoding="utf-8")
            if process is not None:
                process.kill()
            summary = f"{label} timed out after {timeout_seconds} seconds."
            result_path.write_text(
                f"# Worker Result\n\nStatus: failed\n\n{summary}\n",
                encoding="utf-8",
            )
            return self._write_status(
                job_root,
                job,
                "failed",
                summary,
                extra={**base_extra, "exit_code": None, "timeout_seconds": timeout_seconds},
            )
        finally:
            stop_heartbeat.set()
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        returncode = int(returncode)
        status = "completed" if returncode == 0 else "failed"
        summary = summarize(returncode, stderr or "")
        if capture == "stdout" and returncode == 0 and (stdout or "").strip():
            result_path.write_text(stdout, encoding="utf-8")
        if not result_path.is_file() or not result_path.read_text(encoding="utf-8").strip():
            result_path.write_text(
                f"# Worker Result\n\nStatus: {status}\n\n{summary}\n\nSee stdout.txt and stderr.txt.\n",
                encoding="utf-8",
            )
        return self._write_status(
            job_root,
            job,
            status,
            summary,
            extra={**base_extra, "exit_code": returncode},
        )

    def _heartbeat_until_stopped(
        self,
        job_root: Path,
        job: dict[str, Any],
        stop_event: threading.Event,
        *,
        summary: str,
        extra: dict[str, Any],
    ) -> None:
        while not stop_event.wait(WORKER_HEARTBEAT_INTERVAL_SECONDS):
            self._write_status(
                job_root,
                job,
                "running",
                summary,
                extra={**extra, "heartbeat_at": self._now()},
            )

    def _worker_prompt(self, job_root: Path, job: dict[str, Any], *, model: str | None = None) -> str:
        configured_model = (model or str(job.get("model", ""))).strip()
        if not configured_model:
            adapter = str(job.get("adapter", ""))
            spec = CLI_BACKENDS.get(adapter)
            configured_model = spec.model_default if spec is not None else "auto"
        pickup_context = self._render_worker_pickup_context(job_root)
        sandbox_context = _worker_sandbox_context_prompt(job)
        sections = [
                f"# GoodTeam Worker Job {job['id']}",
                f"Role: {job['role']}",
                f"Configured worker backend: {job['adapter']}.",
                f"Configured model: {configured_model}.",
                "Operate non-interactively and do not ask for human approval.",
                "Read the role prompt, task, context, constraints, and expected artifacts below.",
                "When modifying files, keep changes inside the current project root.",
                "If Sandbox Context is available, treat workspace_path as the intended execution workspace.",
                "Use bounded smoke checks. If you start dev servers, browser sessions, watchers, or other long-running processes, run them in a way you can stop and stop any dev servers, browser sessions, or watchers before exiting.",
                "At the end, summarize changed files, verification commands, and unresolved risks.",
                "## Role Prompt",
                (job_root / "role_prompt.md").read_text(encoding="utf-8"),
        ]
        additional_prompt = str(job.get("additional_prompt", "") or "").strip()
        if additional_prompt:
            sections.extend(["## Additional Worker Type Instructions", additional_prompt])
        sections.extend(
            [
                "## Task",
                (job_root / "task.md").read_text(encoding="utf-8"),
                "## Context",
                (job_root / "context.md").read_text(encoding="utf-8"),
                "## Sandbox Context",
                sandbox_context,
                "## Constraints",
                (job_root / "constraints.md").read_text(encoding="utf-8"),
                "## Expected Artifacts",
                (job_root / "expected_artifacts.json").read_text(encoding="utf-8"),
                "## Worker Pickup Signal",
                pickup_context,
                """## Memory Write-Back Sections
End your final response with these markdown sections when you have concise, non-secret takeaways to preserve. Omit a section only when it has no useful bullets.
Do not copy the parenthetical guidance text itself into your final answer.

## Learned
(one short bullet per reusable fact or lesson — omit this section if none)

## Caveats
(one short bullet per risk, gotcha, flaky command, environment constraint, or future-worker warning — omit this section if none)

## Decisions
(one short bullet per durable decision or changed contract — omit this section if none)

## Open Questions
(one short bullet per unresolved question or follow-up decision needed next — omit this section if none)

Keep each bullet short, factual, and free of secrets.""",
            ]
        )
        return "\n\n".join(sections)

    def _render_worker_pickup_context(self, job_root: Path) -> str:
        packet_path = job_root / "role_pickup_packet.json"
        if not packet_path.is_file():
            return "No pickup packet found."
        try:
            packet = self._read_json(packet_path)
        except (OSError, json.JSONDecodeError):
            return "Pickup packet unreadable."

        relevant_events = packet.get("relevant_events")
        summaries = packet.get("summaries")
        lines = [
            "Use retrieved relevant events below as the PRIMARY decision context.",
            "Treat horizon summaries as display-only context.",
        ]
        if isinstance(relevant_events, list) and relevant_events:
            lines.append("")
            lines.append("### Retrieved Relevant Events (Primary Signal)")
            for event in relevant_events:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("id", "")).strip()
                horizon = str(event.get("horizon", "")).strip()
                text = str(event.get("text", "")).strip()
                score = event.get("score")
                if not text:
                    continue
                prefix = f"- [{event_id}]"
                if horizon:
                    prefix += f" ({horizon})"
                if isinstance(score, int | float):
                    prefix += f" score={float(score):.4f}"
                lines.append(f"{prefix}: {text}")
        else:
            lines.append("")
            lines.append("### Retrieved Relevant Events (Primary Signal)")
            lines.append("- None retrieved for this query.")

        if isinstance(summaries, dict):
            long_summary = str(summaries.get("long-term", "")).strip()
            mid_summary = str(summaries.get("mid-term", "")).strip()
            if long_summary or mid_summary:
                lines.append("")
                lines.append("### Horizon Summary (Display-Only)")
                if long_summary:
                    lines.append(f"- long-term: {long_summary}")
                if mid_summary:
                    lines.append(f"- mid-term: {mid_summary}")
        return "\n".join(lines)

    def _write_status(
        self,
        job_root: Path,
        job: dict[str, Any],
        status: str,
        summary: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        extra_payload = dict(extra or {})
        now = str(extra_payload.get("heartbeat_at") or self._now())
        if status == "running" and "heartbeat_at" not in extra_payload:
            extra_payload["heartbeat_at"] = now
        job = {**job, "status": status, "updated_at": now}
        status_payload = {
            "job_id": job["id"],
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": status,
            "summary": summary,
            "updated_at": now,
            "artifacts": [],
            "changed_files": [],
        }
        status_payload.update(_clean_identity_fields(job))
        if isinstance(job.get("sandbox_context"), dict):
            status_payload["sandbox_context"] = job["sandbox_context"]
        if extra_payload:
            status_payload.update(extra_payload)
        provided_evidence = status_payload.get("invocation_evidence")
        if not isinstance(provided_evidence, dict):
            provided_evidence = {}
        status_payload["invocation_evidence"] = {
            **_invocation_evidence(job, summary=summary, extra=status_payload),
            **provided_evidence,
        }
        self._write_json(job_root / "job.json", job)
        self._write_json(job_root / "status.json", status_payload)
        return status_payload

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()


def _timeout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_repo_workspace(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


def _pid_alive(value: object) -> bool:
    try:
        pid = int(value or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _relative_session_evidence_path(value: object, session_root: Path) -> str:
    if not value:
        return ""
    try:
        return str(Path(str(value)).expanduser().resolve().relative_to(session_root.resolve()))
    except (OSError, ValueError):
        return ""


def _worker_runtime_evidence(
    context: WorkerRuntimeContext,
    *,
    pythonpath_workspace_src: bool,
) -> dict[str, Any]:
    return {
        "runtime_cwd": str(context.runtime_cwd),
        "runtime_workspace_mode": context.runtime_workspace_mode,
        "runtime_workspace_status": context.runtime_workspace_status,
        "workspace_context_available": context.workspace_context_available,
        "workspace_cwd_fallback": context.workspace_cwd_fallback,
        "workspace_cwd_fallback_reason": context.workspace_cwd_fallback_reason,
        "pythonpath_workspace_src": pythonpath_workspace_src,
    }


def _ensure_cli_runtime_evidence(
    *,
    job: dict[str, Any],
    runtime_evidence: dict[str, Any],
    command: list[str],
    model: str,
    preflight: dict[str, Any],
    result_path: Path,
) -> dict[str, Any]:
    if runtime_evidence.get("runtime_adapter"):
        return runtime_evidence
    adapter = str(job.get("adapter", ""))
    spec = CLI_BACKENDS.get(adapter)
    if spec is None:
        return runtime_evidence
    binary = _invocation_command(command)
    return {
        **runtime_evidence,
        **_cli_runtime_evidence(
            adapter=adapter,
            spec=spec,
            binary=binary,
            model=model,
            command=command,
            preflight=preflight,
            result_path=result_path,
            detect_version=False,
        ),
    }


def _cli_runtime_evidence(
    *,
    adapter: str,
    spec: CliBackendSpec,
    binary: str,
    model: str,
    command: list[str],
    preflight: dict[str, Any],
    result_path: Path,
    detect_version: bool,
) -> dict[str, Any]:
    available = preflight.get(f"{spec.which_name}_available") is True
    return {
        "runtime_adapter": adapter,
        "runtime_binary": binary,
        **_runtime_version_evidence(binary, available=available, detect=detect_version),
        "runtime_model": model,
        **_runtime_policy_evidence(command),
        "runtime_transcript_path": "",
        "stdout_path": "",
        "stderr_path": "",
        "result_path": str(result_path),
    }


def _runtime_version_evidence(binary: str, *, available: bool, detect: bool) -> dict[str, Any]:
    if not available:
        return _unknown_runtime_version("runtime binary unavailable")
    if not detect:
        return _unknown_runtime_version("version detection not attempted")
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return _unknown_runtime_version("version command timed out")
    except (OSError, TypeError):
        return _unknown_runtime_version("version command failed to start")
    if completed.returncode != 0:
        return _unknown_runtime_version(f"version command exited with code {completed.returncode}")
    version = (completed.stdout or completed.stderr or "").strip().splitlines()
    if not version:
        return _unknown_runtime_version("version command returned empty output")
    return {
        "runtime_version": version[0].strip(),
        "runtime_version_warning": False,
        "runtime_version_warning_reason": "",
        "runtime_version_failure_reason": "",
    }


def _unknown_runtime_version(reason: str) -> dict[str, Any]:
    return {
        "runtime_version": "unknown",
        "runtime_version_warning": True,
        "runtime_version_warning_reason": "runtime_version_unavailable",
        "runtime_version_failure_reason": reason,
    }


def _runtime_policy_evidence(command: list[str]) -> dict[str, Any]:
    values = [str(item) for item in command]
    if "--dangerously-bypass-approvals-and-sandbox" in values:
        return _runtime_policy(
            sandbox_mode="disabled",
            approval_policy="bypass",
            unsafe=True,
            warning_reason="runtime_policy_unsafe",
        )
    if "--dangerously-skip-permissions" in values:
        return _runtime_policy(
            sandbox_mode="unknown",
            approval_policy="bypass",
            unsafe=True,
            warning_reason="runtime_policy_unsafe",
        )
    if _has_flag_value(values, "--approval-mode", "yolo"):
        return _runtime_policy(
            sandbox_mode="unknown",
            approval_policy="yolo",
            unsafe=True,
            warning_reason="runtime_policy_unsafe",
        )
    if _has_flag_value(values, "--sandbox", "read-only"):
        return _runtime_policy(
            sandbox_mode="read-only",
            approval_policy="non-interactive",
            unsafe=False,
            warning_reason="",
        )
    if "--safe-mode" in values and _has_flag_value(values, "--tools", ""):
        return _runtime_policy(
            sandbox_mode="tools-disabled",
            approval_policy="no-tools",
            unsafe=False,
            warning_reason="",
        )
    if _has_flag_value(values, "--approval-mode", "plan") or _has_flag_value(values, "--mode", "plan"):
        return _runtime_policy(
            sandbox_mode="plan",
            approval_policy="plan",
            unsafe=False,
            warning_reason="",
        )
    if _has_flag_value(values, "--agent", "plan"):
        return _runtime_policy(
            sandbox_mode="plan",
            approval_policy="plan",
            unsafe=False,
            warning_reason="",
        )
    return _runtime_policy(
        sandbox_mode="unknown",
        approval_policy="unknown",
        unsafe=False,
        warning_reason="runtime_policy_unverified",
    )


def _runtime_policy(
    *,
    sandbox_mode: str,
    approval_policy: str,
    unsafe: bool,
    warning_reason: str,
) -> dict[str, Any]:
    return {
        "runtime_sandbox_mode": sandbox_mode,
        "runtime_approval_policy": approval_policy,
        "runtime_unsafe_mode": unsafe,
        "runtime_policy_warning": bool(warning_reason),
        "runtime_policy_warning_reason": warning_reason,
    }


def _has_flag_value(values: list[str], flag: str, expected: str) -> bool:
    try:
        return values[values.index(flag) + 1].lower() == expected
    except (ValueError, IndexError):
        return False


def _runtime_artifact_paths(*, stdout_path: Path, stderr_path: Path, result_path: Path) -> dict[str, str]:
    return {
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_path": str(result_path),
    }


def _codex_model_name(value: str) -> str:
    model = value.strip() or DEFAULT_CODEX_MODEL
    if ":" in model:
        return model.split(":", 1)[1].strip() or DEFAULT_CODEX_MODEL
    return model


def _clean_identity_fields(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in WORKER_JOB_IDENTITY_FIELDS:
        value = payload.get(key)
        if value in (None, ""):
            continue
        cleaned[key] = str(value) if key in {"attempt", "retry_attempt"} else value
    return cleaned


def worker_backend_doctor(mode: str) -> dict[str, Any]:
    """Report whether the configured worker backend can actually run, so a
    user sees missing CLIs or credentials at doctor/init time instead of after
    a worker fails repeatedly. Never echoes secret values, only their presence.

    Routes through CLI_BACKENDS, so a newly registered spec is reported here too."""
    mode = (mode or "").strip() or "codex"
    if mode == "manual":  # config alias for the human worker
        mode = "human"
    if mode in BUILTIN_WORKER_MODES:
        return {
            "mode": mode,
            "kind": "builtin",
            "ready": True,
            "hint": f"The `{mode}` worker runs locally and needs no CLI or credentials.",
        }
    if mode in SESSION_BACKENDS:
        report = pi_backend_doctor()
        report["mode"] = "pi" if mode == "pi-rpc" else mode
        return report
    spec = CLI_BACKENDS.get(mode)
    if spec is None:
        known = ", ".join(["fake", *sorted(CLI_BACKENDS), *sorted(SESSION_BACKENDS), "noop", "human"])
        return {
            "mode": mode,
            "kind": "unknown",
            "ready": False,
            "hint": f"Unknown worker_backend.mode '{mode}'. Use one of: {known}.",
        }
    binary = which(spec.which_name) or spec.default_path()
    available = bool(which(spec.which_name) or Path(binary).is_file())
    auth_env = {name: bool(os.getenv(name)) for name in spec.auth_env}
    report: dict[str, Any] = {
        "mode": mode,
        "kind": "cli",
        "command": spec.which_name,
        "command_path": binary if available else "",
        "available": available,
        "auth_env": auth_env,
    }
    if spec.which_name == "codex":
        codex_home = os.getenv("CODEX_HOME", "") or os.path.expanduser("~/.codex")
        auth_file = Path(codex_home) / "auth.json"
        auth_file_present = auth_file.is_file()
        auth_present = any(auth_env.values()) or auth_file_present
        report.update(
            {
                "auth_file": str(auth_file),
                "auth_file_present": auth_file_present,
                "auth_present": auth_present,
                "ready": available and auth_present,
                "hint": _codex_auth_hint(available, auth_env, auth_file_present),
            }
        )
    elif spec.which_name == "claude":
        auth_present = any(auth_env.values())
        # The claude CLI can also run from a `claude login` session we can't
        # detect here, so the binary being present is the hard requirement.
        report.update(
            {
                "auth_present": auth_present,
                "ready": available,
                "hint": _claude_auth_hint(available, auth_present),
            }
        )
    else:
        auth_present = any(auth_env.values())
        report.update(
            {
                "auth_present": auth_present,
                "ready": available,
                "hint": _generic_cli_hint(spec, available, auth_present),
            }
        )
    return report


def worker_backend_local_preflight(
    mode: str,
    *,
    dry_run: bool,
    allow_real_worker: bool,
) -> dict[str, Any]:
    """Inspect CLI readiness without exposing target content or invoking an agent."""
    requested_mode = (mode or "").strip() or "codex"
    canonical_mode = "anthropic" if requested_mode == "claude" else requested_mode
    if canonical_mode in SESSION_BACKENDS:
        doctor = worker_backend_doctor(canonical_mode)
        version = str(doctor.get("version") or "")
        return {
            "mode": "pi",
            "kind": "session",
            "status": "ready" if doctor.get("ready") else "fail",
            "local_only": True,
            "external_invocation": False,
            "real_run_opt_in": allow_real_worker,
            "dry_run": dry_run,
            "checks": {
                "backend_selected": True,
                "binary_available": bool(doctor.get("available")),
                "version_readable": bool(version),
                "session_directory_writable": bool(doctor.get("session_directory_writable")),
                "config_ready": bool(doctor.get("ready")),
            },
            "version": version,
            "planned_command": ["pi", "--mode", "rpc", "--session-dir", "<local-session-dir>"],
            "cwd": "goodteam-runtime-workspace",
            "sandbox_mode": "goodteam-worktree-boundary",
            "capabilities": list(capabilities_dict()),
            "message": str(doctor.get("hint") or ""),
        }
    spec = CLI_BACKENDS.get(canonical_mode)
    if spec is None:
        return _non_cli_local_preflight(canonical_mode, dry_run, allow_real_worker)
    doctor = worker_backend_doctor(canonical_mode)
    available = bool(doctor.get("available"))
    version = _local_cli_version(spec, str(doctor.get("command_path", ""))) if available else None
    auth_present = bool(doctor.get("auth_present"))
    auth_safe_to_determine = spec.which_name != "claude" or auth_present
    status, message = _local_preflight_outcome(spec, available, version, auth_present, auth_safe_to_determine)
    return _cli_local_preflight_report(
        spec=spec,
        canonical_mode=canonical_mode,
        doctor=doctor,
        status=status,
        message=message,
        version=version,
        auth_present=auth_present,
        auth_safe_to_determine=auth_safe_to_determine,
        dry_run=dry_run,
        allow_real_worker=allow_real_worker,
    )


def _local_preflight_outcome(
    spec: CliBackendSpec,
    available: bool,
    version: str | None,
    auth_present: bool,
    auth_safe_to_determine: bool,
) -> tuple[str, str]:
    if not available:
        return "fail", f"{spec.label} binary is not available."
    if version is None:
        return "fail", f"{spec.label} version command failed locally."
    if not auth_present or not auth_safe_to_determine:
        return "warn", f"{spec.label} authentication readiness is incomplete or cannot be safely determined."
    return "ready", f"{spec.label} passed local readiness checks."


def _cli_local_preflight_report(
    *,
    spec: CliBackendSpec,
    canonical_mode: str,
    doctor: dict[str, Any],
    status: str,
    message: str,
    version: str | None,
    auth_present: bool,
    auth_safe_to_determine: bool,
    dry_run: bool,
    allow_real_worker: bool,
) -> dict[str, Any]:
    return {
        "mode": canonical_mode,
        "kind": "cli",
        "status": status,
        "local_only": True,
        "external_invocation": False,
        "real_run_opt_in": allow_real_worker,
        "dry_run": dry_run,
        "checks": {
            "backend_selected": True,
            "binary_available": bool(doctor.get("available")),
            "version_readable": version is not None,
            "auth_present": auth_present,
            "auth_safe_to_determine": auth_safe_to_determine,
            "config_ready": bool(doctor.get("ready")),
        },
        "version": version or "",
        "planned_command": _redacted_command_shape(spec),
        "cwd": "isolated-workspace",
        "sandbox_mode": "isolated-output-root",
        "message": message,
    }


def _non_cli_local_preflight(mode: str, dry_run: bool, allow_real_worker: bool) -> dict[str, Any]:
    doctor = worker_backend_doctor(mode)
    return {
        "mode": mode,
        "kind": str(doctor.get("kind", "unknown")),
        "status": "ready" if doctor.get("ready") else "fail",
        "local_only": True,
        "external_invocation": False,
        "real_run_opt_in": allow_real_worker,
        "dry_run": dry_run,
        "checks": {"backend_selected": True},
        "planned_command": [],
        "cwd": "isolated-workspace",
        "sandbox_mode": "isolated-output-root",
        "message": str(doctor.get("hint", "")),
    }


def _local_cli_version(spec: CliBackendSpec, binary: str) -> str | None:
    safe_env = {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LC_ALL", "PATH", "SHELL", "TERM", "TMPDIR"}
    }
    try:
        with tempfile.TemporaryDirectory(prefix="goodteam-worker-preflight-") as isolated_home:
            safe_env.update(
                {
                    "HOME": isolated_home,
                    "CODEX_HOME": isolated_home,
                    "XDG_CONFIG_HOME": isolated_home,
                }
            )
            completed = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                env=safe_env,
            )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return _redact_version(output[0]) if output else None


def _redact_version(value: str) -> str:
    cleaned = re.sub(r"(?:/[^\s]+)+", "<redacted-path>", value.strip())
    return cleaned[:160]


def _redacted_command_shape(spec: CliBackendSpec) -> list[str]:
    shapes = {
        "codex": [
            "codex",
            "exec",
            "--model",
            "<model>",
            "--output-last-message",
            "<result-file>",
            "<prompt-via-stdin>",
        ],
        "claude": ["claude", "-p", "--model", "<model>", "<prompt-via-stdin>"],
        "gemini": ["gemini", "--model", "<model>", "-p", "<redacted-prompt>"],
        "agy": ["agy", "-p", "<redacted-prompt>", "--model", "<model>"],
        "opencode": ["opencode", "run", "--dir", "<workspace>", "--model", "<model>", "<redacted-prompt>"],
    }
    return shapes.get(spec.which_name, [spec.which_name, "<redacted-arguments>"])


def discover_installed_agents() -> list[dict[str, Any]]:
    """Discover distinct CLI backends registered in CLI_BACKENDS.

    Alias keys are collapsed into one canonical backend entry and reported with
    their alias list.
    """
    aliases_by_spec: dict[int, list[str]] = {}
    spec_by_id: dict[int, CliBackendSpec] = {}
    for backend_key, spec in CLI_BACKENDS.items():
        spec_id = id(spec)
        spec_by_id.setdefault(spec_id, spec)
        aliases_by_spec.setdefault(spec_id, []).append(backend_key)

    discovered: list[dict[str, Any]] = []
    canonical_by_spec: dict[int, str] = {}
    for spec_id, keys in aliases_by_spec.items():
        spec = spec_by_id[spec_id]
        canonical_by_spec[spec_id] = spec.which_name if spec.which_name in keys else spec.name

    for spec_id in sorted(aliases_by_spec, key=lambda value: canonical_by_spec[value]):
        spec = spec_by_id[spec_id]
        aliases = sorted(aliases_by_spec[spec_id])
        canonical = canonical_by_spec[spec_id]
        report = worker_backend_doctor(canonical)
        # Always query the installed agent. A failed probe must not masquerade
        # as a successful catalog using bundled or previously returned names.
        models: tuple[str, ...] = ()
        models_source = "unavailable" if spec.which_name in {"codex", "agy", "opencode"} else "unsupported"
        model_capabilities: dict[str, dict[str, Any]] = {}
        if spec.which_name == "claude" and report.get("available"):
            # Claude Code does not expose a stable non-interactive model-list
            # command. Its documented aliases and current full model names are
            # safe to offer once the CLI itself is present; dispatch still lets
            # Claude validate the selected model at runtime.
            models = spec.models
            models_source = "static"
        elif spec.which_name == "codex" and report.get("available"):
            live_models = _query_codex_models(str(report.get("command_path", "")))
            if live_models:
                models = tuple(live_models)
                model_capabilities = live_models
                models_source = "live"
        elif spec.which_name == "agy" and report.get("available"):
            live_models = _query_agy_models(str(report.get("command_path", "")))
            if live_models:
                models = live_models
                models_source = "live"
        elif spec.which_name == "opencode" and report.get("available"):
            live_models = _query_opencode_models(str(report.get("command_path", "")))
            if live_models:
                models = live_models
                models_source = "live"
        discovered.append(
            {
                "backend": canonical,
                "aliases": aliases,
                "available": bool(report.get("available", False)),
                "ready": bool(report.get("ready", False)),
                "command": str(report.get("command", "")),
                "command_path": str(report.get("command_path", "")),
                "hint": str(report.get("hint", "")),
                "models": list(dict.fromkeys(models)),
                "models_source": models_source,
                "model_default": spec.model_default,
                "model_capabilities": model_capabilities,
                "reasoning_depths": list(BACKEND_REASONING_DEPTHS.get(canonical, ())),
            }
        )
    pi_report = worker_backend_doctor("pi")
    discovered.append(
        {
            "backend": "pi",
            "aliases": ["pi", "pi-rpc"],
            "available": bool(pi_report.get("available")),
            "ready": bool(pi_report.get("ready")),
            "command": "pi",
            "command_path": str(pi_report.get("command_path") or ""),
            "hint": str(pi_report.get("hint") or ""),
            "models": [],
            "models_source": "unsupported",
            "model_default": "auto",
            "reasoning_depths": list(BACKEND_REASONING_DEPTHS["pi"]),
            "runtime_kind": "persistent_session",
            "capabilities": list(capabilities_dict()),
        }
    )
    return sorted(discovered, key=lambda item: str(item["backend"]))


def _generic_cli_hint(spec: CliBackendSpec, available: bool, auth_present: bool) -> str:
    if not available:
        return f"{spec.label} not found. Install `{spec.which_name}` or add it to PATH."
    if spec.auth_env and not auth_present:
        return f"No {' / '.join(spec.auth_env)} set for the {spec.name} worker."
    return f"Ready: {spec.label} detected."


def _codex_auth_hint(available: bool, auth_env: dict[str, bool], auth_file_present: bool) -> str:
    if not available:
        return "Codex CLI not found. Install `codex`, add it to PATH, or set GOODTEAM_CODEX_PATH."
    if auth_env.get("CODEX_API_KEY") and auth_file_present:
        return (
            "CODEX_API_KEY is set and can shadow ~/.codex/auth.json. If workers hit "
            "INVALID_API_KEY, retry with `env -u CODEX_API_KEY` so the auth.json session is used."
        )
    if not any(auth_env.values()) and not auth_file_present:
        return "No Codex credentials found. Run `codex login` or set OPENAI_API_KEY / CODEX_API_KEY."
    return "Ready: Codex CLI and credentials detected."


def _claude_auth_hint(available: bool, auth_present: bool) -> str:
    if not available:
        return "Claude Code CLI not found. Install `claude`, add it to PATH, or set GOODTEAM_CLAUDE_PATH."
    if not auth_present:
        return (
            "No ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN set. Workers will use your `claude login` "
            "session if you have one; otherwise run `claude login` or export ANTHROPIC_API_KEY."
        )
    return "Ready: Claude Code CLI and credentials detected."


def reconcile_stale_job(job: dict[str, Any], status_payload: dict[str, Any]) -> dict[str, Any]:
    reconciled = {**job, "raw_status": "running"}
    if _is_pid_alive(status_payload.get("pid")) is False:
        summary = "Worker heartbeat is stale and the process is no longer alive."
        return {
            **reconciled,
            "status": "failed",
            "failure_class": "stuck",
            "disposition": "abandoned",
            "summary": summary,
        }
    return {**reconciled, "status": "possibly_stuck"}


def _has_worker_runtime_paths(status_payload: dict[str, Any]) -> bool:
    return all(str(status_payload.get(key, "")).strip() for key in ("stdout_path", "stderr_path", "result_path"))


def _heartbeat_reference(status_payload: dict[str, Any]) -> str:
    return str(status_payload.get("heartbeat_at") or status_payload.get("updated_at") or "")


def _is_pid_alive(pid: object) -> bool | None:
    try:
        parsed_pid = int(pid)
    except (TypeError, ValueError):
        return None
    if parsed_pid <= 0:
        return None
    try:
        os.kill(parsed_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_stale_heartbeat(value: str) -> bool:
    if not value.strip():
        return True
    try:
        heartbeat = datetime.fromisoformat(value)
    except ValueError:
        return True
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    return (datetime.now(UTC) - heartbeat.astimezone(UTC)).total_seconds() > WORKER_STALE_AFTER_SECONDS


def _last_relevant_error(stderr: str) -> str:
    for line in reversed(stderr.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if "reconnecting..." in lowered:
            continue
        if "plugin" in lowered or "featured plugin" in lowered:
            continue
        if "error:" in lowered or "unexpected status" in lowered or "stream disconnected" in lowered:
            return stripped
    return ""
