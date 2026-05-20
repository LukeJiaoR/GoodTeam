import os
from pathlib import Path
from shutil import which

from goodteam_os.models import _parse_simple_yaml


ProjectTarget = dict[str, object]


def load_project_target(config_path: Path) -> ProjectTarget:
    return _parse_simple_yaml(config_path)


def _read_env_file(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env_value(name: str, env_file: str = "") -> str:
    if not name:
        return ""

    value = os.getenv(name)
    if value:
        return value
    if not env_file:
        return ""

    return _read_env_file(Path(env_file)).get(name, "")


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
    providers = {}
    for name, provider in dict(target.get("providers", {})).items():
        provider_data = dict(provider)
        api_key_env = str(provider_data.get("api_key_env", ""))
        env_file = str(provider_data.get("env_file", ""))
        providers[name] = {
            "type": str(provider_data.get("type", "")),
            "base_url": str(provider_data.get("base_url", "")),
            "api_key_env": api_key_env,
            "env_file": env_file,
            "configured": bool(_env_value(api_key_env, env_file)),
        }

    return {
        "project": {
            "name": target.get("name", ""),
            "key": target.get("key", ""),
            "plane_project_id": target.get("plane_project_id", ""),
        },
        "repos": repos,
        "workers": workers,
        "providers": providers,
        "role_routes": target.get("role_routes", {}),
    }
