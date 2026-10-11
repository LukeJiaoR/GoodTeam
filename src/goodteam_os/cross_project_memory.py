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
    """Compatibility list API; callers needing completeness use retrieve_report."""
    return retrieve_report(workspace, role_key, query, scope=scope, k=k)["results"]


def retrieve_report(
    workspace: Path,
    role_key: str,
    query: str,
    *,
    scope: list[str],
    k: int = 5,
) -> dict[str, object]:
    """Read the explicit scope without disguising unreadable sources as no hits.

    Coverage concerns this bounded retrieval only, not all project context or
    whether a Worker used any result. Reasons never include source exceptions,
    private paths, or information about projects outside the requested scope.
    """
    if not scope:
        raise ValueError("scope is required.")

    limit = max(0, int(k))
    hits: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    seen: set[str] = set()
    for requested_key in scope:
        try:
            project_key = normalize_project_key(requested_key)
        except ValueError:
            project_key = requested_key
        if project_key in seen:
            continue
        seen.add(project_key)
        source: dict[str, object] = {"project_key": project_key}
        sources.append(source)
        if limit == 0 or not query.strip():
            source.update(status="not_searched", reason="not_requested")
            continue

        # Unknown, unreadable and changed configurations deliberately share one
        # safe category; this endpoint does not enumerate project existence.
        source.update(status="unavailable", reason="source_unavailable")
        try:
            project_config = _project_config_for_key(workspace, project_key)
            if project_config is None:
                continue
            target = load_project_target(project_config)
            if normalize_project_key(str(target.get("key", ""))) != project_key:
                continue
            runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
        except Exception:
            continue

        if runtime_root is None:
            continue

        try:
            results = RoleMemoryStore(runtime_root).retrieve_relevant_events(
                role_key,
                query,
                top_k=limit,
            )
        except Exception:
            source["reason"] = "memory_unreadable"
            continue

        source.update(status="searched", match_count=len(results))
        source.pop("reason", None)
        for result in results:
            mem_id = str(result.get("id", ""))
            if not mem_id:
                continue
            tagged = dict(result)
            tagged["mem_id"] = mem_id
            tagged["id"] = f"{project_key}:{mem_id}"
            tagged["project_key"] = project_key
            hits.append(tagged)

    searched = sum(source["status"] == "searched" for source in sources)
    status = ("complete" if searched == len(sources) else "partial" if searched
              else "not_searched" if all(source["status"] == "not_searched" for source in sources)
              else "unavailable")
    return {
        "results": sorted(hits, key=lambda item: float(item.get("score", 0.0)), reverse=True)[:limit],
        "coverage": {"status": status, "sources": sources},
    }


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
