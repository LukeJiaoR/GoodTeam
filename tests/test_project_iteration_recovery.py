"""One real failed batch resumes only its failed task, then continues safely.

Manager choices and native CLI output are offline protocol fixtures. Dispatch,
Git isolation, failed process evidence, retry admission, quality receipts and
delivery use production paths. Temporarily hiding a terminal status explicitly
models interrupted evidence publication; no running process is called failed.
"""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import web
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from goodteam_os.execution.task_snapshot_store import store_for_project
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.project_iteration_quality import prepare_project_batch_quality
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import git, repository
from test_project_iteration_quality import _offline_native_cli, _review_admission


def _worker_cli(tmp_path, monkeypatch):
    """B fails once after an actual live interval; C is a known terminal failure."""
    signals = tmp_path / "signals"
    signals.mkdir()
    binary = tmp_path / "ordinary-workers/codex"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + r'''
import fcntl, json, os, re, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('offline batch recovery Worker fixture')
    raise SystemExit(0)
name = re.search(r'TASK_FILE=(unit-[abc]\.txt)', sys.stdin.read()).group(1)
signals = Path(os.environ['RECOVERY_FIXTURE_SIGNALS'])
assert Path(sys.argv[sys.argv.index('--cd') + 1]) == Path.cwd()
with (signals / 'calls.jsonl').open('a+') as stream:
    fcntl.flock(stream, fcntl.LOCK_EX)
    stream.seek(0)
    attempt = 1 + sum(json.loads(line)['task'] == name for line in stream)
    stream.write(json.dumps({'task': name, 'attempt': attempt, 'cwd': str(Path.cwd()), 'pid': os.getpid()}) + '\n')
    stream.flush()
if name == 'unit-b.txt' and attempt == 1:
    (signals / 'b-live').touch()
    deadline = time.monotonic() + 30
    while not (signals / 'release-b').exists():
        if time.monotonic() >= deadline:
            raise SystemExit('Offline test did not release the held process')
        time.sleep(.01)
if name == 'unit-c.txt':
    assert Path('unit-a.txt').read_text() == 'fixed\n'
    assert Path('unit-b.txt').read_text() == 'ready\n'
failed = name == 'unit-c.txt' or name == 'unit-b.txt' and attempt == 1
Path(name).write_text('unfinished\n' if failed else 'fixed\n' if name == 'unit-a.txt' else 'ready\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(
    'Explicit offline terminal failure; output retained.\n' if failed else 'Offline candidate awaits independent review.\n')
if failed:
    print('Explicit offline terminal failure', file=sys.stderr)
    raise SystemExit(7)
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RECOVERY_FIXTURE_SIGNALS", str(signals))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "1")
    return signals


def _wait_for(predicate):
    deadline = time.monotonic() + 30
    while not predicate():
        assert time.monotonic() < deadline, "The real background batch did not settle."
        time.sleep(.02)


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_failed_task_retry_preserves_success_then_delivers_continues_and_abandons(tmp_path, monkeypatch):
    binaries, native_home, review_calls = _offline_native_cli(tmp_path, monkeypatch)
    signals = _worker_cli(tmp_path, monkeypatch)
    source = repository(tmp_path / "source")
    initial_head = git(source, "rev-parse", "HEAD")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    ensure_team_state(tmp_path, "DEMO", config)
    ledger = LocalTaskStore(source, "DEMO")
    first = ledger.create_task(title="TASK_FILE=unit-a.txt", description="Write only unit-a.txt.",
        role="implementation", write_domains=["unit-a.txt"], acceptance_criteria=["unit-a.txt contains fixed and a newline."])
    failed = ledger.create_task(title="TASK_FILE=unit-b.txt", description="Write only unit-b.txt.",
        role="implementation", write_domains=["unit-b.txt"], acceptance_criteria=["unit-b.txt contains ready and a newline."])
    later = ledger.create_task(title="TASK_FILE=unit-c.txt", description="Read delivered A/B and write only unit-c.txt.",
        role="implementation", write_domains=["unit-c.txt"], dependencies=[first["id"], failed["id"]],
        acceptance_criteria=["unit-c.txt contains a completed follow-up result."])
    choices = []

    def manager(**kwargs):
        eligible = [row["task_id"] for row in kwargs["backlog"]["candidates"] if row["eligible_to_plan"]]
        chosen = [first["id"], failed["id"]] if not choices else [later["id"]]
        assert set(eligible) == set(chosen)
        if choices:
            assert all(ledger.get_task(identifier)["delivery_verified"] for identifier in choices[0])
            assert (source / "unit-a.txt").read_text() == "fixed\n"
            assert (source / "unit-b.txt").read_text() == "ready\n"
        choices.append(chosen)
        return {"selection": {"selected_task_ids": chosen, "compared_task_ids": eligible,
            "rationale": "First run independent scoped tasks; then use their verified delivered dependency results."}}

    monkeypatch.setattr(continuation, "plan_next_iteration",
                        lambda **kwargs: plan_next_iteration(**kwargs, manager_runner=manager))
    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    endpoint = "/api/projects/DEMO/task-assistant/actions"

    def action(request, *, expected=200, **admissions):
        code, result = web.handle_project_api_post(tmp_path, endpoint, request, **admissions)
        assert code == expected
        return result

    def visible(run_id):
        code, report = web.handle_project_api_get(tmp_path, f"/api/projects/DEMO/iteration-batches/{run_id}")
        assert code == 200
        return report

    def calls():
        return [json.loads(line) for line in (signals / "calls.jsonl").read_text().splitlines()]

    def interrupt_projection(request, task_id, target_status, persisted, *, expected=200):
        """A one-shot disk failure after command persistence, never fake execution."""
        write = LocalTaskStore._write
        failures = []
        def fail_once(store, value):
            batch = session.read()["project_task_batches"][request["run_id"]]
            if (store.project_root == source and not failures and persisted(batch)
                    and any(task["id"] == task_id and task["status"] == target_status for task in value["tasks"])):
                failures.append(True)
                raise OSError("Injected task projection publication failure")
            return write(store, value)
        with monkeypatch.context() as patch:
            patch.setattr(LocalTaskStore, "_write", fail_once)
            with pytest.raises(web.ProjectApiError, match="Injected task projection publication failure"):
                action(request, expected=expected)
        assert failures == [True]

    authorized = action({"action": "start_project_continuation", "task_ids": [first["id"], failed["id"], later["id"]],
        "max_batches": 2, "confirmed": True, "billing_acknowledged": True, "idempotency_key": "recover-two-batches",
        "expected_backlog_version": project_iteration_backlog(source, "DEMO")["version"]})["project_continuation"]
    continuation.tick_authorized_project_continuations(tmp_path)
    _wait_for(lambda: (signals / "b-live").exists() and len(calls()) == 2)
    run_id = session.read()["project_continuation"]["pending_batch"]["run_id"]
    live = visible(run_id)["recovery"]
    live_command = {"run_id": run_id, "expected_recovery_version": live["version"],
                    "idempotency_key": "must-not-start-live", "confirmed": True}
    try:
        assert not live["retryable_task_ids"] and not live["can_abandon"]
        with pytest.raises(web.ProjectApiError):
            action({**live_command, "action": "retry_project_batch", "retry_task_ids": [failed["id"]],
                    "billing_acknowledged": True}, expected=202)
        with pytest.raises(web.ProjectApiError):
            action({**live_command, "action": "abandon_project_batch"})
        assert len(calls()) == 2
    finally:
        (signals / "release-b").touch()
    _wait_for(lambda: session.read()["project_task_batches"][run_id]["status"] == "blocked")
    initial = visible(run_id)
    by_task = {job["project_task_id"]: job for job in initial["jobs"]}
    succeeded_job, failed_job = by_task[first["id"]], by_task[failed["id"]]
    assert succeeded_job["outcome"] == "ok" and failed_job["status"] == "failed"
    assert json.loads(Path(failed_job["status_path"]).read_text())["exit_code"] == 7
    assert ledger.get_task(first["id"])["status"] == "review" and ledger.get_task(failed["id"])["status"] == "blocked"
    initial_report_path = dispatch_artifact_root(source, initial["run_id"]) / "report.json"
    initial_report_bytes = initial_report_path.read_bytes()
    succeeded_root = Path(succeeded_job["status_path"]).parent
    succeeded_files = _files(succeeded_root)
    failed_root = Path(failed_job["status_path"]).parent
    failed_files = _files(failed_root)
    continuation.tick_authorized_project_continuations(tmp_path)
    blocked = session.read()["project_continuation"]
    assert blocked["status"] == "blocked" and blocked["id"] == authorized["id"]
    assert blocked["batches_started"] == blocked["manager_calls"] == 1

    # Fault injection at the persisted evidence boundary, after the process
    # actually exited: missing termination proof must never authorize another call.
    status = Path(failed_job["status_path"])
    hidden = status.with_suffix(".fixture-hidden")
    status.rename(hidden)
    try:
        unknown = visible(run_id)["recovery"]
        assert failed["id"] in unknown["unknown_task_ids"] and not unknown["can_abandon"]
        unknown_command = {"run_id": run_id, "expected_recovery_version": unknown["version"],
                           "idempotency_key": "must-not-start-unknown", "confirmed": True}
        with pytest.raises(web.ProjectApiError):
            action({**unknown_command, "action": "retry_project_batch", "retry_task_ids": [failed["id"]],
                    "billing_acknowledged": True}, expected=202)
        with pytest.raises(web.ProjectApiError):
            action({**unknown_command, "action": "abandon_project_batch"})
        assert len(calls()) == 2
    finally:
        hidden.rename(status)

    ready = visible(run_id)["recovery"]
    assert ready["retryable_task_ids"] == [failed["id"]] and ready["preserved_task_ids"] == [first["id"]]
    assert ready["can_resume_continuation"] is True and ready["continuation_id"] == authorized["id"]
    retry = {"action": "retry_project_batch", "run_id": run_id, "expected_recovery_version": ready["version"],
             "idempotency_key": "retry-only-b", "retry_task_ids": [failed["id"]], "confirmed": True,
             "billing_acknowledged": True, "resume_continuation": True, "continuation_id": authorized["id"]}
    interrupt_projection(retry, failed["id"], "in_progress", lambda batch: bool(batch.get("current_recovery_attempt")), expected=202)
    saved_batch = session.read()["project_task_batches"][run_id]
    saved_attempt = saved_batch["recovery_commands"][saved_batch["current_recovery_attempt"]]["attempt_id"]
    assert len(calls()) == 2 and ledger.get_task(failed["id"])["status"] == "blocked"
    continuation.tick_authorized_project_continuations(tmp_path)
    assert session.read()["project_continuation"]["status"] == "active"
    resumed = action(retry, expected=202)
    assert resumed["attempt_id"] == saved_attempt
    assert action(retry, expected=202)["attempt_id"] == resumed["attempt_id"]
    _wait_for(lambda: session.read()["project_task_batches"][run_id]["status"] == "review")
    recovered = visible(run_id)
    current = {job["project_task_id"]: job for job in recovered["jobs"]}
    assert recovered["all_ok"] and recovered["integration"]["status"] == "succeeded"
    assert current[first["id"]]["job_id"] == succeeded_job["job_id"]
    assert current[first["id"]]["result_commit"] == succeeded_job["result_commit"]
    assert current[failed["id"]]["job_id"] != failed_job["job_id"]
    retry_job = json.loads((Path(current[failed["id"]]["status_path"]).parent / "job.json").read_text())
    assert retry_job["original_failed_job_id"] == failed_job["job_id"]
    assert recovered["integration"]["baseline_commit"] == initial["baseline"]["baseline_commit"]
    assert initial_report_path.read_bytes() == initial_report_bytes and _files(succeeded_root) == succeeded_files
    assert _files(failed_root) == failed_files
    assert sorted(item["task"] for item in calls()) == ["unit-a.txt", "unit-b.txt", "unit-b.txt"]
    active = session.read()["project_continuation"]
    assert active["status"] == "active" and active["id"] == authorized["id"]
    assert active["batches_started"] == active["manager_calls"] == 1 and active["max_batches"] == 2
    assert git(source, "rev-parse", "HEAD") == initial_head and not (source / "unit-a.txt").exists()

    # The recovered effective result enters the same #623 quality/delivery path.
    checks = tmp_path / "checks.log"
    check = ("from pathlib import Path; assert Path('unit-a.txt').read_text() == 'fixed\\n'; "
             "assert Path('unit-b.txt').read_text() == 'ready\\n'; "
             f"Path({str(checks)!r}).open('a').write('passed\\n')")
    prepared = prepare_project_batch_quality(workspace=tmp_path, project_key="DEMO", root=source, run_id=run_id,
        argv=[sys.executable, "-I", "-B", "-c", check], criteria=[0, 1], reviewer_model="offline-fixture",
        fix_model="offline-fixture", seconds=30, wall_seconds=300, fix_seconds=30)
    snapshot = store_for_project(source, existing_only=True).load(prepared["execution_id"])
    captured = {row["task_id"]: row for row in snapshot.task.task_contract.source_intent["input"]["results"]}
    assert {key: row["job_id"] for key, row in captured.items()} == {key: row["job_id"] for key, row in current.items()}
    review_admission = _review_admission(tmp_path, source, snapshot, binaries["review"], native_home)
    quality = action({"action": "advance_project_batch_quality", "run_id": run_id,
        "expected_input_digest": prepared["input_digest"], "confirmed": True, "billing_acknowledged": True},
        codex_review_admission=review_admission)["quality"]
    assert quality["status"] == "reviewed" and quality["candidate_commit"] == recovered["integration"]["candidate_commit"]
    assert checks.read_text() == "passed\n"
    git(source, "merge", "--quiet", "--no-edit", "--no-ff", quality["candidate_commit"])
    action({"action": "accept_project_batch_delivery", "run_id": run_id, "candidate_commit": quality["candidate_commit"],
            "landed_commit": git(source, "rev-parse", "HEAD"), "reviewed": True, "confirmed": True})
    assert all(ledger.get_task(identifier)["status"] == "done" and ledger.get_task(identifier)["delivery_verified"]
               for identifier in (first["id"], failed["id"]))

    continuation.tick_authorized_project_continuations(tmp_path)
    _wait_for(lambda: ledger.get_task(later["id"])["status"] == "blocked")
    second_run = session.read()["project_continuation"]["pending_batch"]["run_id"]
    _wait_for(lambda: session.read()["project_task_batches"][second_run]["status"] == "blocked")
    second = visible(second_run)
    assert "quality" not in second  # Earlier quality enrollment does not configure this later batch.
    assert second_run != run_id and choices == [[first["id"], failed["id"]], [later["id"]]]
    assert len(calls()) == 4 and len({item["cwd"] for item in calls()}) == 4
    second_report_path = dispatch_artifact_root(source, second["run_id"]) / "report.json"
    second_report_bytes = second_report_path.read_bytes()
    later_root = Path(second["jobs"][0]["status_path"]).parent
    later_files = _files(later_root)
    action({"action": "stop_project_continuation", "continuation_id": authorized["id"]})
    abandon = {"action": "abandon_project_batch", "run_id": second_run,
               "expected_recovery_version": visible(second_run)["recovery"]["version"],
               "idempotency_key": "abandon-c", "confirmed": True}
    interrupt_projection(abandon, later["id"], "canceled", lambda batch: batch["status"] == "abandoned")
    assert session.read()["project_task_batches"][second_run]["status"] == "abandoned"
    assert ledger.get_task(later["id"])["status"] == "blocked" and len(calls()) == 4
    action(abandon)
    action(abandon)
    assert session.read()["project_task_batches"][second_run]["status"] == "abandoned"
    assert ledger.get_task(later["id"])["status"] == "canceled" and not ledger.get_task(later["id"])["delivery_verified"]
    assert second_report_path.read_bytes() == second_report_bytes and _files(later_root) == later_files
    assert action(retry, expected=202)["attempt_id"] == resumed["attempt_id"]
    continuation.tick_authorized_project_continuations(tmp_path)
    stopped = session.read()["project_continuation"]
    assert stopped["id"] == authorized["id"] and stopped["status"] == "stopped" and stopped["pending_batch"] is None
    assert stopped["batches_started"] == stopped["manager_calls"] == stopped["max_batches"] == 2
    assert len(calls()) == 4 and len(review_calls.read_text().splitlines()) == 1
    assert len(WorkerJobStore(source).list_jobs()) == 5
    assert initial_report_path.read_bytes() == initial_report_bytes and _files(succeeded_root) == succeeded_files
