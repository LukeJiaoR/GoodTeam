"""Capture standard Skill packages as bounded, immutable values, without execution."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from .filesystem import directory, regular_file
from .models import PackageLimits, SkillDiagnostic, SkillError, canonical_json, content_digest, resource_path

MANIFEST_SCHEMA = "skill_package_manifest.v1"
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
EXECUTION_FIELDS = {"allowed-tools", "hooks", "context", "agent", "model", "disable-model-invocation", "user-invocable"}


class _BoundedLoader(yaml.SafeLoader):
    """Safe constructors plus bounded composition, no aliases/merges or duplicate keys."""

    def __init__(self, text: str):
        self._node_count = 0
        self._depth = 0
        super().__init__(text)

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent) or getattr(self.peek_event(), "anchor", None) is not None:
            raise SkillError("yaml_alias", "Aliases and anchors are not supported in Skill metadata.")
        self._node_count += 1
        self._depth += 1
        try:
            if self._node_count > 4096 or self._depth > 32:
                raise SkillError("metadata_limit", "Skill metadata is too deeply nested or complex.")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise SkillError("invalid_metadata", "Skill metadata mappings must be objects.")
        result = {}
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise SkillError("yaml_merge", "YAML merge keys are not supported.")
            key = self.construct_object(key_node, deep=deep)
            if (not isinstance(key, str) or not key or len(key) > 256
                    or any(ord(char) < 32 or ord(char) == 127 for char in key)):
                raise SkillError("invalid_metadata", "Skill metadata keys must be bounded, non-empty strings.")
            if key in result:
                raise SkillError("duplicate_metadata_key", "Duplicate Skill metadata key.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _json_value(value: Any) -> None:
    if value is None or type(value) in {str, int, bool, float}:
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise SkillError("invalid_metadata", "Only JSON-compatible metadata values are supported.")


def parse_skill(data: bytes, *, root_name: str = "", limits: PackageLimits | None = None):
    limits = limits or PackageLimits()
    if len(data) > limits.max_skill_bytes:
        raise SkillError("package_limit", "SKILL.md exceeds its size limit.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise SkillError("invalid_encoding", "SKILL.md must contain UTF-8 text.") from exc
    match = re.fullmatch(r"---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)", text, re.DOTALL)
    if match is None:
        raise SkillError("invalid_frontmatter", "SKILL.md requires delimited YAML frontmatter.")
    header, body = match.groups()
    if len(header.encode()) > limits.max_frontmatter_bytes:
        raise SkillError("metadata_limit", "Skill frontmatter exceeds its size limit.")
    loader = _BoundedLoader(header)
    try:
        metadata = loader.get_single_data()
        if not isinstance(metadata, dict):
            raise SkillError("invalid_metadata", "Skill frontmatter must be an object.")
        _json_value(metadata)
        encoded = canonical_json(metadata)
        encoded.encode("utf-8")
    except (yaml.YAMLError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        if isinstance(exc, SkillError):
            raise
        raise SkillError("invalid_metadata", "Skill YAML could not be safely parsed.") from exc
    finally:
        loader.dispose()
    name, description = metadata.get("name"), metadata.get("description")
    if (not isinstance(name, str) or not name.strip() or len(name) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise SkillError("invalid_metadata", "A non-empty Skill name is required.")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillError("invalid_metadata", "A non-empty Skill description of at most 1024 characters is required.")
    diagnostics = []
    valid_name = (len(name) <= 64 and not name.startswith("-") and not name.endswith("-")
                  and "--" not in name and all(char == "-" or (char.isalnum() and char == char.lower()) for char in name))
    if not valid_name:
        diagnostics.append(SkillDiagnostic("nonstandard_name", "The display name does not meet standard Skill naming rules."))
    if root_name and name != root_name:
        diagnostics.append(SkillDiagnostic("directory_name_mismatch", "The source directory name differs from the declared Skill name."))
    if data.startswith(b"\xef\xbb\xbf"):
        diagnostics.append(SkillDiagnostic("utf8_bom", "UTF-8 BOM retained in original package bytes."))
    if not body.strip():
        diagnostics.append(SkillDiagnostic("empty_instructions", "The package has metadata but no method instructions."))
    if "license" in metadata and not isinstance(metadata["license"], str):
        diagnostics.append(SkillDiagnostic("nonstandard_license", "License metadata is not a standard string; no license was inferred."))
    compatibility = metadata.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str) or not 1 <= len(compatibility) <= 500):
        diagnostics.append(SkillDiagnostic("nonstandard_compatibility", "Compatibility metadata does not match the standard string field."))
    nested = metadata.get("metadata")
    if nested is not None and (not isinstance(nested, dict) or not all(isinstance(item, str) for item in nested.values())):
        diagnostics.append(SkillDiagnostic("nonstandard_metadata", "The metadata field is not a string-to-string map."))
    for key in sorted(metadata):
        if key in EXECUTION_FIELDS:
            diagnostics.append(SkillDiagnostic("runtime_semantics_unverified", "Runtime-specific field requires adapter validation: " + key))
        elif key not in STANDARD_FIELDS:
            diagnostics.append(SkillDiagnostic("extension_preserved", "Unknown metadata retained without execution: " + key))
    if re.search(r"!`[^`]+`", body):
        diagnostics.append(SkillDiagnostic("dynamic_command_unverified", "Client-specific dynamic command syntax was retained, not evaluated."))
    return encoded, tuple(diagnostics)


@dataclass(frozen=True)
class PackageFile:
    path: str
    data: bytes
    executable: bool = False

    def __post_init__(self) -> None:
        resource_path(self.path)
        if not isinstance(self.data, bytes) or type(self.executable) is not bool:
            raise SkillError("invalid_package", "Package files require immutable bytes and a boolean executable bit.")

    def manifest_entry(self) -> dict[str, Any]:
        return {"path": self.path, "size": len(self.data), "executable": self.executable,
                "digest": content_digest(self.data)}


@dataclass(frozen=True)
class SkillPackage:
    files: tuple[PackageFile, ...]
    directories: tuple[str, ...]
    metadata_json: str
    diagnostics: tuple[SkillDiagnostic, ...]
    root_name: str = ""

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)

    @property
    def manifest(self) -> dict[str, Any]:
        return {"schema_version": MANIFEST_SCHEMA, "directories": list(self.directories),
                "files": [item.manifest_entry() for item in self.files]}

    @property
    def digest(self) -> str:
        return content_digest(canonical_json(self.manifest).encode())

    def resource(self, relative: str) -> bytes:
        resource_path(relative)
        for item in self.files:
            if item.path == relative:
                return item.data
        raise SkillError("resource_missing", "The requested resource is not in this fixed package.")


def package_from_files(files, directories=(), *, root_name="", limits: PackageLimits | None = None) -> SkillPackage:
    """Validate captured bytes; source paths are not reopened when publishing."""
    limits = limits or PackageLimits()
    files = tuple(sorted(files, key=lambda item: item.path))
    directories = tuple(sorted(directories))
    if len(files) > limits.max_files or len(files) + len(directories) > limits.max_entries:
        raise SkillError("package_limit", "Skill package contains too many resources.")
    identities = set()
    for relative in (*directories, *(item.path for item in files)):
        resource_path(relative)
        if len(relative.split("/")) > limits.max_depth:
            raise SkillError("package_limit", "Skill package paths are too deep.")
        normalized = unicodedata.normalize("NFC", relative).casefold()
        if normalized in identities:
            raise SkillError("path_collision", "Package paths collide on supported filesystems.")
        identities.add(normalized)
        for parent in PurePosixPath(relative).parents:
            if str(parent) != "." and str(parent) not in directories:
                raise SkillError("invalid_package", "Package parent directory is missing.")
    if any(len(item.data) > limits.max_file_bytes for item in files) or sum(len(item.data) for item in files) > limits.max_total_bytes:
        raise SkillError("package_limit", "Skill package exceeds its byte limit.")
    main = next((item for item in files if item.path == "SKILL.md"), None)
    if main is None:
        raise SkillError("skill_missing", "Package root must contain SKILL.md.")
    metadata, diagnostics = parse_skill(main.data, root_name=root_name, limits=limits)
    return SkillPackage(files, directories, metadata, diagnostics, root_name)


def capture_directory_fd(root_fd: int, *, root_name="", limits: PackageLimits | None = None) -> SkillPackage:
    limits = limits or PackageLimits()
    files, directories = [], []
    total = 0
    entries = 0

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal total, entries
        before = os.fstat(fd)
        with os.scandir(fd) as listing:
            for entry in listing:
                entries += 1
                if entries > limits.max_entries or depth > limits.max_depth:
                    raise SkillError("package_limit", "Skill package has too many or too deeply nested resources.")
                relative = prefix + entry.name
                resource_path(relative)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        directories.append(relative)
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if len(files) >= limits.max_files:
                        raise SkillError("package_limit", "Skill package has too many files.")
                    maximum = min(limits.max_file_bytes, limits.max_total_bytes - total)
                    data, captured = regular_file(fd, entry.name, maximum=maximum)
                    if captured.st_dev != info.st_dev or captured.st_ino != info.st_ino:
                        raise SkillError("source_changed", "Skill resources changed during capture.")
                    total += len(data)
                    files.append(PackageFile(relative, data, bool(captured.st_mode & 0o111)))
                else:
                    raise SkillError("unsafe_file", "Links and special files are not accepted as Skill resources.")
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise SkillError("source_changed", "Skill directory entries changed during capture.")

    try:
        walk(root_fd, "", 1)
    except OSError as exc:
        raise SkillError("unsafe_file", "A Skill resource could not be safely captured.") from exc
    return package_from_files(files, directories, root_name=root_name, limits=limits)


def capture_package(root: Path, *, limits: PackageLimits | None = None) -> SkillPackage:
    """Caller explicitly authorizes an absolute root; no scanning, install or model calls."""
    with directory(root) as fd:
        return capture_directory_fd(fd, root_name=Path(root).name, limits=limits)
