"""Real filesystem and standard-package tests; never run Skill scripts or models."""
from dataclasses import replace
import os
import shutil

import pytest

from goodteam_os.skills import PackageFile, PackageLimits, SkillError, capture_package, package_from_files
from goodteam_os.skills.models import SkillScope, SkillSource, installed_id
from goodteam_os.skills.package import parse_skill

MAIN = b"---\nname: example\ndescription: Review bounded changes.\n---\nRead references/rules.txt when needed.\n"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "example"
    root.mkdir()
    (root / "SKILL.md").write_bytes(MAIN)
    return root


def codes(package):
    return {item.code for item in package.diagnostics}


def test_standard_package_preserves_arbitrary_resources_and_never_runs_them(source):
    (source / "arbitrary").mkdir()
    (source / "arbitrary/empty").mkdir()
    (source / "arbitrary/picture.bin").write_bytes(b"\x00\xff\x10")
    (source / "LICENSE").write_text("Private example license")
    script = source / "post-install.sh"
    script.write_text("#!/bin/sh\ntouch SHOULD_NEVER_EXIST\n")
    script.chmod(0o755)
    package = capture_package(source)
    assert package.resource("SKILL.md") == MAIN
    assert package.resource("arbitrary/picture.bin") == b"\x00\xff\x10"
    assert package.resource("LICENSE") == b"Private example license"
    assert "arbitrary/empty" in package.directories
    assert next(item for item in package.files if item.path == "post-install.sh").executable
    assert not (source / "SHOULD_NEVER_EXIST").exists()
    assert package.metadata == {"name": "example", "description": "Review bounded changes."}


def test_root_path_and_mtime_do_not_change_content_identity(source, tmp_path):
    first = capture_package(source)
    other = tmp_path / "different-directory"
    shutil.copytree(source, other)
    os.utime(other / "SKILL.md", (1000, 1000))
    (other / "SKILL.md").chmod(0o600)
    second = capture_package(other)
    assert first.digest == second.digest
    assert "directory_name_mismatch" in codes(second)
    (other / "SKILL.md").chmod(0o700)
    assert capture_package(other).digest != first.digest


def test_reference_change_alone_changes_package_version(source):
    (source / "references").mkdir()
    (source / "references/rules.txt").write_text("version one")
    first = capture_package(source)
    (source / "references/rules.txt").write_text("version two")
    second = capture_package(source)
    assert first.resource("SKILL.md") == second.resource("SKILL.md")
    assert first.digest != second.digest
    assert first.resource("references/rules.txt") == b"version one"


def test_original_bytes_bom_crlf_extensions_and_authorship_are_not_rewritten(source):
    data = (b"\xef\xbb\xbf---\r\nname: example\r\ndescription: Test extensions.\r\n"
            b"license: LICENSE.txt\r\nmetadata: {author: someone, version: '1.0'}\r\n"
            b"model: provider-model\r\nx-feature: [a, b]\r\n---\r\nUse !`echo DO_NOT_RUN`.\r\n")
    (source / "SKILL.md").write_bytes(data)
    package = capture_package(source)
    assert package.resource("SKILL.md") == data
    assert package.metadata["metadata"]["author"] == "someone"
    assert package.metadata["x-feature"] == ["a", "b"]
    assert {"utf8_bom", "extension_preserved", "runtime_semantics_unverified", "dynamic_command_unverified"} <= codes(package)
    projected = package.metadata
    projected["name"] = "changed"
    assert package.metadata["name"] == "example"


def test_nonstandard_display_name_warns_but_never_becomes_path_identity(source):
    (source / "SKILL.md").write_bytes(MAIN.replace(b"name: example", b"name: Custom_Name"))
    package = capture_package(source)
    assert "nonstandard_name" in codes(package)
    a = installed_id(SkillScope("user", "owner"), SkillSource("local", "/explicit/source"))
    b = installed_id(SkillScope("user", "owner"), SkillSource("local", "/other/source"))
    assert a != b and "Custom_Name" not in a


@pytest.mark.parametrize("data,code", [
    (b"# no frontmatter", "invalid_frontmatter"),
    (b"---\nname: example\n---\nbody", "invalid_metadata"),
    (b"---\nname: 10\ndescription: test\n---\nbody", "invalid_metadata"),
    (b"---\nname: example\ndescription: ''\n---\nbody", "invalid_metadata"),
    (b"---\nname: example\nname: duplicate\ndescription: test\n---\nbody", "duplicate_metadata_key"),
    (b"---\nname: example\ndescription: test\nmetadata: {a: x, a: y}\n---\nbody", "duplicate_metadata_key"),
    (b"---\nname: &x example\ndescription: *x\n---\nbody", "yaml_alias"),
    (b"---\nname: example\ndescription: test\nmetadata: {<<: {a: b}}\n---\nbody", "yaml_merge"),
    (b"---\nname: example\ndescription: test\nx: !!python/object/apply:os.system [echo INVALID]\n---\nbody", "invalid_metadata"),
    (b"---\nname: example\ndescription: test\nx: .nan\n---\nbody", "invalid_metadata"),
    (b"---\nname: example\ndescription: test\nx: 2026-09-21\n---\nbody", "invalid_metadata"),
    (b"---\nname: example\ndescription: \xff\n---\nbody", "invalid_encoding"),
])
def test_bad_frontmatter_is_a_bounded_error(data, code):
    with pytest.raises(SkillError) as error:
        parse_skill(data)
    assert error.value.code == code


