from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from goodteam_os.state import normalize_project_key


LOGGER = logging.getLogger(__name__)
_REGISTRY_LOCK = threading.Lock()


def get_default_registry_path() -> Path:
    override = os.getenv("GOODTEAM_PROJECTS_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    goodteam_home = os.getenv("GOODTEAM_HOME")
    if goodteam_home:
        return Path(goodteam_home).expanduser().resolve() / "projects.json"
    return Path.home() / ".goodteam" / "projects.json"


class ProjectRegistry:
    def __init__(self, registry_path: Path | None = None):
        self.registry_path = (registry_path or get_default_registry_path()).resolve()

    def list_registered_projects(self) -> list[dict[str, Any]]:
        data = self._read_data()
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return []
        return [p for p in projects if isinstance(p, dict)]

    def list_registered_configs(self) -> list[Path]:
        configs: list[Path] = []
        for entry in self.list_registered_projects():
            config_path_str = str(entry.get("config_path", "")).strip()
            if not config_path_str:
                continue
            config_path = Path(config_path_str).expanduser().resolve()
            if config_path.is_file() and config_path not in configs:
                configs.append(config_path)
        return configs

    def register_project(
        self,
        config_path: Path,
        *,
        root: Path | None = None,
        key: str = "",
        state_path: Path | None = None,
    ) -> None:
        resolved_config = Path(config_path).expanduser().resolve()
        resolved_root = Path(root).expanduser().resolve() if root else None
        resolved_state = Path(state_path).expanduser().resolve() if state_path else None
        project_key = normalize_project_key(key) if key else ""

        if self.registry_path == get_default_registry_path():
            if not os.getenv("GOODTEAM_PROJECTS_REGISTRY") and not os.getenv("GOODTEAM_HOME"):
                path_str = str(resolved_config)
                if path_str.startswith(("/private/var/", "/tmp/", "/var/tmp/")):
                    return

        with _REGISTRY_LOCK:
            data = self._read_data()
            projects = list(data.get("projects", []))
            updated = False
            now_iso = datetime.now(UTC).isoformat()

            for entry in projects:
                if not isinstance(entry, dict):
                    continue
                entry_config = str(entry.get("config_path", "")).strip()
                entry_key = str(entry.get("key", "")).strip()
                match = False
                if entry_config and Path(entry_config).expanduser().resolve() == resolved_config:
                    match = True
                elif project_key and entry_key == project_key:
                    match = True

                if match:
                    if project_key:
                        entry["key"] = project_key
                    entry["config_path"] = str(resolved_config)
                    if resolved_root:
                        entry["root"] = str(resolved_root)
                    if resolved_state:
                        entry["state_path"] = str(resolved_state)
                    entry["updated_at"] = now_iso
                    updated = True
                    break

            if not updated:
                new_entry: dict[str, Any] = {
                    "key": project_key,
                    "config_path": str(resolved_config),
                    "updated_at": now_iso,
                }
                if resolved_root:
                    new_entry["root"] = str(resolved_root)
                if resolved_state:
                    new_entry["state_path"] = str(resolved_state)
                projects.append(new_entry)

            data["version"] = 1
            data["projects"] = projects
            self._write_data(data)

    def unregister_project(self, key_or_config_path: str | Path) -> bool:
        with _REGISTRY_LOCK:
            data = self._read_data()
            projects = list(data.get("projects", []))
            target_str = str(key_or_config_path).strip()
            target_path = Path(target_str).expanduser().resolve() if "/" in target_str or "\\" in target_str else None
            normalized_key = None
            try:
                normalized_key = normalize_project_key(target_str)
            except ValueError:
                normalized_key = None

            initial_count = len(projects)
            filtered = []
            for entry in projects:
                if not isinstance(entry, dict):
                    continue
                entry_key = str(entry.get("key", "")).strip()
                entry_config = str(entry.get("config_path", "")).strip()
                entry_path = Path(entry_config).expanduser().resolve() if entry_config else None

                if normalized_key and entry_key == normalized_key:
                    continue
                if target_path and entry_path == target_path:
                    continue
                filtered.append(entry)

            if len(filtered) != initial_count:
                data["projects"] = filtered
                self._write_data(data)
                return True
            return False

    def find_project(self, project_key: str) -> dict[str, Any] | None:
        key = normalize_project_key(project_key)
        for entry in self.list_registered_projects():
            if entry.get("key") == key:
                return entry
        return None

    def _read_data(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"version": 1, "projects": []}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("projects", [])
                return data
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not read project registry from %s: %s", self.registry_path, exc)
        return {"version": 1, "projects": []}

    def _write_data(self, data: dict[str, Any]) -> None:
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.registry_path.parent,
                prefix=f".{self.registry_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(data, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.registry_path)
        except OSError as exc:
            LOGGER.warning("Could not write project registry to %s: %s", self.registry_path, exc)
