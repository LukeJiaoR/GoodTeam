from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from goodteam_os import dispatch_workspace
from goodteam_os.dispatch_workspace import (
    DispatchWorkspaceError,
    dispatch_artifact_root,
    dispatch_workspace_lock,
    prepare_dispatch_workspace,
)
from goodteam_os.memory_storage import MemoryStorageError
from goodteam_os.repository_access import RepositoryAccessError
from goodteam_os.repository_snapshot import SnapshotLimits


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", *arguments], cwd=root,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "Tests",
             "GIT_AUTHOR_EMAIL": "tests@goodteam.invalid", "GIT_COMMITTER_NAME": "Tests",
             "GIT_COMMITTER_EMAIL": "tests@goodteam.invalid"},
    ).strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "tracked.txt").write_bytes(b"original\n")
    (root / "delete.txt").write_bytes(b"delete me\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "original")
    return root


def test_dirty_raw_baseline_preserves_original_head_index_binary_and_allowed_untracked(repo):
    (repo / ".gitignore").write_text("ignored*\n", encoding="utf-8")
    (repo / "ignored-tracked.bin").write_bytes(b"\x00\xfforiginal")
    git(repo, "add", ".gitignore")
    git(repo, "add", "--force", "ignored-tracked.bin")
    (repo / "tracked.txt").write_bytes(b"staged\r\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_bytes(b"raw unstaged\r\n\xff")
    (repo / "delete.txt").unlink()
    (repo / "untracked.bin").write_bytes(b"\x00\x01\xff")
    (repo / "ignored-local.bin").write_bytes(b"ignored")
    (repo / ".goodteam").mkdir()
    (repo / ".goodteam/private").write_bytes(b"private")
    head, index = git(repo, "rev-parse", "HEAD"), (repo / ".git/index").read_bytes()
    source_status = git(repo, "status", "--porcelain=v1", "-uall")
    source_status = b"\n".join(line for line in source_status.splitlines() if b".goodteam/" not in line)

    workspace = prepare_dispatch_workspace(repo, "raw", ["one", "two"])

    assert workspace.metadata["original_head"] == head.decode()
    assert (workspace.artifact_root / "original.index").read_bytes() == index
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "rev-parse", "HEAD") == head
    for job in ["one", "two"]:
        root = workspace.workspace(job)
        assert git(root, "rev-parse", "HEAD") == head
        assert git(root, "rev-parse", "refs/goodteam/dispatch-baseline").decode() == workspace.metadata["baseline_commit"]
        assert (root / ".git/index").read_bytes() == index
        assert git(root, "status", "--porcelain=v1", "-uall") == source_status
        assert (root / "tracked.txt").read_bytes() == b"raw unstaged\r\n\xff"
        assert (root / "ignored-tracked.bin").read_bytes() == b"\x00\xfforiginal"
        assert (root / "untracked.bin").read_bytes() == b"\x00\x01\xff"
        assert not (root / "ignored-local.bin").exists()
        assert not (root / ".goodteam").exists()
        assert not (root / "delete.txt").exists()
        assert workspace.collect(job)["changed_files"] == []
    object_path = f".git/objects/{head[:2].decode()}/{head[2:].decode()}"
    assert (repo / object_path).stat().st_ino != (workspace.workspace("one") / object_path).stat().st_ino
    assert workspace.workspace("one") != workspace.workspace("two")


def test_collect_compares_raw_bytes_after_worker_commit_rewrite_delete_rename_and_binary(repo):
    (repo / "initial-untracked.txt").write_bytes(b"initial raw\n")
    workspace = prepare_dispatch_workspace(repo, "collect", ["one"])
    root = workspace.workspace("one")
    (root / "tracked.txt").write_bytes(b"rewritten\n")
    (root / "delete.txt").unlink()
    (root / "initial-untracked.txt").rename(root / "renamed.bin")
    (root / "renamed.bin").write_bytes(b"\x00binary\xff")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "worker committed its files")
    (root / "new.bin").write_bytes(b"\x00new")
    original_index = (root / ".git/index").read_bytes()

    result = workspace.collect("one")

    assert result["changed_files"] == ["delete.txt", "initial-untracked.txt", "new.bin", "renamed.bin", "tracked.txt"]
    assert "files" not in result
    assert git(root, "rev-parse", "refs/goodteam/dispatch-result").decode() == result["result_commit"]
    assert (root / ".git/index").read_bytes() == original_index
    assert git(root, "show", f"{result['result_commit']}:renamed.bin") == b"\x00binary\xff"
    assert (repo / "tracked.txt").read_bytes() == b"original\n"
    assert (repo / "initial-untracked.txt").read_bytes() == b"initial raw\n"


