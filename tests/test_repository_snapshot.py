from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from goodteam_os import repository_access, repository_snapshot
from goodteam_os.repository_snapshot import (
    RepositorySnapshotError,
    SnapshotLimits,
    capture_repository_snapshot,
    compile_snapshot_path_manifest,
    compute_repository_snapshot_id,
    snapshot_identity_payload,
    validate_snapshot_path_entry,
    validate_snapshot_text_content,
    verify_repository_snapshot,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "GoodTeam Tests",
            "GIT_AUTHOR_EMAIL": "tests@goodteam.invalid",
            "GIT_COMMITTER_NAME": "GoodTeam Tests",
            "GIT_COMMITTER_EMAIL": "tests@goodteam.invalid",
        },
    )
    return result.stdout.strip()


def init_git_repo(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-q")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "initial")
    return root


def test_clean_git_snapshot_is_deterministic(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    first = capture_repository_snapshot(root, project_key="DEMO")
    second = capture_repository_snapshot(root, project_key="DEMO")

    assert first.schema == "repository_snapshot.v1"
    assert first.snapshot_id == second.snapshot_id
    assert first.source_control["head_sha"] == git(root, "rev-parse", "HEAD")
    assert first.source_control["dirty"] is False
    assert first.content_scope["complete"] is True


def test_clean_git_capture_does_not_read_tracked_file_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    for index in range(4):
        (root / f"tracked-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "add tracked files")
    calls = 0
    real_read_text = repository_access.AnchoredRepository.read_text

    def counting_read_text(self, path: str, *, max_bytes: int):
        nonlocal calls
        calls += 1
        return real_read_text(self, path, max_bytes=max_bytes)

    monkeypatch.setattr(
        repository_access.AnchoredRepository,
        "read_text",
        counting_read_text,
    )

    capture_repository_snapshot(root, project_key="DEMO")

    assert calls == 0


def test_git_capture_uses_no_raw_target_path_metadata_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")

    def fail_lstat(path: object, *args, **kwargs):
        raise AssertionError(f"unexpected raw lstat: {path}")

    monkeypatch.setattr(repository_snapshot.os, "lstat", fail_lstat)

    capture_repository_snapshot(root, project_key="DEMO")


def test_snapshot_id_matches_published_identity_payload(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.snapshot_id == compute_repository_snapshot_id(snapshot)


def test_snapshot_public_api_accepts_positional_optional_arguments(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    limits = SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100)

    snapshot = capture_repository_snapshot(root, "DEMO", None, limits)
    error = RepositorySnapshotError("example", "message", {"field": "value"})

    assert snapshot.project_key == "DEMO"
    assert error.details == {"field": "value"}


def test_dirty_states_each_change_identity(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    clean = capture_repository_snapshot(root, project_key="DEMO")
    (root / "README.md").write_text("unstaged\n", encoding="utf-8")
    unstaged = capture_repository_snapshot(root, project_key="DEMO")
    git(root, "add", "README.md")
    staged = capture_repository_snapshot(root, project_key="DEMO")
    (root / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
    untracked = capture_repository_snapshot(root, project_key="DEMO")

    assert len(
        {
            clean.snapshot_id,
            unstaged.snapshot_id,
            staged.snapshot_id,
            untracked.snapshot_id,
        }
    ) == 4


def test_staged_and_unstaged_versions_have_distinct_digests(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / "README.md").write_text("staged\n", encoding="utf-8")
    git(root, "add", "README.md")
    (root / "README.md").write_text("worktree\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.source_control["index_digest"] != snapshot.source_control[
        "worktree_digest"
    ]


def test_unmerged_git_snapshot_parses_status_and_binds_all_index_stages(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    base_branch = git(root, "branch", "--show-current")
    git(root, "switch", "-qc", "conflicting-side")
    (root / "README.md").write_text("side\n", encoding="utf-8")
    git(root, "commit", "-qam", "side change")
    git(root, "switch", "-q", base_branch)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "commit", "-qam", "base change")
    merge = subprocess.run(
        ["git", "merge", "conflicting-side"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0
    raw_index = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    parsed_index = repository_snapshot._parse_index_records(raw_index)
    assert len([item for item in parsed_index if item["path"] == "README.md"]) == 3

    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.source_control["dirty"] is True
    assert snapshot.source_control["index_digest"] == repository_snapshot._digest_json(
        repository_snapshot._index_identity_records(parsed_index)
    )


def test_dirty_git_hashes_exact_path_without_listing_its_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    for index in range(20):
        path = root / f"file-{index:02d}.txt"
        path.write_text("initial\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "add files")
    (root / "file-00.txt").write_text("changed\n", encoding="utf-8")
    observed_entries = 0
    real_repository_entry = repository_access._repository_entry

    def counting_repository_entry(*args, **kwargs):
        nonlocal observed_entries
        observed_entries += 1
        return real_repository_entry(*args, **kwargs)

    monkeypatch.setattr(
        repository_access,
        "_repository_entry",
        counting_repository_entry,
    )
    capture_repository_snapshot(root, project_key="DEMO")

    assert observed_entries == 0


def test_verification_fails_after_drift(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RepositorySnapshotError) as captured:
        verify_repository_snapshot(root, snapshot)

    assert captured.value.code == "repository_snapshot_changed"


def test_non_git_snapshot_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("initial\n", encoding="utf-8")

    first = capture_repository_snapshot(root, project_key="DEMO")
    second = capture_repository_snapshot(root, project_key="DEMO")

    assert first.snapshot_id == second.snapshot_id
    assert first.source_control["kind"] == "none"
    assert first.content_scope["eligible_files"] == 1


def test_snapshot_identity_payload_includes_non_git_path_manifest_without_raw_contents(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text("hello\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    payload = snapshot_identity_payload(snapshot)
    manifest = payload["path_manifest"]

    assert manifest["complete"] is True
    assert manifest["entries"] == [
        {
            "path": "README.md",
            "kind": "file",
            "identity_kind": "worktree",
            "size": 6,
            "content_hash": "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
            "metadata": {
                "mode": readme.stat().st_mode,
                "mtime_ns": readme.stat().st_mtime_ns,
                "size": 6,
            },
        }
    ]
    assert "text" not in json.dumps(manifest, sort_keys=True)
    assert "hello" not in json.dumps(manifest, sort_keys=True)


def test_snapshot_identity_payload_includes_clean_git_index_entry_manifest(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    payload = snapshot_identity_payload(snapshot)
    manifest = payload["path_manifest"]
    head_blob = git(root, "rev-parse", "HEAD:README.md")

    assert manifest["entries"] == [
        {
            "path": "README.md",
            "kind": "file",
            "identity_kind": "git_index",
            "git": {
                "mode": "100644",
                "object_id": head_blob,
                "stage": 0,
            },
        }
    ]


def test_snapshot_identity_payload_includes_dirty_git_content_manifest(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    tracked = root / "README.md"
    tracked.write_text("dirty\n", encoding="utf-8")
    extra = root / "extra.txt"
    extra.write_text("extra\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    payload = snapshot_identity_payload(snapshot)
    manifest = payload["path_manifest"]
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    dirty_hash = "sha256:" + hashlib.sha256(b"dirty\n").hexdigest()
    extra_hash = "sha256:" + hashlib.sha256(b"extra\n").hexdigest()

    assert entries["README.md"]["identity_kind"] == "worktree"
    assert entries["README.md"]["content_hash"] == dirty_hash
    assert entries["extra.txt"]["identity_kind"] == "untracked"
    assert entries["extra.txt"]["content_hash"] == extra_hash


def test_snapshot_identity_payload_path_manifest_is_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(3):
        (root / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
    limits = SnapshotLimits(max_files=2, max_total_bytes=100, max_file_bytes=100)

    first = snapshot_identity_payload(
        capture_repository_snapshot(root, project_key="DEMO", limits=limits)
    )["path_manifest"]
    second = snapshot_identity_payload(
        capture_repository_snapshot(root, project_key="DEMO", limits=limits)
    )["path_manifest"]

    assert first == second
    assert first["complete"] is False
    assert first["omitted_paths"] == ["file-2.txt"]


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_manifest_type",
        "missing_entries",
        "missing_excluded",
        "missing_omitted",
        "wrong_entries_type",
        "wrong_excluded_type",
        "wrong_omitted_type",
        "non_object_entry",
        "non_object_excluded",
        "non_object_omitted",
        "missing_entry_path",
        "non_string_entry_path",
        "noncanonical_entry_path",
        "wrong_complete_type",
        "wrong_omitted_paths_type",
        "mismatched_omitted_paths",
        "unsupported_top_level_field",
    ],
)
def test_compile_snapshot_path_manifest_rejects_malformed_raw_shape(
    mutation: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    manifest = dict(snapshot.path_manifest)

    if mutation == "wrong_manifest_type":
        malformed = replace(snapshot, path_manifest=None)
    elif mutation == "missing_entries":
        manifest.pop("entries")
    elif mutation == "missing_excluded":
        manifest.pop("excluded")
    elif mutation == "missing_omitted":
        manifest.pop("omitted")
    elif mutation == "wrong_entries_type":
        manifest["entries"] = {}
    elif mutation == "wrong_excluded_type":
        manifest["excluded"] = {}
    elif mutation == "wrong_omitted_type":
        manifest["omitted"] = {}
    elif mutation == "non_object_entry":
        manifest["entries"] = ["README.md"]
    elif mutation == "non_object_excluded":
        manifest["excluded"] = ["README.md"]
    elif mutation == "non_object_omitted":
        manifest["omitted"] = ["README.md"]
        manifest["omitted_paths"] = ["README.md"]
        manifest["complete"] = False
    elif mutation == "missing_entry_path":
        manifest["entries"] = [{"kind": "directory"}]
    elif mutation == "non_string_entry_path":
        manifest["entries"] = [{"path": 7, "kind": "directory"}]
    elif mutation == "noncanonical_entry_path":
        manifest["entries"] = [{"path": "../README.md", "kind": "directory"}]
    elif mutation == "wrong_complete_type":
        manifest["complete"] = "yes"
    elif mutation == "wrong_omitted_paths_type":
        manifest["omitted_paths"] = {}
    elif mutation == "mismatched_omitted_paths":
        manifest["omitted_paths"] = ["README.md"]
    elif mutation == "unsupported_top_level_field":
        manifest["raw_content"] = "must not be accepted"
    if mutation != "wrong_manifest_type":
        malformed = replace(snapshot, path_manifest=manifest)

    with pytest.raises(RepositorySnapshotError) as captured:
        compile_snapshot_path_manifest(malformed)

    assert captured.value.code == "repository_snapshot_invalid"
    assert str(captured.value) == "Repository snapshot path manifest is invalid."
    assert set(captured.value.details) in ({"path"}, {"schema"})
    assert "hello" not in str(captured.value.details)


@pytest.mark.parametrize(
    "mutation",
    [
        "unsupported_identity_kind",
        "missing_content_hash",
        "invalid_metadata",
        "missing_git_identity",
        "unsupported_exclusion_reason",
        "unsupported_omission_reason",
    ],
)
def test_compile_snapshot_path_manifest_rejects_invalid_state_record(
    mutation: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    manifest = {
        key: list(value) if isinstance(value, list) else value
        for key, value in snapshot.path_manifest.items()
    }
    entry = dict(manifest["entries"][0])

    if mutation == "unsupported_identity_kind":
        entry["identity_kind"] = "filesystem"
        manifest["entries"] = [entry]
    elif mutation == "missing_content_hash":
        entry.pop("content_hash")
        manifest["entries"] = [entry]
    elif mutation == "invalid_metadata":
        entry["metadata"] = {"mode": "regular", "mtime_ns": 1, "size": 6}
        manifest["entries"] = [entry]
    elif mutation == "missing_git_identity":
        entry["identity_kind"] = "git_index"
        entry.pop("size")
        entry.pop("content_hash")
        entry.pop("metadata")
        manifest["entries"] = [entry]
    elif mutation == "unsupported_exclusion_reason":
        manifest["entries"] = []
        manifest["excluded"] = [
            {"path": "README.md", "kind": "file", "reason": "secret"}
        ]
    elif mutation == "unsupported_omission_reason":
        manifest["entries"] = []
        manifest["omitted"] = [
            {"path": "README.md", "kind": "file", "reason": "later"}
        ]
        manifest["omitted_paths"] = ["README.md"]
        manifest["complete"] = False

    malformed = replace(snapshot, path_manifest=manifest)

    with pytest.raises(RepositorySnapshotError) as captured:
        compile_snapshot_path_manifest(malformed)

    assert captured.value.code == "repository_snapshot_invalid"
    assert captured.value.details == {"path": "README.md"}


@pytest.mark.parametrize(
    "duplicate_state",
    [
        "entries",
        "excluded",
        "omitted",
        "entries_excluded",
        "entries_omitted",
    ],
)
def test_compile_snapshot_path_manifest_rejects_duplicate_and_cross_state_paths(
    duplicate_state: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    represented = dict(snapshot.path_manifest["entries"][0])
    manifest = {
        "complete": duplicate_state != "omitted",
        "entries": [represented],
        "excluded": [],
        "omitted": [],
        "omitted_paths": [],
    }
    if duplicate_state == "entries":
        manifest["entries"].append(dict(represented))
    elif duplicate_state == "excluded":
        manifest["entries"] = []
        exclusion = {"path": "README.md", "kind": "file", "reason": "binary"}
        manifest["excluded"] = [exclusion, dict(exclusion)]
    elif duplicate_state == "omitted":
        manifest["entries"] = []
        omission = {"path": "README.md", "kind": "file", "reason": "max_files"}
        manifest["omitted"] = [omission, dict(omission)]
        manifest["omitted_paths"] = ["README.md"]
    elif duplicate_state == "entries_excluded":
        manifest["excluded"] = [
            {"path": "README.md", "kind": "file", "reason": "binary"}
        ]
    else:
        manifest["omitted"] = [
            {"path": "README.md", "kind": "file", "reason": "max_files"}
        ]
        manifest["omitted_paths"] = ["README.md"]

    malformed = replace(snapshot, path_manifest=manifest)

    with pytest.raises(RepositorySnapshotError) as captured:
        compile_snapshot_path_manifest(malformed)

    assert captured.value.code == "repository_snapshot_invalid"
    assert captured.value.details == {"path": "README.md"}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_bucket",
        "wrong_bucket_type",
        "non_object_record",
        "missing_record_path",
        "mismatched_record_path",
        "unsupported_identity_kind",
        "cross_state_path",
    ],
)
def test_validate_snapshot_path_entry_rejects_malformed_compiled_manifest(
    mutation: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "README.md"
    target.write_text("hello\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    compiled = compile_snapshot_path_manifest(snapshot)
    manifest: object = {
        state: {path: dict(record) for path, record in bucket.items()}
        for state, bucket in compiled.items()
    }
    entry = repository_access.RepositoryEntry(
        path="README.md",
        kind="file",
        size=6,
        mtime_ns=target.stat().st_mtime_ns,
    )

    if mutation == "missing_bucket":
        manifest.pop("entries")
    elif mutation == "wrong_bucket_type":
        manifest["entries"] = []
    elif mutation == "non_object_record":
        manifest["entries"]["README.md"] = []
    elif mutation == "missing_record_path":
        manifest["entries"]["README.md"].pop("path")
    elif mutation == "mismatched_record_path":
        manifest["entries"]["README.md"]["path"] = "other.md"
    elif mutation == "unsupported_identity_kind":
        manifest["entries"]["README.md"]["identity_kind"] = "filesystem"
    elif mutation == "cross_state_path":
        manifest["excluded"]["README.md"] = {
            "path": "README.md",
            "kind": "file",
            "reason": "binary",
        }

    with pytest.raises(RepositorySnapshotError) as captured:
        validate_snapshot_path_entry(manifest, entry)

    assert captured.value.code == "repository_snapshot_invalid"
    assert str(captured.value) == "Repository snapshot path manifest is invalid."
    assert set(captured.value.details) in ({"path"}, {"schema"})


def test_excluded_and_omitted_snapshot_entries_are_no_content_sentinels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "binary.bin").write_bytes(b"a\0b")
    (root / "large.txt").write_text("12345", encoding="utf-8")
    limits = SnapshotLimits(max_files=10, max_total_bytes=100, max_file_bytes=4)
    snapshot = capture_repository_snapshot(root, "DEMO", None, limits)
    manifest = compile_snapshot_path_manifest(snapshot)
    read_calls = 0

    def fail_read_text(self, path: str, *, max_bytes: int):
        nonlocal read_calls
        read_calls += 1
        raise AssertionError(f"excluded/omitted path was read: {path}")

    monkeypatch.setattr(
        repository_access.AnchoredRepository,
        "read_text",
        fail_read_text,
    )
    with repository_access.AnchoredRepository(
        root,
        expected_identity=snapshot.root_identity,
    ) as repository:
        entries = {entry.path: entry for entry in repository.list_directory("")}
        for path, expected_state in (
            ("binary.bin", "excluded"),
            ("large.txt", "omitted"),
        ):
            state = validate_snapshot_path_entry(manifest, entries[path])
            assert state is not None
            assert state["snapshot_state"] == expected_state
            assert (
                validate_snapshot_text_content(
                    repository,
                    manifest,
                    snapshot.source_control,
                    entries[path],
                    max_bytes=100,
                )
                is None
            )

    assert read_calls == 0


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (b"binary\x00content", "repository_binary_file"),
        (b"\xff\xfeinvalid-utf8", "repository_non_text_file"),
    ],
)
def test_git_index_snapshot_text_validation_rejects_non_text_drift(
    tmp_path: Path,
    payload: bytes,
    expected_error: str,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    manifest = compile_snapshot_path_manifest(snapshot)
    tracked = root / "README.md"
    tracked.write_bytes(payload)

    with repository_access.AnchoredRepository(
        root,
        expected_identity=snapshot.root_identity,
    ) as repository:
        entry = next(item for item in repository.list_directory("") if item.path == "README.md")
        with pytest.raises(RepositorySnapshotError) as captured:
            validate_snapshot_text_content(
                repository,
                manifest,
                snapshot.source_control,
                entry,
                max_bytes=entry.size,
            )

    assert captured.value.code == "repository_snapshot_changed"
    assert captured.value.details == {"path": "README.md"}
    assert expected_error in {"repository_binary_file", "repository_non_text_file"}


def test_snapshot_records_generated_binary_and_symlink_exclusions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external.txt"
    root.mkdir()
    external.write_text("secret\n", encoding="utf-8")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "large.js").write_text("generated\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"alpha\x00beta")
    (root / "linked").symlink_to(external)

    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.content_scope["complete"] is True
    assert snapshot.content_scope["excluded_files"] == 3
    reasons = snapshot.content_scope["exclusion_reasons"]
    assert reasons["ignored_directory"] == 1
    assert reasons["binary"] == 1
    assert reasons["symlink"] == 1


def test_ceiling_omissions_make_snapshot_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(3):
        (root / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        project_key="DEMO",
        limits=SnapshotLimits(max_files=2, max_total_bytes=100, max_file_bytes=100),
    )

    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"]["max_files"] == 1


def test_non_git_file_ceiling_counts_only_eligible_files_and_exact_omissions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    for index in range(4):
        (source / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "generated.js").write_text("ignored\n", encoding="utf-8")
    (root / "linked").symlink_to(source / "file-0.txt")

    snapshot = capture_repository_snapshot(
        root,
        "DEMO",
        None,
        SnapshotLimits(max_files=2, max_total_bytes=100, max_file_bytes=100),
    )

    assert snapshot.content_scope["eligible_files"] == 2
    assert snapshot.content_scope["excluded_files"] == 2
    assert snapshot.content_scope["omitted_files"] == 2
    assert snapshot.content_scope["exclusion_reasons"] == {
        "ignored_directory": 1,
        "max_files": 2,
        "symlink": 1,
    }


def test_non_git_max_file_bytes_is_an_omission(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "large.txt").write_text("12345", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        "DEMO",
        None,
        SnapshotLimits(max_files=10, max_total_bytes=100, max_file_bytes=4),
    )

    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["excluded_files"] == 0
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"] == {"max_file_bytes": 1}


def test_git_index_ceiling_makes_snapshot_incomplete(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / "second.txt").write_text("second\n", encoding="utf-8")
    git(root, "add", "second.txt")
    git(root, "commit", "-qm", "second")

    snapshot = capture_repository_snapshot(
        root,
        project_key="DEMO",
        limits=SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100),
    )

    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"]["max_files"] == 1


def test_git_index_identity_includes_staged_path_beyond_file_ceiling(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    staged = root / "z-staged.txt"
    staged.write_text("first\n", encoding="utf-8")
    git(root, "add", staged.name)
    limits = SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100)
    first = capture_repository_snapshot(root, "DEMO", None, limits)

    staged.write_text("second\n", encoding="utf-8")
    git(root, "add", staged.name)
    second = capture_repository_snapshot(root, "DEMO", None, limits)

    assert first.content_scope["complete"] is False
    assert second.content_scope["complete"] is False
    assert first.source_control["index_digest"] != second.source_control["index_digest"]
    assert first.snapshot_id != second.snapshot_id


def test_git_status_identity_includes_untracked_path_beyond_file_ceiling(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    first_path = root / "z-first.txt"
    first_path.write_text("same\n", encoding="utf-8")
    limits = SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100)
    first = capture_repository_snapshot(root, "DEMO", None, limits)

    first_path.rename(root / "z-second.txt")
    second = capture_repository_snapshot(root, "DEMO", None, limits)

    assert first.content_scope["complete"] is False
    assert second.content_scope["complete"] is False
    assert first.source_control["status_digest"] != second.source_control["status_digest"]
    assert first.snapshot_id != second.snapshot_id


def test_git_status_identity_canonicalizes_paths_and_rename_origins() -> None:
    base = (
        b"2 R. N... 100644 100644 100644 "
        + b"0" * 40
        + b" "
        + b"1" * 40
        + b" R100 target.txt\0source.txt\0"
    )
    canonical_equivalent = (
        b"2 R. N... 100644 100644 100644 "
        + b"0" * 40
        + b" "
        + b"1" * 40
        + b" R100 target.txt/\0source.txt/\0"
    )

    first = repository_snapshot._status_identity_records(
        repository_snapshot._parse_status(base),
    )
    second = repository_snapshot._status_identity_records(
        repository_snapshot._parse_status(canonical_equivalent),
    )

    assert first == second
    assert repository_snapshot._digest_json(first) == repository_snapshot._digest_json(
        second
    )


def test_git_status_identity_changes_for_material_fields() -> None:
    base = (
        b"2 R. N... 100644 100644 100644 "
        + b"0" * 40
        + b" "
        + b"1" * 40
        + b" R100 target.txt\0source.txt\0"
    )
    renamed_differently = (
        b"2 R. N... 100644 100644 100644 "
        + b"0" * 40
        + b" "
        + b"1" * 40
        + b" R090 target.txt\0source.txt\0"
    )

    first = repository_snapshot._status_identity_records(
        repository_snapshot._parse_status(base),
    )
    second = repository_snapshot._status_identity_records(
        repository_snapshot._parse_status(renamed_differently),
    )

    assert repository_snapshot._digest_json(first) != repository_snapshot._digest_json(
        second
    )


def test_git_index_identity_canonicalizes_paths_without_collapsing_stages() -> None:
    base = (
        b"100644 "
        + b"0" * 40
        + b" 1\tconflict.txt\0"
        + b"100644 "
        + b"1" * 40
        + b" 2\tconflict.txt\0"
    )
    canonical_equivalent = (
        b"100644 "
        + b"0" * 40
        + b" 1\tconflict.txt/\0"
        + b"100644 "
        + b"1" * 40
        + b" 2\tconflict.txt/\0"
    )

    first = repository_snapshot._index_identity_records(
        repository_snapshot._parse_index_records(base),
    )
    second = repository_snapshot._index_identity_records(
        repository_snapshot._parse_index_records(canonical_equivalent),
    )

    assert [record["stage"] for record in first] == [1, 2]
    assert first == second
    assert repository_snapshot._digest_json(first) == repository_snapshot._digest_json(
        second
    )


def test_git_index_identity_changes_for_material_fields() -> None:
    base = b"100644 " + b"0" * 40 + b" 1\tconflict.txt\0"
    different_stage = b"100644 " + b"0" * 40 + b" 2\tconflict.txt\0"

    first = repository_snapshot._index_identity_records(
        repository_snapshot._parse_index_records(base),
    )
    second = repository_snapshot._index_identity_records(
        repository_snapshot._parse_index_records(different_stage),
    )

    assert repository_snapshot._digest_json(first) != repository_snapshot._digest_json(
        second
    )


def test_git_submodule_identity_canonicalizes_paths() -> None:
    base = b" " + b"0" * 40 + b" deps/lib (heads/main)\n"
    canonical_equivalent = b" " + b"0" * 40 + b" deps/lib/ (heads/main)\n"

    first = repository_snapshot._parse_submodule_records(base)
    second = repository_snapshot._parse_submodule_records(canonical_equivalent)

    assert first == second
    assert repository_snapshot._digest_json(first) == repository_snapshot._digest_json(
        second
    )


def test_git_submodule_identity_changes_for_material_fields() -> None:
    base = b" " + b"0" * 40 + b" deps/lib (heads/main)\n"
    different_description = b" " + b"0" * 40 + b" deps/lib (tags/v1.0)\n"

    first = repository_snapshot._parse_submodule_records(base)
    second = repository_snapshot._parse_submodule_records(different_description)

    assert repository_snapshot._digest_json(first) != repository_snapshot._digest_json(
        second
    )


@pytest.mark.parametrize(
    "value",
    [
        b"? ../escape.txt\0",
        b"! invalid//ignored.txt\0",
        b"! invalid//\0",
        (
            b"2 R. N... 100644 100644 100644 "
            + b"0" * 40
            + b" "
            + b"1" * 40
            + b" R100 target.txt\0../origin.txt\0"
        ),
    ],
)
def test_git_status_rejects_noncanonical_paths(value: bytes) -> None:
    with pytest.raises(RepositorySnapshotError) as captured:
        repository_snapshot._parse_status(value)

    assert captured.value.code == "repository_snapshot_failed"


def test_git_index_rejects_noncanonical_staged_only_path() -> None:
    value = b"100644 " + b"0" * 40 + b" 0\t../escape.txt\0"

    with pytest.raises(RepositorySnapshotError) as captured:
        repository_snapshot._parse_index_records(value)

    assert captured.value.code == "repository_snapshot_failed"


def test_git_submodule_output_rejects_noncanonical_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    real_require_git = repository_snapshot._require_git

    def invalid_submodule(root: Path, arguments: list[str]) -> bytes:
        if arguments == ["submodule", "status", "--recursive"]:
            return b" " + b"0" * 40 + b" ../escape (heads/main)\n"
        return real_require_git(root, arguments)

    monkeypatch.setattr(repository_snapshot, "_require_git", invalid_submodule)

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_failed"


@pytest.mark.parametrize(
    ("path_name", "prepare"),
    [
        (
            "README.md",
            lambda root: (root / "README.md").write_text("dirty\n", encoding="utf-8"),
        ),
        (
            "extra.txt",
            lambda root: (root / "extra.txt").write_text("untracked\n", encoding="utf-8"),
        ),
    ],
)
def test_git_selected_dirty_path_missing_metadata_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_name: str,
    prepare,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    prepare(root)
    real_anchored_file_metadata = repository_snapshot._anchored_file_metadata

    def missing_selected_metadata(repository, path: str):
        if path == path_name:
            return None
        return real_anchored_file_metadata(repository, path)

    monkeypatch.setattr(
        repository_snapshot,
        "_anchored_file_metadata",
        missing_selected_metadata,
    )

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_entry_changed"
    assert captured.value.details == {"path": path_name}


def test_git_total_byte_ceiling_is_aggregate_across_worktree_and_untracked(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    tracked = root / "README.md"
    tracked.write_text("staged-a\n", encoding="utf-8")
    git(root, "add", "README.md")
    tracked.write_text("worktree\n", encoding="utf-8")
    extra = root / "extra.txt"
    extra.write_text("ABCDEFG\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        project_key="DEMO",
        limits=SnapshotLimits(max_files=10, max_total_bytes=10, max_file_bytes=100),
    )

    assert snapshot.source_control["dirty"] is True
    assert snapshot.content_scope["eligible_files"] == 1
    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"]["max_total_bytes"] == 1
    included_buckets = [
        snapshot.source_control["worktree_digest"]
        != "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        snapshot.source_control["untracked_digest"]
        != "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ]
    assert included_buckets.count(True) == 1


def test_git_total_byte_ceiling_bounds_physical_anchored_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / "README.md").write_text("12345678", encoding="utf-8")
    (root / "extra.txt").write_text("ABCDEFGH", encoding="utf-8")
    physical_bytes = 0
    real_read_bounded = repository_access._read_bounded

    def counting_read_bounded(descriptor: int, limit: int) -> bytes:
        nonlocal physical_bytes
        value = real_read_bounded(descriptor, limit)
        physical_bytes += len(value)
        return value

    monkeypatch.setattr(repository_access, "_read_bounded", counting_read_bounded)
    limits = SnapshotLimits(max_files=10, max_total_bytes=10, max_file_bytes=100)

    snapshot = capture_repository_snapshot(root, "DEMO", None, limits)

    assert snapshot.content_scope["complete"] is False
    assert physical_bytes <= limits.max_total_bytes * 2


def test_git_max_file_bytes_is_an_omission(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / "large.txt").write_text("12345", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        "DEMO",
        None,
        SnapshotLimits(max_files=10, max_total_bytes=100, max_file_bytes=4),
    )

    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"]["max_file_bytes"] == 1


def test_git_file_ceiling_counts_unique_paths_across_index_and_untracked(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    tracked = root / "README.md"
    tracked.write_text("staged\n", encoding="utf-8")
    git(root, "add", "README.md")
    tracked.write_text("worktree\n", encoding="utf-8")
    (root / "extra.txt").write_text("extra\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        project_key="DEMO",
        limits=SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100),
    )

    assert snapshot.source_control["dirty"] is True
    assert snapshot.content_scope["eligible_files"] == 1
    assert snapshot.content_scope["complete"] is False
    assert snapshot.content_scope["omitted_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"]["max_files"] == 1


def test_modified_tracked_path_counts_once_when_it_fits(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    tracked = root / "README.md"
    tracked.write_text("staged\n", encoding="utf-8")
    git(root, "add", "README.md")
    tracked.write_text("worktree\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(
        root,
        project_key="DEMO",
        limits=SnapshotLimits(max_files=1, max_total_bytes=100, max_file_bytes=100),
    )

    assert snapshot.source_control["dirty"] is True
    assert snapshot.content_scope["eligible_files"] == 1
    assert snapshot.content_scope["complete"] is True
    assert snapshot.content_scope["omitted_files"] == 0
    assert snapshot.source_control["index_digest"] != ""
    assert (
        snapshot.source_control["worktree_digest"]
        != "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    )


def test_git_policy_exclusions_are_counted_without_dirtying_identity(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore generated output")
    (root / "binary.bin").write_bytes(b"alpha\x00beta")
    git(root, "add", "binary.bin")
    git(root, "commit", "-qm", "add binary fixture")
    (root / "binary.bin").write_bytes(b"next\x00value")
    (root / "linked").symlink_to("README.md")
    git(root, "add", "linked")
    git(root, "commit", "-qm", "add symlink fixture")
    (root / "node_modules").mkdir()
    generated = root / "node_modules" / "generated.js"
    generated.write_text("one\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.source_control["kind"] == "git"
    assert snapshot.source_control["dirty"] is False
    assert snapshot.content_scope["complete"] is True
    assert snapshot.content_scope["excluded_files"] == 3
    assert snapshot.content_scope["eligible_files"] == 3
    assert snapshot.content_scope["exclusion_reasons"] == {
        "binary": 1,
        "ignored_directory": 1,
        "symlink": 1,
    }
    assert (
        snapshot.source_control["worktree_digest"]
        == snapshot.source_control["untracked_digest"]
    )


def test_git_ignored_paths_outside_default_policy_are_counted_stably(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / ".gitignore").write_text("generated.log\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore generated log")
    (root / "generated.log").write_text("noise\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.source_control["dirty"] is False
    assert snapshot.content_scope["complete"] is True
    assert snapshot.content_scope["excluded_files"] == 1
    assert snapshot.content_scope["exclusion_reasons"] == {"git_ignored": 1}


def test_ignored_node_modules_change_does_not_change_identity_once_present(
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore generated output")
    (root / "node_modules").mkdir()
    generated = root / "node_modules" / "generated.js"
    generated.write_text("one\n", encoding="utf-8")
    first = capture_repository_snapshot(root, project_key="DEMO")
    generated.write_text("two\n", encoding="utf-8")
    second = capture_repository_snapshot(root, project_key="DEMO")

    assert first.content_scope["excluded_files"] == 1
    assert first.content_scope["exclusion_reasons"]["ignored_directory"] == 1
    assert first.snapshot_id == second.snapshot_id


def test_git_worktree_subdirectory_fails_closed(tmp_path: Path) -> None:
    repository_root = init_git_repo(tmp_path / "repo")
    package_root = repository_root / "pkg"
    package_root.mkdir()
    (package_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(package_root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_failed"
    assert captured.value.details["operation"] == "rev-parse"


def test_bare_git_repository_does_not_fall_back_to_non_git_capture(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo.git"
    git(tmp_path, "init", "--bare", "-q", str(root))

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_failed"


def test_capture_rejects_change_between_stability_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "README.md"
    target.write_text("first\n", encoding="utf-8")
    real_capture_once = repository_snapshot._capture_once
    calls = 0

    def changing_capture(*args, **kwargs):
        nonlocal calls
        result = real_capture_once(*args, **kwargs)
        calls += 1
        if calls == 1:
            target.write_text("second\n", encoding="utf-8")
        return result

    monkeypatch.setattr(repository_snapshot, "_capture_once", changing_capture)
    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_changed"


def test_non_git_capture_rejects_metadata_change_between_stability_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "README.md"
    target.write_text("stable\n", encoding="utf-8")
    real_capture_once = repository_snapshot._capture_once
    calls = 0

    def touching_capture(*args, **kwargs):
        nonlocal calls
        result = real_capture_once(*args, **kwargs)
        calls += 1
        if calls == 1:
            before = target.stat()
            os.utime(
                target,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(repository_snapshot, "_capture_once", touching_capture)

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_changed"


def test_dirty_git_capture_rejects_metadata_change_between_stability_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = init_git_repo(tmp_path / "repo")
    target = root / "README.md"
    target.write_text("dirty\n", encoding="utf-8")
    real_capture_once = repository_snapshot._capture_once
    calls = 0

    def touching_capture(*args, **kwargs):
        nonlocal calls
        result = real_capture_once(*args, **kwargs)
        calls += 1
        if calls == 1:
            before = target.stat()
            os.utime(
                target,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(repository_snapshot, "_capture_once", touching_capture)

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_snapshot_changed"


def test_snapshot_rejects_root_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "README.md"
    target.write_text("stable\n", encoding="utf-8")
    displaced = tmp_path / "repo-original"
    real_capture_once = repository_snapshot._capture_once
    calls = 0

    def replacing_capture(*args, **kwargs):
        nonlocal calls
        result = real_capture_once(*args, **kwargs)
        calls += 1
        if calls == 1:
            root.rename(displaced)
            root.mkdir()
            (root / "README.md").write_text("stable\n", encoding="utf-8")
        return result

    monkeypatch.setattr(repository_snapshot, "_capture_once", replacing_capture)

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(root, project_key="DEMO")

    assert captured.value.code == "repository_root_changed"


def test_snapshot_rejects_root_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    identity = root.stat()

    with pytest.raises(RepositorySnapshotError) as captured:
        capture_repository_snapshot(
            root,
            project_key="DEMO",
            expected_identity={"device": identity.st_dev, "inode": identity.st_ino + 1},
        )

    assert captured.value.code == "repository_root_changed"
