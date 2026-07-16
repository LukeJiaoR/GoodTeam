from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}
MODE_STEPS = {
    "new_project": ["new_project_init", "project_brief", "worker_preflight"],
    "existing_project": [
        "repository_understanding",
        "command_verification",
        "constraints_confirmation",
        "worker_preflight",
    ],
}


def classify_project_mode(root: Path) -> str:
    entries = [entry for entry in root.iterdir() if entry.name not in IGNORED_NAMES]
    visible = [entry for entry in entries if entry.name != ".git"]
    if not visible:
        return "new_project"
    if (root / ".git").exists():
        return "existing_project"
    for entry in visible:
        if entry.name in MANIFEST_NAMES or entry.suffix.lower() in SOURCE_SUFFIXES:
            return "existing_project"
        if entry.is_dir() and entry.name in {"src", "app", "frontend", "backend", "packages"}:
            return "existing_project"
    return "unconfirmed"


class ProjectReadinessStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def initialize(self, project_key: str, root: Path) -> dict[str, object]:
        project_mode = classify_project_mode(root)
        mode_confirmed = project_mode != "unconfirmed"
        readiness: dict[str, object] = {
            "schema_version": 1,
            "project_key": project_key,
            "canonical_root": str(root.resolve()),
            "project_mode": project_mode,
            "lifecycle": "preparing" if mode_confirmed else "attention_required",
            "steps": [
                {"id": "project_record", "status": "complete", "required": True},
                {"id": "safe_preflight", "status": "complete", "required": True},
                {
                    "id": "project_mode",
                    "status": "complete" if mode_confirmed else "needs_input",
                    "required": True,
                },
            ],
            "updated_at": _now(),
        }
        if mode_confirmed:
            readiness["steps"] = [
                *readiness["steps"],
                *_pending_steps(MODE_STEPS[project_mode]),
            ]
        self._write(project_key, readiness)
        return readiness

    def read(self, project_key: str) -> dict[str, object]:
        data = json.loads(self._path(project_key).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"readiness for {project_key!r} must be a JSON object")
        return data

    def confirm_mode(self, project_key: str, mode: str) -> dict[str, object]:
        if mode not in MODE_STEPS:
            raise ValueError("mode must be new_project or existing_project")

        readiness = self.read(project_key)
        mode_step_ids = {step_id for steps in MODE_STEPS.values() for step_id in steps}
        existing_steps = readiness.get("steps", [])
        base_steps = [
            dict(step)
            for step in existing_steps
            if isinstance(step, dict) and step.get("id") not in mode_step_ids
        ]
        for step in base_steps:
            if step.get("id") == "project_mode":
                step["status"] = "complete"

        readiness.update(
            {
                "project_mode": mode,
                "lifecycle": "preparing",
                "steps": [*base_steps, *_pending_steps(MODE_STEPS[mode])],
                "updated_at": _now(),
            }
        )
        self._write(project_key, readiness)
        return readiness

    def _path(self, project_key: str) -> Path:
        return self.workspace / "state" / "projects" / project_key / "readiness.json"

    def _write(self, project_key: str, readiness: dict[str, object]) -> None:
        path = self._path(project_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(readiness, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def build_capabilities(readiness: dict[str, object]) -> dict[str, object]:
    return {
        "inspect_project_facts": True,
        "edit_project_settings": True,
        "run_readiness_steps": True,
        "start_workflow": False,
        "start_workflow_reason": (
            "project_mode_unconfirmed"
            if readiness["project_mode"] == "unconfirmed"
            else "mode_specific_readiness_pending"
        ),
    }


def _pending_steps(step_ids: list[str]) -> list[dict[str, object]]:
    return [{"id": step_id, "status": "pending", "required": True} for step_id in step_ids]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
