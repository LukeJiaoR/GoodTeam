import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TASK_STATUSES = {"todo", "in_progress", "blocked", "review", "done", "canceled"}


class LocalTaskStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.path = project_root / ".goodteam" / "tasks" / "tasks.json"

    def list_tasks(self) -> list[dict[str, Any]]:
        return list(self._read().get("tasks", []))

    def get_task(self, task_id: str) -> dict[str, Any]:
        for task in self.list_tasks():
            if task["id"] == task_id:
                return task
        raise ValueError(f"Unknown task id: {task_id}")

    def create_task(
        self,
        *,
        title: str,
        description: str,
        role: str,
        acceptance_criteria: list[str] | None = None,
        priority: str = "medium",
        dependencies: list[str] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        ledger = self._read()
        tasks = list(ledger.get("tasks", []))
        now = self._now()
        task = {
            "id": f"GT-{ledger.get('next_id', 1)}",
            "title": title,
            "description": description,
            "role": role,
            "status": "todo",
            "priority": priority,
            "dependencies": dependencies or [],
            "acceptance_criteria": acceptance_criteria or [],
            "artifact_refs": artifact_refs or [],
            "external_refs": [],
            "created_at": now,
            "updated_at": now,
        }
        tasks.append(task)
        ledger["tasks"] = tasks
        ledger["next_id"] = int(ledger.get("next_id", 1)) + 1
        self._write(ledger)
        return task

    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        ledger = self._read()
        tasks = list(ledger.get("tasks", []))
        for index, task in enumerate(tasks):
            if task["id"] != task_id:
                continue
            if "status" in updates and updates["status"] not in TASK_STATUSES:
                raise ValueError(f"Unknown task status: {updates['status']}")
            updated = {**task, **updates, "updated_at": self._now()}
            tasks[index] = updated
            ledger["tasks"] = tasks
            self._write(ledger)
            return updated
        raise ValueError(f"Unknown task id: {task_id}")

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "next_id": 1, "tasks": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, ledger: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
