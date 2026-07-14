from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from goodteam_os.execution.github import GitHubIssueSnapshot
from goodteam_os.operator import enqueue_github_issue
from goodteam_os.operator_github import parse_github_issue_url, resolve_known_project
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os import operator, telegram


class FakeGitHubClient:
    def __init__(self):
        self.calls = []

    def fetch_issue(self, repo, issue_number):
        self.calls.append((repo, issue_number))
        return GitHubIssueSnapshot(
            repo=repo,
            issue_number=issue_number,
            title="Durable operator",
            body="Acceptance\n- survives restart",
            url=f"https://github.com/{repo}/issues/{issue_number}",
            labels=["p0"],
            updated_at="2026-07-14T01:02:03Z",
            default_branch="main",
        )


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    projects = workspace / "projects"
    projects.mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:LukeJiaoR/GoodTeam.git"],
        check=True,
    )
    (projects / "goodteam.local.yaml").write_text(
        "\n".join(
            [
                "name: GoodTeam",
                "key: GOODTEAM",
                "repos:",
                f"  app: {repo}",
                "worker_backend:",
                "  mode: fake",
                "base_branch: release/smoke",
                "merge_method: squash",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return workspace, repo


def test_issue_url_is_normalized_and_query_fragment_removed():
    parsed = parse_github_issue_url(
        "https://github.com/LukeJiaoR/GoodTeam/issues/341/?utm_source=test#fragment"
    )
    assert parsed.url == "https://github.com/LukeJiaoR/GoodTeam/issues/341"
    assert parsed.repository == "LukeJiaoR/GoodTeam"
    assert parsed.issue_number == 341


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "https://gitlab.com/LukeJiaoR/GoodTeam/issues/1",
        "https://github.com/LukeJiaoR/GoodTeam/pull/1",
        "https://github.com/LukeJiaoR/GoodTeam/issues/0",
    ],
)
def test_malformed_or_non_issue_urls_are_rejected(value):
    with pytest.raises(ValueError, match="GitHub Issue URL"):
        parse_github_issue_url(value)


def test_known_project_is_resolved_from_local_config_and_remote(tmp_path):
    workspace, repo = _workspace(tmp_path)
    project = resolve_known_project(workspace, "lukejiaor/goodteam")

    assert project.key == "GOODTEAM"
    assert project.project_root == repo.resolve()
    assert project.repository == "LukeJiaoR/GoodTeam"
    assert project.worker_backend == "fake"
    assert project.base_branch == "release/smoke"


def test_unknown_project_is_rejected_with_adoption_issue(tmp_path):
    workspace, _repo = _workspace(tmp_path)
    with pytest.raises(ValueError, match="#339"):
        resolve_known_project(workspace, "LukeJiaoR/Unknown")


def test_enqueue_validates_issue_and_deduplicates_source(tmp_path):
    workspace, _repo = _workspace(tmp_path)
    client = FakeGitHubClient()
    first, created = enqueue_github_issue(
        workspace=workspace,
        state_dir=tmp_path / "state",
        issue_url="https://github.com/LukeJiaoR/GoodTeam/issues/341",
        github_client=client,
    )
    duplicate, duplicate_created = enqueue_github_issue(
        workspace=workspace,
        state_dir=tmp_path / "state",
        issue_url="https://github.com/LukeJiaoR/GoodTeam/issues/341?again=1",
        github_client=client,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert first["source_revision"] == "2026-07-14T01:02:03Z"
    assert first["metadata"]["default_branch"] == "release/smoke"
    assert first["metadata"]["project_root"].endswith("/repo")
    assert len(DurableOperatorQueue(tmp_path / "state").list_items()) == 1
    assert client.calls == [("LukeJiaoR/GoodTeam", 341), ("LukeJiaoR/GoodTeam", 341)]


def _telegram_task_update(user_id="111", update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": int(user_id)},
            "chat": {"id": 12345},
            "text": "/gt task https://github.com/LukeJiaoR/GoodTeam/issues/341",
        },
    }


def test_telegram_task_requires_explicit_allowed_user(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operator,
        "enqueue_github_issue",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not enqueue")),
    )
    result = telegram.process_telegram_update(
        _telegram_task_update(),
        workspace=tmp_path,
        allowed_users=[],
        processed_callbacks=set(),
    )
    assert result == {"status": "ignored", "reason": "unauthorized user"}


def test_authorized_telegram_task_is_deduplicated_by_queue_adapter(tmp_path, monkeypatch):
    sent = []
    task = {
        "id": "task-1",
        "project_key": "GOODTEAM",
        "source_url": "https://github.com/LukeJiaoR/GoodTeam/issues/341",
        "phase": "queued",
    }
    outcomes = iter([(task, True), (task, False)])
    monkeypatch.setattr(operator, "enqueue_github_issue", lambda **kwargs: next(outcomes))
    monkeypatch.setattr(
        telegram,
        "_telegram_request",
        lambda token, method, params: sent.append({"method": method, "params": params}) or {"ok": True},
    )

    first = telegram.process_telegram_update(
        _telegram_task_update(update_id=1),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )
    duplicate = telegram.process_telegram_update(
        _telegram_task_update(update_id=1),
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert first["created"] is True
    assert duplicate["created"] is False
    assert "Already queued" in sent[-1]["params"]["text"]
