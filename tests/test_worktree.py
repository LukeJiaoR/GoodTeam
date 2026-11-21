"""Unit tests for git worktree isolation, base_ref chaining, scope check, and merge."""
from __future__ import annotations

import subprocess
import pytest

from goodteam.agents.worktree import (
    WorktreeError,
    branch_diff,
    cleanup_worktree,
    collect_result,
    create_iteration_branch,
    create_worktree,
    detect_default_branch,
    merge_branch,
)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "src").mkdir()
    (path / "docs").mkdir()
    (path / "src" / "app.py").write_text("print('v1')\n")
    (path / "docs" / "readme.md").write_text("# Doc\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def test_worktree_isolation_scope_check_and_merge(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert detect_default_branch(repo) == "main"

    branch, wt, base = create_worktree(repo, "run-1", state_dir=tmp_path / "state")
    assert base == "main"
    assert (wt / "src" / "app.py").is_file()

    # Edit inside allowed domain
    (wt / "src" / "app.py").write_text("print('v2')\n")
    res = collect_result(wt, base, write_domains=["src"], read_only=False)
    assert res["scope_ok"] is True
    assert res["changed_files"] == ["src/app.py"]
    assert res["diff_stat"]["files"] == 1
    assert "print('v2')" in res["diff_text"]
    # Main checkout untouched!
    assert "v1" in (repo / "src" / "app.py").read_text()

    # Violation when editing outside domain
    branch2, wt2, base2 = create_worktree(repo, "run-2", state_dir=tmp_path / "state")
    (wt2 / "docs" / "readme.md").write_text("# Modified\n")
    res2 = collect_result(wt2, base2, write_domains=["src"], read_only=False)
    assert res2["scope_ok"] is False

    # Read-only violation
    branch3, wt3, base3 = create_worktree(repo, "run-3", state_dir=tmp_path / "state")
    (wt3 / "src" / "extra.py").write_text("x = 1\n")
    res3 = collect_result(wt3, base3, read_only=True)
    assert res3["scope_ok"] is False

    # Iteration branch chaining (Strategy A): merge run-1 into iter branch without touching main checkout
    iter_branch = create_iteration_branch(repo, "it1", base_ref="main")
    merge_branch(repo, branch, iter_branch)
    # Main is still untouched
    assert "v1" in (repo / "src" / "app.py").read_text()
    # Run-4 branched off iter_branch sees v2!
    branch4, wt4, base4 = create_worktree(repo, "run-4", base_ref=iter_branch, state_dir=tmp_path / "state")
    assert "v2" in (wt4 / "src" / "app.py").read_text()

    diff = branch_diff(repo, "main", iter_branch)
    assert diff["changed_files"] == ["src/app.py"]

    # Human merges iter_branch into checked-out main
    merge_branch(repo, iter_branch, "main")
    assert "v2" in (repo / "src" / "app.py").read_text()

    for b, w in ((branch, wt), (branch2, wt2), (branch3, wt3), (branch4, wt4)):
        cleanup_worktree(repo, w, branch_name=b, delete_branch=True)
    assert not wt.exists()


def test_merge_fails_if_main_has_uncommitted_changes(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    branch, wt, base = create_worktree(repo, "r1", state_dir=tmp_path / "state")
    (wt / "src" / "app.py").write_text("print('v2')\n")
    collect_result(wt, base, write_domains=["src"])

    (repo / "src" / "app.py").write_text("dirty\n")
    with pytest.raises(WorktreeError):
        merge_branch(repo, branch, "main")
    cleanup_worktree(repo, wt, branch_name=branch, delete_branch=True)
