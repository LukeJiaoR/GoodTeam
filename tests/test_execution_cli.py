from __future__ import annotations

import json

import pytest

import goodteam_os.cli as cli
from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 230): _snapshot()}
        self.created_prs: list[dict[str, object]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_or_update_branch(self, **kwargs) -> dict[str, object]:
        return dict(kwargs)

    def commit_files(self, **kwargs) -> dict[str, object]:
        return dict(kwargs)

    def create_pr(self, **kwargs) -> GitHubBinding:
        self.created_prs.append(dict(kwargs))
        return GitHubBinding(
            repo=str(kwargs["repo"]),
            issue_number=int(kwargs["issue_number"]),
            pr_number=230,
            branch_name=str(kwargs["branch_name"]),
            execution_id=str(kwargs["execution_id"]),
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        return {"check_runs": [{"status": "completed", "conclusion": "success"}]}


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=230,
        title="P0-1F: Execution CLI/API Surface and E2E Test Contract",
        body="Expose the execution loop through stable CLI and local API surfaces.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/230",
        labels=["p0", "execution"],
    )


def _install_fake_api(monkeypatch, client: FakeGitHubClient):
    from goodteam_os.execution.api import ExecutionAPI

    def factory(project_root, *, github_client=None, clock=None):
        return ExecutionAPI(project_root, github_client=client, clock=clock)

    monkeypatch.setattr(cli, "ExecutionAPI", factory)


def test_execution_cli_commands_emit_stable_json(tmp_path, monkeypatch, capsys):
    client = FakeGitHubClient()
    _install_fake_api(monkeypatch, client)

    assert (
        cli.main(
            [
                "execution",
                "intake-github-issue",
                "--repo",
                "LukeJiaoR/GoodTeam",
                "--issue",
                "230",
                "--project-root",
                str(tmp_path),
                "--adapter",
                "fake",
            ]
        )
        == 0
    )
    intake = json.loads(capsys.readouterr().out)
    execution_id = intake["execution_id"]
    assert intake["status"] == "ok"

    for command, key in [("run", "worker_job"), ("pr", "binding"), ("ci", "ci"), ("replay", "events")]:
        assert (
            cli.main(["execution", command, "--execution-id", execution_id, "--project-root", str(tmp_path)])
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["execution_id"] == execution_id
        assert key in payload


def test_execution_cli_missing_required_args_returns_parser_error(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["execution", "run", "--project-root", str(tmp_path)])

    assert exc_info.value.code == 2


def test_execution_cli_service_error_returns_nonzero(tmp_path, capsys):
    exit_code = cli.main(
        [
            "execution",
            "intake-github-issue",
            "--repo",
            "LukeJiaoR/GoodTeam",
            "--issue",
            "230",
            "--project-root",
            str(tmp_path),
            "--adapter",
            "fake",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "GitHubClient is required" in captured.err
    assert captured.out == ""
