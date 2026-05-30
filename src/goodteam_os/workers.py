import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any
from uuid import uuid4

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.roles import role_definition, stable_role_prompt


DEFAULT_CODEX_PATH = os.getenv("GOODTEAM_CODEX_PATH", "/opt/homebrew/bin/codex")
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
CODEX_AUTH_ENV_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
DEFAULT_CLAUDE_PATH = os.getenv("GOODTEAM_CLAUDE_PATH", "claude")
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_AUTH_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
BUILTIN_WORKER_MODES = ("fake", "noop", "human")
WORKER_HEARTBEAT_INTERVAL_SECONDS = 10
WORKER_STALE_AFTER_SECONDS = 120


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
        (job_root / "role_prompt.md").write_text(stable_role_prompt(role), encoding="utf-8")
        role_config = role_definition(role)
        role_memory_store = RoleMemoryStore(self.project_root)
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
                        }
                    },
                }
                if status_payload.get("status") == "running" and _is_stale_heartbeat(str(status_payload.get("heartbeat_at", ""))):
                    job["raw_status"] = "running"
                    job["status"] = "possibly_stuck"
            jobs.append(job)
        return jobs

    def run_job(self, job_id: str) -> dict[str, Any]:
        job_root = self.root / job_id
        job = self._read_json(job_root / "job.json")
        adapter = str(job["adapter"])
        if adapter == "noop":
            return self._complete_noop(job_root, job)
        if adapter == "fake":
            return self._complete_fake(job_root, job)
        if adapter == "human":
            return self._wait_for_human(job_root, job)
        if adapter == "codex":
            return self._run_codex(job_root, job)
        if adapter in ("anthropic", "claude"):
            return self._run_claude(job_root, job)
        raise ValueError(f"Unsupported worker adapter: {adapter}")

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

    def _run_codex(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        codex = which("codex") or DEFAULT_CODEX_PATH
        model = _codex_model_name(str(job.get("model", "")))
        preflight = self._codex_preflight(codex=codex, model=model)
        if not preflight["codex_available"]:
            summary = "Codex CLI is not available. Install codex or add it to PATH before running codex workers."
            return self._write_status(
                job_root,
                job,
                "failed",
                summary,
                extra={
                    "command": [codex, "exec", "--model", model],
                    "model": model,
                    "exit_code": None,
                    "preflight": preflight,
                },
            )
        command = [
            codex,
            "exec",
            "--model",
            model,
            "--skip-git-repo-check",
            "--cd",
            str(self.project_root),
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message",
            str(job_root / "result.md"),
            "-",
        ]
        return self._run_cli_worker(
            job_root,
            job,
            command=command,
            model=model,
            preflight=preflight,
            label="Codex CLI",
            capture="output_file",
            summarize=self._codex_summary,
        )

    def _run_claude(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        claude = which("claude") or DEFAULT_CLAUDE_PATH
        model = _claude_model_name(str(job.get("model", "")))
        preflight = self._claude_preflight(claude=claude, model=model)
        if not preflight["claude_available"]:
            summary = (
                "Claude Code CLI is not available. Install `claude` or add it to PATH "
                "before running anthropic workers."
            )
            return self._write_status(
                job_root,
                job,
                "failed",
                summary,
                extra={
                    "command": [claude, "-p", "--model", model],
                    "model": model,
                    "exit_code": None,
                    "preflight": preflight,
                },
            )
        command = [claude, "-p", "--model", model, "--dangerously-skip-permissions"]
        return self._run_cli_worker(
            job_root,
            job,
            command=command,
            model=model,
            preflight=preflight,
            label="Claude Code CLI",
            capture="stdout",
            summarize=self._claude_summary,
        )

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
        started_at = self._now()
        running_summary = f"{label} is running."
        base_extra = {
            "command": command,
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
                command,
                cwd=self.project_root,
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
            stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
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

    def _codex_preflight(self, *, codex: str, model: str) -> dict[str, Any]:
        executable = Path(codex)
        return {
            "codex_command": codex,
            "codex_available": bool(which("codex") or executable.is_file()),
            "model": model,
            "auth_env": {name: bool(os.getenv(name)) for name in CODEX_AUTH_ENV_NAMES},
            "codex_home": os.getenv("CODEX_HOME", ""),
            "http_proxy": bool(os.getenv("http_proxy") or os.getenv("HTTP_PROXY")),
            "https_proxy": bool(os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")),
        }

    def _claude_preflight(self, *, claude: str, model: str) -> dict[str, Any]:
        executable = Path(claude)
        return {
            "claude_command": claude,
            "claude_available": bool(which("claude") or executable.is_file()),
            "model": model,
            "auth_env": {name: bool(os.getenv(name)) for name in CLAUDE_AUTH_ENV_NAMES},
            "http_proxy": bool(os.getenv("http_proxy") or os.getenv("HTTP_PROXY")),
            "https_proxy": bool(os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")),
        }

    def _claude_summary(self, exit_code: int, stderr: str) -> str:
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

    def _codex_summary(self, exit_code: int, stderr: str) -> str:
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

    def _worker_prompt(self, job_root: Path, job: dict[str, Any]) -> str:
        model = _codex_model_name(str(job.get("model", "")))
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
            ]
        )

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


def _claude_model_name(value: str) -> str:
    model = value.strip() or DEFAULT_CLAUDE_MODEL
    if ":" in model:
        return model.split(":", 1)[1].strip() or DEFAULT_CLAUDE_MODEL
    return model


def worker_backend_doctor(mode: str) -> dict[str, Any]:
    """Report whether the configured worker backend can actually run, so a
    user sees missing CLIs or credentials at doctor/init time instead of after
    a worker fails repeatedly. Never echoes secret values, only their presence."""
    mode = (mode or "").strip() or "codex"
    if mode in BUILTIN_WORKER_MODES:
        return {
            "mode": mode,
            "kind": "builtin",
            "ready": True,
            "hint": f"The `{mode}` worker runs locally and needs no CLI or credentials.",
        }
    if mode == "codex":
        command = which("codex") or DEFAULT_CODEX_PATH
        available = bool(which("codex") or Path(command).is_file())
        auth_env = {name: bool(os.getenv(name)) for name in CODEX_AUTH_ENV_NAMES}
        codex_home = os.getenv("CODEX_HOME", "") or os.path.expanduser("~/.codex")
        auth_file = Path(codex_home) / "auth.json"
        auth_file_present = auth_file.is_file()
        auth_present = any(auth_env.values()) or auth_file_present
        return {
            "mode": mode,
            "kind": "cli",
            "command": "codex",
            "command_path": command if available else "",
            "available": available,
            "auth_env": auth_env,
            "auth_file": str(auth_file),
            "auth_file_present": auth_file_present,
            "auth_present": auth_present,
            "ready": available and auth_present,
            "hint": _codex_auth_hint(available, auth_env, auth_file_present),
        }
    if mode in ("anthropic", "claude"):
        command = which("claude") or DEFAULT_CLAUDE_PATH
        available = bool(which("claude") or Path(command).is_file())
        auth_env = {name: bool(os.getenv(name)) for name in CLAUDE_AUTH_ENV_NAMES}
        auth_present = any(auth_env.values())
        # The claude CLI can also run from a `claude login` session we can't
        # detect here, so the binary being present is the hard requirement.
        return {
            "mode": mode,
            "kind": "cli",
            "command": "claude",
            "command_path": command if available else "",
            "available": available,
            "auth_env": auth_env,
            "auth_present": auth_present,
            "ready": available,
            "hint": _claude_auth_hint(available, auth_present),
        }
    return {
        "mode": mode,
        "kind": "unknown",
        "ready": False,
        "hint": f"Unknown worker_backend.mode '{mode}'. Use one of: fake, codex, anthropic, noop, human.",
    }


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
