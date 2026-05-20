from pathlib import Path
from shutil import which

from goodteam_os.models import _parse_simple_yaml


ProjectTarget = dict[str, object]


def load_project_target(config_path: Path) -> ProjectTarget:
    return _parse_simple_yaml(config_path)


def check_project_target(target: ProjectTarget) -> dict[str, object]:
    repos = {
        name: {
            "path": path,
            "exists": Path(str(path)).is_dir(),
        }
        for name, path in dict(target.get("repos", {})).items()
    }
    workers = {}
    for role, command in dict(target.get("workers", {})).items():
        resolved = which(str(command))
        workers[role] = {
            "command": str(command),
            "available": resolved is not None,
            "path": resolved or "",
        }

    return {
        "project": {
            "name": target.get("name", ""),
            "key": target.get("key", ""),
            "plane_project_id": target.get("plane_project_id", ""),
        },
        "repos": repos,
        "workers": workers,
        "role_routes": target.get("role_routes", {}),
    }
