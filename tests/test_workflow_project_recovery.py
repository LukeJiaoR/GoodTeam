"""A failed parent keeps its identity while its confirmed recovery uses the pool."""
import json
import os
from hashlib import sha256
from pathlib import Path
import sys
import time

import pytest

from goodteam_os import project_iteration_continuation as continuation, web
from goodteam_os.messages import append_message
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_execution import iteration_batch_report
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.project_iteration_quality import prepare_project_batch_quality, advance_project_batch_quality
from goodteam_os.project_iteration_recovery import project_batch_recovery_evidence
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore
from goodteam_os.workflow_workspace import prepare_workflow_workspace
from test_dispatch_isolation import git, repository


def test_failed_workflow_reviews_and_recovers_without_completing_parent(tmp_path, monkeypatch):
    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"""key: DEMO
source_type: existing_project
repos:
  root: {source}
worker_backend:
  mode: codex
reviewer_profiles:
  release:
    - correctness-security
reviewer_dispatch:
  mode: parallel
  rollout:
    enabled: true
    acknowledge: parallel-reviewer-dispatch-is-advisory
  phases:
    release: parallel
""")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    signals = tmp_path / "signals"
    signals.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, re, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('offline Workflow recovery fixture')
    raise SystemExit(0)
prompt = sys.stdin.read()
output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
match = re.search(r'TASK_FILE=(unit-\d+\.txt)', prompt)
if not match:
    if os.environ.get('WORKFLOW_RECOVERY_EMPTY_REVIEW') and 'QA_FAILURE_FIXTURE' not in prompt:
        raise SystemExit(0)
    output.write_text('Status: failed\nSF09 missing\n' if 'QA_FAILURE_FIXTURE' in prompt else
        '# Summary\nReviewed exact source.\n# Findings\nNo actionable findings.\n'
        '# Evidence\nRead-only source inspection.\n# Risk assessment\nParent QA still failed.\n# Recommendation\nContinue bounded recovery.\n')
    raise SystemExit(0)
name = match.group(1)
assert Path('app.txt').read_text() == 'retained application\n'
signals = Path(os.environ['WORKFLOW_RECOVERY_SIGNALS'])
def event(kind):
    with (signals / 'calls.jsonl').open('a') as log:
        log.write(json.dumps({'event': kind, 'task': name, 'cwd': str(Path.cwd())}) + '\n')
event('start')
(signals / (name + '.ready')).touch()
deadline = time.monotonic() + 10
while len(list(signals.glob('*.ready'))) < 2:
    assert time.monotonic() < deadline, 'two Workers did not overlap'
    time.sleep(.01)
if name == 'unit-2.txt' and not (signals / 'failed-once').exists():
    (signals / 'failed-once').touch()
    event('failed')
    raise SystemExit(4)
