"""Native observation integration with local event files and a simulated Manager."""
import json

import pytest

from goodteam_os import manager_app_server
from goodteam_os.native_worker_events import _native_event
from goodteam_os.project_iteration_continuation import stop_project_continuation
from goodteam_os.project_observation_runtime import judge_with_production_runtime
from goodteam_os.project_running_observation import observe_running_batch
from goodteam_os.task_assistant import worker_invocation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore
from native_observation_fixtures import NativeObservationFixture


def test_native_reports_feed_existing_observer_and_budget_once_per_new_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    fixture = NativeObservationFixture(tmp_path, monkeypatch)
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(run_id="attempt", role="implementation", adapter="codex", task="Implement task",
                             context="Original scope", project_key="DEMO", project_task_id="task")
    job_root = workers.root / job["id"]
    workers._write_status(job_root, job, "running", "Local native fixture", extra={"heartbeat_at": workers._now()})
    events_path = job_root / "native_events.jsonl"
    def report(index):
        return _native_event(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
            "id": "report-" + str(index), "text": "Working on stage " + str(index)}}), job)
    # More than the cursor API's default page: observations need the latest end.
    events_path.write_text("".join(json.dumps(report(i)) + "\n" for i in range(80)))
    fixture.output["observed_job_ids"] = [job["id"]]
    def observe():
        return observe_running_batch(store=fixture.store, root=tmp_path, project_key="DEMO",
                                     value=fixture.store.read()["project_continuation"])
    observed = observe()
    key = observed["running_observation"]["digest"]
    row = observed["running_observation"]["packet"]["workers"][0]
    assert [entry["entry_id"] for entry in row["worker_output"]["entries"]] == ["report-77", "report-78", "report-79"]
    assert row["observation_available"] and not row["follow_up_supported"]
    assert row["worker_output"]["snapshot_truncated"]
    assert not row["worker_output"]["control_reply_attributed"]
    runtime = worker_invocation_context(tmp_path, "DEMO", fixture.store.read(), observe_runtime=True)["current"][0]["runtime_observation"]
    assert runtime["source"] == "WorkerJobStore.native_events" and runtime["evidence"]["heartbeat"]
    assert runtime["evidence"]["process_owned"] is False and "process_alive" not in runtime["evidence"]
    judged = fixture.judge()
    assert judged["manager_calls"] == 1 and judged["running_judgments"][key]["status"] == "recorded"
    assert judged["running_judgments"][key]["advice"] == fixture.output

    # A repeated poll/heartbeat is not another paid judgment or a Worker reply.
    workers._write_status(job_root, job, "running", "Heartbeat only", extra={"heartbeat_at": workers._now()})
    assert observe()["running_observation"]["digest"] == key
    assert fixture.judge()["manager_calls"] == 1 and len(fixture.calls) == 1
    with events_path.open("a") as events:
        events.write(json.dumps(report(80)) + "\n")
    changed = observe()["running_observation"]["digest"]
    assert changed != key
    observe()  # report arrival and periodic collection share one existing digest
    assert fixture.judge()["manager_calls"] == 2 and len(fixture.calls) == 2
    assert fixture.store.read()["project_task_batches"]["batch"] == fixture.batch
    assert {item["role"] for item in workers.list_jobs()} == {"implementation"}


def test_stop_interrupts_background_after_durable_revocation_and_late_advice_stays_stale(tmp_path, monkeypatch):
    fixture = NativeObservationFixture(tmp_path, monkeypatch)
    fixture.candidate()
    stopped = []
    def stop_background(workspace, project_key):
        assert workspace == tmp_path and project_key == "DEMO"
        # A separate store can read the durable revocation before native Stop.
        assert TaskAssistantSessionStore(workspace, project_key).read()["project_continuation"]["status"] == "stopped"
        stopped.append(project_key)
        return [{"status": "stopped"}]
    monkeypatch.setattr(manager_app_server, "stop_background_work", stop_background)
    def stop_during_call(call):
        stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id="grant")
        assert not call["authority_check"]()
    fixture.on_run = stop_during_call
    result = fixture.judge()
    assert stopped == ["DEMO"] and result["status"] == "stopped"
    assert result["manager_calls"] == 1 and result["running_judgments"]["one"]["status"] == "stale_result"
    assert not result["running_judgments"]["one"]["actions_executed"]
    with pytest.raises(ValueError, match="stopped"):
        fixture.judge()
    assert len(fixture.calls) == 1 and fixture.store.read()["project_task_batches"]["batch"] == fixture.batch


def test_new_permission_does_not_expand_old_grants(tmp_path):
    from goodteam_os.project_outcome_discovery import normalize_discovery
    grant = dict(objective="Inspect running Worker evidence safely.", allowed_write_domains=["src"], max_new_tasks=1, max_planning_calls=2)
    assert "allow_running_observation" not in normalize_discovery(grant)
    assert normalize_discovery({**grant, "allow_running_observation": True})["allow_running_observation"]
    with pytest.raises(ValueError):
        normalize_discovery({**grant, "allow_running_observation": "true"})
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"discovery": grant, "manager_calls": 0}
    assert judge_with_production_runtime(store=store, root=tmp_path, project_config=tmp_path / "unused",
        project_key="DEMO", value=value, authority_check=lambda: False) is value
