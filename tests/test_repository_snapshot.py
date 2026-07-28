from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from goodteam_os import repository_access, repository_snapshot
from goodteam_os.repository_snapshot import (
    RepositorySnapshotError,
    SnapshotLimits,
    capture_repository_snapshot,
    compute_repository_snapshot_id,
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


def test_snapshot_id_matches_published_identity_payload(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    assert snapshot.snapshot_id == compute_repository_snapshot_id(snapshot)


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


def test_staged_and_later_unstaged_content_have_separate_digests(
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


def test_snapshot_records_policy_exclusions_without_losing_completeness(
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


def test_ignored_node_modules_change_does_not_change_identity(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    baseline = capture_repository_snapshot(root, project_key="DEMO")
    (root / "node_modules").mkdir()
    generated = root / "node_modules" / "generated.js"
    generated.write_text("one\n", encoding="utf-8")
    first = capture_repository_snapshot(root, project_key="DEMO")
    generated.write_text("two\n", encoding="utf-8")
    second = capture_repository_snapshot(root, project_key="DEMO")

    assert baseline.snapshot_id == first.snapshot_id == second.snapshot_id


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
