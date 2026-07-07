from __future__ import annotations

from goodteam_os.execution import ExecutionLedger, LedgerEventType
from goodteam_os.execution.dogfood import (
    P0_1_DOGFOOD_ISSUE_NUMBER,
    P0_1_DOGFOOD_REPO,
    LocalDogfoodGitHubClient,
    expected_p0_1_ids,
    run_p0_1_dogfood,
)
from goodteam_os.execution.idempotency import execution_id_from_task, task_id_from_github_issue
from goodteam_os.workers import WorkerJobStore


def test_deterministic_task_and_execution_ids_are_created_from_issue():
    task_id = task_id_from_github_issue(P0_1_DOGFOOD_REPO, P0_1_DOGFOOD_ISSUE_NUMBER)
    execution_id = execution_id_from_task(task_id)

    assert (task_id, execution_id) == expected_p0_1_ids()
    assert task_id == "task_ac94f46d1c87e29cc99ff55ca3a48f1d"
    assert execution_id == "exec_7655aa3eda146a76df1e6505cf23d604"


def test_first_run_intake_writes_task_run_worker_job_and_replay(tmp_path):
    result = run_p0_1_dogfood(project_root=tmp_path, adapter="noop")

    assert result.task_path.is_file()
    assert result.run_path.is_file()
    assert result.events_path.is_file()
    assert result.run_status == "succeeded"

    ledger = ExecutionLedger(tmp_path)
    task = ledger.read_task(result.execution_id)
    run = ledger.read_run(result.execution_id)
    jobs = WorkerJobStore(tmp_path).list_jobs()

    assert task is not None
    assert task.task_id == result.task_id
    assert run is not None
    assert run.execution_id == result.execution_id
    assert run.worker_job_id == result.worker_job_id
    assert len(jobs) == 1
    assert jobs[0]["id"] == result.worker_job_id
    assert jobs[0]["adapter"] == "noop"
    assert jobs[0]["run_id"] == result.execution_id

    events = ledger.read_events(result.execution_id)
    assert [event.event_type for event in events[:2]] == [
        LedgerEventType.CREATED,
        LedgerEventType.STATUS_CHANGED,
    ]
    assert [event.payload["transition"] for event in events[:2]] == [
        "intake_created",
        "worker_job_created",
    ]

    replay = result.replay
    assert replay["task"] == task
    assert replay["run"] == run
    assert replay["events"] == events
    assert result.event_transitions == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "sandbox_created",
        "execution_completed",
        "sandbox_destroyed",
    ]


def test_re_running_dogfood_path_does_not_duplicate_run_job_or_events(tmp_path):
    first = run_p0_1_dogfood(project_root=tmp_path, adapter="noop")
    first_events = ExecutionLedger(tmp_path).read_events(first.execution_id)

    second = run_p0_1_dogfood(project_root=tmp_path, adapter="noop")
    second_events = ExecutionLedger(tmp_path).read_events(second.execution_id)

    assert second.execution_id == first.execution_id
    assert second.worker_job_id == first.worker_job_id
    assert len(WorkerJobStore(tmp_path).list_jobs()) == 1
    assert second_events == first_events


def test_first_run_uses_local_github_client_without_network(tmp_path):
    client = LocalDogfoodGitHubClient()

    result = run_p0_1_dogfood(
        project_root=tmp_path,
        adapter="fake",
        github_client=client,
    )

    assert client.fetches == [(P0_1_DOGFOOD_REPO, P0_1_DOGFOOD_ISSUE_NUMBER)]
    assert result.adapter == "fake"
    assert WorkerJobStore(tmp_path).list_jobs()[0]["adapter"] == "fake"
