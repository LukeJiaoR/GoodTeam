import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2, which
from typing import Any
from uuid import uuid4

from goodteam_os.memory import RoleMemoryStore, default_embedder
from goodteam_os.roles import role_definition, stable_role_prompt


DEFAULT_CODEX_PATH = os.getenv("GOODTEAM_CODEX_PATH", "/opt/homebrew/bin/codex")
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
CODEX_AUTH_ENV_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
DEFAULT_CLAUDE_PATH = os.getenv("GOODTEAM_CLAUDE_PATH", "claude")
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_AUTH_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
DEFAULT_GEMINI_PATH = os.getenv("GOODTEAM_GEMINI_PATH", "gemini")
DEFAULT_GEMINI_MODEL = "auto"
DEFAULT_AGY_PATH = os.getenv("GOODTEAM_AGY_PATH", "agy")
DEFAULT_AGY_MODEL = "auto"
AGY_AUTH_ENV_NAMES = ()
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


def _query_codex_models(command_path: str) -> tuple[str, ...] | None:
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

    models = payload.get("models")
    if not isinstance(models, list):
        return None

    slugs: list[str] = []
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        if isinstance(slug, str) and slug:
            slugs.append(slug)
    return tuple(slugs) or None


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
    if model and model != DEFAULT_AGY_MODEL:
        command.extend(["--model", model])
    return command


def _command_with_prompt(command: list[str], prompt: str, *, prompt_via: str) -> list[str]:
    if prompt_via == "stdin":
        return list(command)
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
    models=(DEFAULT_CODEX_MODEL, "gpt-5.5", "gpt-5.3-codex"),
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
}


class WorkerJobStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.root = project_root / ".goodteam" / "workers" / "jobs"

    def create_job(
        self,
        *,
        run_id: str,
        role: str,
        adapter: str,
        model: str = "",
        task: str,
        context: str,
        constraints: list[str] | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
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
            "expected_artifacts": expected_artifacts or [],
        }
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
        (job_root / "context.md").write_text(context, encoding="utf-8")
        (job_root / "constraints.md").write_text("\n".join(f"- {item}" for item in constraints or []), encoding="utf-8")
        (job_root / "expected_artifacts.json").write_text(json.dumps(expected_artifacts or [], indent=2), encoding="utf-8")
        (job_root / "result.md").write_text("", encoding="utf-8")
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        jobs = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            job_path = path / "job.json"
            if not job_path.is_file():
                continue
            job = self._read_json(job_path)
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
                            "exit_code",
                            "failure_class",
                            "disposition",
                        }
                    },
                }
                if status_payload.get("status") == "running" and _is_stale_heartbeat(_heartbeat_reference(status_payload)):
                    job = reconcile_stale_job(job, status_payload)
            jobs.append(job)
        return jobs

    def run_job(self, job_id: str) -> dict[str, Any]:
        job_root = self.root / job_id
        job = self._read_json(job_root / "job.json")
        memory_citations = self._memory_citations_from_pickup_packet(job_root)
        adapter = str(job["adapter"])
        if adapter == "noop":
            status_payload = self._complete_noop(job_root, job)
        elif adapter == "fake":
            status_payload = self._complete_fake(job_root, job)
        elif adapter == "human":
            status_payload = self._wait_for_human(job_root, job)
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
        text = (
            f"Dispatched job {job['id']} (run {job['run_id']}, adapter={job['adapter']}, model={job['model']}) "
            f"for task: {task_excerpt}. Outcome: {status}{failure_segment}."
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
        summary = "Completed by noop adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(f"# Worker Result\n\n{summary}\n", encoding="utf-8")
        return self._write_status(job_root, job, "completed", summary)

    def _complete_fake(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        summary = "Completed by fake smoke adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(
            f"# Worker Result\n\nStatus: pass\n\n{summary}\n",
            encoding="utf-8",
        )
        return self._write_status(job_root, job, "completed", summary)

    def _wait_for_human(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
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
        binary = which(spec.which_name) or spec.default_path()
        raw = str(job.get("model", ""))
        bare = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()
        model = bare or spec.model_default
        result_path = job_root / "result.md"
        preflight = _cli_preflight(spec, binary, model)
        command = spec.build_command(binary, model, self.project_root, result_path)
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
            worker_env=self._worker_env(job_root, spec),
        )

    def _worker_env(self, job_root: Path, spec: "CliBackendSpec") -> dict[str, str] | None:
        if not spec.isolated_home:
            return None
        isolated_home = self._prepare_isolated_codex_home(job_root)
        return {**os.environ, "CODEX_HOME": str(isolated_home)}

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
        timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Run an agentic coding CLI (codex/claude) over the job protocol files.

        ``capture`` controls where the worker result comes from: ``output_file``
        means the CLI wrote it (codex ``--output-last-message``); ``stdout`` means
        the final message is printed to stdout (claude ``-p``)."""
        result_path = job_root / "result.md"
        stdout_path = job_root / "stdout.txt"
        stderr_path = job_root / "stderr.txt"
        prompt = self._worker_prompt(job_root, job)
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
        }
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        process: subprocess.Popen[str] | None = None
        stop_heartbeat = threading.Event()
        try:
            process = subprocess.Popen(
                process_command,
                cwd=self.project_root,
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
        returncode = int(process.returncode if process is not None and process.returncode is not None else 1)
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

    def _worker_prompt(self, job_root: Path, job: dict[str, Any]) -> str:
        model = _codex_model_name(str(job.get("model", "")))
        pickup_context = self._render_worker_pickup_context(job_root)
        return "\n\n".join(
            [
                f"# GoodTeam Worker Job {job['id']}",
                f"Role: {job['role']}",
                f"Use model {model}. Operate non-interactively and do not ask for human approval.",
                "Read the role prompt, task, context, constraints, and expected artifacts below.",
                "When modifying files, keep changes inside the current project root.",
                "Use bounded smoke checks. If you start dev servers, browser sessions, watchers, or other long-running processes, run them in a way you can stop and stop any dev servers, browser sessions, or watchers before exiting.",
                "At the end, summarize changed files, verification commands, and unresolved risks.",
                "## Role Prompt",
                (job_root / "role_prompt.md").read_text(encoding="utf-8"),
                "## Task",
                (job_root / "task.md").read_text(encoding="utf-8"),
                "## Context",
                (job_root / "context.md").read_text(encoding="utf-8"),
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
        if extra_payload:
            status_payload.update(extra_payload)
        self._write_json(job_root / "job.json", job)
        self._write_json(job_root / "status.json", status_payload)
        return status_payload

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()


def _timeout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _codex_model_name(value: str) -> str:
    model = value.strip() or DEFAULT_CODEX_MODEL
    if ":" in model:
        return model.split(":", 1)[1].strip() or DEFAULT_CODEX_MODEL
    return model


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
    spec = CLI_BACKENDS.get(mode)
    if spec is None:
        known = ", ".join(["fake", *sorted(CLI_BACKENDS), "noop", "human"])
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
        models = spec.models
        models_source = "static"
        if spec.which_name == "codex" and report.get("available"):
            live_models = _query_codex_models(str(report.get("command_path", "")))
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
            }
        )
    return discovered


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
        return {
            **reconciled,
            "status": "failed",
            "failure_class": "stuck",
            "disposition": "abandoned",
            "summary": str(status_payload.get("summary") or "Worker heartbeat is stale and the process is no longer alive."),
        }
    return {**reconciled, "status": "possibly_stuck"}


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
