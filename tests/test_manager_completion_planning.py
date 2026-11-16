"""Native planning replay keeps the original billable turn and input binding.

The app-server protocol peer is an explicit offline subprocess fixture.
"""
import json
import os
import sys

import pytest

from goodteam_os.project_iteration_planner import run_manager_iteration_choice
from goodteam_os.manager_execution import ManagerExecutionError


NATIVE_SERVER = r'''
import json, sys
from pathlib import Path

response, calls = map(Path, sys.argv[1:])
def send(record):
    print(json.dumps(record), flush=True)

counter = 0
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params", {})
    if method == "initialized":
        continue
    if method == "initialize":
        result = {}
    elif method in {"thread/start", "thread/resume", "thread/read"}:
        result = {"thread":{"id":"native-planning-thread","status":{"type":"idle"}}}
    elif method == "turn/start":
        counter += 1
        turn = "native-turn-" + str(counter)
        thread = params["threadId"]
        with calls.open("a") as handle:
            handle.write(json.dumps(params) + "\n")
        text = response.read_text()
        send({"id":request["id"],"result":{"turn":{"id":turn,"status":"inProgress"}}})
        send({"method":"turn/started","params":{"threadId":thread,"turn":{"id":turn}}})
        send({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,
            "itemId":"answer","delta":text}})
        send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,
            "item":{"id":"answer","type":"agentMessage","phase":"final_answer","text":text}}})
        send({"method":"turn/completed","params":{"threadId":thread,
            "turn":{"id":turn,"status":"completed","items":[]}}})
        continue
    else:
        raise AssertionError(method)
    send({"id":request["id"],"result":result})
'''


@pytest.fixture
def native_manager(tmp_path, monkeypatch):
    from goodteam_os.manager_app_server import ManagerAppServer, close_runtimes

    root = tmp_path / "target"
    root.mkdir()
    projects = tmp_path / "projects"
    projects.mkdir()
    config = projects / "demo.local.yaml"
    config.write_text(f"key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n")
    script, response, calls = (tmp_path / name for name in ("native_server.py", "response.txt", "calls.jsonl"))
    script.write_text(NATIVE_SERVER)
    response.write_text("{}")
    monkeypatch.setattr(ManagerAppServer, "_runtime_config", lambda self: (
        [sys.executable, "-u", str(script), str(response), str(calls)], dict(os.environ),
        {"model": "fixture", "reasoning_depth": "high", "additional_prompt": "", "provider_connection": None},
        "native-fixture",
    ))
    close_runtimes()
    yield tmp_path, root, config, response, calls
    close_runtimes()


def test_public_planning_reuses_native_turn_and_rejects_changed_reserved_evidence(native_manager):
    _, root, config, response, calls = native_manager
    selection = dict(selected_task_ids=["DEMO-1"], compared_task_ids=["DEMO-1"],
        rationale="The supplied facts justify this already authorized independent task.")
    response.write_text(json.dumps(selection))
    kwargs = dict(project_config=config, project_key="DEMO", backlog={"version": "fixture", "candidates": []},
        context={"schema_version": "fixture_supplied_context.v1", "project_key": "DEMO",
                 "facts": ["DEMO-1 is the already authorized next task."]},
        planning_attempt_id="original-authorized-reservation")
    first = run_manager_iteration_choice(**kwargs)
    assert first["selection"] == selection
    native = first["manager_execution"]
    assert native["adapter"] == "codex-app-server" and native["thread_id"] and native["turn_id"]
    before = calls.read_bytes()
    for _ in range(2):
        replay = run_manager_iteration_choice(**kwargs)
        assert replay == first
    assert calls.read_bytes() == before
    assert not list((root / ".goodteam/workers/jobs").glob("*/job.json"))
    changed = {**kwargs, "context": {**kwargs["context"], "facts": ["The original evidence changed."]}}
    with pytest.raises(ManagerExecutionError, match="different input|changed"):
        run_manager_iteration_choice(**changed)
    assert calls.read_bytes() == before
