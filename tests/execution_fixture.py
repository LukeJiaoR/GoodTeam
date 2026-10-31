"""Minimal execution fixtures shared by the retained sandbox smoke checks."""

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot


class FakeClock:
    def __init__(self, timestamps=None):
        self.timestamps = list(timestamps or ["2026-07-04T03:16:40Z"])
        self.index = 0

    def __call__(self):
        position = min(self.index, len(self.timestamps) - 1)
        self.index += 1
        return self.timestamps[position]


class FakeGitHubClient:
    def __init__(self, issues):
        self.issues = issues
        self.fetches = []

    def fetch_issue(self, repo, issue_number):
        self.fetches.append((repo, issue_number))
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_pr(self, **_kwargs) -> GitHubBinding:
        raise AssertionError("This test fixture must not create pull requests.")


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=228,
        title="P0-1D: Execution Orchestrator Skeleton",
        body="Wire GitHub issue intake to worker dispatch and ledger replay.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/228",
        labels=["p0", "execution"],
    )
