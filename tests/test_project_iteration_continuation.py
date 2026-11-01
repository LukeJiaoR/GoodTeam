"""Two distinct project batches through public actions and the service tick.

Only the model response and provider executable are offline fixtures. Queue,
Worker subprocesses, isolated repositories, fan-in, landing and receipts are real.
"""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from goodteam_os import web
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os.project_iteration_execution import iteration_batch_report
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from test_dispatch_isolation import git, repository


def test_verified_delivery_automatically_replans_next_scoped_batch_and_survives_replay(tmp_path, monkeypatch):
    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, re, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('offline project continuation fixture')
    raise SystemExit(0)
name = re.search(r'TASK_FILE=(unit-\d+\.txt)', sys.stdin.read()).group(1)
with Path(os.environ['CONTINUATION_FIXTURE_CALLS']).open('a') as calls:
    calls.write(json.dumps({'task': name, 'cwd': str(Path.cwd())}) + '\n')
if name == 'unit-2.txt':
    assert Path('unit-1.txt').read_text() == 'delivered unit-1.txt\n'
    deadline = time.monotonic() + 10
    while not Path(os.environ['CONTINUATION_FIXTURE_RELEASE']).exists():
        if time.monotonic() > deadline:
            raise SystemExit('Fixture was not released')
        time.sleep(.01)
