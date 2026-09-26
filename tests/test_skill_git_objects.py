"""Offline Git object fixtures, including an actual Git-created repository."""
from dataclasses import replace
import hashlib
import os
import subprocess

import pytest

from goodteam_os.skills.models import PackageLimits, SkillError, SkillScope, SkillSource
from goodteam_os.skills.package import capture_package
from goodteam_os.skills.sources.git import GitCaptureLimits, GitObject, capture_git_package
from goodteam_os.skills.store import SkillStore

MAIN = b"---\nname: review\ndescription: Check changed contracts.\n---\nCite evidence.\n"
URL = "https://example.test/methods.git"


class Objects(dict):
    def __init__(self, algorithm="sha1"):
        super().__init__()
        self.algorithm = algorithm
        self.reads = []

    def add(self, kind, data):
        oid = hashlib.new(self.algorithm, f"{kind} {len(data)}\0".encode() + data).hexdigest()
        self[oid] = GitObject(kind, data)
        return oid

    def tree(self, entries):
        return self.add("tree", b"".join(
            mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid)
            for mode, name, oid in entries
        ))

    def commit(self, tree):
        return self.add("commit", f"tree {tree}\nauthor Example <example@example.test> 0 +0000\n"
                        f"committer Example <example@example.test> 0 +0000\n\nFixed input.\n".encode())

    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)


def fixture(algorithm="sha1", extras=()):
    objects = Objects(algorithm)
    main = objects.add("blob", MAIN)
    tree = objects.tree([("100644", "SKILL.md", main), *[
        (mode, name, objects.add("blob", data)) for mode, name, data in extras
    ]])
    root = objects.tree([("40000", "review", tree), ("160000", "unselected-module", "0" * len(main))])
    source = SkillSource("git", URL, "review", objects.commit(root))
    return objects, source, main


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_selected_commit_verifies_both_object_formats_and_ignores_unselected_module(algorithm):
    objects, source, _ = fixture(algorithm)
    package = capture_git_package(source, objects)
    assert package.resource("SKILL.md") == MAIN
    assert package.root_name == "review"
    assert len(package.files) == 1
    assert "0" * len(source.revision) not in objects.reads


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_real_git_objects_preserve_attributes_scripts_and_match_local_package(tmp_path, algorithm):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    env = {"PATH": os.defpath, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}

    def git(*args, data=None):
        return subprocess.run(["git", *args], cwd=repo, env=env, input=data,
                              capture_output=True, check=True, timeout=10).stdout

    git("init", "--bare", "--template=", f"--object-format={algorithm}", ".")
    entries = []
    objects = {}
    local = tmp_path / "review"
    local.mkdir()
    resources = [("SKILL.md", MAIN, False), (".gitattributes", b"SKILL.md export-ignore\n", False),
                 ("LICENSE", b"Example license\n", False),
                 ("script.sh", b"#!/bin/sh\ntouch MUST_NOT_EXIST\n", True)]
    for name, data, executable in resources:
        oid = git("hash-object", "-w", "--stdin", data=data).decode().strip()
        objects[oid] = GitObject("blob", git("cat-file", "blob", oid))
        entries.append(f"{'100755' if executable else '100644'} blob {oid}\t{name}\n")
        (local / name).write_bytes(data)
        (local / name).chmod(0o700 if executable else 0o600)
    tree = git("mktree", data="".join(entries).encode()).decode().strip()
    objects[tree] = GitObject("tree", git("cat-file", "tree", tree))
    commit = git("-c", "user.name=Example", "-c", "user.email=example@example.test",
                 "commit-tree", tree, data=b"Offline fixture\n").decode().strip()
    objects[commit] = GitObject("commit", git("cat-file", "commit", commit))
    package = capture_git_package(SkillSource("git", URL, revision=commit), objects)
    assert package.manifest == capture_package(local).manifest
    assert package.digest == capture_package(local).digest
    assert package.resource("SKILL.md") == MAIN
    assert package.resource(".gitattributes") == b"SKILL.md export-ignore\n"
    assert not (repo / "MUST_NOT_EXIST").exists()
    store = SkillStore(tmp_path / "store", SkillScope("user", "operator"))
    published = store.publish(package, SkillSource("git", URL, revision=commit), command_key="fixed-git")
    assert store.read_resource(published.installed.skill_id, published.version.version_id, "SKILL.md") == MAIN


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_changed_or_missing_object_fails_without_partial_package(kind):
    objects, source, _ = fixture()
    oid = next(key for key, value in objects.items() if value.kind == kind)
    original = objects[oid]
    objects[oid] = replace(original, data=original.data + b"changed")
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == "git_object_changed"
    del objects[oid]
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == "git_object_missing"


