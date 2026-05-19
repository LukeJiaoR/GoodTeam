from copy import deepcopy
from pathlib import Path


ModelProfile = dict[str, dict[str, str]]


def load_model_profile(config_path: Path, profile_name: str) -> ModelProfile:
    config = _parse_simple_yaml(config_path)
    profiles = config.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown model profile '{profile_name}'. Available profiles: {available}")
    return deepcopy(profiles[profile_name])


def apply_role_overrides(profile: ModelProfile, overrides: list[str]) -> ModelProfile:
    updated = deepcopy(profile)
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"Role override must use role=provider:model format: {override}")
        role, model = override.split("=", 1)
        role = role.strip()
        model = model.strip()
        if not role or not model:
            raise ValueError(f"Role override must use role=provider:model format: {override}")
        if role not in updated:
            updated[role] = {}
        updated[role]["primary"] = model
    return updated


def _parse_simple_yaml(path: Path) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped:
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if value:
            parent[key] = value
        else:
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))

    return root
