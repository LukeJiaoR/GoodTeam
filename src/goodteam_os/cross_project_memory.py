from pathlib import Path

from goodteam_os.graph import resolve_project_runtime_root
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.projects import load_project_target
from goodteam_os.state import normalize_project_key


def retrieve(
    workspace: Path,
    role_key: str,
    query: str,
    *,
    scope: list[str],
    k: int = 5,
) -> list[dict[str, object]]:
    if not scope:
        raise ValueError("scope is required.")

    limit = max(0, int(k))
    if limit == 0:
        return []

    hits: list[dict[str, object]] = []
    for requested_key in scope:
        project_config = _project_config_for_key(workspace, requested_key)
        if project_config is None:
            continue

        runtime_root = _runtime_root_for_config(project_config)
        if runtime_root is None:
            continue

        try:
            target = load_project_target(project_config)
            project_key = normalize_project_key(str(target.get("key", requested_key)))
            results = RoleMemoryStore(runtime_root).retrieve_relevant_events(
                role_key,
                query,
                top_k=limit,
            )
        except Exception:
            continue

        for result in results:
            mem_id = str(result.get("id", ""))
            if not mem_id:
                continue
            tagged = dict(result)
            tagged["mem_id"] = mem_id
            tagged["id"] = f"{project_key}:{mem_id}"
            tagged["project_key"] = project_key
            hits.append(tagged)

    return sorted(hits, key=lambda item: float(item.get("score", 0.0)), reverse=True)[:limit]


def _project_config_for_key(workspace: Path, project_key: str) -> Path | None:
    try:
        key = normalize_project_key(project_key)
    except ValueError:
        return None

    projects_dir = workspace / "projects"
    if not projects_dir.is_dir():
        return None

    for config_path in sorted(projects_dir.glob("*.local.yaml"), key=lambda path: path.name.lower()):
        try:
            target = load_project_target(config_path)
            target_key = normalize_project_key(str(target.get("key", config_path.stem)))
        except Exception:
            continue
        if target_key == key:
            return config_path
    return None


def _runtime_root_for_config(config_path: Path) -> Path | None:
    try:
        target = load_project_target(config_path)
        return resolve_project_runtime_root(dict(target.get("repos", {})))
    except Exception:
        return None
