from __future__ import annotations

from pathlib import Path

from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities
from goodteam_os.projects import build_registered_project_config, load_project_target
from goodteam_os.state import normalize_project_key


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
        return _existing_result(readiness_store, existing, root)

    name = display_name.strip() or root.name
    base_key = normalize_project_key(requested_key or name)
    projects_dir.mkdir(parents=True, exist_ok=True)
    occupied_keys = {project["key"] for project in registered}
    suffix = 1
    while True:
        key = base_key if suffix == 1 else f"{base_key}-{suffix}"
        if key in occupied_keys:
            suffix += 1
            continue
        config_path = projects_dir / f"{key.lower()}.local.yaml"
        try:
            with config_path.open("x", encoding="utf-8") as config_file:
                config_file.write(build_registered_project_config(key, name, root))
        except FileExistsError:
            registered = _load_registered_projects(sorted(projects_dir.glob("*.local.yaml")))
            existing = _find_root_match(registered, root)
            if existing is not None:
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
