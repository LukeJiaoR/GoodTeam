import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any
from uuid import uuid4

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.roles import role_definition, stable_role_prompt


DEFAULT_CODEX_PATH = "/opt/homebrew/bin/codex"
CODEX_AUTH_ENV_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")


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
        self._write_json(job_root / "role_pickup_packet.json", role_memory_store.build_pickup_packet(role))
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
            jobs.append(self._read_json(job_path))
        return jobs

    def run_job(self, job_id: str) -> dict[str, Any]:
        job_root = self.root / job_id
        job = self._read_json(job_root / "job.json")
        adapter = str(job["adapter"])
        if adapter == "noop":
            return self._complete_noop(job_root, job)
        if adapter == "human":
            return self._wait_for_human(job_root, job)
        if adapter == "codex":
            return self._run_codex(job_root, job)
        raise ValueError(f"Unsupported worker adapter: {adapter}")

    def _complete_noop(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        summary = "Completed by noop adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(f"# Worker Result\n\n{summary}\n", encoding="utf-8")
        return self._write_status(job_root, job, "completed", summary)

    def _wait_for_human(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        summary = "Fill result.md manually, then update status.json when ready."
        return self._write_status(job_root, job, "needs_review", summary)

    def _run_codex(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        codex = which("codex") or DEFAULT_CODEX_PATH
        model = "gpt-5.5"
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
        result_path = job_root / "result.md"
        stdout_path = job_root / "stdout.txt"
        stderr_path = job_root / "stderr.txt"
        prompt = self._codex_prompt(job_root, job)
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
            str(result_path),
            "-",
        ]
        started_at = self._now()
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        if not result_path.is_file():
            result_path.write_text(
                "# Worker Result\n\nCodex did not write a final result file. See stdout.txt and stderr.txt.\n",
                encoding="utf-8",
            )
        status = "completed" if completed.returncode == 0 else "failed"
        summary = self._codex_summary(completed.returncode, completed.stderr or "")
        return self._write_status(
            job_root,
            job,
            status,
            summary,
            extra={
                "command": command,
                "model": model,
                "exit_code": completed.returncode,
                "started_at": started_at,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "result_path": str(result_path),
                "preflight": preflight,
            },
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

    def _codex_summary(self, exit_code: int, stderr: str) -> str:
        if exit_code == 0:
            return "Codex CLI exited with code 0."
        normalized = stderr.lower()
        if "invalid_api_key" in normalized or "invalid api key" in normalized:
            return f"Codex CLI exited with code {exit_code}: invalid API key."
        if "401 unauthorized" in normalized:
            return f"Codex CLI exited with code {exit_code}: 401 Unauthorized."
        if "stream disconnected" in normalized:
            return f"Codex CLI exited with code {exit_code}: stream disconnected before response.completed."
        return f"Codex CLI exited with code {exit_code}."

    def _codex_prompt(self, job_root: Path, job: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                f"# GoodTeam Worker Job {job['id']}",
                f"Role: {job['role']}",
                "Use model gpt-5.5. Operate non-interactively and do not ask for human approval.",
                "Read the role prompt, task, context, constraints, and expected artifacts below.",
                "When modifying files, keep changes inside the current project root.",
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
        now = self._now()
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
        if extra:
            status_payload.update(extra)
        self._write_json(job_root / "job.json", job)
        self._write_json(job_root / "status.json", status_payload)
        return status_payload

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
