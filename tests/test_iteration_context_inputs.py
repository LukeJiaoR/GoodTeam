"""Representative continuity, complete-prompt budget and planner replay contracts."""
import json
import os
import sys

import pytest

from goodteam_os.context_continuity import conversation_context
from goodteam_os.assistant_queue import queue_manager_iteration_choice
from goodteam_os.project_iteration import latest_iteration_choice, project_iteration_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import git, repository


def test_explicit_project_statements_survive_compaction_without_expanding_intake(tmp_path):
    from goodteam_os.task_assistant import _manager_chat_context

    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    statement = "项目长期约束：只能离线运行，禁止依赖外部云接口。"
    timeline = [{"message_id": f"message-{i}", "actor": "user", "content": f"已讨论普通实现细节 {i}"}
                for i in range(100)]
    timeline[30]["content"] = statement
    timeline[31].update(content="项目约束：仅限另一个任务", run_id="other-task")
    timeline[32].update(actor="task_assistant", content="项目约束：模型自己推断的约束")
    store._mutate(lambda session: {**session, "timeline": timeline})
    before = store.path.read_bytes()
    projected = conversation_context(store.read(), query="下一轮做什么")
    statements = projected["project_statements"]
    assert [item["content"] for item in statements["excerpts"]] == [statement]
    assert statements["authority"] == "historical_context_only"
    source = statements["excerpts"][0]
    assert source["message_id"] == "message-30" and source["source_index"] == 30
    assert statement == timeline[30]["content"][source["source_start"]:source["source_end"]]
    rendered = _manager_chat_context({}, {}, store.read(), current_message_id="message-99")
    assert statement in rendered
    intake = json.loads(rendered.split("Task intake source messages: ", 1)[1].split("\n", 1)[0])
    assert "message-30" not in {item["message_id"] for item in intake}
    assert store.path.read_bytes() == before


def test_complete_ordinary_prompt_budget_rejects_before_cli_call(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    workers = WorkerJobStore(tmp_path)
    context = "补充上下文" * 12_000  # Context alone fits; the complete prompt does not.
    job = workers.create_job(run_id="budget", role="implementation", adapter="claude",
                             task="Preserve this exact task.", context=context,
                             constraints=["Preserve this exact constraint."],
                             additional_prompt="Role guidance. " * 1400)
    job_root = workers.root / job["id"]
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen",
                        lambda *args, **kwargs: pytest.fail("Oversized input reached the CLI"))
    with pytest.raises(ValueError, match="worker_prompt_size_limit"):
        workers._run_cli_worker(job_root, job, command=[sys.executable, "-c", "pass"],
                                model="offline", preflight={}, label="Offline", capture="stdout",
                                summarize=lambda *_: "unused")
    assert (job_root / "context.md").read_text() == context
    assert (job_root / "task.md").read_text() == "Preserve this exact task."
    (job_root / "context.md").write_text("Small complete context.")
    prompt = workers._worker_prompt(job_root, job, model="offline")
    assert "Preserve this exact task." in prompt and "Preserve this exact constraint." in prompt
    assert "Small complete context." in prompt and len(prompt.encode()) <= 192 * 1024


