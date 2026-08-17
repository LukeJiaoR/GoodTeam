import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TASK_STATUSES = {"todo", "in_progress", "blocked", "review", "done", "canceled"}
TASK_BOARD_COLUMNS = ["backlog", "doing", "blocked", "done"]
TASK_BOARD_STATUS_MAP = {
    "todo": "backlog",
    "review": "backlog",
    "in_progress": "doing",
    "blocked": "blocked",
    "done": "done",
}

STAGE_TASKS = (
    {
        "node": "product_requirement",
        "title": "Draft and approve PRD",
        "role": "product_requirement",
    },
    {
        "node": "architect",
        "title": "Design RFC & Security Review",
        "role": "architect",
    },
    {
        "node": "implementation",
        "title": "Implement approved design",
        "role": "implementation",
    },
    {
        "node": "qa",
        "title": "Verify implementation",
        "role": "qa",
    },
    {
        "node": "release",
        "title": "Approve and complete release",
        "role": "release",
    },
)


class LocalTaskStore:
    def __init__(self, project_root: Path, project_key: str = ""):
        self.project_root = project_root
        self.project_key = str(project_key or "").strip()
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
        external_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        ledger = self._read()
        tasks = list(ledger.get("tasks", []))
        now = self._now()
        prefix = str(self.project_key or ledger.get("prefix", "GT")).strip().upper()
        next_id = int(ledger.get("next_id", 1))
        existing_ids = {str(t.get("id", "")) for t in tasks}
        while f"{prefix}-{next_id}" in existing_ids:
            next_id += 1
        task_id = f"{prefix}-{next_id}"
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "role": role,
            "status": "todo",
            "priority": priority,
            "dependencies": dependencies or [],
            "acceptance_criteria": acceptance_criteria or [],
            "artifact_refs": artifact_refs or [],
            "external_refs": external_refs or [],
            "created_at": now,
            "updated_at": now,
        }
        tasks.append(task)
        ledger["tasks"] = tasks
        ledger["next_id"] = next_id + 1
        self._write(ledger)
        return task

    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        ledger = self._read()
        tasks = list(ledger.get("tasks", []))
        new_id = str(updates.get("id", task_id)).strip()
        if new_id and new_id != task_id:
            # Collision guard: do not overwrite an existing different task
            if any(t.get("id") == new_id for t in tasks if t.get("id") != task_id):
                updates.pop("id", None)
                new_id = task_id
            else:
                # Synchronize dependencies across all tasks
                for t in tasks:
                    if "dependencies" in t and isinstance(t["dependencies"], list):
                        t["dependencies"] = [
                            new_id if dep == task_id else dep
                            for dep in t["dependencies"]
                        ]
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
            return {"tasks": [], "next_id": 1}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"tasks": [], "next_id": 1}
        if not isinstance(payload, dict):
            return {"tasks": [], "next_id": 1}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()


def sync_stage_tasks(state: Any, store: LocalTaskStore) -> list[dict[str, Any]]:
    statuses = _stage_statuses(state)
    existing = store.list_tasks()
    tasks: list[dict[str, Any]] = []
    for index, stage in enumerate(STAGE_TASKS, start=1):
        node = str(stage["node"])
        external_ref = f"stage:{node}"
        task = next(
            (
                item
                for item in existing
                if external_ref in [str(ref) for ref in item.get("external_refs", [])]
            ),
            None,
        )
        prefix = str(store.project_key or "GT").strip().upper()
        expected_id = f"{prefix}-{index}"
        if task is None:
            task = store.create_task(
                title=str(stage["title"]),
                description=f"Workflow stage task for {node}.",
                role=str(stage["role"]),
                external_refs=[external_ref],
            )
            existing.append(task)
        updates: dict[str, Any] = {
            "title": str(stage["title"]),
            "status": statuses[node],
        }
        if store.project_key and not str(task.get("id", "")).startswith(f"{prefix}-"):
            updates["id"] = expected_id
        artifact_ref = _stage_artifact_ref(state, node)
        updates["artifact_refs"] = [artifact_ref] if artifact_ref else []
        task = store.update_task(str(task["id"]), **updates)
        tasks.append(task)
    return tasks


