from __future__ import annotations

import pytest

from goodteam_os.execution import (
    ExecutionLedger,
    ExecutionOrchestrator,
    ExecutionRunStatus,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
    GitHubBinding,
    LedgerEventType,
    execution_id_from_task,
    task_id_from_github_issue,
)
from goodteam_os.workers import WorkerJobStore


CREATED_AT = "2026-07-04T03:16:40Z"
STARTED_AT = "2026-07-04T03:20:00Z"
FINISHED_AT = "2026-07-04T03:25:00Z"


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
    def __init__(self, issues: dict[tuple[str, int], GitHubIssueSnapshot]):
        self.issues = issues
        self.fetches: list[tuple[str, int]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        self.fetches.append((repo, issue_number))
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_pr(self, **_kwargs) -> GitHubBinding:
        raise AssertionError("orchestrator skeleton must not create pull requests")


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=228,
        title="P0-1D: Execution Orchestrator Skeleton",
        body="Wire GitHub issue intake to worker dispatch and ledger replay.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/228",
        labels=["p0", "execution"],
    )


def _orchestrator(tmp_path, *, clock: FakeClock | None = None) -> ExecutionOrchestrator:
    orchestrator = ExecutionOrchestrator(
        tmp_path,
        FakeGitHubClient({("lukejiaor/goodteam", 228): _snapshot()}),
    )
    orchestrator._clock = clock or FakeClock()
    return orchestrator


def _execution_id() -> str:
    return execution_id_from_task(task_id_from_github_issue("LukeJiaoR/GoodTeam", 228))


def test_fixture_github_issue_creates_exactly_one_worker_job(tmp_path):
    run = _orchestrator(tmp_path).intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=228,
        adapter="fake",
        role="implementation",
    )

    jobs = WorkerJobStore(tmp_path).list_jobs()
    assert run.execution_id == _execution_id()
    assert run.status is ExecutionRunStatus.QUEUED
    assert len(jobs) == 1
    assert jobs[0]["run_id"] == run.execution_id
    assert jobs[0]["role"] == "implementation"
    assert jobs[0]["adapter"] == "fake"
    assert jobs[0]["task_source"] == "github:LukeJiaoR/GoodTeam#228"


def test_duplicate_intake_returns_existing_execution_without_second_worker_job(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    first = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=228, adapter="fake")
    second = orchestrator.intake_github_issue(repo=" lukejiaor/goodteam ", issue_number=228, adapter="fake")

    assert second == first
    assert len(WorkerJobStore(tmp_path).list_jobs()) == 1


def test_intake_writes_task_run_and_expected_transition_events(tmp_path):
    run = _orchestrator(tmp_path).intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=228,
        adapter="fake",
        role="implementation",
    )

    ledger = ExecutionLedger(tmp_path)
    task = ledger.read_task(run.execution_id)
    events = ledger.read_events(run.execution_id)

    assert task is not None
    assert task.task_id == run.task_id
    assert ledger.read_run(run.execution_id) == run
    assert [event.event_type for event in events] == [
        LedgerEventType.CREATED,
        LedgerEventType.STATUS_CHANGED,
    ]
    assert events[0].payload["transition"] == "intake_created"
    assert events[0].payload["repo"] == "LukeJiaoR/GoodTeam"
    assert events[0].payload["issue_number"] == 228
    assert events[1].payload["transition"] == "worker_job_created"
    assert events[1].payload["worker_job_id"] == run.worker_job_id
    assert events[1].payload["adapter"] == "fake"
    assert events[1].payload["role"] == "implementation"
    assert events[1].payload["branch_name"] == run.branch_name


def test_run_execution_with_fake_adapter_appends_start_and_completion_events(tmp_path):
    orchestrator = _orchestrator(tmp_path, clock=FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT]))
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=228, adapter="fake")

    completed = orchestrator.run_execution(queued.execution_id)

    events = ExecutionLedger(tmp_path).read_events(queued.execution_id)
    assert completed.status is ExecutionRunStatus.SUCCEEDED
    assert completed.updated_at == FINISHED_AT
    assert [event.event_type for event in events] == [
        LedgerEventType.CREATED,
        LedgerEventType.STATUS_CHANGED,
        LedgerEventType.STATUS_CHANGED,
        LedgerEventType.COMPLETED,
    ]
    assert events[2].payload["transition"] == "execution_started"
    assert events[3].payload["transition"] == "execution_completed"
    assert events[3].payload["worker_status"] == "completed"
    assert "fake smoke adapter" in events[3].payload["summary"]


def test_failed_worker_status_is_recorded_in_ledger(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, clock=FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT]))
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=228, adapter="fake")

    def fail_job(self, job_id: str) -> dict[str, object]:
        return {
            "job_id": job_id,
            "run_id": queued.execution_id,
            "status": "failed",
            "summary": "Worker failed during smoke execution.",
            "failure_class": "test_failure",
        }

    monkeypatch.setattr(WorkerJobStore, "run_job", fail_job)

    failed = orchestrator.run_execution(queued.execution_id)

    events = ExecutionLedger(tmp_path).read_events(queued.execution_id)
    assert failed.status is ExecutionRunStatus.FAILED
    assert events[-1].event_type is LedgerEventType.FAILED
    assert events[-1].payload["transition"] == "execution_failed"
    assert events[-1].payload["failure_class"] == "test_failure"
    assert events[-1].payload["summary"] == "Worker failed during smoke execution."


def test_worker_exception_is_recorded_then_reraised(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, clock=FakeClock([CREATED_AT, STARTED_AT, FINISHED_AT]))
    queued = orchestrator.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=228, adapter="fake")

    def raise_job(self, job_id: str) -> dict[str, object]:
        raise RuntimeError(f"boom while running {job_id}")

    monkeypatch.setattr(WorkerJobStore, "run_job", raise_job)

    with pytest.raises(RuntimeError, match="boom while running"):
        orchestrator.run_execution(queued.execution_id)

    run = ExecutionLedger(tmp_path).read_run(queued.execution_id)
    events = ExecutionLedger(tmp_path).read_events(queued.execution_id)
    assert run is not None
    assert run.status is ExecutionRunStatus.FAILED
    assert events[-1].event_type is LedgerEventType.FAILED
    assert events[-1].payload["failure_class"] == "RuntimeError"
    assert "boom while running" in events[-1].payload["error"]


def test_status_reconstructs_from_disk_with_fresh_orchestrator(tmp_path):
    run = _orchestrator(tmp_path).intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=228,
        adapter="fake",
    )

    status = ExecutionOrchestrator(
        tmp_path,
        FakeGitHubClient({("lukejiaor/goodteam", 228): _snapshot()}),
    ).status(run.execution_id)

    assert status["execution_id"] == run.execution_id
    assert status["task"]["task_id"] == run.task_id
    assert status["run"]["worker_job_id"] == run.worker_job_id
    assert [event["payload"]["transition"] for event in status["events"]] == [
        "intake_created",
        "worker_job_created",
    ]
    assert status["worker_job"]["id"] == run.worker_job_id
    assert status["worker_job"]["run_id"] == run.execution_id


def test_run_execution_unknown_execution_raises_clear_value_error(tmp_path):
    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        ExecutionOrchestrator(tmp_path).run_execution("exec_missing")
