import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_ROLE_CONFIG_PATH = Path(__file__).with_name("role_prompts.json")


def load_role_config(config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def list_role_definitions(config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> list[dict[str, str]]:
    roles = load_role_config(config_path).get("roles", [])
    return deepcopy(roles)


def role_definition(role_key: str, config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> dict[str, str]:
    for role in list_role_definitions(config_path):
        if role.get("key") == role_key:
            return role
    raise ValueError(f"Unknown role: {role_key}")


def stable_role_prompt(role_key: str, config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> str:
    role = role_definition(role_key, config_path)
    lines = [
        f"# Role Prompt: {role['name']}",
        "",
        role["stable_prompt"],
        "",
        "## Role Boundary",
        "",
        f"- Key: `{role['key']}`",
        f"- Default artifact: `{role['artifact']}`",
        f"- Responsibility: {role['description']}",
        f"- Next action rule: {role['next_action']}",
        "",
        "Concrete task instructions belong in task.md. Project evidence belongs in context.md.",
    ]
    return "\n".join(lines) + "\n"


def context_file_specs(config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> dict[str, list[dict[str, str]]]:
    return deepcopy(load_role_config(config_path).get("context_sources", {}))