def _stage_statuses(state: Any) -> dict[str, str]:
    approvals = {
        str(key): str(value)
        for key, value in dict(getattr(state, "approvals", {}) or {}).items()
    }
    artifacts = dict(getattr(state, "artifacts", {}) or {})
    current_node = str(getattr(state, "current_node", "") or "")
    next_node = str(getattr(state, "next_node", "") or "")

    statuses = {str(stage["node"]): "todo" for stage in STAGE_TASKS}
    if approvals.get("takeover") != "approved" and (
        current_node == "takeover_review_gate" or next_node in {"await_takeover_approval", "revise_takeover"}
    ):
        return statuses
    if approvals.get("prd") != "approved":
        statuses["product_requirement"] = "review" if artifacts.get("prd_proposed") else "in_progress"
        return statuses
    statuses["product_requirement"] = "done"

    if approvals.get("rfc") != "approved" or approvals.get("security") != "approved":
        statuses["architect"] = "in_progress"
        return statuses
    statuses["architect"] = "done"

    implementation_active = (
        current_node in {"planner", "ui_design", "implementation", "code_review", "implementation_workers"}
        or next_node in {"planner", "ui_design", "implementation", "code_review", "blocked"}
        or (
            approvals.get("staging") != "approved"
            and current_node not in {"staging_review_gate", "release_review_gate", "complete"}
            and next_node not in {"await_staging_approval", "release", "await_release_approval", "retro_memory"}
        )
    )
    if implementation_active:
        statuses["implementation"] = "in_progress"
        return statuses
    statuses["implementation"] = "done"

    release_reached = current_node in {"release_review_gate", "complete"} or next_node in {
        "release",
        "await_release_approval",
        "retro_memory",
    }
    if approvals.get("staging") != "approved" and not release_reached:
        statuses["qa"] = "in_progress"
        return statuses
    statuses["qa"] = "done"

    if approvals.get("release") == "approved" or current_node == "complete":
        statuses["release"] = "done"
    else:
        statuses["release"] = "in_progress"
    return statuses


def _stage_artifact_ref(state: Any, node: str) -> str:
    artifact_keys = {
        "product_requirement": "prd",
        "architect": "rfc",
        "implementation": "implementation_handoff",
        "qa": "qa_report",
        "release": "release_notes",
    }
    return str(dict(getattr(state, "artifacts", {}) or {}).get(artifact_keys[node], "") or "")


def build_task_board(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    board: dict[str, list[dict[str, Any]]] = {column: [] for column in TASK_BOARD_COLUMNS}
    for task in tasks:
        column = TASK_BOARD_STATUS_MAP.get(str(task.get("status", "")))
        if column is None:
            continue
        board[column].append(task)
    return board


def serialize_task_board(board: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    columns = {
        column: [_serialize_task(task) for task in board.get(column, [])]
        for column in TASK_BOARD_COLUMNS
    }
    return {
        "columns": columns,
        "counts": {column: len(items) for column, items in columns.items()},
    }


def _serialize_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(task.get("id", "")),
        "title": str(task.get("title", "")),
        "description": str(task.get("description", "")),
        "role": str(task.get("role", "")),
        "status": str(task.get("status", "")),
        "priority": str(task.get("priority", "")),
        "dependencies": list(task.get("dependencies", [])),
        "acceptance_criteria": list(task.get("acceptance_criteria", [])),
        "artifact_refs": list(task.get("artifact_refs", [])),
        "external_refs": list(task.get("external_refs", [])),
        "created_at": str(task.get("created_at", "")),
        "updated_at": str(task.get("updated_at", "")),
    }
