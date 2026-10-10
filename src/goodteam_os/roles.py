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


def workflow_steps_for_source(
    source_type: str,
    config_path: Path = DEFAULT_ROLE_CONFIG_PATH,
) -> list[dict[str, str]]:
    config = load_role_config(config_path)
    steps = deepcopy(config.get("workflow_steps", []))
    if source_type == "existing_project":
        takeover = deepcopy(config.get("takeover_workflow_step", {}))
        if takeover:
            return [takeover, *steps]
    return steps


def context_file_specs(config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> dict[str, list[dict[str, str]]]:
    return deepcopy(load_role_config(config_path).get("context_sources", {}))


def artifact_contract(
    artifact_key: str,
    config_path: Path = DEFAULT_ROLE_CONFIG_PATH,
) -> dict[str, Any]:
    contracts = load_role_config(config_path).get("artifact_contracts", {})
    if artifact_key not in contracts:
        raise ValueError(f"Unknown artifact contract: {artifact_key}")
    return deepcopy(contracts[artifact_key])


def approval_artifact_map(config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> dict[str, str]:
    return deepcopy(load_role_config(config_path).get("approval_artifacts", {}))


# Fix-profile gates reuse artifact files from the configured workflow. Keep
# this resolution shared by web review and notification adapters.
_FIX_APPROVAL_ARTIFACTS: dict[str, str] = {
    "fix_plan": "fix_plan",
    "code_review": "implementation_handoff",
    "qa": "qa_report",
}


def approval_artifact_for_gate(gate: str, config_path: Path = DEFAULT_ROLE_CONFIG_PATH) -> str:
    """Resolve configured gates first, then fix-profile overlays or the key."""
    base = approval_artifact_map(config_path)
    if gate in base:
        return base[gate]
    return _FIX_APPROVAL_ARTIFACTS.get(gate, gate)
