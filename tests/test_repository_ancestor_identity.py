"""Ancestor churn is unrelated content, but replacement is still an error."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import repository_access as access
from goodteam_os.onboarding_store import OnboardingArtifactStore, OnboardingObservationStore
from goodteam_os.project_import import import_project
from goodteam_os.repository_context_provider import (
    ContextProviderError,
    OnboardingBudget,
    RepositoryContextProvider,
)
from goodteam_os.repository_snapshot import capture_repository_snapshot


def _identity(value):
    return value.st_dev, value.st_ino


def _after_lstat(monkeypatch, target: Path, mutate):
    original = access._lstat_at
    wanted = _identity(target.stat())
    seen = []

    def intercepted(parent_fd, name):
        value = original(parent_fd, name)
        if not seen and _identity(value) == wanted:
            seen.append(True)
            mutate(target)
        return value

    monkeypatch.setattr(access, "_lstat_at", intercepted)
    return seen


def _churn(target: Path):
    before = target.stat()
    (target / "unrelated-sibling").mkdir()
    # Deterministic even on filesystems with coarse timestamp resolution.
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))


def _repo(tmp_path: Path):
    ancestor = tmp_path / "ancestor"
    root = ancestor / "repo"
    root.mkdir(parents=True)
    (root / "README.md").write_text("one\ntwo\n", encoding="utf-8")
    return ancestor, root


def test_snapshot_ignores_unrelated_ancestor_churn(tmp_path, monkeypatch):
    ancestor, root = _repo(tmp_path)
    before = capture_repository_snapshot(root, project_key="DEMO")
    seen = _after_lstat(monkeypatch, ancestor, _churn)
    after = capture_repository_snapshot(root, project_key="DEMO")
    assert seen
    assert after.snapshot_id == before.snapshot_id
    with access.AnchoredRepository(root) as repository:
        assert repository.read_text("README.md", max_bytes=100).text == "one\ntwo\n"


def test_error_observation_survives_unrelated_ancestor_churn(tmp_path, monkeypatch):
    ancestor, root = _repo(tmp_path)
    workspace = tmp_path / "workspace"
    imported = import_project(workspace, str(root))
    project_key = str(imported["project"]["project_key"])
    snapshot = capture_repository_snapshot(root, project_key=project_key)
    OnboardingArtifactStore(workspace, project_key, root).publish_snapshot(snapshot)
    store = OnboardingObservationStore(workspace, project_key, root, "job-1", snapshot.snapshot_id)
    provider = RepositoryContextProvider(
        workspace=workspace, project_key=project_key, root=root,
        snapshot=snapshot, observation_store=store, budget=OnboardingBudget(),
    )
    seen = _after_lstat(monkeypatch, ancestor, _churn)
    with pytest.raises(ContextProviderError) as error:
        provider.call(
            "read_file_excerpt",
            {"path": "README.md", "start_line": 2, "line_count": 3},
            model_turn=1,
        )
    assert seen
    assert error.value.code == "context_tool_limit_exceeded"
    records = store.read_all()
    assert len(records) == 1
    assert records[0]["response_metadata"] == {"error": {"code": "context_tool_limit_exceeded"}}


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_ancestor_replacement_between_stat_and_open_is_rejected(tmp_path, monkeypatch, replacement):
    ancestor, root = _repo(tmp_path)
    original = ancestor.stat()

    def replace(target):
        moved = target.with_name("moved")
        target.rename(moved)
        if replacement == "symlink":
            target.symlink_to(moved, target_is_directory=True)
        else:
            target.mkdir()
            (target / "repo").mkdir()
            os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

    seen = _after_lstat(monkeypatch, ancestor, replace)
    with pytest.raises(access.RepositoryAccessError) as error:
        access.AnchoredRepository(root)
    assert seen
    assert error.value.code == (
        "context_path_rejected" if replacement == "symlink" else "repository_entry_changed"
    )


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_ancestor_replacement_after_open_is_rejected_and_fd_closed(tmp_path, monkeypatch, replacement):
    ancestor, root = _repo(tmp_path)
    original_open = os.open
    opened = []
    wanted_parent = _identity(ancestor.parent.stat())

    def intercepted(name, flags, mode=0o777, *, dir_fd=None):
        descriptor = original_open(name, flags, mode, dir_fd=dir_fd)
        if (not opened and name == ancestor.name and dir_fd is not None
                and _identity(os.fstat(dir_fd)) == wanted_parent):
            opened.append(descriptor)
            moved = ancestor.with_name("moved")
            ancestor.rename(moved)
            if replacement == "symlink":
                ancestor.symlink_to(moved, target_is_directory=True)
            else:
                ancestor.mkdir()
        return descriptor

    monkeypatch.setattr(access.os, "open", intercepted)
    with pytest.raises(access.RepositoryAccessError) as error:
        access.AnchoredRepository(root)
    assert error.value.code == "repository_entry_changed"
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("location", ["root", "subdirectory"])
def test_repository_directory_churn_still_fails_closed(tmp_path, monkeypatch, location):
    _, root = _repo(tmp_path)
    target = root if location == "root" else root / "src"
    if target != root:
        target.mkdir()
    seen = _after_lstat(monkeypatch, target, _churn)
    with pytest.raises(access.RepositoryAccessError) as error:
        with access.AnchoredRepository(root) as repository:
            repository.list_directory("src")
    assert seen
    assert error.value.code == "repository_entry_changed"


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_mode", "st_uid", "st_gid"])
def test_ancestor_identity_and_permissions_are_not_ignored(tmp_path, field):
    before = tmp_path.stat()
    values = {key: getattr(before, key) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")}
    values[field] += 1
    with pytest.raises(access.RepositoryAccessError) as error:
        access._require_same_directory_identity(str(tmp_path), before, SimpleNamespace(**values))
    assert error.value.code == "repository_entry_changed"