@pytest.mark.parametrize("mode,content,code", [
    ("120000", b"/private/secret", "git_resource_unsupported"),
    ("160000", b"submodule", "git_resource_unsupported"),
    ("100644", b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n", "git_lfs_unsupported"),
])
def test_unacquired_or_unsafe_resources_are_not_silently_omitted(mode, content, code):
    objects, source, _ = fixture(extras=[(mode, "resource", content)])
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == code


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/b", "a\\b", ".", ".."])
def test_invalid_tree_names_are_rejected(name):
    objects, source, _ = fixture(extras=[("100644", name, b"data")])
    with pytest.raises(SkillError):
        capture_git_package(source, objects)


@pytest.mark.parametrize("data", [b"100644 name\0short", b"invalid", b"100600 name\0" + b"x" * 20])
def test_malformed_trees_are_rejected_even_with_valid_object_hash(data):
    objects = Objects()
    commit = objects.commit(objects.add("tree", data))
    with pytest.raises(SkillError) as error:
        capture_git_package(SkillSource("git", URL, revision=commit), objects)
    assert error.value.code == "git_tree_invalid"


@pytest.mark.parametrize("limits", [GitCaptureLimits(max_objects=2), GitCaptureLimits(max_tree_entries=1),
                                    GitCaptureLimits(max_metadata_bytes=5), GitCaptureLimits(max_tree_bytes=5)])
def test_metadata_budget_stops_before_reading_blobs(limits):
    objects, source, blob = fixture()
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects, limits=limits)
    assert error.value.code == "git_capture_limit"
    assert blob not in objects.reads


@pytest.mark.parametrize("limits", [PackageLimits(max_files=1), PackageLimits(max_entries=1),
                                    PackageLimits(max_total_bytes=len(MAIN)), PackageLimits(max_file_bytes=5)])
def test_package_limits_apply_during_materialization(limits):
    objects, source, _ = fixture(extras=[("100644", "extra", b"data")])
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects, package_limits=limits)
    assert error.value.code in {"git_capture_limit", "package_limit"}


def test_new_commit_does_not_change_original_capture():
    objects, source, _ = fixture()
    original = capture_git_package(source, objects)
    changed = objects.add("blob", MAIN + b"New instructions.\n")
    tree = objects.tree([("100644", "SKILL.md", changed)])
    new_source = SkillSource("git", URL, revision=objects.commit(tree))
    assert capture_git_package(new_source, objects).digest != original.digest
    assert capture_git_package(source, objects).digest == original.digest


def test_missing_subdirectory_and_wrong_object_kind_fail():
    objects, source, blob = fixture()
    with pytest.raises(SkillError) as error:
        capture_git_package(replace(source, subdirectory="missing"), objects)
    assert error.value.code == "git_subdirectory_missing"
    objects[blob] = GitObject("tree", MAIN)
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == "git_object_invalid"


@pytest.mark.parametrize("name,code", [("SKILL.md", "git_tree_invalid"), ("skill.md", "path_collision")])
def test_duplicate_and_case_colliding_paths_fail(name, code):
    objects, source, _ = fixture(extras=[("100644", name, MAIN)])
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == code


def test_resource_depth_limit_stops_before_descendant_blob_read():
    objects = Objects()
    blob = objects.add("blob", MAIN)
    deep = objects.tree([("100644", "SKILL.md", blob)])
    root = objects.tree([("40000", "nested", deep)])
    source = SkillSource("git", URL, revision=objects.commit(root))
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects, package_limits=PackageLimits(max_depth=1))
    assert error.value.code == "package_limit"
    assert deep not in objects.reads and blob not in objects.reads


def test_subdirectory_depth_limit_applies_before_object_access():
    objects, source, _ = fixture()
    with pytest.raises(SkillError) as error:
        capture_git_package(replace(source, subdirectory="a/b"), objects,
                            limits=GitCaptureLimits(max_subdirectory_depth=1))
    assert error.value.code == "git_capture_limit"
    assert not objects.reads


@pytest.mark.parametrize("data", [b"tree main\n\nbody", b"tree " + b"a" * 40 + b"\nno separator"])
def test_malformed_commit_is_not_used_as_a_tree_authority(data):
    objects = Objects()
    source = SkillSource("git", URL, revision=objects.add("commit", data))
    with pytest.raises(SkillError) as error:
        capture_git_package(source, objects)
    assert error.value.code == "git_commit_invalid"


def test_offline_capture_does_not_accept_local_source_or_invalid_limits():
    with pytest.raises(SkillError) as error:
        capture_git_package(SkillSource("local", "/explicit/source"), {})
    assert error.value.code == "invalid_source"
    for value in (0, -1, True, 1.5):
        with pytest.raises(SkillError) as error:
            GitCaptureLimits(max_objects=value)
        assert error.value.code == "invalid_limits"
