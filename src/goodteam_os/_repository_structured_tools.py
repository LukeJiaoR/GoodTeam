from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath

from goodteam_os._repository_content_policy import (
    FILE_READ_MAX_BYTES,
    hash_bytes,
    reject_sensitive_path,
)
from goodteam_os.repository_access import (
    AnchoredRepository,
    RepositoryAccessError,
    canonical_relative_path,
)
from goodteam_os.repository_context_provider import ContextProviderError
from goodteam_os.repository_snapshot import RepositorySnapshot


_MANIFEST_NAMES = frozenset({"package.json", "pyproject.toml", "Cargo.toml", "go.mod"})
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?")
_TEST_FILE = re.compile(r"(?:^test_.*\.py$|.*_test\.py$|.*\.(?:test|spec)\.[^.]+$)")
_TEST_DIRECTORIES = frozenset({"tests", "test", "__tests__"})
_MAX_STRUCTURED_FILES = 50


def inspect_manifest(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
) -> dict[str, object]:
    path = _validate_manifest_request(request)
    reject_sensitive_path(path)
    name = PurePosixPath(path).name
    if name not in _MANIFEST_NAMES:
        raise ContextProviderError(
            "context_tool_unsupported",
            "Repository context tool is unsupported.",
        )
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        text = repository.read_text(path, max_bytes=FILE_READ_MAX_BYTES)
    if text.truncated:
        return {"status": "invalid", "observations": [], "truncated": True}
    raw = text.text.encode("utf-8")
    try:
        observation = _parse_manifest(name, text.text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError, TypeError):
        return {"status": "invalid", "observations": [], "truncated": False}
    observation = {
        "kind": name,
        "path": path,
        "content_hash": hash_bytes(raw),
        **observation,
    }
    if _string_chars(observation) > result_char_allowance:
        return {"observations": [], "truncated": True}
    return {"observations": [observation], "truncated": False}


def inspect_tests(
    *,
    root,
    snapshot: RepositorySnapshot,
    request: dict[str, object],
    result_char_allowance: int,
) -> dict[str, object]:
    path, max_files = _validate_tests_request(request)
    reject_sensitive_path(path)
    scan_limit = max(max_files * 20, max_files + 1)
    with AnchoredRepository(root, expected_identity=snapshot.root_identity) as repository:
        entries = repository.walk(path, max_entries=scan_limit + 1)
    observations: list[dict[str, object]] = []
    returned_chars = 0
    truncated = len(entries) > scan_limit
    for entry in entries[:scan_limit]:
        if entry.kind != "file" or not _is_test_candidate(entry.path):
            continue
        try:
            reject_sensitive_path(entry.path)
        except RepositoryAccessError:
            truncated = True
            continue
        observation = {
            "kind": "test_candidate",
            "path": entry.path,
            "classification": "test_candidate",
            "basis": "path_convention",
        }
        next_chars = returned_chars + _string_chars(observation)
        if next_chars > result_char_allowance:
            truncated = True
            break
        observations.append(observation)
        returned_chars = next_chars
        if len(observations) >= max_files:
            truncated = any(
                candidate.kind == "file" and _is_test_candidate(candidate.path)
                for candidate in entries[entries.index(entry) + 1 : scan_limit]
            ) or truncated
            break
    return {"observations": observations, "truncated": truncated}


def _validate_manifest_request(request: dict[str, object]) -> str:
    if set(request) != {"path"} or not isinstance(request.get("path"), str):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    return canonical_relative_path(request["path"])


def _validate_tests_request(request: dict[str, object]) -> tuple[str, int]:
    if set(request) - {"path", "max_files"}:
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    path = request.get("path", "")
    max_files = request.get("max_files", 20)
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    if (
        isinstance(max_files, bool)
        or not isinstance(max_files, int)
        or max_files <= 0
        or max_files > _MAX_STRUCTURED_FILES
    ):
        raise ContextProviderError(
            "context_tool_limit_exceeded",
            "Repository context request exceeded a structured-tool limit.",
            details={"limit": "max_files"},
        )
    return canonical_relative_path(path, allow_root=True), max_files


def _parse_manifest(name: str, text: str) -> dict[str, object]:
    if name == "package.json":
        value = json.loads(text)
        if type(value) is not dict:
            raise ValueError("manifest root must be an object")
        return _parse_package_json(value)
    if name == "go.mod":
        return _parse_go_mod(text)
    value = tomllib.loads(text)
    if name == "pyproject.toml":
        return _parse_pyproject(value)
    return _parse_cargo(value)


