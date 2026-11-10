"""Real job persistence/readback with fixture states, not live-model acceptance."""
from copy import deepcopy
import json

import pytest

from goodteam_os.dispatch import _create_track_job
from goodteam_os.project_iteration_execution import _publish_batch_result
from goodteam_os.task_assistant import _manager_chat_context, worker_invocation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore


def test_role_job_handle_is_scoped_fresh_and_retained_without_reusing_finished_identity(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    jobs = WorkerJobStore(root)
    conversations = TaskAssistantSessionStore(tmp_path, "DEMO")
    batch = {"run_id": "batch-fixture", "dispatch_run_id": "dispatch-fixture", "confirmed": True,
             "project_root": str(root.resolve()), "status": "claimed"}
    conversations._mutate(lambda value: {**value, "project_task_batches": {batch["run_id"]: batch}})
    assert worker_invocation_context(root, "DEMO", conversations.read())["current"] == []

    track = {"role": "research", "adapter": "fake", "task": "Investigate API behavior.\nPrivate task body.",
             "context": "PRIVATE_RESEARCH_CONTEXT", "project_task_id": "DEMO-1", "track_id": "DEMO-1",
             "worker_id": "research-a", "worker_name": "Legacy config profile"}
    def create(value, key):
        return _create_track_job(jobs, run_id=batch["dispatch_run_id"], track=value, constraints=[],
                                 project_key="DEMO", command_key=key)
    first = create(track, "invocation-fixture-1")
    assert first["id"] != first["worker_id"]
    assert first["project_task_id"] == first["track_id"] == "DEMO-1"
    assert create(track, "invocation-fixture-1")["id"] == first["id"]
    with pytest.raises(ValueError, match="another task or track"):
        create({**track, "project_task_id": "DEMO-2"}, "invocation-fixture-1")

    context = _manager_chat_context({}, {}, conversations.read(), root=root, project_key="DEMO")
    visible = json.loads(context.split("Worker invocation records (fresh read-only state): ", 1)[1])
    assert visible["current"][0]["job_id"] == first["id"]
    assert visible["current"][0]["status"] == "queued"
    assert visible["current"][0]["adapter"] == "fake"
    assert "PRIVATE_RESEARCH_CONTEXT" not in context and "Private task body" not in context
    assert "Legacy config profile" not in context and "research-a" not in context

    blocked = {**batch, "status": "blocked", "error": "Fixture: isolation failed before startup."}
    conversations._mutate(lambda value: {**value, "project_task_batches": {batch["run_id"]: blocked}})
    queued = worker_invocation_context(root, "DEMO", conversations.read())["current"][0]
    assert queued["status"] == "queued" and queued["batch_status"] == "blocked"
    assert queued["batch_error"] == blocked["error"]
    jobs._write_status(jobs.root / first["id"], first, "running", "Fixture running observation.",
                       extra={"heartbeat_at": "2026-11-10T00:00:00+00:00"})
    assert worker_invocation_context(root, "DEMO", conversations.read())["current"][0]["status"] == "running"
    jobs._complete_fake(jobs.root / first["id"], first)

    second = create({**track, "role": "implementation", "task": "Implement the selected API fix.",
                     "context": "PRIVATE_IMPLEMENTATION_CONTEXT", "project_task_id": "DEMO-2", "track_id": "DEMO-2",
                     "worker_id": "implementation-a"},
                    "invocation-fixture-2")
    assert second["id"] != first["id"]
    assert (jobs.root / first["id"] / "context.md").read_text() == "PRIVATE_RESEARCH_CONTEXT"
    assert (jobs.root / second["id"] / "context.md").read_text() == "PRIVATE_IMPLEMENTATION_CONTEXT"
    jobs.create_job(run_id=batch["dispatch_run_id"], role="qa", adapter="fake", task="Other project's job.",
                    context="", project_key="OTHER")
    jobs.create_job(run_id="unrelated-run", role="qa", adapter="fake", task="Unrelated historical job.",
                    context="", project_key="DEMO")
    latest = worker_invocation_context(root, "DEMO", conversations.read())
    assert [row["job_id"] for row in latest["current"]] == [second["id"]]
    assert [row["job_id"] for row in latest["recent_history"]] == [first["id"]]
    assert latest["recent_history"][0]["status"] == "completed"
    assert latest["current"][0]["role"] == "implementation"
    assert "PRIVATE_" not in json.dumps(latest)
    assert worker_invocation_context(root, "DEMO", conversations.read(), run_id="missing-run")["current"] == []

    report = {"jobs": [{"job_id": first["id"], "role": "research", "status": "completed",
                         "project_task_id": "DEMO-1", "dispatch_run_id": batch["dispatch_run_id"],
                         "worker_id": "research-a"}]}
    _publish_batch_result(tmp_path, "DEMO", root, batch, report, "review")
    publication = conversations.read()["timeline"][-1]
    result = publication["details"]["source_evidence"]["worker_results"][0]
    assert result["job_id"] == first["id"] and result["project_task_id"] == "DEMO-1"
    assert "worker_id" not in result and first["id"] in publication["content"]
    _publish_batch_result(tmp_path, "DEMO", root, batch, report, "review")
    assert conversations.read()["timeline"][-1] == publication
    (jobs.root / first["id"] / "result.md").write_text("Changed new publication cannot silently replay.")
    with pytest.raises(ValueError, match="different content"):
        _publish_batch_result(tmp_path, "DEMO", root, batch, report, "review")

    old_batch = {**batch, "run_id": "historical-batch"}
    conversations.append_event("batch-result:historical-batch:initial", "conversation_replied", content="Retained old format.",
                               details={"run_id": "historical-batch", "source_evidence": {
                                   "worker_results": [{"role": "research", "worker_id": "research-a", "summary": "Old result."}]}})
    historical = deepcopy(conversations.read()["timeline"][-1])
    _publish_batch_result(tmp_path, "DEMO", root, old_batch, report, "review")
    assert conversations.read()["timeline"][-1] == historical