def test_yaml_nesting_and_frontmatter_size_limits():
    nested = b"---\nname: example\ndescription: test\nx: " + b"[" * 40 + b"0" + b"]" * 40 + b"\n---\nbody"
    with pytest.raises(SkillError, match="nested"):
        parse_skill(nested)
    with pytest.raises(SkillError) as error:
        parse_skill(MAIN, limits=PackageLimits(max_frontmatter_bytes=10))
    assert error.value.code == "metadata_limit"


@pytest.mark.parametrize("relative", ["../escape", "/absolute", "a/../b", "a//b", "a/./b", "a\\b", "C:relative", "line\nbreak"])
def test_unsafe_relative_paths_are_rejected(relative):
    with pytest.raises(SkillError):
        PackageFile(relative, b"data")


def test_missing_main_and_case_collisions_are_rejected():
    with pytest.raises(SkillError) as missing:
        package_from_files([PackageFile("other.txt", b"data")])
    assert missing.value.code == "skill_missing"
    with pytest.raises(SkillError) as collision:
        package_from_files([PackageFile("SKILL.md", MAIN), PackageFile("skill.md", b"data")])
    assert collision.value.code == "path_collision"


@pytest.mark.parametrize("kind", ["file-link", "directory-link", "hardlink", "fifo"])
def test_links_and_special_files_never_escape_or_hang(source, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("retain")
    target = source / "unsafe"
    if kind == "file-link":
        target.symlink_to(outside / "sentinel")
    elif kind == "directory-link":
        target.symlink_to(outside, target_is_directory=True)
    elif kind == "hardlink":
        os.link(outside / "sentinel", target)
    else:
        os.mkfifo(target)
    with pytest.raises(SkillError):
        capture_package(source)
    assert (outside / "sentinel").read_text() == "retain"


def test_symlinked_source_ancestor_requires_explicit_canonical_authorization(source, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(source.parent, target_is_directory=True)
    with pytest.raises(SkillError):
        capture_package(alias / source.name)


@pytest.mark.parametrize("limit", [
    PackageLimits(max_files=1), PackageLimits(max_total_bytes=len(MAIN)),
    PackageLimits(max_file_bytes=1), PackageLimits(max_entries=1), PackageLimits(max_depth=1),
])
def test_resource_limits_are_enforced_during_capture(source, limit):
    (source / "extra").mkdir()
    (source / "extra/data").write_bytes(b"extra")
    with pytest.raises(SkillError) as error:
        capture_package(source, limits=limit)
    assert error.value.code == "package_limit"


def test_racing_file_replacement_does_not_get_published(source, monkeypatch):
    from goodteam_os.skills import package as module
    original = module.regular_file

    def swapped(fd, name, **kwargs):
        saved = source / "saved"
        os.rename(source / name, saved)
        (source / name).write_bytes(MAIN)
        return original(fd, name, **kwargs)

    monkeypatch.setattr(module, "regular_file", swapped)
    with pytest.raises(SkillError) as error:
        capture_package(source)
    assert error.value.code == "source_changed"


def test_forged_mutable_projection_is_not_part_of_content_identity(source):
    original = capture_package(source)
    changed = replace(original, metadata_json='{"name":"fake"}')
    assert changed.digest == original.digest  # Manifest is raw bytes, not caller claims.


@pytest.mark.parametrize("url", ["https://token@host/repo", "https://host/repo?token=x", "file:///tmp/repo", "http://host/repo"])
def test_git_records_require_credential_free_https_and_resolved_identity(url):
    with pytest.raises(SkillError):
        SkillSource("git", url, revision="a" * 40)
    with pytest.raises(SkillError):
        SkillSource("git", "https://host/repo", revision="main")


def test_git_revision_is_distinct_from_logical_installed_identity():
    scope = SkillScope("user", "alice")
    first = SkillSource("git", "https://host/repo", "skills/review", "a" * 40)
    second = replace(first, revision="b" * 40)
    assert installed_id(scope, first) == installed_id(scope, second)
    assert installed_id(scope, first) != installed_id(SkillScope("user", "bob"), first)


def test_programmatic_capture_stops_reading_at_the_configured_entry_bound():
    consumed = []

    def files():
        for index in range(100):
            consumed.append(index)
            yield PackageFile(f"file-{index}", b"data")

    with pytest.raises(SkillError) as error:
        package_from_files(files(), limits=PackageLimits(max_files=3))
    assert error.value.code == "package_limit"
    assert consumed == [0, 1, 2, 3]
