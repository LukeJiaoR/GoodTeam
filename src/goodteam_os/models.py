import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


ModelProfile = dict[str, dict[str, str]]

# Only pure model-profile parsing is reused. Always read the current file first;
# filesystem metadata, project Team state, Skill locks and authorization are not
# cache keys or cached results. Keep retained source versions and size bounded.
_MODEL_CONFIG_CACHE_SIZE = 8
_MAX_CACHED_MODEL_CONFIG_CHARS = 131_072


def _read_model_config(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    if len(source) > _MAX_CACHED_MODEL_CONFIG_CHARS:
        return _parse_yaml_source(source)
    return _cached_model_config(source)


@lru_cache(maxsize=_MODEL_CONFIG_CACHE_SIZE)
def _cached_model_config(source: str) -> dict[str, object]:
    # Private read-only parse tree; public model accessors return selected copies
    # or new key lists so caller overrides cannot mutate another task's models.
    return _parse_yaml_source(source)


def list_model_profiles(config_path: Path) -> list[str]:
    config = _read_model_config(config_path)
    return sorted(dict(config.get("profiles", {})).keys())


def load_model_profile(config_path: Path, profile_name: str) -> ModelProfile:
    config = _read_model_config(config_path)
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


def resolve_project_model_profile(
    config_path: Path,
    project_target: dict[str, object],
    *,
    explicit_profile: str = "",
    role_overrides: list[str] | None = None,
) -> tuple[str, ModelProfile]:
    profile_name = explicit_profile.strip() or str(project_target.get("model_profile", "") or "balanced")
    profile = load_model_profile(config_path, profile_name)
    project_overrides = _project_role_overrides(project_target.get("role_model_overrides", {}))
    updated = apply_role_overrides(profile, project_overrides)
    updated = apply_role_overrides(updated, role_overrides or [])
    return profile_name, updated


def _project_role_overrides(value: object) -> list[str]:
    if isinstance(value, dict):
        return [f"{role}={model}" for role, model in value.items() if str(role).strip() and str(model).strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


YamlLine = tuple[int, str, str]


def _parse_simple_yaml(path: Path) -> dict[str, object]:
    return _parse_yaml_source(path.read_text(encoding="utf-8"))


def _parse_yaml_source(source: str) -> dict[str, object]:
    lines = _yaml_source_lines(source)
    if not lines:
        return {}
    parsed, index = _parse_yaml_block(lines, 0)
    if index != len(lines):
        raise ValueError(f"Unsupported YAML line: {lines[index][2]}")
    if not isinstance(parsed, dict):
        raise ValueError("Unsupported YAML document: root must be a mapping")
    return parsed


def _yaml_source_lines(source: str) -> list[YamlLine]:
    lines: list[YamlLine] = []
    for raw_line in source.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line.strip(), raw_line))
    return lines


def _parse_yaml_block(lines: list[YamlLine], index: int) -> tuple[object, int]:
    indent, stripped, _raw_line = lines[index]
    if stripped.startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_mapping(lines, index, indent)


def _parse_yaml_mapping(lines: list[YamlLine], index: int, indent: int) -> tuple[dict[str, object], int]:
    parsed: dict[str, object] = {}
    while index < len(lines):
        line_indent, stripped, raw_line = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent or stripped.startswith("- "):
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        if ":" not in stripped:
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        index += 1
        if value:
            parsed[key] = _parse_yaml_scalar(value)
            continue
        if index < len(lines) and lines[index][0] > line_indent:
            parsed[key], index = _parse_yaml_block(lines, index)
        else:
            parsed[key] = {}
    return parsed, index


def _parse_yaml_list(lines: list[YamlLine], index: int, indent: int) -> tuple[list[object], int]:
    parsed: list[object] = []
    while index < len(lines):
        line_indent, stripped, raw_line = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent or not stripped.startswith("- "):
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        value = stripped[2:].strip()
        index += 1
        if value:
            parsed.append(_parse_yaml_scalar(value))
        elif index < len(lines) and lines[index][0] > line_indent:
            child, index = _parse_yaml_block(lines, index)
            parsed.append(child)
        else:
            parsed.append("")
    return parsed, index


def _parse_yaml_scalar(value: str) -> object:
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
    return _unquote(value)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(decoded, str):
            return decoded
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value
