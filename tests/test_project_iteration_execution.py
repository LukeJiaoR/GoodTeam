"""[INPUT]: 已确认的任务与本地 CLI 进程夹具。
[OUTPUT]: 验证派工、候选结果和原任务卡状态。
[POS]: 覆盖从 Manager 选择到隔离 Worker 的项目批次入口。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import os
from pathlib import Path
import sys
import time
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
    with pytest.raises(ValueError, match="project source changed"):
        run_queued_iteration_batch(**request, confirmed=True, expected_authority={})
    # Revocation during runtime capture shares admission's session lock; the
    # final atomic confirmation must refuse it before any task/Worker claim.
    from goodteam_os import project_iteration_execution as execution
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    session._mutate(lambda current: {**current, "project_continuation": {"id": "permit", "status": "active"}})
    preview = execution._runtime_tracks
    def revoke_during_preview(*args):
        tracks = preview(*args)
        session._mutate(lambda current: {**current, "project_continuation": {"id": "permit", "status": "stopped"}})
        return tracks
    with monkeypatch.context() as patch:
        patch.setattr(execution, "_runtime_tracks", revoke_during_preview)
        with pytest.raises(ValueError, match="stopped or replaced"):
            run_queued_iteration_batch(**request, confirmed=True, expected_continuation_id="permit")
    assert not session.read().get("project_task_batches")
    session._mutate(lambda current: {**current, "project_continuation": None})
    assert not (signals / "calls.jsonl").exists()

    callers = Barrier(2)
    def start_once():
        callers.wait()
        try:
            return run_queued_iteration_batch(**request, confirmed=True, background=True)
        except ValueError as exc:
            assert "busy" in str(exc), str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start_once(), range(2)))
    first = next(item for item in responses if item is not None)
    # Eleven isolated Git workspaces plus evidence fan-in take about 30 seconds
    # on a development host. Keep a bounded wait without racing that baseline;
    # the provider overlap deadline and all execution assertions stay intact.
    deadline = time.monotonic() + 60
    while any(task["status"] in {"todo", "in_progress"} for task in ledger.list_tasks()):
        assert time.monotonic() < deadline, "The background batch did not settle."
        # Repeated service ticks observe the same live thread and do not
        # incorrectly block on its workflow lock or accumulate new attempts.
        observed = run_queued_iteration_batch(**request, confirmed=True, background=True)
        assert observed["run_id"] == first["run_id"]
        time.sleep(.02)
    report = iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source, run_id=first["run_id"])
    assert report["all_ok"], report
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

    event_id = f"batch-result:{first['run_id']}:initial"
    pending_heading = "Worker results returned. Candidate acceptance and delivery are pending."
    legacy_heading = "Worker results returned; independent review and delivery remain outstanding."

    def retain_legacy_heading(current):
        result = next(item for item in current["timeline"] if item.get("message_id") == event_id)
        assert result["content"].startswith(pending_heading)
        result["content"] = result["content"].replace(pending_heading, legacy_heading, 1)
        return current

    session._mutate(retain_legacy_heading)
    again = run_queued_iteration_batch(**request, confirmed=True)
    assert again["status"] == "review"
    assert again["report"] == report
    assert len((signals / "calls.jsonl").read_text().splitlines()) == 22
    assert iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source,
                                  run_id=first["run_id"]) == report


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


def test_codebuddy_worker_settles_project_batches_from_final_json(tmp_path, monkeypatch):
    from goodteam_os.team_control_plane import execute_team_commands
    from goodteam_os.workers import worker_backend_doctor
    from goodteam_os.web import WORKER_BACKEND_OPTIONS

    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: fake\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    binary = binaries / "codebuddy"
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, sys
from pathlib import Path

if "--version" in sys.argv:
    print("CodeBuddy CLI fixture")
    raise SystemExit(0)
assert "--model" not in sys.argv
assert sys.argv[sys.argv.index("--output-format") + 1] == "json"
assert sys.argv[sys.argv.index("--permission-mode") + 1] == "dontAsk"
assert sys.argv[sys.argv.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"
assert Path(os.environ["CODEBUDDY_CONFIG_DIR"]).parent != Path.cwd()
assert "OPENAI_API_KEY" not in os.environ
assert not Path("node_modules").exists()
prompt = sys.stdin.read()
if "TASK_ABORT" in prompt:
    credential = os.environ["CODEBUDDY_API_KEY"]
    print(json.dumps([{"type": "result", "subtype": "error_during_execution", "is_error": True,
                       "result": f"Fixture response binding {credential}"}]))
else:
    assert "CODEBUDDY_API_KEY" not in os.environ and "CODEBUDDY_AUTH_TOKEN" not in os.environ
    assert "CODEBUDDY_BASE_URL" not in os.environ
    Path("isolated.txt").write_text("CodeBuddy fixture change\n")
    print(json.dumps([{"type": "result", "subtype": "success", "is_error": False,
                       "result": "Fixture CLI sign-in response\n"}]))
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("CODEBUDDY_API_KEY", raising=False)
    monkeypatch.delenv("CODEBUDDY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEBUDDY_BASE_URL", "https://codebuddy-fixture.invalid")
    assert "codebuddy" in WORKER_BACKEND_OPTIONS
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-unrelated-secret")
    doctor = worker_backend_doctor("codebuddy")
    assert doctor["ready"] is True and doctor["auth_present"] is False
    execute_team_commands(tmp_path, "DEMO", [
        {"kind": "initialize_worker", "role": "implementation", "worker_id": "builder-codebuddy",
         "name": "CodeBuddy", "runtime": {"provider": "codebuddy"}},
    ], actor="user", surface="team_ui", config_path=config)
    (source / ".git/info/exclude").write_text("node_modules/\n")
    (source / "node_modules").mkdir()
    ledger = LocalTaskStore(source, "DEMO")
    first = ledger.create_task(title="Write isolated.txt", description="Write the assigned file.",
        role="implementation", worker_id="builder-codebuddy",
        write_domains=["isolated.txt"], acceptance_criteria=["Contains the fixture change."])
    second = ledger.create_task(title="TASK_ABORT", description="Exercise a terminal execution error.",
        role="implementation", worker_id="builder-codebuddy",
        write_domains=["aborted.txt"], acceptance_criteria=["Contains the next fixture change."])

    def run_selected(task_id, *, previous=None):
        def manager(*, backlog, **_):
            candidates = {item["task_id"]: item for item in backlog["candidates"]}
            if previous is not None:
                assert candidates[previous]["status"] == "review"
            return {"selection": {"selected_task_ids": [task_id], "compared_task_ids": list(candidates),
                                  "rationale": "Advance the next eligible task."}}

        snapshot = project_iteration_backlog(source, "DEMO")
        plan_next_iteration(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
            expected_backlog_version=snapshot["version"], confirmed=True, manager_runner=manager)
        queued = queue_manager_iteration_choice(workspace=tmp_path, project_key="DEMO", root=source,
            expected_backlog_version=snapshot["version"])
        ids = queued["queued_proposal_ids"]
        return run_queued_iteration_batch(workspace=tmp_path, project_key="DEMO", root=source, project_config=config,
            proposal_ids=ids, expected_revisions=dict.fromkeys(ids, 1), confirmed=True)

    completed = run_selected(first["id"])
    assert completed["status"] == "review", completed
    report = completed["report"]
    job = report["jobs"][0]
    assert job["adapter"] == "codebuddy" and job["worker_id"] == "builder-codebuddy"
    assert job["write_scope_verified"] is True
    assert Path(job["result_path"]).read_text() == "Fixture CLI sign-in response\n"
    assert Path(report["integration"]["candidate_path"], "isolated.txt").read_text() == "CodeBuddy fixture change\n"
    assert not (source / "isolated.txt").exists()
    monkeypatch.setenv("CODEBUDDY_API_KEY", "fixture-only-secret")
    blocked = run_selected(second["id"], previous=first["id"])
    assert blocked["status"] == "blocked"
    failed = blocked["report"]["jobs"][0]
    status = json.loads(Path(failed["status_path"]).read_text())
    assert status["exit_code"] == 0
    assert failed["summary"].startswith("CodeBuddy reported an execution error")
    assert "Fixture response binding [REDACTED]" in Path(status["stdout_path"]).read_text()