def test_planning_reuses_only_current_inputs_and_pins_an_automatic_attempt(tmp_path, monkeypatch):
    source = repository(tmp_path / "source")
    config = tmp_path / "project.yaml"
    config.write_text(f"key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    calls = tmp_path / "planning-calls.jsonl"
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('offline context planning fixture')
    raise SystemExit(0)
prompt = sys.stdin.read()
packet = json.loads(prompt.split('## Context\n\n', 1)[1].split('\n\n## Sandbox Context', 1)[0])
assert packet['project_head']
assert packet['previous_delivery']['status'] == 'unavailable'
ids = [row['task_id'] for row in packet['backlog']['candidates'] if row['eligible_to_plan']]
assert not set(ids) & {row['task_id'] for row in packet['out_of_scope_tasks']}
with Path(os.environ['PLANNING_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps(packet) + '\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(json.dumps({
    'selected_task_id': ids[-1] if packet['project_head'] == os.environ['PLANNING_FIXTURE_INITIAL_HEAD'] else ids[0],
    'compared_task_ids': ids,
    'rationale': 'Choose this eligible task using the current repository and supplied project evidence.'}))
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PLANNING_FIXTURE_CALLS", str(calls))
    initial_head = git(source, "rev-parse", "HEAD")
    monkeypatch.setenv("PLANNING_FIXTURE_INITIAL_HEAD", initial_head)
    ledger = LocalTaskStore(source, "DEMO")
    tasks = [ledger.create_task(title=f"Iteration {i}", description="Implement a bounded project improvement.",
                                role="implementation") for i in range(3)]
    spec = dict(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
                expected_backlog_version=project_iteration_backlog(source, "DEMO")["version"],
                allowed_task_ids=[task["id"] for task in tasks[:2]], confirmed=True)
    first = plan_next_iteration(**spec)
    assert plan_next_iteration(**spec)["reused"] is True
    git(source, "commit", "--allow-empty", "-m", "New repository revision")
    second = plan_next_iteration(**spec)
    assert second["reused"] is False
    assert second["iteration_choice"]["source_event_id"] != first["iteration_choice"]["source_event_id"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    assert second["iteration_choice"]["selected_task_id"] != first["iteration_choice"]["selected_task_id"]
    # Returning to an earlier input reuses its Worker but makes that advice
    # current again for both the display and the existing queue handoff.
    git(source, "checkout", "--detach", initial_head)
    restored = plan_next_iteration(**spec)["iteration_choice"]
    assert restored["planning_input_digest"] == first["iteration_choice"]["planning_input_digest"]
    assert latest_iteration_choice(store.read(), project_iteration_backlog(source, "DEMO")) == restored
    assert restored["source_event_id"] != first["iteration_choice"]["source_event_id"]
    assert plan_next_iteration(**spec)["reused"] is True
    assert len(calls.read_text().splitlines()) == 2
    queued = queue_manager_iteration_choice(workspace=tmp_path, project_key="DEMO", root=source,
        expected_backlog_version=spec["expected_backlog_version"],
        expected_choice_event_id=restored["source_event_id"], expected_task_ids=restored["selected_task_ids"])
    assert queued["queued_task_ids"] == first["iteration_choice"]["selected_task_ids"]
    assert queued["workflow_started"] is False
    store.append_message("new-goal", "user", "项目目标：优先完成可离线验证的迭代。")
    assert plan_next_iteration(**spec)["reused"] is False
    assert len(calls.read_text().splitlines()) == 3
    statement = json.loads(calls.read_text().splitlines()[-1])["conversation"]["project_statements"]["excerpts"][0]
    assert store.read()["timeline"][statement["source_index"]]["message_id"] == statement["message_id"] == "new-goal"
    # An automatic reservation is finite even if a reply was lost and HEAD moved.
    plan_next_iteration(**spec, planning_attempt_id="continuation:one-paid-slot")
    git(source, "commit", "--allow-empty", "-m", "Changed during reserved attempt")
    with pytest.raises(ValueError, match="planning input changed"):
        plan_next_iteration(**spec, planning_attempt_id="continuation:one-paid-slot")
    assert len(calls.read_text().splitlines()) == 4
    assert all(job["execution_policy"] == "read_only" for job in WorkerJobStore(source).list_jobs())


def test_legacy_reserved_planning_cannot_spend_again_after_identity_upgrade(tmp_path):
    from goodteam_os.project_iteration_continuation import configure_project_continuation, tick_project_continuation

    source = repository(tmp_path / "source")
    config = tmp_path / "project.yaml"
    config.write_text(f"key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: fake\n")
    task = LocalTaskStore(source, "DEMO").create_task(title="An approved iteration", role="implementation",
                                                   description="A bounded project improvement.")
    version = project_iteration_backlog(source, "DEMO")["version"]
    args = dict(workspace=tmp_path, project_key="DEMO", root=source, project_config=config)
    configure_project_continuation(**args, request={"task_ids": [task["id"]], "max_batches": 1,
        "confirmed": True, "billing_acknowledged": True, "idempotency_key": "legacy-slot",
        "expected_backlog_version": version})
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store._mutate(lambda session: {**session, "project_continuation": {
        **session["project_continuation"], "planning_backlog_version": version, "manager_calls": 1}})
    result = tick_project_continuation(**args,
        manager_runner=lambda **_: pytest.fail("A legacy reserved call was spent again"))
    assert result["status"] == "blocked" and result["manager_calls"] == 1
    assert "older reserved planning attempt" in result["reason"]
    assert not WorkerJobStore(source).list_jobs()