Path(name).write_text('delivered ' + name + '\n')
output.write_text('Candidate ready for review.\n')
event('end')
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("WORKFLOW_RECOVERY_SIGNALS", str(signals))
    state = create_initial_state("failed-parent", "DEMO", "Finish the original application and real parallel delivery")
    state.source_ref = str(config)
    state.current_node, state.next_node = "implementation", "failure_triage"
    state.blockers = ["qa worker reported release-blocking failure"]
    state.repository_baseline = prepare_workflow_workspace(source, project_key="DEMO", run_id=state.run_id)
    slot = Path(state.repository_baseline["workspace_path"])
    (slot / "app.txt").write_text("retained application\n")
    states = StateStore(tmp_path)
    states.write_project_current(state)
    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    session._mutate(lambda value: {**value, "proposed_task": {"id": "parent", "run_id": state.run_id,
        "confirmed": True, "revision": 1, "exact_request": state.idea}, "status": "workflow_active"})
    workers = WorkerJobStore(source)
    qa = workers.create_job(run_id=state.run_id, role="qa", adapter="codex", task="QA_FAILURE_FIXTURE",
        context="Original task", project_key="DEMO", workflow_workspace=state.repository_baseline)
    assert workers.run_job(qa["id"])["status"] == "completed"
    qa_bytes = (workers.root / qa["id"] / "result.md").read_bytes()
    feedback = "Reproduce the retained SF09 failure before choosing recovery work."
    append_message(tmp_path, "DEMO", state.run_id, from_role="human", to=["review"],
                   kind="reject", body=feedback)
    endpoint = "/api/projects/DEMO/task-assistant/actions"

    def action(request, expected=200):
        code, result = web.handle_project_api_post(tmp_path, endpoint, request)
        assert code == expected
        return result

    target = web.workflow_retry_target(tmp_path, states.read_project_current("DEMO"))
    selected = dict(action="prepare_workflow_recovery", expected_run_id=state.run_id,
                    expected_retry_version=target["version"])
    prepared = action(selected)
    with pytest.raises(web.ProjectApiError, match="current failed"):
        action({**selected, "expected_run_id": "another-parent"})
    record = prepared["workflow_recovery"]
    qa_report = record["inputs"]["qa_report"]
    assert qa_report["job_id"] == qa["id"]
    assert qa_report["report_version"] == "sha256:" + sha256(qa_bytes).hexdigest()
    assert qa_report["content"] == qa_bytes.decode()
    reviewer_context = workers.root / prepared["reviewer_jobs"][0]["id"] / "context.md"
    assert feedback in reviewer_context.read_text()
    assert prepared["approval_unchanged"] and not (source / "app.txt").exists()
    reference = {"run_id": state.run_id, "version": record["version"]}
    ledger = LocalTaskStore(source, "DEMO")
    tasks = [ledger.create_task(title=f"TASK_FILE=unit-{i}.txt", description="Write only the assigned file.",
        role="implementation", write_domains=[f"unit-{i}.txt"], acceptance_criteria=["Expected text exists."])
        for i in range(1, 4)]
    request = dict(action="start_project_continuation", task_ids=[task["id"] for task in tasks], max_batches=2,
        confirmed=True, billing_acknowledged=True, idempotency_key="recover-parent", workflow_recovery=reference,
        expected_backlog_version=project_iteration_backlog(source, "DEMO")["version"])
    with pytest.raises(web.ProjectApiError, match="read-only recovery review"):
        action(request)
    monkeypatch.setenv("WORKFLOW_RECOVERY_EMPTY_REVIEW", "1")
    empty_review = dict(project_key="DEMO", run_id=state.run_id, approval="release",
        artifact_key="workflow_recovery", artifact_version=record["version"], job_id=prepared["reviewer_jobs"][0]["id"])
    assert web.run_queued_reviewer_jobs_action(tmp_path, empty_review)[0] == 200
    with pytest.raises(web.ProjectApiError, match="read-only recovery review"):
        action(request)
    monkeypatch.delenv("WORKFLOW_RECOVERY_EMPTY_REVIEW")
    target = web.workflow_retry_target(tmp_path, states.read_project_current("DEMO"))
    prepared = action({**selected, "expected_retry_version": target["version"]})
    record = prepared["workflow_recovery"]
    request["workflow_recovery"] = reference = {"run_id": state.run_id, "version": record["version"]}
    reviewer = prepared["reviewer_jobs"][0]
    review_request = dict(project_key="DEMO", run_id=state.run_id, approval="release",
                          artifact_key="workflow_recovery", artifact_version=record["version"], job_id=reviewer["id"])
    original_app = (slot / "app.txt").read_bytes()
    (slot / "app.txt").write_text("changed after review preparation\n")
    assert web.run_queued_reviewer_jobs_action(tmp_path, review_request)[0] == 409
    (slot / "app.txt").write_bytes(original_app)
    (workers.root / qa["id"] / "result.md").write_bytes(qa_bytes + b"\nChanged finding.\n")
    assert web.run_queued_reviewer_jobs_action(tmp_path, review_request)[0] == 409
    (workers.root / qa["id"] / "result.md").write_bytes(qa_bytes)
    code, reviewed = web.run_queued_reviewer_jobs_action(tmp_path, review_request)
    assert code == 200 and reviewed["ran_count"] == 1 and reviewed["approval_unchanged"], reviewed
    with pytest.raises(web.ProjectApiError, match="Land the exact"):
        action(request)
    integrated = record["repository_result"]["integration"]
    git(source, "fetch", "--quiet", integrated["candidate_path"], integrated["candidate_commit"])
    git(source, "merge", "--quiet", "--ff-only", integrated["candidate_commit"])
    grant = action(request)["project_continuation"]
    assert action(request)["project_continuation"] == grant
    choices = []

    def manager(**kwargs):
        eligible = [item["task_id"] for item in kwargs["backlog"]["candidates"] if item["eligible_to_plan"]]
        chosen = eligible[:2]
        if len(choices) == 1:
            assert all(ledger.get_task(task["id"])["status"] == "review" for task in tasks[:2])
            assert choices == [[task["id"] for task in tasks[:2]]]
        choices.append(chosen)
        return {"selection": {"selected_task_ids": chosen, "compared_task_ids": eligible,
            "rationale": "Choose independent original-scope tasks using the retained failed parent and freshly observed task outcomes."}}

    monkeypatch.setattr(continuation, "plan_next_iteration", lambda **kwargs: plan_next_iteration(**kwargs, manager_runner=manager))
    continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=source, project_config=config)
    deadline = time.monotonic() + 15
    while any(ledger.get_task(task["id"])["status"] not in {"review", "blocked"} for task in tasks[:2]):
        assert time.monotonic() < deadline
        time.sleep(.02)
    saved = session.read()["project_continuation"]
    report = iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source, run_id=saved["pending_batch"]["run_id"])
    assert not report["all_ok"] and ledger.get_task(tasks[1]["id"])["status"] == "blocked", report
    events = [json.loads(line) for line in (signals / "calls.jsonl").read_text().splitlines()]
    assert [item["event"] for item in events[:2]] == ["start", "start"]
    assert len({item["cwd"] for item in events}) == 2
    assert not list(source.glob("unit-*.txt"))
    run_id = saved["pending_batch"]["run_id"]
    recovery = project_batch_recovery_evidence(workspace=tmp_path, project_key="DEMO", root=source, run_id=run_id)
    assert recovery["retryable_task_ids"] == [tasks[1]["id"]]
    action(dict(action="retry_project_batch", run_id=run_id, expected_recovery_version=recovery["version"],
        idempotency_key="retry-only-failed", retry_task_ids=[tasks[1]["id"]], confirmed=True,
        billing_acknowledged=True), expected=202)
    deadline = time.monotonic() + 15
    while ledger.get_task(tasks[1]["id"])["status"] != "review":
        assert time.monotonic() < deadline
        time.sleep(.02)
    quality_run = dict(workspace=tmp_path, project_key="DEMO", root=source, run_id=run_id)
    quality = prepare_project_batch_quality(**quality_run,
        argv=[sys.executable, "-I", "-B", "-c", "from pathlib import Path; assert Path('unit-1.txt').exists() and Path('unit-2.txt').exists()"],
        criteria=[0, 1], reviewer_model="offline-fixture", fix_model="offline-fixture")
    assert quality["status"] == "prepared", quality
    advanced = advance_project_batch_quality(**quality_run, expected_input_digest=quality["input_digest"],
        confirmed=True, billing_acknowledged=True)
    assert advanced["quality"]["reason"] == "batch_quality_reviewer_admission_required", advanced
    plan_next_iteration(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
        expected_backlog_version=project_iteration_backlog(source, "DEMO")["version"], confirmed=True,
        allowed_task_ids=grant["task_ids"], manager_runner=manager)
    assert choices[-1] == [tasks[2]["id"]]
    parent = states.read_project_current("DEMO")
    assert (parent.run_id, parent.current_node, parent.next_node, parent.approvals) == (
        state.run_id, state.current_node, state.next_node, state.approvals)
    assert session.read()["proposed_task"]["run_id"] == state.run_id
    assert (workers.root / qa["id"] / "result.md").read_bytes() == qa_bytes
    assert qa_bytes.startswith(b"Status: failed")
    retry = web.workflow_retry_target(tmp_path, parent)
    with pytest.raises(web.ProjectApiError, match="settle its admitted batch"):
        web.retry_blocked_workflow(workspace=tmp_path, project_key="DEMO", expected_run_id=state.run_id,
                                   expected_retry_version=retry["version"])
    continuation.stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id=grant["id"])
    assert continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
        project_config=config)["status"] == "stopped"
    recovery = project_batch_recovery_evidence(**quality_run)
    action(dict(action="abandon_project_batch", run_id=run_id, expected_recovery_version=recovery["version"],
        idempotency_key="settle-before-parent", confirmed=True))
    next_grant = action({**request, "task_ids": [tasks[2]["id"]], "max_batches": 1,
        "idempotency_key": "unadmitted-reservation", "expected_backlog_version": project_iteration_backlog(source, "DEMO")["version"]})["project_continuation"]

    def refuse_admission(**kwargs):
        raise ValueError("Offline fixture: batch admission refused before any claim")

    blocked = continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
        project_config=config, batch_runner=refuse_admission)
    assert blocked["pending_batch"] and not blocked["pending_batch"]["run_id"]
    continuation.stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id=next_grant["id"])
    monkeypatch.setattr(web, "_retry_bound_workflow", lambda **kwargs: {"resumed_original_parent": kwargs["state"].run_id})
    retry = web.workflow_retry_target(tmp_path, states.read_project_current("DEMO"))
    assert web.retry_blocked_workflow(workspace=tmp_path, project_key="DEMO", expected_run_id=state.run_id,
        expected_retry_version=retry["version"])["resumed_original_parent"] == state.run_id
