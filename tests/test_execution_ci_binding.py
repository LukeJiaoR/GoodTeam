from __future__ import annotations

from pathlib import Path

from goodteam_os.execution import (
    ExecutionLedger,
    ExecutionOrchestrator,
    GitHubBinding,
    GitHubExecutionBindingStore,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
)
from goodteam_os.execution.ci import CIStatusService


CREATED_AT = "2026-07-04T03:16:40Z"
STARTED_AT = "2026-07-04T03:20:00Z"
FINISHED_AT = "2026-07-04T03:25:00Z"
CI_AT = "2026-07-04T03:40:00Z"


class FakeClock:
    def __init__(self, timestamps: list[str] | None = None):
        self.timestamps = list(timestamps or [CREATED_AT])
        self.index = 0

    def __call__(self) -> str:
        if self.index >= len(self.timestamps):
            return self.timestamps[-1]
        value = self.timestamps[self.index]
        self.index += 1
        return value


class FakeGitHubClient:
    def __init__(self, checks: dict[str, object] | None = None):
        self.issues = {("lukejiaor/goodteam", 229): _snapshot()}
        self.checks = checks or {}
        self.fetches: list[tuple[str, str]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_pr(self, **_kwargs) -> GitHubBinding:
        raise AssertionError("CI tests should not create pull requests")

    def create_or_update_branch(self, **_kwargs) -> dict[str, object]:
        raise AssertionError("CI tests should not create branches")

    def commit_files(self, **_kwargs) -> dict[str, object]:
        raise AssertionError("CI tests should not commit files")

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        self.fetches.append((repo, ref))
        return self.checks


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        title="P0-1E: PR and CI Binding Loop",
        body="Bind a completed execution to a PR and observe checks.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/229",
        labels=["p0", "execution"],
    )


def _completed_execution(tmp_path: Path, client: FakeGitHubClient):
    orchestrator = ExecutionOrchestrator(tmp_path, client)
    orchestrator._clock = FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT])
    queued = orchestrator.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        adapter="fake",
    )
    return orchestrator.run_execution(queued.execution_id)


def _check_run(status: str, conclusion: str | None = None) -> dict[str, str | None]:
    return {"status": status, "conclusion": conclusion}


def test_successful_checks_summarize_to_success(tmp_path):
    service = CIStatusService(tmp_path, FakeGitHubClient())

    summary = service.summarize_checks(
        {"check_runs": [_check_run("completed", "success"), _check_run("completed", "neutral")]}
    )

    assert summary["state"] == "success"
    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert summary["failed"] == 0
    assert summary["pending"] == 0


def test_failed_checks_summarize_to_failure(tmp_path):
    service = CIStatusService(tmp_path, FakeGitHubClient())

    summary = service.summarize_checks(
        {"check_runs": [_check_run("completed", "success"), _check_run("completed", "failure")]}
    )

    assert summary["state"] == "failure"
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1


def test_pending_checks_summarize_to_pending(tmp_path):
    service = CIStatusService(tmp_path, FakeGitHubClient())

    summary = service.summarize_checks(
        {"check_runs": [_check_run("completed", "success"), _check_run("in_progress")]}
    )

    assert summary["state"] == "pending"
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["pending"] == 1


def test_empty_or_unavailable_checks_summarize_to_unavailable(tmp_path):
    service = CIStatusService(tmp_path, FakeGitHubClient())

    assert service.summarize_checks({})["state"] == "unavailable"
    assert service.summarize_checks({"check_runs": []})["state"] == "unavailable"
    assert service.summarize_checks({"unavailable": True})["state"] == "unavailable"


def test_observing_ci_fetches_checks_and_appends_ledger_event(tmp_path):
    checks = {"check_runs": [_check_run("completed", "success")]}
    client = FakeGitHubClient(checks)
    run = _completed_execution(tmp_path, client)
    binding = GitHubBinding(
        repo="LukeJiaoR/GoodTeam",
        issue_number=229,
        pr_number=123,
        branch_name=run.branch_name,
        execution_id=run.execution_id,
    )
    GitHubExecutionBindingStore(tmp_path).write_binding(binding)

    summary = CIStatusService(tmp_path, client, clock=FakeClock([CI_AT])).observe_execution_checks(
        run.execution_id
    )

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert client.fetches == [("LukeJiaoR/GoodTeam", run.branch_name)]
    assert summary["state"] == "success"
    assert events[-1].payload["transition"] == "ci_observed"
    assert events[-1].payload["repo"] == "LukeJiaoR/GoodTeam"
    assert events[-1].payload["ref"] == run.branch_name
    assert events[-1].payload["state"] == "success"


def test_observing_ci_without_pr_binding_records_unavailable(tmp_path):
    client = FakeGitHubClient({"check_runs": [_check_run("completed", "success")]})
    run = _completed_execution(tmp_path, client)
    binding_path = tmp_path / ".goodteam" / "executions" / run.execution_id / "bindings.json"
    binding_path.unlink()

    summary = CIStatusService(tmp_path, client, clock=FakeClock([CI_AT])).observe_execution_checks(
        run.execution_id
    )

    events = ExecutionLedger(tmp_path).read_events(run.execution_id)
    assert client.fetches == []
    assert summary["state"] == "unavailable"
    assert events[-1].payload["transition"] == "ci_observed"
    assert events[-1].payload["reason"] == "missing_pr_binding"