Path(name).write_text('delivered ' + name + '\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Candidate ready for independent review.\n')
''')
    binary.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CONTINUATION_FIXTURE_CALLS", str(calls))
    release = tmp_path / "release-second-worker"
    monkeypatch.setenv("CONTINUATION_FIXTURE_RELEASE", str(release))
    ledger = LocalTaskStore(source, "DEMO")

    def task(number, **kwargs):
        return ledger.create_task(title=f"TASK_FILE=unit-{number}.txt", description="Write only the requested file.",
            role="implementation", write_domains=[f"unit-{number}.txt"],
            acceptance_criteria=["The requested file has the expected content."], **kwargs)

    first_task = task(1)
    second_task = task(2, dependencies=[first_task["id"]])
    unapproved = task(99, priority="critical")
    choices = []

    def manager(**kwargs):
        snapshot = kwargs["backlog"]
        assert unapproved["id"] not in {item["task_id"] for item in snapshot["candidates"]}
        eligible = [item for item in snapshot["candidates"] if item["eligible_to_plan"]]
        selected = eligible[0]["task_id"]
        choices.append(selected)
        if selected == second_task["id"]:
            assert ledger.get_task(first_task["id"])["delivery_verified"] is True
            assert (source / "unit-1.txt").read_text() == "delivered unit-1.txt\n"
        return {"selection": {"selected_task_ids": [selected], "compared_task_ids": [item["task_id"] for item in eligible],
                "rationale": "Choose the approved eligible task using freshly observed dependency and delivery evidence."}}

    monkeypatch.setattr(continuation, "plan_next_iteration", lambda **kwargs: plan_next_iteration(**kwargs, manager_runner=manager))
    endpoint = "/api/projects/DEMO/task-assistant/actions"

    def action(request):
        code, result = web.handle_project_api_post(tmp_path, endpoint, request)
        assert code == 200
        return result

    def wait_for(predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            assert time.monotonic() < deadline, "Background batch did not settle"
            time.sleep(.01)

    authorization = dict(action="start_project_continuation", task_ids=[first_task["id"], second_task["id"]],
                         max_batches=2, confirmed=True, billing_acknowledged=True, idempotency_key="two-batches",
                         expected_backlog_version=project_iteration_backlog(source, "DEMO")["version"])
    authorized = action(authorization)["project_continuation"]
    assert action(authorization)["project_continuation"] == authorized
    assert not calls.exists()
    continuation.tick_authorized_project_continuations(tmp_path)
    wait_for(lambda: ledger.get_task(first_task["id"])["status"] in {"review", "blocked"})
    session = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    first_run = session["project_continuation"]["pending_batch"]["run_id"]
    first_report = iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source, run_id=first_run)
    assert ledger.get_task(first_task["id"])["status"] == "review"
    assert ledger.get_task(second_task["id"])["status"] == "todo"
    continuation.tick_authorized_project_continuations(tmp_path)
    assert choices == [first_task["id"]]

    def delivery_request(run_id, report):
        return dict(action="accept_project_batch_delivery", run_id=run_id,
                    candidate_commit=report["integration"]["candidate_commit"], landed_commit=git(source, "rev-parse", "HEAD"),
                    reviewed=True, confirmed=True)

    with pytest.raises(web.ProjectApiError, match="has not landed"):
        action(delivery_request(first_run, first_report))
    assert ledger.get_task(first_task["id"])["status"] == "review"

    def land(report):
        integration = report["integration"]
        git(source, "fetch", "--quiet", integration["candidate_path"], integration["candidate_commit"])
        git(source, "merge", "--quiet", "--no-edit", "--no-ff", integration["candidate_commit"])

    land(first_report)
    first_acceptance = delivery_request(first_run, first_report)
    candidate_file = Path(first_report["integration"]["candidate_path"]) / "unit-1.txt"
    original_content = candidate_file.read_text()
    candidate_file.write_text("Uncommitted candidate edit after review\n")
    with pytest.raises(web.ProjectApiError, match="candidate contains uncommitted"):
        action(first_acceptance)
    candidate_file.write_text(original_content)
    verify = continuation._verify_landed_batch

    def concurrent_edit(**kwargs):
        report = verify(**kwargs)
        ledger.update_task(first_task["id"], title="Changed during delivery verification")
        return report

    with monkeypatch.context() as patch:
        patch.setattr(continuation, "_verify_landed_batch", concurrent_edit)
        with pytest.raises(web.ProjectApiError, match="does not match every original"):
            action(first_acceptance)
    ledger.update_task(first_task["id"], title=first_task["title"])
    action(first_acceptance)
    assert ledger.get_task(first_task["id"])["status"] == "done"
    assert len(calls.read_text().splitlines()) == 1

    # No user start/pick follows delivery: the existing service starts batch 2.
    continuation.tick_authorized_project_continuations(tmp_path)
    current = TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_continuation"]
    second_run = current["pending_batch"]["run_id"]
    assert second_run != first_run
    assert choices == [first_task["id"], second_task["id"]]
    assert current["batches_started"] == current["manager_calls"] == 2
    wait_for(lambda: len(calls.read_text().splitlines()) == 2)
    # The next regular service tick reuses the active background execution;
    # it must not wait for its workflow lock, block continuation or start again.
    time.sleep(1.05)
    continuation.tick_authorized_project_continuations(tmp_path)
    assert TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_continuation"]["status"] == "active"
    action(dict(action="stop_project_continuation", continuation_id=authorized["id"]))
    assert ledger.get_task(second_task["id"])["status"] == "in_progress"
    release.touch()
    wait_for(lambda: ledger.get_task(second_task["id"])["status"] in {"review", "blocked"})
    second_report = iteration_batch_report(workspace=tmp_path, project_key="DEMO", root=source, run_id=second_run)
    code, visible = web.handle_project_api_get(tmp_path, f"/api/projects/DEMO/iteration-batches/{second_run}")
    assert code == 200 and visible["integration"] == second_report["integration"]

    land(second_report)
    action(delivery_request(second_run, second_report))
    assert action(first_acceptance)["reused"] is True  # HEAD has moved since batch 1.
    stopped = TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_continuation"]
    assert stopped["status"] == "stopped" and stopped["pending_batch"] is None
    continuation.tick_authorized_project_continuations(tmp_path)
    assert ledger.get_task(unapproved["id"])["status"] == "todo"
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [item["task"] for item in observed] == ["unit-1.txt", "unit-2.txt"]
    assert len({item["cwd"] for item in observed}) == 2 and all(Path(item["cwd"]) != source for item in observed)
    # A stopped authorization can be replaced after its delivery is settled.
    restarted = action({**authorization, "idempotency_key": "new-authorization",
                        "expected_backlog_version": project_iteration_backlog(source, "DEMO")["version"]})["project_continuation"]
    assert restarted["id"] != authorized["id"]
    continuation.tick_authorized_project_continuations(tmp_path)
    assert TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_continuation"]["status"] == "completed"
    assert len(calls.read_text().splitlines()) == 2


def test_competing_manual_recommendation_cannot_replace_authorized_queue_handoff(tmp_path, monkeypatch):
    from goodteam_os.project_iteration import validate_iteration_choice

    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: fake\n")
    ledger = LocalTaskStore(source, "DEMO")
    allowed = ledger.create_task(title="Approved work", description="Scoped feature", role="implementation")
    other = ledger.create_task(title="Unapproved work", description="A separate feature", role="implementation")
    continuation.configure_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
        project_config=config, request={"task_ids": [allowed["id"]], "max_batches": 1, "confirmed": True,
                                      "expected_backlog_version": project_iteration_backlog(source, "DEMO")["version"],
                                      "billing_acknowledged": True, "idempotency_key": "scoped-handoff"})
    queue = continuation.queue_manager_iteration_choice

    def competing_choice(**kwargs):
        snapshot = project_iteration_backlog(source, "DEMO", limit=None)
        choice = validate_iteration_choice(snapshot, {"selected_task_ids": [other["id"]],
            "compared_task_ids": [other["id"]], "rationale": "A concurrent manual recommendation chose another task."})
        TaskAssistantSessionStore(tmp_path, "DEMO").append_event(
            f"iteration-choice:{snapshot['version']}", "project_iteration_choice_recorded", details={"choice": choice})
        return queue(**kwargs)

    monkeypatch.setattr(continuation, "queue_manager_iteration_choice", competing_choice)
    result = continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
        project_config=config, manager_runner=lambda **_: {"selection": {"selected_task_ids": [allowed["id"]],
            "compared_task_ids": [allowed["id"]], "rationale": "Only the approved existing project task can execute."}},
        batch_runner=lambda **_: pytest.fail("A competing recommendation must never dispatch"))
    assert result["status"] == "blocked" and "recommendation changed" in result["reason"]
    assert all(task["status"] == "todo" for task in ledger.list_tasks())
    assert TaskAssistantSessionStore(tmp_path, "DEMO").read().get("queued_task_proposals", []) == []

    monkeypatch.setattr(continuation, "queue_manager_iteration_choice", queue)
    request = {"task_ids": [allowed["id"]], "max_batches": 1, "confirmed": True, "billing_acknowledged": True}
    selection = {"selection": {"selected_task_ids": [allowed["id"]], "compared_task_ids": [allowed["id"]],
                  "rationale": "Only the approved existing project task can execute."}}
    for command in ("stop-during-planning", "configuration-during-planning"):
        ledger.update_task(allowed["id"], priority="low")  # a fresh source revision, with the same task scope
        request["expected_backlog_version"] = project_iteration_backlog(source, "DEMO")["version"]
        authorization = continuation.configure_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
            project_config=config, request={**request, "idempotency_key": command})

        def manager(**_kwargs):
            if command == "stop-during-planning":
                continuation.stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id=authorization["id"])
            else:
                config.write_text(config.read_text() + "# configuration changed during planning\n")
            return selection

        result = continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
            project_config=config, manager_runner=manager,
            batch_runner=lambda **_: pytest.fail("Revoked or changed authority must never dispatch"))
        assert result["status"] == ("stopped" if command == "stop-during-planning" else "blocked")
        assert all(task["status"] == "todo" for task in ledger.list_tasks())
    replacement = continuation.configure_project_continuation(workspace=tmp_path, project_key="DEMO", root=source,
        project_config=config, request={**request, "idempotency_key": "after-unclaimed-configuration-block"})
    assert replacement["status"] == "active" and replacement["pending_batch"] is None
