"""[INPUT]: 模拟原生 CLI 和临时 Git 项目。
[OUTPUT]: 验证原生工具到任务队列和 Worker 结果的链路。
[POS]: 保留 Codex app-server 与 CodeBuddy ACP 的集成验证。
[PROTOCOL]: Update this header on change, then check AGENTS.md

Both providers are offline protocol fixtures, proving routing, not model quality.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time

import pytest

from goodteam_os.project_iteration_execution import run_queued_iteration_batch
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import ensure_team_state, execute_team_commands, resolve_worker_from_state
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import repository


MANAGER_PROTOCOL = r'''
import json, sys
from pathlib import Path

calls = Path(sys.argv[1])
counter = 0
pending = None
thread, turn = "assignment-thread", ""
job_id, batch_run_id = "", ""

def send(value):
    print(json.dumps(value), flush=True)

def tool(name, arguments):
    global pending
    pending = name
    send({"id": "tool:" + name + ":" + turn, "method": "item/tool/call", "params": {
        "threadId": thread, "turnId": turn, "callId": name + ":" + turn,
        "tool": name, "arguments": arguments}})

def reply(text):
    send({"method": "item/agentMessage/delta", "params": {
        "threadId": thread, "turnId": turn, "itemId": "reply", "delta": text}})
    send({"method": "item/completed", "params": {
        "threadId": thread, "turnId": turn,
        "item": {"id": "reply", "type": "agentMessage", "phase": "final_answer", "text": text}}})
    send({"method": "turn/completed", "params": {
        "threadId": thread, "turn": {"id": turn, "status": "completed", "items": []}}})

for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params", {})
    with calls.open("a") as handle:
        handle.write(json.dumps(request) + "\n")
    if method is None:
        receipt = request["result"]
        assert receipt["success"], receipt
        value = json.loads(receipt["contentItems"][0]["text"])
        if pending == "goodteam_delegate":
            assert value["task_id"] and value["proposal_id"] and not value["confirmed"]
            reply("已委派研究任务 " + value["task_id"] + "，等待现有执行确认。")
        elif pending == "goodteam_workers":
            assert len(value["workers"]) == 1, value
            worker = value["workers"][0]
            assert worker["role"] == "research" and worker["adapter"] == "codex"
            job_id, batch_run_id = worker["job_id"], worker["batch_run_id"]
            events = worker["native_progress"]["events"]
            assert any(event.get("content") == "Observed greeting in README; checking the audience." for event in events)
            if counter == 2:
                assert worker["native_progress"]["capabilities"]["process_owned"]
                reply("Worker 正在工作；已收到追加要求：结果请标明受众仍待确认。")
            else:
                assert "Observed greeting in README" in json.dumps(worker["worker_output"])
                tool("goodteam_result", {"run_id": batch_run_id})
        elif pending == "goodteam_result":
            assert value["report"]["jobs"][0]["job_id"] == job_id
            assert "Observed greeting in README" in value["worker_results"][0]["summary"]
            assert value["review"]["review_status"] == "missing" and not value["accepted"]
            reply("已检查真实成果：README 有问候语，受众仍待确认；下一步需要独立审查，尚未验收。")
        else:
            raise AssertionError(pending)
        continue
    if method == "initialized":
        continue
    if method == "initialize":
        result = {}
    elif method in {"thread/start", "thread/resume"}:
        if method == "thread/start":
            assert "goodteam_delegate" in {item["name"] for item in params["dynamicTools"]}
        result = {"thread": {"id": thread, "status": {"type": "idle"}}}
    elif method == "turn/start":
        counter += 1
        turn = "assignment-turn-" + str(counter)
        send({"id": request["id"], "result": {"turn": {"id": turn, "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}})
        if counter == 1:
            tool("goodteam_delegate", {
                "operation_id": "research-assignment", "source_message_ids": ["research"],
                "role": "research", "execution_policy": "read_only", "write_domains": [],
                "acceptance_criteria": ["Identify the greeting and remaining uncertainty."],
                "delegation": {"purpose": "Explain the product purpose from retained evidence.",
                    "context": "README evidence; unresolved audience question.",
                    "scope": "Read repository only; do not implement."}})
        else:
            tool("goodteam_workers", {})
        continue
    else:
        raise AssertionError(method)
    send({"id": request["id"], "result": result})
'''


ACP_PROTOCOL = r'''
import json
import subprocess
import sys

assert "--acp" in sys.argv and sys.argv[sys.argv.index("--tools") + 1] == ""

def send(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)

def mcp(peer, method, params=None):
    peer.stdin.write(json.dumps({"jsonrpc": "2.0", "id": method,
                                 "method": method, "params": params or {}}) + "\n")
    peer.stdin.flush()
    return json.loads(peer.stdout.readline())["result"]

for line in sys.stdin:
    request = json.loads(line)
    method, params = request["method"], request["params"]
    if method == "initialize":
        assert params["clientCapabilities"] == {}
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
    elif method == "session/new":
        server, = params["mcpServers"]
        assert server["name"] == "goodteam"
        result = {"sessionId": "assignment-acp"}
    elif method == "session/prompt":
        with subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as peer:
            assert mcp(peer, "initialize")["serverInfo"]["name"] == "goodteam"
            assert "goodteam_delegate" in {tool["name"] for tool in mcp(peer, "tools/list")["tools"]}
            tool = mcp(peer, "tools/call", {"name": "goodteam_delegate", "arguments": {
                "operation_id": "read-readme", "source_message_ids": ["acp-assignment"],
                "role": "research", "execution_policy": "read_only", "write_domains": [],
                "acceptance_criteria": ["Quote the README greeting."],
                "delegation": {"purpose": "Identify the project greeting.",
                               "context": "The project documents itself in README.md.",
                               "scope": "Read the README."}}})
            assert tool["isError"] is False, tool
            receipt = json.loads(tool["content"][0]["text"])
            assert receipt["confirmed"] is False
            peer.stdin.close()
        send({"method": "session/update", "params": {"sessionId": "assignment-acp",
              "update": {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": receipt["task_id"]}}}})
        result = {"stopReason": "end_turn"}
    else:
        raise AssertionError(method)
    send({"id": request["id"], "result": result})
'''


def test_codebuddy_acp_uses_the_existing_manager_assignment_queue(tmp_path, monkeypatch):
    from goodteam_os.manager_app_server import close_runtimes

    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: fake\n")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    execute_team_commands(tmp_path, "DEMO", [{"kind": "change_manager_runtime", "provider": "codebuddy"}],
                          actor="user", surface="team_ui", config_path=config)
    binary = tmp_path / "codebuddy"
    binary.write_text(f"#!{sys.executable}\n" + ACP_PROTOCOL)
    binary.chmod(0o755)
    monkeypatch.setenv("GOODTEAM_CODEBUDDY_PATH", str(binary))
    monkeypatch.setenv("GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS", "10")
    service = TaskAssistantService(tmp_path, project_config=config)
    monkeypatch.setattr(service, "_readiness_state", lambda *_: {"project_mode": "existing_project", "lifecycle": "ready"})
    request = dict(project_key="DEMO", root=source, text="请委派 Research 读取 README。", message_id="acp-assignment")
    try:
        session = service.send_message(**request)["session"]
        receipt = session["manager_app_server"]["requests"]["acp-assignment"]["result"]
        task, = LocalTaskStore(source, "DEMO").list_tasks()
        queued, = session["queued_task_proposals"]
        assert queued["project_task_id"] == receipt["text"] == task["id"]
        assert task["description"] == request["text"] and not queued["confirmed"]
        assert receipt["manager_execution"]["adapter"] == "codebuddy-acp"
        assert not WorkerJobStore(source).list_jobs()
        assert service.send_message(**request)["session"]["queued_task_proposals"] == [queued]
    finally:
        close_runtimes()


@pytest.fixture
def manager_protocol(tmp_path, monkeypatch):
    from goodteam_os.manager_app_server import ManagerAppServer, close_runtimes

    script, calls = tmp_path / 'manager_protocol.py', tmp_path / 'manager_calls.jsonl'
    script.write_text(MANAGER_PROTOCOL)
    monkeypatch.setenv('GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS', '10')
    monkeypatch.setattr(ManagerAppServer, '_runtime_config', lambda self: (
        [sys.executable, '-u', str(script), str(calls)], dict(os.environ),
        {'model': 'manager-fixture', 'reasoning_depth': 'high', 'additional_prompt': '', 'provider_connection': None},
        'assignment-fixture'))
    close_runtimes()
    yield calls
    close_runtimes()


@pytest.fixture
def assignment_git(monkeypatch):
    """Inject only this offline fixture's Git; host ownership is unverified here.

    Repository snapshot tests cover production executable admission separately.
    This protocol test retains exact executable identity and real isolated Git
    operations, including on containers whose system UID ownership is remapped.
    """
    if sys.platform == "darwin":
        return  # 使用生产路径解析已安装的 Command Line Tools Git。
    from goodteam_os import dispatch_workspace, repository_snapshot
    from goodteam_os._trusted_system_executable import (
        SYSTEM_EXECUTION_PATH,
        SYSTEM_GIT_CANDIDATES,
        ExecutableIdentity,
    )

    # 按宿主平台选择现有 Git，避免在 macOS 依赖 Xcode 启动器。
    executable = next(path for path, _ in SYSTEM_GIT_CANDIDATES if os.access(path, os.X_OK))
    value = os.stat(executable, follow_symlinks=False)
    identity = ExecutableIdentity(value.st_dev, value.st_ino, value.st_mode)
    def fixture_executable(root, operation):
        return executable, SYSTEM_EXECUTION_PATH, identity
    monkeypatch.setattr(repository_snapshot, '_trusted_git_executable', fixture_executable)
    monkeypatch.setattr(dispatch_workspace, '_trusted_git_executable', fixture_executable)


def test_manager_assignment_returns_isolated_research_output_once(tmp_path, monkeypatch, manager_protocol, assignment_git):
    source = repository(tmp_path / 'source')
    (source / 'README.md').write_text('Hello from the project. The intended audience is not specified.\n')
    monkeypatch.setattr('goodteam_os.workers.default_embedder', lambda: None)
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n'
                      'worker_type_settings:\n  research:\n    desired_instances: 2\n')
    ProjectRegistry().register_project(config, root=source, key='DEMO')
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    binary = binary_dir / 'codex'
    release = tmp_path / 'release_worker'
    binary.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('assignment protocol fixture')
    raise SystemExit()
prompt = sys.stdin.read()
assert '这个项目是干嘛的' in prompt
assert 'RESEARCH_ROLE_RUNTIME' in prompt
assert 'Explain the product purpose from retained evidence.' in prompt
assert 'README evidence; unresolved audience question.' in prompt
assert 'Read repository only; do not implement.' in prompt
assert 'Identify the greeting and remaining uncertainty.' in prompt
assert sys.argv[sys.argv.index('--model') + 1] == 'research-role-fixture'
assert Path('README.md').read_text() == 'Hello from the project. The intended audience is not specified.\\n'
print(json.dumps({'type': 'item.completed', 'item': {'id': 'progress', 'type': 'agent_message',
    'text': 'Observed greeting in README; checking the audience.'}}), flush=True)
deadline = time.monotonic() + 15
while not Path(os.environ['ASSIGNMENT_RELEASE']).exists():
    assert time.monotonic() < deadline, 'Worker was not released by the test'
    time.sleep(0.02)
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Observed greeting in README. Remaining: independent review.\\n')
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 5}}), flush=True)
''')
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('ASSIGNMENT_RELEASE', str(release))
    team = ensure_team_state(tmp_path, 'DEMO', config)
    assert resolve_worker_from_state(team, 'research')['worker_id'] == 'research-a'
    execute_team_commands(tmp_path, 'DEMO', [
        {'kind': 'change_worker_runtime', 'worker_id': 'research-a', 'model': 'research-role-fixture',
         'additional_prompt': 'RESEARCH_ROLE_RUNTIME'},
    ], actor='user', surface='team_ui', config_path=config)
    service = TaskAssistantService(tmp_path, project_config=config)
    # This fixture exercises native orchestration from an already-ready project.
    monkeypatch.setattr(service, '_readiness_state', lambda *_: {'project_mode': 'existing_project', 'lifecycle': 'ready'})
    monkeypatch.setattr(service, 'session', lambda *_, **__: TaskAssistantSessionStore(tmp_path, 'DEMO').read())
    request = dict(project_key='DEMO', root=source, text='这个项目是干嘛的', message_id='research')
    result = service.send_message(**request)
    queued = result['session']['queued_task_proposals']
    assert not result['session']['proposed_task']
    assert len(queued) == len(LocalTaskStore(source, 'DEMO').list_tasks()) == 1
    task = LocalTaskStore(source, 'DEMO').get_task(queued[0]['project_task_id'])
    assert task['role'] == 'research' and task['execution_policy'] == 'read_only'
    assert 'worker_id' not in task and 'worker_id' not in queued[0]['assignment']
    assert task['description'] == queued[0]['exact_request'] == request['text']
    assert task['delegation']['source_messages'] == [{'message_id': 'research', 'content': request['text']}]
    assert not queued[0]['confirmed']
    calls = manager_protocol.read_bytes()
    service.send_message(**request)
    assert manager_protocol.read_bytes() == calls
    args = dict(workspace=tmp_path, project_key='DEMO', root=source, project_config=config,
                proposal_ids=[queued[0]['id']], expected_revisions={queued[0]['id']: 1})
    with pytest.raises(ValueError, match='Confirm'):
        run_queued_iteration_batch(**args)
    from goodteam_os import dispatch

    created_jobs = []
    create = dispatch._create_track_job
    def observe_creation(*args, **kwargs):
        actual = create(*args, **kwargs)
        assert actual['role'] == 'research' and actual['id'] != actual['worker_id']
        assert actual['model'] == 'research-role-fixture' and actual['additional_prompt'] == 'RESEARCH_ROLE_RUNTIME'
        persisted = next(item for item in WorkerJobStore(source).list_jobs() if item['id'] == actual['id'])
        assert persisted['worker_id'] == actual['worker_id']
        created_jobs.append(actual['id'])
        return actual
    monkeypatch.setattr(dispatch, '_create_track_job', observe_creation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(run_queued_iteration_batch, **args, confirmed=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if running.done():
                    pytest.fail(f'Batch ended before native progress: {running.result()}')
                jobs = WorkerJobStore(source).list_jobs()
                if jobs and WorkerJobStore(source).native_events(jobs[0]['id'])['events']:
                    break
                time.sleep(0.02)
            else:
                pytest.fail('Native Worker progress did not arrive while the batch was running.')
            additional = service.send_message(project_key='DEMO', root=source,
                text='结果请标明受众仍待确认。', message_id='additional')
            assert additional['session']['assistant_requests']['additional']['status'] == 'succeeded'
            assert not running.done()
        finally:
            release.touch()
        result = running.result(timeout=10)
    assert result['status'] == 'review', result
    job = result['report']['jobs'][0]
    assert job['role'] == 'research' and job['write_scope_verified']
    assert created_jobs == [job['job_id']]
    persisted = WorkerJobStore(source).list_jobs()[0]
    # The immutable task intent protects the same delegated context on replay.
    from goodteam_os.tasks import project_task_intent
    assert project_task_intent(task)['delegation'] == task['delegation']
    assert persisted['role'] == 'research'
    timeline = TaskAssistantSessionStore(tmp_path, 'DEMO').read()['timeline']
    replies = [item for item in timeline if item['message_id'].startswith('batch-result:')]
    assert len(replies) == 1
    assert 'Observed greeting in README' in replies[0]['content']
    assert 'independent review' in replies[0]['content']
    returned = replies[0]['details']['source_evidence']['worker_results'][0]
    assert returned['job_id'] == job['job_id'] and 'worker_id' not in returned
    run_queued_iteration_batch(**args, confirmed=True)
    assert TaskAssistantSessionStore(tmp_path, 'DEMO').read()['timeline'] == timeline
    assert LocalTaskStore(source, 'DEMO').get_task(task['id'])['status'] == 'review'
    checked = service.send_message(project_key='DEMO', root=source,
        text='请检查真实成果并说明下一步。', message_id='check-result')['session']
    assert checked['assistant_requests']['check-result']['status'] == 'succeeded'
    assert any('下一步需要独立审查，尚未验收' in item.get('content', '') for item in checked['timeline'])
    requests = [json.loads(line) for line in manager_protocol.read_text().splitlines()]
    assert [item['method'] for item in requests if item.get('method') in {'thread/start', 'thread/resume'}] == [
        'thread/start', 'thread/resume', 'thread/resume']
    assert len([item for item in requests if item.get('method') == 'turn/start']) == 3
    assert len(WorkerJobStore(source).list_jobs()) == 1  # Manager creates no Worker job.
