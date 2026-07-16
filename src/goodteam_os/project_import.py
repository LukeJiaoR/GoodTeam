from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator

from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities
from goodteam_os.projects import build_registered_project_config, load_project_target
from goodteam_os.state import normalize_project_key


IMPORT_LOCK_NAME = ".project-import.lock"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class ProjectImportError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def import_project(
    workspace: Path,
    root_value: str,
    display_name: str = "",
    requested_key: str = "",
    existing_configs: list[Path] | None = None,
) -> dict[str, object]:
    root = Path(root_value).expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    with _workspace_import_lock(workspace):
        return _import_project_locked(
            workspace,
            root,
            display_name=display_name,
            requested_key=requested_key,
            existing_configs=existing_configs,
        )


def _import_project_locked(
    workspace: Path,
    root: Path,
    *,
    display_name: str,
    requested_key: str,
    existing_configs: list[Path] | None,
) -> dict[str, object]:
    projects_dir = workspace / "projects"
    config_paths = (
        list(existing_configs)
        if existing_configs is not None
        else sorted(projects_dir.glob("*.local.yaml"))
    )
    readiness_store = ProjectReadinessStore(workspace)
    registered = _load_registered_projects(config_paths)

    existing = _find_root_match(registered, root)
    if existing is not None:
        _validate_project_state_destination(workspace, root, str(existing["key"]))
        return _existing_result(readiness_store, existing, root)

    name = display_name.strip() or root.name
    base_key = normalize_project_key(requested_key or name)
    occupied_keys = {project["key"] for project in registered}
    suffix = 1
    while True:
        key = base_key if suffix == 1 else f"{base_key}-{suffix}"
        if key in occupied_keys:
            suffix += 1
            continue
        config_path = projects_dir / f"{key.lower()}.local.yaml"
        _validate_write_destination(workspace, root, config_path)
        _validate_project_state_destination(workspace, root, key)
        projects_dir.mkdir(parents=True, exist_ok=True)
        try:
            _publish_config_exclusively(
                config_path,
                build_registered_project_config(key, name, root),
            )
        except FileExistsError:
            registered = _load_registered_projects(sorted(projects_dir.glob("*.local.yaml")))
            existing = _find_root_match(registered, root)
            if existing is not None:
                _validate_project_state_destination(workspace, root, str(existing["key"]))
                return _existing_result(readiness_store, existing, root)
            occupied_keys.update(project["key"] for project in registered)
            suffix += 1
            continue

        readiness = readiness_store.initialize(key, root)
        return _result(
            created=True,
            key=key,
            name=name,
            root=root,
            config_path=config_path,
            readiness=readiness,
        )


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise ProjectImportError(
            "project_root_not_found",
            f"Project root does not exist: {root}",
            details={"root": str(root)},
        )
    if not root.is_dir():
        raise ProjectImportError(
            "project_root_not_directory",
            f"Project root is not a directory: {root}",
            details={"root": str(root)},
        )
    try:
        list(root.iterdir())
    except PermissionError as exc:
        raise ProjectImportError(
            "project_root_not_readable",
            f"Project root is not readable: {root}",
            details={"root": str(root)},
        ) from exc


def _validate_workspace_isolation(workspace: Path, root: Path) -> Path:
    canonical_workspace = Path(workspace).expanduser().resolve(strict=False)
    destinations = [
        canonical_workspace,
        canonical_workspace / "projects",
        canonical_workspace / "state",
        canonical_workspace / "state" / "projects",
        canonical_workspace / IMPORT_LOCK_NAME,
    ]
    for destination in destinations:
        _validate_write_destination(canonical_workspace, root, destination)
    return canonical_workspace


def _validate_project_state_destination(workspace: Path, root: Path, key: str) -> None:
    project_state_dir = workspace / "state" / "projects" / key
    _validate_write_destination(workspace, root, project_state_dir)
    _validate_write_destination(workspace, root, project_state_dir / "readiness.json")


def _validate_write_destination(workspace: Path, root: Path, destination: Path) -> None:
    canonical_destination = destination.resolve(strict=False)
    if not _path_is_within(canonical_destination, root):
        return
    raise ProjectImportError(
        "project_workspace_not_isolated",
        f"GoodTeam workspace write path overlaps the project root: {canonical_destination}",
        details={
            "root": str(root),
            "workspace": str(workspace),
            "destination": str(canonical_destination),
        },
    )


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _thread_lock_for(workspace: Path) -> threading.Lock:
    lock_key = str(workspace)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(lock_key, threading.Lock())


@contextmanager
def _workspace_import_lock(workspace: Path) -> Iterator[None]:
    with _thread_lock_for(workspace):
        workspace.mkdir(parents=True, exist_ok=True)
        lock_path = workspace / IMPORT_LOCK_NAME
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _publish_config_exclusively(destination: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_registered_projects(config_paths: list[Path]) -> list[dict[str, object]]:
    registered: list[dict[str, object]] = []
    for config_path in config_paths:
        target = load_project_target(config_path)
        registered.append(
            {
                "config_path": config_path,
                "key": normalize_project_key(str(target.get("key", config_path.stem))),
                "name": str(target.get("name", config_path.stem)),
                "root": _configured_root(config_path, target),
            }
        )
    return registered


def _find_root_match(
    registered: list[dict[str, object]],
    root: Path,
) -> dict[str, object] | None:
    return next((project for project in registered if project["root"] == root), None)


def _existing_result(
    readiness_store: ProjectReadinessStore,
    project: dict[str, object],
    root: Path,
) -> dict[str, object]:
    key = str(project["key"])
    readiness = readiness_store.read(key) or readiness_store.initialize(key, root)
    return _result(
        created=False,
        key=key,
        name=str(project["name"]),
        root=root,
        config_path=Path(project["config_path"]),
        readiness=readiness,
    )


def _configured_root(config_path: Path, target: dict[str, object]) -> Path | None:
    repos = target.get("repos")
    if not isinstance(repos, dict):
        return None
    root_value = repos.get("root") or repos.get("app")
    if not root_value:
        return None
    root = Path(str(root_value)).expanduser()
    if not root.is_absolute():
        root = config_path.parent / root
    return root.resolve()


def _result(
    *,
    created: bool,
    key: str,
    name: str,
    root: Path,
    config_path: Path,
    readiness: dict[str, object],
) -> dict[str, object]:
    project = {
        "project_key": key,
        "name": name,
        "root": str(root),
        "config_path": str(config_path),
    }
    return {
        "created": created,
        "project": project,
        "readiness": readiness,
        "capabilities": build_capabilities(readiness),
        "home_url": f"/projects/{key}/home",
    }