def test_existing_untracked_rewrite_is_reported_without_a_file_count_change(repo):
    (repo / "notes.txt").write_bytes(b"before\n")
    workspace = prepare_dispatch_workspace(repo, "rewrite", ["one"])
    (workspace.workspace("one") / "notes.txt").write_bytes(b"after!\n")
    assert workspace.collect("one")["changed_files"] == ["notes.txt"]


def test_integration_combines_independent_jobs_without_touching_source_or_siblings(repo):
    (repo / "tracked.txt").write_bytes(b"dirty task baseline\n")
    (repo / "local.bin").write_bytes(b"\x00baseline")
    workspace = prepare_dispatch_workspace(repo, "merge", ["one", "two"])
    (workspace.workspace("one") / "tracked.txt").write_bytes(b"one result\n")
    assert (workspace.workspace("two") / "tracked.txt").read_bytes() == b"dirty task baseline\n"
    (workspace.workspace("two") / "two.bin").write_bytes(b"\x00two\xff")
    (repo / "tracked.txt").write_bytes(b"source continued working\n")
    source_index = (repo / ".git/index").read_bytes()

    result = workspace.integrate(["one", "two"])

    assert result["status"] == "succeeded"
    assert result["changed_files"] == ["tracked.txt", "two.bin"]
    candidate = Path(result["candidate_path"])
    assert candidate not in [repo, workspace.workspace("one"), workspace.workspace("two")]
    assert (candidate / "tracked.txt").read_bytes() == b"one result\n"
    assert (candidate / "two.bin").read_bytes() == b"\x00two\xff"
    assert (candidate / "local.bin").read_bytes() == b"\x00baseline"
    assert (repo / "tracked.txt").read_bytes() == b"source continued working\n"
    assert (repo / ".git/index").read_bytes() == source_index
    assert not (repo / "two.bin").exists()


def test_conflicting_jobs_retain_conflicted_candidate_and_both_worker_results(repo):
    workspace = prepare_dispatch_workspace(repo, "conflict", ["one", "two"])
    (workspace.workspace("one") / "tracked.txt").write_bytes(b"first result\n")
    (workspace.workspace("two") / "tracked.txt").write_bytes(b"second result\n")
    result = workspace.integrate(["one", "two"])
    assert result["status"] == "conflicted"
    assert result["candidate_commit"] is None
    assert result["conflicts"] == ["tracked.txt"]
    candidate = Path(result["candidate_path"])
    assert b"<<<<<<<" in (candidate / "tracked.txt").read_bytes()
    assert git(candidate, "ls-files", "--unmerged")
    assert (candidate.parent / "integration.json").exists()
    assert (workspace.workspace("one") / "tracked.txt").read_bytes() == b"first result\n"
    assert (workspace.workspace("two") / "tracked.txt").read_bytes() == b"second result\n"
    assert (repo / "tracked.txt").read_bytes() == b"original\n"


def test_raw_attributes_do_not_normalize_baseline_or_result(repo):
    (repo / ".gitattributes").write_bytes(b"*.txt text eol=crlf\n")
    (repo / "tracked.txt").write_bytes(b"raw\r\nline\n")
    workspace = prepare_dispatch_workspace(repo, "attributes", ["one"])
    assert workspace.collect("one")["changed_files"] == []
    (workspace.workspace("one") / "tracked.txt").write_bytes(b"changed\r\nraw\n")
    result = workspace.integrate(["one"])
    assert result["status"] == "succeeded"
    assert (Path(result["candidate_path"]) / "tracked.txt").read_bytes() == b"changed\r\nraw\n"


