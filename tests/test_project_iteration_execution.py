"""One real subprocess batch proves queue -> isolated pool -> original cards."""
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from goodteam_os.assistant_queue import queue_manager_iteration_choice
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_execution import iteration_batch_report, run_queued_iteration_batch
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import repository


def test_confirmed_batch_runs_same_role_workers_in_isolation_and_replays_once(tmp_path, monkeypatch):
    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "codex"
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, re, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('isolated project-batch fixture')
    raise SystemExit(0)
prompt = sys.stdin.read()
name = re.search(r'TASK_FILE=(unit-\d+\.txt)', prompt).group(1)
signals = Path(os.environ['BATCH_FIXTURE_SIGNALS'])
def event(kind):
    with (signals / 'calls.jsonl').open('a') as log:
        log.write(json.dumps({'event': kind, 'task': name, 'cwd': str(Path.cwd())}) + '\n')
event('start')
(signals / (name + '.ready')).touch()
deadline = time.monotonic() + 20
while len(list(signals.glob('*.ready'))) < 10:
    if time.monotonic() > deadline:
        raise SystemExit('Ten Workers did not overlap')
    time.sleep(.01)
Path(name).write_text('delivered by ' + name + '\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Task output retained for review.\n')
event('end')
''')
    binary.chmod(0o755)
    signals = tmp_path / "signals"
    signals.mkdir()
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("BATCH_FIXTURE_SIGNALS", str(signals))
    ledger = LocalTaskStore(source, "DEMO")
    tasks = [ledger.create_task(title=f"TASK_FILE=unit-{i}.txt", description="Write only the assigned file.",
             role="implementation", write_domains=[f"unit-{i}.txt"],
             acceptance_criteria=["The assigned file contains the requested text."]) for i in range(11)]
    task_ids = [task["id"] for task in tasks]
    snapshot = project_iteration_backlog(source, "DEMO")
    plan_next_iteration(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
        expected_backlog_version=snapshot["version"], confirmed=True, manager_runner=lambda **_: {
            "selection": {"selected_task_ids": task_ids, "compared_task_ids": task_ids,
                "rationale": "All eleven tasks have disjoint explicit file scopes and independent acceptance criteria."}})
    queued = queue_manager_iteration_choice(workspace=tmp_path, project_key="DEMO", root=source,
        expected_backlog_version=snapshot["version"])
    request = dict(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
        proposal_ids=queued["queued_proposal_ids"], expected_revisions={key: 1 for key in queued["queued_proposal_ids"]})
    with pytest.raises(ValueError, match="Confirm"):
        run_queued_iteration_batch(**request)
    assert not (signals / "calls.jsonl").exists()

    callers = Barrier(2)
    def start_once():
        callers.wait()
        try:
            return run_queued_iteration_batch(**request, confirmed=True)
        except ValueError as exc:
            assert "busy" in str(exc), str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start_once(), range(2)))
    first = next(item for item in responses if item is not None)
    assert first["status"] == "review", first
    report = first["report"]
    assert report["worker_capacity"]["pool_size"] == 10
    assert len(report["jobs"]) == len(WorkerJobStore(source).list_jobs()) == 11
    assert {job["project_task_id"] for job in report["jobs"]} == set(task_ids)
    assert {job["role"] for job in report["jobs"]} == {"implementation"}
    assert all(job["write_scope_verified"] for job in report["jobs"])
    events = [json.loads(line) for line in (signals / "calls.jsonl").read_text().splitlines()]
    active = peak = 0
    for event in events:
        active += 1 if event["event"] == "start" else -1
        peak = max(active, peak)
    assert active == 0 and peak == 10
    assert len({event["cwd"] for event in events}) == 11
    assert all(Path(event["cwd"]) != source for event in events)
    assert not list(source.glob("unit-*.txt"))
    candidate = Path(report["integration"]["candidate_path"])
    assert len(list(candidate.glob("unit-*.txt"))) == 11
    assert all(task["status"] == "review" and not task["delivery_verified"]
               and task["run_id"] == first["run_id"] and task["dispatch_job_id"] for task in ledger.list_tasks())

    again = run_queued_iteration_batch(**request, confirmed=True)
    assert again["report"] == report
    assert len((signals / "calls.jsonl").read_text().splitlines()) == 22
    assert iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source,
                                  run_id=first["run_id"]) == report
    # An interrupted outer publication does not authorize replay over a later
    # Workflow. Existing Worker receipts remain, but no new execution starts.
    from goodteam_os.dispatch_workspace import dispatch_artifact_root
    from goodteam_os.state import StateStore, create_initial_state
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    report_path = dispatch_artifact_root(source, first["dispatch_run_id"]) / "report.json"
    retained_report = report_path.read_bytes()
    report_path.unlink()
    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    def interrupted(value):
        value["project_task_batches"][first["run_id"]]["status"] = "blocked"
        return value
    session._mutate(interrupted)
    later = create_initial_state("later-workflow", "DEMO", "Separate owner-authorized task")
    StateStore(tmp_path).write_project_current(later)
    with pytest.raises(ValueError, match="active project execution"):
        run_queued_iteration_batch(**request, confirmed=True)
    assert len((signals / "calls.jsonl").read_text().splitlines()) == 22
    report_path.write_bytes(retained_report)


def test_batch_captured_write_scope_violation_blocks_candidate_and_preserves_source(tmp_path, monkeypatch):
    """An observed out-of-scope write cannot become a successful fan-in."""
    from goodteam_os.dispatch import dispatch_tracks

    source = repository(tmp_path / "source")
    run = WorkerJobStore.run_job

    def writing_worker(store, identifier, **kwargs):
        Path(kwargs["sandbox_context"]["workspace_path"], "outside.txt").write_text("unapproved")
        return run(store, identifier, **kwargs)

    monkeypatch.setattr(WorkerJobStore, "run_job", writing_worker)
    result = dispatch_tracks(project_root=source, logical_task_id="scope-violation", require_isolation=True,
        tracks=[{"track_id": "APP-1", "role": "implementation", "adapter": "fake", "task": "Write allowed.txt",
                 "project_task_id": "APP-1", "write_domains": ["allowed.txt"]}])
    assert result["all_ok"] is False
    assert result["integration"]["status"] == "blocked"
    assert result["jobs"][0]["write_scope_verified"] is False
    assert not (source / "outside.txt").exists()
