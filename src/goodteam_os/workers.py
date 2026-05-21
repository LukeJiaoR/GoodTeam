import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.roles import role_definition, stable_role_prompt


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
        raise ValueError(f"Unsupported worker adapter: {adapter}")

    def _complete_noop(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        summary = "Completed by noop adapter. No external CLI was invoked."
        (job_root / "result.md").write_text(f"# Worker Result\n\n{summary}\n", encoding="utf-8")
        return self._write_status(job_root, job, "completed", summary)

    def _wait_for_human(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
        summary = "Fill result.md manually, then update status.json when ready."
        return self._write_status(job_root, job, "needs_review", summary)

    def _write_status(self, job_root: Path, job: dict[str, Any], status: str, summary: str) -> dict[str, Any]:
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
        self._write_json(job_root / "job.json", job)
        self._write_json(job_root / "status.json", status_payload)
        return status_payload

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
