"""Materialize one selected Skill from already acquired, immutable Git objects.

This offline primitive neither resolves refs nor acquires objects. Its caller
must enforce destination, transfer, process and storage limits BEFORE acquiring
objects and establish repository provenance. Object hashes prove content
integrity, not that a repository served those objects. No production route uses
this module until that acquisition boundary is implemented.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re

from ..models import PackageLimits, SkillError, SkillSource, resource_path
from ..package import PackageFile, SkillPackage, package_from_files


@dataclass(frozen=True)
class GitObject:
    kind: str
    data: bytes


@dataclass(frozen=True)
class GitCaptureLimits:
    max_objects: int = 2048
    max_tree_entries: int = 8192
    max_metadata_bytes: int = 4 * 1024 * 1024
    max_tree_bytes: int = 1024 * 1024
    max_commit_bytes: int = 64 * 1024
    max_subdirectory_depth: int = 16

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise SkillError("invalid_limits", "Git capture limits must be positive integers.")


class _Capture:
    def __init__(self, source, objects, limits, package_limits):
        self.objects = objects
        self.limits = limits
        self.package_limits = package_limits
        self.hash_name = "sha1" if len(source.revision) == 40 else "sha256"
        self.oid_bytes = len(source.revision) // 2
        self.object_count = 0
        self.tree_entries = 0
        self.metadata_bytes = 0
        self.content_bytes = 0
        self.files = []
        self.directories = []

    def read(self, oid: str, kind: str, maximum: int) -> bytes:
        self.object_count += 1
        if self.object_count > self.limits.max_objects:
            raise SkillError("git_capture_limit", "Git capture exceeds its object limit.")
        try:
            obj = self.objects[oid]
        except KeyError:
            raise SkillError("git_object_missing", "A fixed Git object is missing; acquire the source again.") from None
        if not isinstance(obj, GitObject) or obj.kind != kind or not isinstance(obj.data, bytes):
            raise SkillError("git_object_invalid", "Git object type does not match the fixed tree.")
        if len(obj.data) > maximum:
            raise SkillError("git_capture_limit", "Git object exceeds its capture byte limit.")
        header = f"{kind} {len(obj.data)}\0".encode("ascii")
        digest = hashlib.new(self.hash_name, header + obj.data).hexdigest()
        if digest != oid:
            raise SkillError("git_object_changed", "Git object bytes do not match their fixed identity.")
        if kind != "blob":
            self.metadata_bytes += len(obj.data)
            if self.metadata_bytes > self.limits.max_metadata_bytes:
                raise SkillError("git_capture_limit", "Git metadata exceeds its capture byte limit.")
        return obj.data

    def tree(self, oid):
        data = self.read(oid, "tree", min(
            self.limits.max_tree_bytes, self.limits.max_metadata_bytes - self.metadata_bytes,
        ))
        entries = []
        names = set()
        cursor = 0
        while cursor < len(data):
            self.tree_entries += 1
            if self.tree_entries > self.limits.max_tree_entries:
                raise SkillError("git_capture_limit", "Git tree scan exceeds its entry limit.")
            space = data.find(b" ", cursor)
            nul = data.find(b"\0", space + 1) if space >= 0 else -1
            end = nul + 1 + self.oid_bytes
            if space < 0 or nul < 0 or end > len(data):
                raise SkillError("git_tree_invalid", "Git tree has an incomplete entry.")
            mode = data[cursor:space]
            if mode not in {b"40000", b"100644", b"100755", b"120000", b"160000"}:
                raise SkillError("git_tree_invalid", "Git tree has an unsupported entry mode.")
            try:
                name = data[space + 1:nul].decode("utf-8")
            except UnicodeError:
                raise SkillError("git_tree_invalid", "Git resource names must be UTF-8.") from None
            resource_path(name)
            if "/" in name or name in names:
                raise SkillError("git_tree_invalid", "Git tree has a duplicate or invalid entry name.")
            names.add(name)
            entries.append((mode, name, data[nul + 1:end].hex()))
            cursor = end
        return entries

    def walk(self, oid, prefix="", depth=1):
        if depth > self.package_limits.max_depth:
            raise SkillError("package_limit", "Skill package paths are too deep.")
        for mode, name, child in self.tree(oid):
            relative = prefix + name
            resource_path(relative)
            if len(self.files) + len(self.directories) >= self.package_limits.max_entries:
                raise SkillError("package_limit", "Skill package contains too many resources.")
            if mode == b"40000":
                self.directories.append(relative)
                self.walk(child, relative + "/", depth + 1)
            elif mode in {b"120000", b"160000"}:
                raise SkillError("git_resource_unsupported", "Skill packages cannot contain symbolic links or submodules.")
            else:
                if len(self.files) >= self.package_limits.max_files:
                    raise SkillError("package_limit", "Skill package contains too many files.")
                maximum = min(self.package_limits.max_file_bytes,
                              self.package_limits.max_total_bytes - self.content_bytes)
                if relative == "SKILL.md":
                    maximum = min(maximum, self.package_limits.max_skill_bytes)
                data = self.read(child, "blob", maximum)
                if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise SkillError("git_lfs_unsupported", "An LFS resource is a pointer; its required content has not been acquired.")
                self.content_bytes += len(data)
                self.files.append(PackageFile(relative, data, mode == b"100755"))


def capture_git_package(
    source: SkillSource,
    objects: Mapping[str, GitObject],
    *,
    limits: GitCaptureLimits | None = None,
    package_limits: PackageLimits | None = None,
) -> SkillPackage:
    """Verify a resolved commit and materialize only its explicitly selected tree.

    Input values are decompressed Git object bodies, keyed by their complete
    SHA-1 or SHA-256 object IDs. Missing objects fail without fetching. Attributes
    are retained as ordinary bytes: no filters, export substitution or hooks run.
    """
    if not isinstance(source, SkillSource) or source.kind != "git":
        raise SkillError("invalid_source", "Git capture requires a resolved Git source identity.")
    limits = limits or GitCaptureLimits()
    package_limits = package_limits or PackageLimits()
    parts = source.subdirectory.split("/") if source.subdirectory else []
    if len(parts) > limits.max_subdirectory_depth:
        raise SkillError("git_capture_limit", "Selected Git subdirectory is too deep.")
    capture = _Capture(source, objects, limits, package_limits)
    commit = capture.read(source.revision, "commit", min(limits.max_commit_bytes, limits.max_metadata_bytes))
    first = commit.split(b"\n", 1)[0]
    if not re.fullmatch(rb"tree [0-9a-f]{" + str(len(source.revision)).encode() + rb"}", first) or b"\n\n" not in commit:
        raise SkillError("git_commit_invalid", "Git commit does not identify a complete root tree.")
    tree = first[5:].decode("ascii")
    for part in parts:
        match = next((entry for entry in capture.tree(tree) if entry[1] == part), None)
        if match is None or match[0] != b"40000":
            raise SkillError("git_subdirectory_missing", "Selected Skill subdirectory is not a fixed Git tree.")
        tree = match[2]
    capture.walk(tree)
    root_name = PurePosixPath(source.subdirectory).name if source.subdirectory else ""
    return package_from_files(capture.files, capture.directories, root_name=root_name, limits=package_limits)
