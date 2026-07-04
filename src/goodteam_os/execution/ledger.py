from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

from goodteam_os.execution.idempotency import execution_id_from_task
from goodteam_os.execution.models import ExecutionRun, ExecutionTask, LedgerEvent


_JsonModelT = TypeVar("_JsonModelT", ExecutionTask, ExecutionRun)


class ExecutionLedger:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.executions_root = self.project_root / ".goodteam" / "executions"

    def create_task(self, task: ExecutionTask) -> Path:
        execution_id = execution_id_from_task(task.task_id)
        path = self._execution_dir(execution_id) / "task.json"
        self._write_json(path, task.to_json())
        return path

    def create_run(self, run: ExecutionRun) -> Path:
        path = self._execution_dir(run.execution_id) / "run.json"
        self._write_json(path, run.to_json())
        return path

    def append_event(self, event: LedgerEvent) -> Path:
        path = self._execution_dir(event.execution_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{event.to_json()}\n".encode("utf-8")
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            self._write_all(fd, payload)
        finally:
            os.close(fd)
        return path

    def read_events(self, execution_id: str) -> list[LedgerEvent]:
        path = self._execution_dir(execution_id) / "events.jsonl"
        if not path.exists():
            return []

        events: list[LedgerEvent] = []
        with path.open(encoding="utf-8") as event_file:
            for line_number, line in enumerate(event_file, start=1):
                events.append(self._parse_event_line(path, line_number, line))
        return events

    def read_task(self, execution_id: str) -> ExecutionTask | None:
        return self._read_json_model(self._execution_dir(execution_id) / "task.json", ExecutionTask)

    def read_run(self, execution_id: str) -> ExecutionRun | None:
        return self._read_json_model(self._execution_dir(execution_id) / "run.json", ExecutionRun)

    def replay(self, execution_id: str) -> dict[str, Any]:
        return {
            "execution_id": execution_id,
            "task": self.read_task(execution_id),
            "run": self.read_run(execution_id),
            "events": self.read_events(execution_id),
        }

    def _execution_dir(self, execution_id: str) -> Path:
        return self.executions_root / execution_id

    @staticmethod
    def _write_json(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    @staticmethod
    def _parse_event_line(path: Path, line_number: int, line: str) -> LedgerEvent:
        try:
            return LedgerEvent.from_json(line)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Malformed ledger event at {path}: line {line_number}: {exc}") from exc

    @staticmethod
    def _read_json_model(path: Path, model_type: type[_JsonModelT]) -> _JsonModelT | None:
        if not path.exists():
            return None
        return model_type.from_json(path.read_text(encoding="utf-8"))
