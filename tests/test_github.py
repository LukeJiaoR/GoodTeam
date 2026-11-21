"""Unit tests for GitHub CLI integration (issues, task import, PR, CI checks)."""
from __future__ import annotations

import json
import subprocess
import pytest

from goodteam.integrations import github as gh_client
from goodteam.store import Store


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_github_issues_import_idempotency_and_pr_flows(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(state_dir=tmp_path / "state")
    monkeypatch.setattr(gh_client, "_gh_exe", lambda: "/usr/bin/fake-gh")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "git" and "push" in cmd:
            return _fake_completed(stdout="Branch pushed\n")
        if cmd[1:3] == ["repo", "view"]:
            return _fake_completed(stdout=json.dumps({"nameWithOwner": "org/demo", "url": "https://github.com/org/demo"}))
        if cmd[1:3] == ["issue", "list"]:
            return _fake_completed(stdout=json.dumps([{"number": 42, "title": "Fix bug", "state": "OPEN"}]))
        if cmd[1:3] == ["issue", "view"]:
            return _fake_completed(stdout=json.dumps({
                "number": 42, "title": "Fix bug", "body": "Details here", "url": "https://github.com/org/demo/issues/42"
            }))
        if cmd[1:3] == ["pr", "create"]:
            return _fake_completed(stdout="https://github.com/org/demo/pull/7\n")
        if cmd[1:3] == ["pr", "view"]:
            return _fake_completed(stdout=json.dumps({
                "number": 7, "state": "OPEN", "url": "https://github.com/org/demo/pull/7",
                "statusCheckRollup": [{"name": "pytest", "conclusion": "SUCCESS"}],
            }))
        return _fake_completed(returncode=1, stderr="unexpected cmd")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        proj = store.create_project(name="demo", path=str(repo))
        assert gh_client.repo_info(repo)["nameWithOwner"] == "org/demo"

        issues = gh_client.list_issues(repo, state="open", limit=10)
        assert len(issues) == 1 and issues[0]["number"] == 42

        task1 = gh_client.import_issue_to_task(store, proj["id"], repo, 42)
        assert task1["title"] == "#42: Fix bug"
        assert "https://github.com/org/demo/issues/42" in task1["description"]
        assert task1["source_ref"] == "github:issue:42"

        # Importing same issue again returns existing task without duplicating
        task2 = gh_client.import_issue_to_task(store, proj["id"], repo, 42)
        assert task2["id"] == task1["id"]
        assert len(store.list_tasks(proj["id"])) == 1

        pr = gh_client.push_and_create_pr(repo, branch="goodteam/run-1", base="main", title="Fix #42")
        assert pr["url"] == "https://github.com/org/demo/pull/7"

        checks = gh_client.pr_checks(repo, pr["url"])
        assert checks["statusCheckRollup"][0]["conclusion"] == "SUCCESS"
    finally:
        store.close()


def test_github_existing_pr_fallback_and_error_guards(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(gh_client, "_gh_exe", lambda: "/usr/bin/fake-gh")

    # Input validation
    with pytest.raises(gh_client.GitHubError):
        gh_client.list_issues(repo, state="invalid")
    with pytest.raises(gh_client.GitHubError):
        gh_client.get_issue(repo, 0)
    with pytest.raises(gh_client.GitHubError):
        gh_client.push_and_create_pr(repo, branch="-evil", base="main", title="x")
    with pytest.raises(gh_client.GitHubError):
        gh_client.pr_checks(repo, "--bad")

    # Existing PR fallback when `gh pr create` returns non-zero
    def fake_existing_pr(cmd, **kwargs):
        if cmd[0] == "git":
            return _fake_completed()
        if cmd[1:3] == ["pr", "create"]:
            return _fake_completed(returncode=1, stderr="a pull request already exists")
        if cmd[1:3] == ["pr", "view"]:
            return _fake_completed(stdout=json.dumps({"url": "https://github.com/org/demo/pull/9", "number": 9, "state": "OPEN"}))
        return _fake_completed(returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_existing_pr)
    pr = gh_client.push_and_create_pr(repo, branch="goodteam/run-9", base="main", title="Existing")
    assert pr["url"] == "https://github.com/org/demo/pull/9"
    assert pr["number"] == 9

    # Timeout and bad JSON mapped cleanly to GitHubError
    def fake_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=20)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    with pytest.raises(gh_client.GitHubError):
        gh_client.list_issues(repo)

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _fake_completed(stdout="not-json"))
    with pytest.raises(gh_client.GitHubError):
        gh_client.list_issues(repo)
