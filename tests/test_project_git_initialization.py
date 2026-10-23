from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import web
from goodteam_os.project_import import import_project
from goodteam_os.repository_snapshot import inspect_repository_git


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def configure(workspace: Path, root: Path, **kwargs):
    return web.create_local_project_config(
        workspace=workspace, project_name="Example", project_key="EXAMPLE",
        project_root=str(root), **kwargs,
    )


@pytest.mark.parametrize("failed_operation", ["init", "add", "commit"])
def test_new_project_initialization_failure_is_visible_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_operation: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    run = web._run_git_project_command

    def fail(command, *args, **kwargs):
        if failed_operation in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(web, "_run_git_project_command", fail)
    result = configure(workspace, root)["git"]
    assert result["initialized"] is False
    assert result["baseline_required"] is True
    assert result["state"] == ("non_git" if failed_operation == "init" else "unborn")
    assert "initial_files" in result["error"]
    assert any("simulated failure" in warning for warning in result["warnings"])

    monkeypatch.setattr(web, "_run_git_project_command", run)
    # A restarted request cannot treat leftover .git as a completed baseline,
    # or silently include a file created after the failed operation.
    (root / "private.txt").write_text("user file\n", encoding="utf-8")
    request = {"project_key": "EXAMPLE"}
    assert web.initialize_project_git_for_request(workspace, request)["baseline_required"] is True
    request["initial_files"] = ["README.md", ".gitignore"]
    recovered = web.initialize_project_git_for_request(workspace, request)
    assert recovered["initialized"] is True
    assert recovered["state"] == "valid_head"
    assert recovered["baseline_required"] is False
    assert git(root, "ls-tree", "--name-only", "HEAD").splitlines() == [".gitignore", "README.md"]
    head = recovered["head_sha"]
    replayed = web.initialize_project_git_for_request(workspace, request)
    assert replayed["initialized"] is False
    assert replayed["head_sha"] == head
    assert git(root, "rev-list", "--count", "HEAD") == "1"
    assert (root / "private.txt").read_text(encoding="utf-8") == "user file\n"


def test_imported_non_git_directory_requires_explicit_file_selection(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "secret.txt").write_text("private\n", encoding="utf-8")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    result = web.initialize_project_git_for_request(workspace, {"project_key": key})
    assert result["state"] == "non_git"
    assert result["baseline_required"] is True
    assert not (root / ".git").exists()
    result = web.initialize_project_git_for_request(
        workspace, {"project_key": key, "initial_files": ["app.py"]},
    )
    assert result["baseline_required"] is False
    assert git(root, "ls-tree", "--name-only", "HEAD") == "app.py"
    assert not (root / "README.md").exists()


def test_existing_unborn_selection_preserves_unrelated_index_and_user_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-qb", "user-branch")
    git(root, "config", "user.name", "Project Owner")
    git(root, "config", "user.email", "owner@example.invalid")
    (root / "selected.txt").write_text("selected\n", encoding="utf-8")
    (root / "staged.txt").write_text("staged version\n", encoding="utf-8")
    git(root, "add", "staged.txt")
    staged_oid = git(root, "rev-parse", ":staged.txt")
    (root / "staged.txt").write_text("unstaged user change\n", encoding="utf-8")
    before_config = (root / ".git" / "config").read_bytes()
    configure(workspace, root, init_git=False)
    result = web.initialize_project_git_for_request(
        workspace, {"project_key": "EXAMPLE", "initial_files": ["selected.txt"]},
    )
    assert result["state"] == "valid_head"
    assert result["branch"] == "user-branch"
    assert git(root, "ls-tree", "--name-only", "HEAD") == "selected.txt"
    assert git(root, "rev-parse", ":staged.txt") == staged_oid
    assert (root / "staged.txt").read_text(encoding="utf-8") == "unstaged user change\n"
    assert (root / ".git" / "config").read_bytes() == before_config
    assert git(root, "log", "-1", "--format=%an <%ae>") == "Project Owner <owner@example.invalid>"


@pytest.mark.parametrize("selection", [["."], ["../outside.txt"], [".git/config"], [".goodteam/state"], ["folder"], ["link.txt"], "app.py", []])
def test_initial_file_selection_rejects_unbounded_or_nonregular_paths(tmp_path: Path, selection) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("pass\n", encoding="utf-8")
    (root / "folder").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    configure(workspace, root, init_git=False)
    with pytest.raises(ValueError):
        web.initialize_project_git_for_request(
            workspace, {"project_key": "EXAMPLE", "initial_files": selection},
        )
    assert not (root / ".git").exists()
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_git_init_reuses_existing_history_and_linked_worktree(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    first = configure(workspace, root)["git"]["head_sha"]
    git(root, "worktree", "add", "-qb", "topic", str(tmp_path / "linked"))
    linked = tmp_path / "linked"
    (linked / "user.txt").write_text("untracked\n", encoding="utf-8")
    config_before = (root / ".git" / "config").read_bytes()
    configure(workspace, linked)
    result = web.initialize_project_git_for_request(workspace, {"project_key": "EXAMPLE"})
    assert result["initialized"] is False
    assert result["head_sha"] == first
    assert result["baseline_required"] is False
    assert (root / ".git" / "config").read_bytes() == config_before
    assert git(linked, "status", "--short") == "?? user.txt"


def test_git_init_refuses_invalid_metadata_and_nested_project_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    result = configure(workspace, root)["git"]
    assert result["state"] == "invalid"
    assert result["baseline_required"] is True
    assert list((root / ".git").iterdir()) == []
    (root / ".git").rmdir()
    git(root, "init", "-qb", "main")
    child = root / "child"
    child.mkdir()
    result = configure(workspace, child)["git"]
    assert result["state"] == "invalid"
    assert not (child / ".git").exists()


def test_git_initialization_ignores_ambient_git_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    git(unrelated, "init", "-qb", "other")
    before = (unrelated / ".git" / "config").read_bytes()
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    result = configure(workspace, root)["git"]
    assert result["state"] == "valid_head"
    assert inspect_repository_git(unrelated)["state"] == "unborn"
    assert (unrelated / ".git" / "config").read_bytes() == before


def test_home_baseline_projection_refreshes_after_initial_commit_without_rewriting_readiness(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"name": "example"}), encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    readiness_file = workspace / "state" / "projects" / key / "readiness.json"
    readiness_before = readiness_file.read_bytes()
    status, first = web.handle_project_api_get(workspace, f"/api/projects/{key}/home")
    assert status == 200
    assert first["git_baseline"]["baseline_required"] is True
    assert not (root / ".git").exists()
    initialized = web.initialize_project_git_for_request(
        workspace, {"project_key": key, "initial_files": ["package.json"]},
    )
    status, refreshed = web.handle_project_api_get(workspace, f"/api/projects/{key}/home")
    assert status == 200
    assert refreshed["git_baseline"]["baseline_required"] is False
    assert refreshed["git_baseline"]["head_sha"] == initialized["head_sha"]
    assert refreshed["capabilities"] == first["capabilities"]
    assert readiness_file.read_bytes() == readiness_before


def test_remote_creation_is_not_attempted_without_a_valid_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("GH_TOKEN", "test-token")
    run = web._run_git_project_command

    def check_no_remote(command, *args, **kwargs):
        assert command[0] != "gh"
        return run(command, *args, **kwargs)

    monkeypatch.setattr(web, "_run_git_project_command", check_no_remote)
    result = configure(tmp_path / "workspace", root, init_git=False, create_remote=True)["git"]
    assert result["remote_created"] is False
    assert result["baseline_required"] is True