def _parse_package_json(value: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    _copy_string(value, "name", result, "name")
    _copy_string(value, "packageManager", result, "package_manager")
    scripts = value.get("scripts")
    if type(scripts) is dict:
        result["scripts"] = {
            str(key): item
            for key, item in sorted(scripts.items())
            if isinstance(key, str) and isinstance(item, str)
        }
    for source, target in (
        ("dependencies", "dependencies"),
        ("devDependencies", "dev_dependencies"),
        ("optionalDependencies", "optional_dependencies"),
        ("peerDependencies", "peer_dependencies"),
    ):
        names = _mapping_names(value.get(source))
        if names:
            result[target] = names
    workspaces = value.get("workspaces")
    if type(workspaces) is dict:
        workspaces = workspaces.get("packages")
    names = _string_list(workspaces)
    if names:
        result["workspace_names"] = names
    return result


def _parse_pyproject(value: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    project = value.get("project")
    if type(project) is dict:
        _copy_string(project, "name", result, "name")
        dependencies = _python_dependency_names(project.get("dependencies"))
        if dependencies:
            result["dependencies"] = dependencies
        optional = project.get("optional-dependencies")
        if type(optional) is dict:
            result["feature_names"] = sorted(str(key) for key in optional if isinstance(key, str))
    build = value.get("build-system")
    if type(build) is dict:
        _copy_string(build, "build-backend", result, "build_backend")
        dependencies = _python_dependency_names(build.get("requires"))
        if dependencies:
            result["build_dependencies"] = dependencies
    tools = value.get("tool")
    if type(tools) is dict:
        result["tool_names"] = sorted(str(key) for key in tools if isinstance(key, str))
    return result


def _parse_cargo(value: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    package = value.get("package")
    if type(package) is dict:
        _copy_string(package, "name", result, "name")
    for source, target in (
        ("dependencies", "dependencies"),
        ("dev-dependencies", "dev_dependencies"),
        ("build-dependencies", "build_dependencies"),
    ):
        names = _mapping_names(value.get(source))
        if names:
            result[target] = names
    features = _mapping_names(value.get("features"))
    if features:
        result["feature_names"] = features
    workspace = value.get("workspace")
    if type(workspace) is dict:
        names = _string_list(workspace.get("members"))
        if names:
            result["workspace_names"] = names
    targets: list[str] = []
    library = value.get("lib")
    if type(library) is dict and isinstance(library.get("name"), str):
        targets.append(library["name"])
    binaries = value.get("bin")
    if isinstance(binaries, list):
        targets.extend(
            item["name"]
            for item in binaries
            if type(item) is dict and isinstance(item.get("name"), str)
        )
    if targets:
        result["target_names"] = sorted(set(targets))
    return result


def _parse_go_mod(text: str) -> dict[str, object]:
    result: dict[str, object] = {}
    dependencies: set[str] = set()
    in_require_block = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if fields[:1] == ["module"] and len(fields) == 2 and "module" not in result:
            result["module"] = fields[1]
        elif fields[:1] == ["go"] and len(fields) == 2 and "go_version" not in result:
            result["go_version"] = fields[1]
        elif fields == ["require", "("]:
            in_require_block = True
        elif line == ")" and in_require_block:
            in_require_block = False
        elif fields[:1] == ["require"] and len(fields) >= 3:
            dependencies.add(fields[1])
        elif in_require_block and len(fields) >= 2 and "=>" not in fields:
            dependencies.add(fields[0])
    if "module" not in result:
        raise ValueError("go.mod has no module directive")
    if dependencies:
        result["dependencies"] = sorted(dependencies)
    return result


def _copy_string(
    source: dict[str, object], source_key: str, target: dict[str, object], target_key: str
) -> None:
    value = source.get(source_key)
    if isinstance(value, str):
        target[target_key] = value


def _mapping_names(value: object) -> list[str]:
    if type(value) is not dict:
        return []
    return sorted(str(key) for key in value if isinstance(key, str))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(item for item in value if isinstance(item, str))


def _python_dependency_names(value: object) -> list[str]:
    names: set[str] = set()
    for item in _string_list(value):
        match = _DEPENDENCY_NAME.match(item.strip())
        if match is not None:
            names.add(match.group(0).split("[", 1)[0])
    return sorted(names)


def _is_test_candidate(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(_TEST_DIRECTORIES.intersection(candidate.parts[:-1])) or bool(
        _TEST_FILE.fullmatch(candidate.name)
    )


def _string_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_string_chars(item) for item in value)
    if type(value) is dict:
        return sum(_string_chars(item) for item in value.values())
    return 0