def test_replay_reopens_fixed_baseline_without_recapturing_source(repo):
    workspace = prepare_dispatch_workspace(repo, "replay", ["one"])
    (workspace.workspace("one") / "tracked.txt").write_bytes(b"partial result\n")
    (repo / "tracked.txt").write_bytes(b"new source work\n")
    replay = prepare_dispatch_workspace(repo, "replay", None)
    assert replay.metadata == workspace.metadata
    assert replay.workspace("one") == workspace.workspace("one")
    assert replay.collect("one")["changed_files"] == ["tracked.txt"]
    with pytest.raises(DispatchWorkspaceError, match="identity"):
        prepare_dispatch_workspace(repo, "replay", ["another-job"])
    with pytest.raises(DispatchWorkspaceError, match="No retained"):
        prepare_dispatch_workspace(repo, "absent", None)
    (workspace.artifact_root / "baseline/tracked.txt").write_bytes(b"corrupt baseline\n")
    with pytest.raises(DispatchWorkspaceError, match="baseline bytes changed"):
        prepare_dispatch_workspace(repo, "replay", None)


@pytest.mark.parametrize("unsupported", ["symlink", "sparse", "split", "gitlink"])
def test_unsupported_source_capture_fails_closed(repo, tmp_path, unsupported):
    if unsupported == "symlink":
        secret = tmp_path / "secret"
        secret.write_bytes(b"outside")
        (repo / "link").symlink_to(secret)
    elif unsupported == "sparse":
        git(repo, "config", "core.sparseCheckout", "true")
    elif unsupported == "split":
        git(repo, "update-index", "--split-index")
    else:
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{git(repo, 'rev-parse', 'HEAD').decode()},submodule")
    with pytest.raises((DispatchWorkspaceError, RepositoryAccessError)):
        prepare_dispatch_workspace(repo, "unsupported", ["one"])
    assert not (dispatch_artifact_root(repo, "unsupported") / "baseline.json").exists()


def test_incomplete_limit_and_source_change_do_not_publish_baseline(repo, monkeypatch):
    with pytest.raises(DispatchWorkspaceError, match="limit"):
        prepare_dispatch_workspace(repo, "limited", ["one"], limits=SnapshotLimits(max_file_bytes=2))
    real_clone = dispatch_workspace._clone

    def concurrent_change(source, destination):
        real_clone(source, destination)
        (repo / "tracked.txt").write_bytes(b"concurrent source work\n")

    monkeypatch.setattr(dispatch_workspace, "_clone", concurrent_change)
    with pytest.raises(DispatchWorkspaceError, match="Source changed"):
        prepare_dispatch_workspace(repo, "changing", ["one"])
    artifacts = dispatch_artifact_root(repo, "changing")
    assert (artifacts / "baseline").exists()
    assert not (artifacts / "baseline.json").exists()
    assert not (artifacts / "jobs").exists()
    with pytest.raises(DispatchWorkspaceError, match="Incomplete"):
        prepare_dispatch_workspace(repo, "changing", ["one"])


def test_worker_git_rebinding_is_rejected_before_result_collection(repo):
    workspace = prepare_dispatch_workspace(repo, "rebind", ["one"])
    root = workspace.workspace("one")
    git(root, "config", "core.worktree", str(repo))
    with pytest.raises(DispatchWorkspaceError, match="worktree"):
        workspace.collect("one")
    assert (repo / "tracked.txt").read_bytes() == b"original\n"


def test_source_fsmonitor_is_never_executed(repo, tmp_path):
    marker, executable = tmp_path / "executed", tmp_path / "monitor.sh"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    executable.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(executable))
    prepare_dispatch_workspace(repo, "monitor", ["one"])
    assert not marker.exists()


def test_task_lock_is_reentrant_and_rejects_symlink_lock(repo, tmp_path):
    with dispatch_workspace_lock(repo, "lock") as artifact_root:
        with dispatch_workspace_lock(repo, "lock") as same_root:
            assert artifact_root == same_root
    unsafe = tmp_path / "unsafe-artifacts"
    unsafe.mkdir()
    secret = tmp_path / "secret"
    secret.write_bytes(b"untouched")
    (unsafe / ".store.lock").symlink_to(secret)
    with pytest.raises(MemoryStorageError):
        with dispatch_workspace_lock(repo, "unsafe", artifact_root=unsafe):
            pytest.fail("Unsafe lock acquired")
    assert secret.read_bytes() == b"untouched"
